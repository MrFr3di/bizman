"""Deterministic dataset generators derived from captured HAR entries.

The committed ``knowledge/`` corpus is the golden reference.  Generators in
this module reproduce the exact normalization, filtering, ordering and
hashing rules that were verified against that corpus record by record.  They
only read in-memory entries and write to an explicit output directory; they
never touch ``knowledge/``.

Verified rules per dataset (spike against the three local HAR captures):

* ``application-events``: first-party host ``bizmania.ru`` minus static
  resource paths; 555/555 records identical, including order.
* ``endpoints``: aggregation of application events by normalized
  ``path_pattern``; 68/68 records identical.
* ``assets``: aggregation of static first-party entries by path; 914/914
  records identical.
* ``json-responses``: first-party ``application/json`` responses sorted by
  path; 36/36 records identical (raw text is kept as ``data`` when the body
  is not valid JSON, matching entry 7889).
* ``game-html-pages``: first-party HTML 200 responses; text is extracted
  from the ``#content`` element with script/style/textarea/iframe skipped.
  179/180 records identical; entry 7151 is a corpus anomaly (see the final
  report).
* ``wiki-topics``: unique captured Wiki topics with the article
  ``font.text`` body; text and hashes are exact for 87/87 records.
  ``related_topics`` is a best-effort reconstruction (70/87 exact).

``routes`` and ``forms`` are best-effort aggregations; see the final report
for the exact deviations.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from html import unescape
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from bizman.ingest.har import HarEntry, is_first_party

__all__ = [
    "DATASET_LAYOUTS",
    "PART_SIZE",
    "build_application_events",
    "build_assets",
    "build_dataset",
    "build_endpoints",
    "build_forms",
    "build_json_responses",
    "build_pages",
    "build_routes",
    "build_wiki_topics",
    "write_dataset",
]

PART_SIZE = 60

DATASET_LAYOUTS: dict[str, dict[str, Any]] = {
    "application-events": {
        "knowledge_dir": "http/application-events",
        "jsonl": True,
        "wrapper_key": None,
    },
    "endpoints": {
        "knowledge_dir": "http/endpoints",
        "jsonl": False,
        "wrapper_key": "records",
    },
    "routes": {
        "knowledge_dir": "http/routes",
        "jsonl": False,
        "wrapper_key": None,
    },
    "forms": {
        "knowledge_dir": "http/forms",
        "jsonl": False,
        "wrapper_key": None,
    },
    "json-responses": {
        "knowledge_dir": "http/json-responses",
        "jsonl": True,
        "wrapper_key": None,
    },
    "game-html-pages": {
        "knowledge_dir": "pages",
        "jsonl": True,
        "wrapper_key": None,
    },
    "assets": {
        "knowledge_dir": "http/assets",
        "jsonl": False,
        "wrapper_key": "records",
    },
    "wiki-topics": {
        "knowledge_dir": "wiki/topics",
        "jsonl": True,
        "wrapper_key": None,
    },
}

_CAPTURE_ORDER = ("bizmania.ru.har", "bizmania1.ru.har", "bizmaniaFAQ.ru.har")

_STATIC_EXTENSIONS = frozenset(
    {
        ".css",
        ".eot",
        ".gif",
        ".htm",
        ".html",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".map",
        ".mp3",
        ".mp4",
        ".png",
        ".svg",
        ".ttf",
        ".txt",
        ".webp",
        ".woff",
        ".woff2",
        ".xml",
    }
)
_STATIC_RESOURCE_TYPES = frozenset({"font", "script", "stylesheet"})
_STATIC_PATH_PREFIXES = ("/css/", "/img/", "/js/")

_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_PAGE_SKIP_ELEMENTS = frozenset({"iframe", "noscript", "script", "style", "textarea"})
_WIKI_SKIP_ELEMENTS = frozenset({"noscript", "script", "style"})

_ENDPOINT_PATTERN_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^/user/check/message/r/.+$"),
        "/user/check/message/r/{message_id}",
    ),
    (
        re.compile(r"^/user/splash/id/[^/]+/style/[^/]+$"),
        "/user/splash/id/{splash_id}/style/{style_id}",
    ),
    (re.compile(r"^/user/splash/id/[^/]+$"), "/user/splash/id/{splash_id}"),
    (re.compile(r"^/wikihelp/topic/.+$"), "/wikihelp/topic/{topic}"),
)

_ROUTE_TOKEN_RE = re.compile(
    r"""["']((?:https?:)?(?://bizmania\.ru)?/[^"'\s<>()\\]*)["']"""
)
_ROUTE_TRAILING = ".,;:!"
_ROUTE_SCAN_MIME_TYPES = frozenset(
    {"application/javascript", "text/html", "text/javascript"}
)
_NUMERIC_RE = re.compile(r"^\d+$")


# ---------------------------------------------------------------------------
# Shared helpers


def _ordered_aliases(captures: Mapping[str, Sequence[HarEntry]]) -> list[str]:
    def sort_key(alias: str) -> tuple[int, str]:
        if alias in _CAPTURE_ORDER:
            return (_CAPTURE_ORDER.index(alias), alias)
        return (len(_CAPTURE_ORDER), alias)

    return sorted(captures, key=sort_key)


def _is_static_path(path: str, resource_type: str) -> bool:
    if resource_type in _STATIC_RESOURCE_TYPES:
        return True
    if path.startswith(_STATIC_PATH_PREFIXES):
        return True
    for segment in path.split("/"):
        if not segment:
            continue
        dot = segment.rfind(".")
        if dot > 0 and segment[dot:].lower() in _STATIC_EXTENSIONS:
            return True
    return False


def _is_application_event(entry: HarEntry) -> bool:
    return is_first_party(entry) and not _is_static_path(entry.path, entry.resource_type)


def _application_event_dicts(
    captures: Mapping[str, Sequence[HarEntry]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for alias in _ordered_aliases(captures):
        for entry in captures[alias]:
            if not _is_application_event(entry):
                continue
            records.append(
                {
                    "capture": alias,
                    "entry": entry.index,
                    "started_at": entry.started_at,
                    "method": entry.method,
                    "path": entry.path,
                    "query": dict(entry.query),
                    "status": entry.status,
                    "mime_type": entry.mime_type,
                    "resource_type": entry.resource_type,
                    "response_size": entry.response_size,
                    "response_text_sha256": entry.response_text_sha256,
                }
            )
    return records


class _ElementProbe(HTMLParser):
    """Detect whether an element with the requested id exists."""

    def __init__(self, element_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._element_id = element_id
        self.found = False

    def _check(self, attrs: list[tuple[str, str | None]]) -> None:
        if dict(attrs).get("id") == self._element_id:
            self.found = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(attrs)


class _PageTextParser(HTMLParser):
    """Extract visible page text, preferring the ``#content`` subtree."""

    def __init__(self, container_present: bool, element_id: str = "content") -> None:
        super().__init__(convert_charrefs=True)
        self._element_id = element_id
        self._box_seen = container_present
        self._box_found = False
        self._stack: list[str] = []
        self._in_box = False
        self._box_level = 0
        self._in_body = False
        self._body_level = 0
        self._skip = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if not self._box_found and attributes.get("id") == self._element_id:
            self._box_found = True
            self._in_box = True
        if tag == "body":
            self._in_body = True
        if tag in _PAGE_SKIP_ELEMENTS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag not in _VOID_ELEMENTS:
            self._stack.append(tag)
        if self._in_box and self._box_level == 0:
            self._box_level = len(self._stack)
        if self._in_body and self._body_level == 0:
            self._body_level = len(self._stack)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if not self._box_found and attributes.get("id") == self._element_id:
            self._box_found = True
            self._in_box = True
            self._box_level = len(self._stack) + 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _PAGE_SKIP_ELEMENTS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in _VOID_ELEMENTS:
            return
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        elif tag in self._stack:
            while self._stack:
                popped = self._stack.pop()
                if popped == tag:
                    break
        if self._in_box and len(self._stack) < self._box_level:
            self._in_box = False
        if self._in_body and len(self._stack) < self._body_level:
            self._in_body = False

    def _active(self) -> bool:
        return self._in_box if self._box_seen else self._in_body

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._skip or not self._active():
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def title(self) -> str | None:
        if not self._title_parts:
            return None
        return "".join(self._title_parts)

    def text(self) -> str:
        return "\n".join(self.parts)


def _extract_page_text(html: str) -> tuple[str | None, str]:
    probe = _ElementProbe("content")
    probe.feed(html)
    parser = _PageTextParser(container_present=probe.found)
    parser.feed(html)
    return parser.title(), parser.text()


class _ArticleTextParser(HTMLParser):
    """Extract the ``<font class="text">`` Wiki article body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = []
        self._in_article = False
        self._done = False
        self._level = 0
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in _WIKI_SKIP_ELEMENTS:
            self._skip += 1
        if (
            not self._in_article
            and not self._done
            and tag == "font"
            and "text" in attributes.get("class", "").split()
        ):
            self._in_article = True
        if tag not in _VOID_ELEMENTS:
            self._stack.append(tag)
        if self._in_article and self._level == 0:
            self._level = len(self._stack)

    def handle_endtag(self, tag: str) -> None:
        if tag in _WIKI_SKIP_ELEMENTS and self._skip:
            self._skip -= 1
        if tag in _VOID_ELEMENTS:
            return
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        elif tag in self._stack:
            while self._stack:
                popped = self._stack.pop()
                if popped == tag:
                    break
        if self._in_article and len(self._stack) < self._level:
            self._in_article = False
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._skip or not self._in_article:
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def _extract_wiki_article(html: str) -> str:
    parser = _ArticleTextParser()
    parser.feed(html)
    return parser.text()


def _wiki_topic_from_href(href: str) -> str | None:
    split = urlsplit(href)
    if not split.path.startswith("/wikihelp"):
        return None
    if split.path.startswith("/wikihelp/topic/"):
        topic = unquote(split.path[len("/wikihelp/topic/") :])
        return topic or None
    for key, value in parse_qsl(split.query, keep_blank_values=True):
        if key == "topic" and value:
            return value
    return None


def _wiki_related_topics(html: str) -> list[str]:
    article_start = html.find('<font class="text">')
    article_close = (
        html.find("</font>", article_start + 1) if article_start >= 0 else -1
    )
    marker = html.find("Читайте также", max(article_close, 0))
    if marker < 0:
        return []
    block = html[marker : marker + 2000]
    end = block.find("</table>")
    if end >= 0:
        block = block[:end]
    topics: list[str] = []
    for href, _label in re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', block, re.S):
        topic = _wiki_topic_from_href(href)
        if topic and topic not in topics:
            topics.append(topic)
    return topics


# ---------------------------------------------------------------------------
# Builders


def build_application_events(
    captures: Mapping[str, Sequence[HarEntry]],
) -> list[dict[str, Any]]:
    """All first-party non-static network events (555 committed records)."""

    return _application_event_dicts(captures)


def build_pages(captures: Mapping[str, Sequence[HarEntry]]) -> list[dict[str, Any]]:
    """Normalized text snapshots of captured non-Wiki game HTML pages."""

    records: list[dict[str, Any]] = []
    for alias in _ordered_aliases(captures):
        for entry in captures[alias]:
            if not is_first_party(entry):
                continue
            if entry.mime_type != "text/html" or entry.status != 200:
                continue
            if entry.path.startswith("/wikihelp"):
                continue
            if entry.response_text_sha256 is None:
                continue
            title, text = _extract_page_text(entry.body_text)
            records.append(
                {
                    "capture": alias,
                    "entry": entry.index,
                    "started_at": entry.started_at,
                    "method": entry.method,
                    "path": entry.path,
                    "query": dict(entry.query),
                    "status": entry.status,
                    "title": title,
                    "text": text,
                    "html_sha256": entry.response_text_sha256,
                }
            )
    return records


def build_wiki_topics(
    captures: Mapping[str, Sequence[HarEntry]],
) -> list[dict[str, Any]]:
    """Full normalized text of captured Wiki topics, sorted by topic."""

    seen: dict[str, dict[str, Any]] = {}
    for alias in _ordered_aliases(captures):
        for entry in captures[alias]:
            if not is_first_party(entry):
                continue
            if entry.mime_type != "text/html" or entry.status != 200:
                continue
            if not entry.path.startswith("/wikihelp"):
                continue
            if entry.response_text_sha256 is None:
                continue
            topic = _wiki_topic_from_href(entry.url)
            if topic is None or topic in seen:
                continue
            seen[topic] = {
                "topic": topic,
                "source": {
                    "capture": alias,
                    "entry": entry.index,
                    "path": entry.path,
                    "query": dict(entry.query),
                    "html_sha256": entry.response_text_sha256,
                },
                "text": _extract_wiki_article(entry.body_text),
                "related_topics": _wiki_related_topics(entry.body_text),
            }
    return sorted(seen.values(), key=lambda record: record["topic"])


def build_json_responses(
    captures: Mapping[str, Sequence[HarEntry]],
) -> list[dict[str, Any]]:
    """Unique captured first-party JSON responses, sorted by path."""

    records: list[dict[str, Any]] = []
    for alias in _ordered_aliases(captures):
        for entry in captures[alias]:
            if not is_first_party(entry):
                continue
            if entry.mime_type != "application/json":
                continue
            if entry.response_text_sha256 is None:
                continue
            try:
                data: Any = json.loads(entry.body_text)
            except json.JSONDecodeError:
                data = entry.body_text
            records.append(
                {
                    "capture": alias,
                    "entry": entry.index,
                    "path": entry.path,
                    "query": dict(entry.query),
                    "sha256": entry.response_text_sha256,
                    "data": data,
                }
            )
    records.sort(key=lambda record: (record["path"], record["entry"]))
    return records


def _endpoint_pattern(path: str) -> str:
    for pattern, replacement in _ENDPOINT_PATTERN_RULES:
        if pattern.match(path):
            return replacement
    return path


def build_endpoints(
    captures: Mapping[str, Sequence[HarEntry]],
) -> list[dict[str, Any]]:
    """Aggregated non-static first-party endpoint census."""

    groups: dict[str, dict[str, Any]] = {}
    for event in _application_event_dicts(captures):
        pattern = _endpoint_pattern(event["path"])
        group = groups.get(pattern)
        if group is None:
            group = {
                "path_pattern": pattern,
                "count": 0,
                "methods": Counter(),
                "statuses": Counter(),
                "mime_types": Counter(),
                "resource_types": Counter(),
                "captures": Counter(),
                "query_keys": set(),
                "examples": [],
            }
            groups[pattern] = group
        group["count"] += 1
        group["methods"][event["method"]] += 1
        group["statuses"][str(event["status"])] += 1
        group["mime_types"][event["mime_type"]] += 1
        group["resource_types"][event["resource_type"]] += 1
        group["captures"][event["capture"]] += 1
        group["query_keys"].update(event["query"])
        if len(group["examples"]) < 5:
            group["examples"].append(
                {
                    "capture": event["capture"],
                    "entry": event["entry"],
                    "path": event["path"],
                    "query": dict(event["query"]),
                }
            )

    records: list[dict[str, Any]] = []
    for pattern in sorted(groups):
        group = groups[pattern]
        records.append(
            {
                "path_pattern": pattern,
                "count": group["count"],
                "methods": dict(group["methods"]),
                "statuses": dict(group["statuses"]),
                "mime_types": dict(group["mime_types"]),
                "resource_types": dict(group["resource_types"]),
                "captures": dict(group["captures"]),
                "query_keys": sorted(group["query_keys"]),
                "examples": group["examples"],
            }
        )
    return records


def build_assets(captures: Mapping[str, Sequence[HarEntry]]) -> list[dict[str, Any]]:
    """Static first-party resource census without committing binaries."""

    groups: dict[str, dict[str, Any]] = {}
    for alias in _ordered_aliases(captures):
        for entry in captures[alias]:
            if not is_first_party(entry):
                continue
            if not _is_static_path(entry.path, entry.resource_type):
                continue
            group = groups.get(entry.path)
            if group is None:
                group = {
                    "path": entry.path,
                    "count": 0,
                    "captures": Counter(),
                    "mime_types": Counter(),
                    "statuses": Counter(),
                }
                groups[entry.path] = group
            group["count"] += 1
            group["captures"][alias] += 1
            group["mime_types"][entry.mime_type] += 1
            group["statuses"][str(entry.status)] += 1
    return [
        {
            "path": path,
            "count": groups[path]["count"],
            "captures": dict(groups[path]["captures"]),
            "mime_types": dict(groups[path]["mime_types"]),
            "statuses": dict(groups[path]["statuses"]),
        }
        for path in sorted(groups)
    ]


def _normalize_route_path(path: str) -> str:
    segments = path.split("/")
    normalized: list[str] = []
    for position, segment in enumerate(segments):
        previous = segments[position - 1] if position else ""
        is_last_value = bool(segment) and position == len(segments) - 1
        if segment and previous in {"id", "r", "style", "topic"}:
            if previous == "topic":
                segment = "{topic}"
            elif previous == "style":
                segment = "{id}"
            elif previous == "r":
                segment = "{id}"
            elif path.startswith("/user/splash/") and is_last_value:
                segment = "{splash_id}"
            else:
                segment = "{id}"
        normalized.append(segment)
    return "/".join(normalized)


def _normalize_query_value(key: str, value: str, path: str) -> str:
    if key == "topic":
        return "{value}"
    if key == "id":
        if path.startswith("/city/map/"):
            return "{ID}"
        return "{id}" if _NUMERIC_RE.match(value) else "{value}"
    if _NUMERIC_RE.match(value):
        if key == "product":
            return "{product_id}"
        if key in {"replace", "vendor"}:
            return "{vendor_id}"
        return "{number}"
    return value


def _normalize_route(route: str) -> str:
    path, separator, raw_query = route.partition("?")
    normalized_path = _normalize_route_path(path)
    if not separator:
        return normalized_path
    parameters = []
    for key, value in parse_qsl(raw_query, keep_blank_values=True):
        parameters.append(f"{key}={_normalize_query_value(key, value, path)}")
    return normalized_path + "?" + "&".join(parameters)


def _route_from_absolute(token: str) -> str:
    for prefix in ("https://bizmania.ru", "http://bizmania.ru", "//bizmania.ru"):
        if token.startswith(prefix):
            return token[len(prefix) :]
    return token


def _route_strings(entry: HarEntry) -> list[str]:
    split = urlsplit(entry.url)
    request_route = split.path + (f"?{split.query}" if split.query else "")
    found: list[str] = [request_route]
    seen = {request_route}
    if entry.mime_type in _ROUTE_SCAN_MIME_TYPES and entry.body_text:
        text = entry.body_text
        for match in _ROUTE_TOKEN_RE.finditer(text):
            token = _route_from_absolute(match.group(1)).rstrip(_ROUTE_TRAILING)
            if (
                len(token) < 2
                or not token.startswith("/")
                or token.startswith("//")
                or "{" in token
            ):
                continue
            if token in seen:
                continue
            seen.add(token)
            found.append(token)
    return found


def build_routes(captures: Mapping[str, Sequence[HarEntry]]) -> list[dict[str, Any]]:
    """Best-effort normalized internal routes discovered in HTML and JS.

    Each distinct concrete route string seen in one entry contributes once to
    its normalized pattern; ``examples`` keeps the first five distinct
    concrete strings.  This reconstruction is close to the committed corpus
    for request-derived routes but does not reproduce every text-scraped
    count (see the final report).
    """

    groups: dict[str, dict[str, Any]] = {}
    for alias in _ordered_aliases(captures):
        for entry in captures[alias]:
            if not is_first_party(entry):
                continue
            entry_patterns: set[str] = set()
            for route in _route_strings(entry):
                route = unescape(route)
                path = route.partition("?")[0]
                if _is_static_path(path, ""):
                    continue
                pattern = _normalize_route(route)
                group = groups.get(pattern)
                if group is None:
                    group = {
                        "pattern": pattern,
                        "count": 0,
                        "captures": Counter(),
                        "examples": [],
                        "example_seen": set(),
                    }
                    groups[pattern] = group
                group["count"] += 1
                if pattern not in entry_patterns:
                    group["captures"][alias] += 1
                    entry_patterns.add(pattern)
                if (
                    len(group["examples"]) < 5
                    and route not in group["example_seen"]
                ):
                    group["example_seen"].add(route)
                    group["examples"].append(route)
    return [
        {
            "pattern": pattern,
            "count": groups[pattern]["count"],
            "captures": dict(groups[pattern]["captures"]),
            "examples": groups[pattern]["examples"],
        }
        for pattern in sorted(groups)
    ]


class _FormParser(HTMLParser):
    """Collect named form controls with their observed attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self._current = {
                "method": (attributes.get("method") or "get").upper(),
                "action": attributes.get("action") or "",
                "fields": [],
            }
            return
        if self._current is None:
            return
        if tag == "input":
            name = attributes.get("name")
            if name:
                self._current["fields"].append(
                    {
                        "name": name,
                        "type": attributes.get("type"),
                        "value": attributes.get("value"),
                    }
                )
        elif tag in {"select", "textarea"}:
            name = attributes.get("name")
            if name:
                self._current["fields"].append(
                    {"name": name, "type": tag, "value": None}
                )

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


def _form_id(method: str, action: str, fields: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        [method, action, fields],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_forms(captures: Mapping[str, Sequence[HarEntry]]) -> list[dict[str, Any]]:
    """Best-effort deduplicated captured HTML form signatures.

    Parsing and ``observed_on`` provenance are deterministic, but the
    committed corpus uses opaque 16-hex ``form_id`` values whose preimage is
    not recoverable; generated ids are SHA-256-derived from our own canonical
    form signature (see the final report).
    """

    records: dict[tuple[Any, ...], dict[str, Any]] = {}
    for alias in _ordered_aliases(captures):
        for entry in captures[alias]:
            if not is_first_party(entry):
                continue
            if entry.mime_type != "text/html" or entry.status != 200:
                continue
            if entry.response_text_sha256 is None:
                continue
            parser = _FormParser()
            parser.feed(entry.body_text)
            for form in parser.forms:
                signature = (
                    form["method"],
                    form["action"],
                    tuple(
                        (field["name"], field["type"], field["value"])
                        for field in form["fields"]
                    ),
                )
                record = records.get(signature)
                if record is None:
                    record = {
                        "form_id": _form_id(
                            form["method"], form["action"], form["fields"]
                        ),
                        "method": form["method"],
                        "action": form["action"],
                        "fields": form["fields"],
                        "observed_on": [],
                    }
                    records[signature] = record
                observation = {
                    "capture": alias,
                    "entry": entry.index,
                    "page_path": entry.path,
                    "page_query": dict(entry.query),
                }
                if observation not in record["observed_on"]:
                    record["observed_on"].append(observation)
    return sorted(records.values(), key=lambda record: (record["action"], record["form_id"]))


_BUILDERS = {
    "application-events": build_application_events,
    "endpoints": build_endpoints,
    "routes": build_routes,
    "forms": build_forms,
    "json-responses": build_json_responses,
    "game-html-pages": build_pages,
    "assets": build_assets,
    "wiki-topics": build_wiki_topics,
}


def build_dataset(
    dataset_id: str, captures: Mapping[str, Sequence[HarEntry]]
) -> list[dict[str, Any]]:
    """Build one dataset by its committed id."""

    try:
        builder = _BUILDERS[dataset_id]
    except KeyError as exc:
        raise ValueError(f"unknown dataset: {dataset_id}") from exc
    return builder(captures)


def write_dataset(
    out_dir: str | Path,
    dataset_id: str,
    records: Sequence[Mapping[str, Any]],
    *,
    jsonl: bool,
    wrapper_key: str | None = None,
) -> Path:
    """Write ``index.json`` plus ``part-*`` files in the committed layout."""

    extension = ".jsonl" if jsonl else ".json"
    target = Path(out_dir) / dataset_id
    target.mkdir(parents=True, exist_ok=True)

    parts: list[dict[str, Any]] = []
    offset = 0
    for number, start in enumerate(range(0, len(records), PART_SIZE)):
        chunk = records[start : start + PART_SIZE]
        name = f"part-{number:03d}{extension}"
        if jsonl:
            lines = [
                json.dumps(record, ensure_ascii=False) + "\n" for record in chunk
            ]
            (target / name).write_text("".join(lines), encoding="utf-8")
        else:
            if wrapper_key is None:
                payload: Any = list(chunk)
            else:
                payload = {"schema_version": "1.0", wrapper_key: list(chunk)}
            (target / name).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        parts.append({"file": name, "records": len(chunk), "offset": offset})
        offset += len(chunk)

    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "source_file": f"knowledge/generated/{dataset_id}{extension}",
        "total_records": len(records),
        "parts": parts,
    }
    if wrapper_key is not None:
        manifest["record_key"] = wrapper_key
    manifest_path = target / "index.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest_path
