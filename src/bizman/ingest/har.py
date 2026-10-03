"""Bounded, fail-closed reader for HAR captures.

The reader is deliberately passive: it only decodes what a HAR already
contains and never performs network access.  Two size ceilings keep a hostile
or accidental capture from exhausting the process: the whole file must fit in
``MAX_HAR_BYTES`` and any single decoded response body must fit in
``MAX_BODY_BYTES``.

Verified normalization rules (checked against the committed
``knowledge/http/application-events`` corpus, 555/555 records):

* ``started_at`` is the HAR ``startedDateTime`` value; already canonical
  RFC 3339 UTC strings are preserved verbatim.
* ``query`` keeps the first value per key, in first-seen order, decoded the
  same way ``urllib.parse.parse_qsl`` decodes query strings.
* ``response_size`` is the HAR ``content.size`` when present, otherwise the
  number of UTF-8 bytes of the response text, otherwise ``0`` for responses
  without a readable body.
* ``response_text_sha256`` is SHA-256 over the exact ``content.text`` string
  encoded as UTF-8.  For base64-encoded bodies this hashes the encoded text,
  not the decoded bytes; that is the rule the committed corpus used (entries
  906/907/908/909/7825).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit

__all__ = [
    "FIRST_PARTY_HOSTS",
    "MAX_BODY_BYTES",
    "MAX_HAR_BYTES",
    "HarEntry",
    "HarFormatError",
    "is_first_party",
    "load_har",
]

MAX_HAR_BYTES = 256 * 1024 * 1024
MAX_BODY_BYTES = 8 * 1024 * 1024

FIRST_PARTY_HOSTS = frozenset({"bizmania.ru"})

_CANONICAL_STARTED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class HarFormatError(ValueError):
    """HAR bytes, JSON shape or a single entry payload is invalid."""


@dataclass(frozen=True, slots=True)
class HarEntry:
    """One readable HAR entry with its position in ``log.entries``."""

    index: int
    started_at: str
    method: str
    url: str
    path: str
    query: dict[str, str]
    status: int
    mime_type: str
    resource_type: str
    body_text: str
    response_size: int
    response_text_sha256: str | None


def is_first_party(entry: HarEntry) -> bool:
    """Return True when the request host is a canonical BizMania host."""

    return urlsplit(entry.url).netloc in FIRST_PARTY_HOSTS


def load_har(path: str | Path) -> tuple[HarEntry, ...]:
    """Read a HAR file into immutable entries, failing closed on any defect."""

    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise HarFormatError(f"cannot stat HAR file: {source}") from exc
    if size > MAX_HAR_BYTES:
        raise HarFormatError(
            f"HAR file exceeds {MAX_HAR_BYTES} bytes: {source} ({size} bytes)"
        )
    try:
        raw = source.read_bytes()
        document = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarFormatError(f"cannot decode HAR JSON: {source}") from exc
    return _parse_document(document)


def _parse_document(document: object) -> tuple[HarEntry, ...]:
    if not isinstance(document, dict):
        raise HarFormatError("HAR root must be a JSON object")
    log = document.get("log")
    if not isinstance(log, dict):
        raise HarFormatError("HAR root is missing a log object")
    raw_entries = log.get("entries")
    if not isinstance(raw_entries, list):
        raise HarFormatError("HAR log.entries must be a JSON array")
    return tuple(
        _parse_entry(index, raw_entry) for index, raw_entry in enumerate(raw_entries)
    )


def _parse_entry(index: int, raw_entry: object) -> HarEntry:
    if not isinstance(raw_entry, dict):
        raise HarFormatError(f"entry {index} must be a JSON object")
    request = raw_entry.get("request")
    if not isinstance(request, dict):
        raise HarFormatError(f"entry {index} is missing a request object")
    response = raw_entry.get("response")
    if not isinstance(response, dict):
        raise HarFormatError(f"entry {index} is missing a response object")

    method = request.get("method")
    url = request.get("url")
    if not isinstance(method, str) or not method:
        raise HarFormatError(f"entry {index} request.method must be a string")
    if not isinstance(url, str) or not url:
        raise HarFormatError(f"entry {index} request.url must be a string")

    status = response.get("status")
    if isinstance(status, bool) or not isinstance(status, int):
        raise HarFormatError(f"entry {index} response.status must be an integer")

    started_at = raw_entry.get("startedDateTime")
    if not isinstance(started_at, str) or not started_at:
        raise HarFormatError(f"entry {index} startedDateTime must be a string")

    content = response.get("content", {})
    if not isinstance(content, dict):
        raise HarFormatError(f"entry {index} response.content must be an object")
    mime_type = content.get("mimeType", "")
    if not isinstance(mime_type, str):
        mime_type = ""
    resource_type = raw_entry.get("_resourceType", "")
    if not isinstance(resource_type, str):
        resource_type = ""

    text = content.get("text")
    if text is None:
        body_text = ""
        body_size = 0
        digest: str | None = None
    else:
        if not isinstance(text, str):
            raise HarFormatError(f"entry {index} response content.text must be a string")
        body_bytes = _decode_text_body(index, text, content.get("encoding"))
        if len(body_bytes) > MAX_BODY_BYTES:
            raise HarFormatError(
                f"entry {index} decoded body exceeds {MAX_BODY_BYTES} bytes"
            )
        body_text = (
            body_bytes.decode("utf-8", errors="replace")
            if content.get("encoding") == "base64"
            else text
        )
        body_size = len(text.encode("utf-8"))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    size = content.get("size")
    if isinstance(size, bool) or not isinstance(size, int):
        size = body_size

    split = urlsplit(url)
    return HarEntry(
        index=index,
        started_at=_canonical_started_at(index, started_at),
        method=method,
        url=url,
        path=split.path,
        query=_first_value_query(split.query),
        status=status,
        mime_type=mime_type,
        resource_type=resource_type,
        body_text=body_text,
        response_size=size,
        response_text_sha256=digest,
    )


def _decode_text_body(index: int, text: str, encoding: object) -> bytes:
    if encoding == "base64":
        try:
            return base64.b64decode(text)
        except (binascii.Error, ValueError) as exc:
            raise HarFormatError(
                f"entry {index} response content.text is not valid base64"
            ) from exc
    return text.encode("utf-8")


def _first_value_query(raw_query: str) -> dict[str, str]:
    query: dict[str, str] = {}
    for key, value in parse_qsl(raw_query, keep_blank_values=True):
        if key not in query:
            query[key] = value
    return query


def _canonical_started_at(index: int, value: str) -> str:
    if _CANONICAL_STARTED_AT.match(value):
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HarFormatError(
            f"entry {index} startedDateTime is not RFC 3339: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    utc = parsed.astimezone(timezone.utc)
    millis = utc.microsecond // 1000
    return f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"
