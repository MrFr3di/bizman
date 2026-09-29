from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Mapping

from bizman.current.model import CompanyState, UnitState
from bizman.foundation.fingerprint import canonical_json_bytes
from bizman.sessions.evidence import EvidenceReader


_ROSTER_PATH = "/company/"
_PAGE_SCHEMA_VERSION = "1.0"
_SANITIZER_VERSION = 1
_UNIT_LINE_RE = re.compile(r"^(.+?) #([1-9][0-9]*)$", re.ASCII)
_LEVEL_RE = re.compile(r"^[1-9][0-9]*$", re.ASCII)
_COMPANY_RE = re.compile(r"^Компания\s+(.+)$")
_HEADER_VALUES = frozenset(
    {
        "Город",
        "Предприятие",
        "Уровень",
        "Эффект.",
        "Продукты",
        "Прибыль",
        "Прибыль МСФО",
        "Проблемы",
    }
)


class CompanyUnitsArtifactError(ValueError):
    """Sanitized page evidence is malformed or violates its artifact contract."""


class CompanyUnitsParserIncompatible(ValueError):
    """A recognized company roster no longer matches parser v1 semantics."""


@dataclass(frozen=True, slots=True)
class CompanyRosterProjection:
    company: CompanyState
    units: tuple[UnitState, ...]


def _query_values(query: object, key: str) -> list[str] | None:
    if not isinstance(query, Mapping):
        return None
    value = query.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return value


def _company_id_from_event(event: Mapping[str, object]) -> str | None:
    company_ids = _query_values(event.get("query"), "id")
    if company_ids is None or len(company_ids) != 1:
        return None
    company_id = company_ids[0]
    if (
        not company_id.isascii()
        or not company_id.isdigit()
        or company_id.startswith("0")
    ):
        return None
    return company_id


def is_company_roster_event(event: Mapping[str, object]) -> bool:
    if event.get("event_type") != "http.response_body":
        return False
    if event.get("source") != "cdp.network":
        return False
    if event.get("method") != "GET" or event.get("url_path") != _ROSTER_PATH:
        return False
    if event.get("status_code") != 200:
        return False
    if _company_id_from_event(event) is None:
        return False
    return _query_values(event.get("query"), "tab") == ["units"]


def _load_page_artifact(
    reader: EvidenceReader,
    ref: object,
) -> tuple[str | None, str]:
    if not isinstance(ref, str):
        raise CompanyUnitsArtifactError(
            "company roster response_body_ref must be a verified CAS reference"
        )
    payload = reader.read_verified_artifact(ref)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyUnitsArtifactError(
            "company roster page artifact is not canonical UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise CompanyUnitsArtifactError("company roster page artifact must be an object")
    if set(value) != {
        "schema_version",
        "sanitizer_version",
        "media_type",
        "title",
        "text",
    }:
        raise CompanyUnitsArtifactError(
            "company roster page artifact fields do not match schema v1"
        )
    if canonical_json_bytes(value) != payload:
        raise CompanyUnitsArtifactError(
            "company roster page artifact is not canonically encoded"
        )
    if value.get("schema_version") != _PAGE_SCHEMA_VERSION:
        raise CompanyUnitsArtifactError("unsupported company roster page artifact schema")
    if value.get("sanitizer_version") != _SANITIZER_VERSION:
        raise CompanyUnitsArtifactError("unsupported company roster sanitizer version")
    if value.get("media_type") != "text/html":
        raise CompanyUnitsArtifactError("company roster artifact media_type must be text/html")
    title = value.get("title")
    if title is not None and not isinstance(title, str):
        raise CompanyUnitsArtifactError("company roster artifact title is invalid")
    text = value.get("text")
    if not isinstance(text, str):
        raise CompanyUnitsArtifactError("company roster artifact text is invalid")
    return title, text


def _company_name(lines: tuple[str, ...], title: object) -> str:
    for line in lines:
        match = _COMPANY_RE.fullmatch(line)
        if match is not None:
            name = match.group(1).strip()
            if name:
                return name
    if isinstance(title, str) and title.startswith("Компания "):
        name = title.removeprefix("Компания ").split(" · ", 1)[0].strip()
        if name:
            return name
    raise CompanyUnitsParserIncompatible(
        "recognized company roster does not expose a company name"
    )


def _parse_units(
    *,
    lines: tuple[str, ...],
    company_id: str,
    session_id: str,
    sequence: int,
    observed_at: str,
) -> tuple[UnitState, ...]:
    results: list[UnitState] = []
    seen: set[str] = set()

    for index, line in enumerate(lines):
        match = _UNIT_LINE_RE.fullmatch(line)
        if match is None:
            continue
        if index == 0 or index + 1 >= len(lines):
            raise CompanyUnitsParserIncompatible(
                "company roster unit row is missing city or level"
            )
        city_name = lines[index - 1].strip()
        level_text = lines[index + 1].strip()
        if (
            not city_name
            or city_name in _HEADER_VALUES
            or _LEVEL_RE.fullmatch(level_text) is None
        ):
            raise CompanyUnitsParserIncompatible(
                "company roster unit row no longer matches parser v1"
            )

        display_name = match.group(1).strip()
        unit_id = match.group(2)
        if not display_name or unit_id in seen:
            raise CompanyUnitsParserIncompatible(
                "company roster contains invalid or duplicate unit identity"
            )
        seen.add(unit_id)
        results.append(
            UnitState(
                unit_id=unit_id,
                company_id=company_id,
                display_name=display_name,
                city_name=city_name,
                level=int(level_text),
                source_session_id=session_id,
                source_sequence=sequence,
                observed_at=observed_at,
            )
        )

    return tuple(sorted(results, key=lambda item: item.unit_id))


def project_company_roster_event(
    reader: EvidenceReader,
    event: Mapping[str, object],
) -> CompanyRosterProjection | None:
    if not is_company_roster_event(event):
        return None

    company_id = _company_id_from_event(event)
    if company_id is None:
        return None

    session_id = event.get("session_id")
    sequence = event.get("sequence")
    observed_at = event.get("observed_at")
    if (
        not isinstance(session_id, str)
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or not isinstance(observed_at, str)
    ):
        raise CompanyUnitsArtifactError(
            "validated roster event lacks canonical provenance fields"
        )

    title, text = _load_page_artifact(
        reader,
        event.get("response_body_ref"),
    )
    lines = tuple(
        value
        for raw in text.splitlines()
        if (value := " ".join(raw.split()).strip())
    )

    if "Предприятия" not in lines:
        raise CompanyUnitsParserIncompatible(
            "recognized company roster no longer exposes the enterprises section"
        )

    company = CompanyState(
        company_id=company_id,
        name=_company_name(lines, title),
        source_session_id=session_id,
        source_sequence=sequence,
        observed_at=observed_at,
    )
    units = _parse_units(
        lines=lines,
        company_id=company_id,
        session_id=session_id,
        sequence=sequence,
        observed_at=observed_at,
    )
    return CompanyRosterProjection(company=company, units=units)


__all__ = [
    "CompanyRosterProjection",
    "CompanyUnitsArtifactError",
    "CompanyUnitsParserIncompatible",
    "is_company_roster_event",
    "project_company_roster_event",
]
