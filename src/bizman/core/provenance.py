from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Mapping

from bizman.core.context import CoreContext
from bizman.core.errors import AssetError, DataIntegrityError


_EVIDENCE_REF_RE = re.compile(
    r"^(?P<source>[a-z0-9][a-z0-9.-]*)#(?P<locator>entry|seq)-"
    r"(?P<ordinal>0|[1-9][0-9]*)$"
)
_HAR_SOURCE_ID_RE = re.compile(r"^src\.[a-z0-9][a-z0-9.-]*$")
_PROMOTED_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MAX_EVIDENCE_REF_LENGTH = 512


def _require_rfc3339(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty RFC3339 string")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be RFC3339") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return value


@dataclass(frozen=True, slots=True)
class EvidenceTraceRequest:
    evidence_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_ref, str):
            raise TypeError("evidence_ref must be a string")
        if not self.evidence_ref:
            raise ValueError("evidence_ref must be non-empty")
        if len(self.evidence_ref) > _MAX_EVIDENCE_REF_LENGTH:
            raise ValueError(
                f"evidence_ref exceeds {_MAX_EVIDENCE_REF_LENGTH} characters"
            )
        if _EVIDENCE_REF_RE.fullmatch(self.evidence_ref) is None:
            raise ValueError("evidence_ref has unsupported provenance syntax")


@dataclass(frozen=True, slots=True)
class EvidenceTrace:
    evidence_ref: str
    source_id: str
    source_kind: str
    locator_kind: str
    ordinal: int
    source_record_count: int
    raw_source_committed: bool
    source_sha256: str | None
    runtime_session_id: str | None
    observed_from: str
    observed_to: str | None
    privacy: str
    provenance_policy: str

    def __post_init__(self) -> None:
        evidence_match = _EVIDENCE_REF_RE.fullmatch(self.evidence_ref)
        if evidence_match is None:
            raise ValueError("evidence_ref has unsupported provenance syntax")
        if evidence_match.group("source") != self.source_id:
            raise ValueError("evidence_ref source does not match source_id")
        if self.source_kind not in {
            "har_capture",
            "promoted_session",
            "webcopy_snapshot",
        }:
            raise ValueError("unsupported evidence source_kind")
        expected_locator = (
            "sequence"
            if self.source_kind == "promoted_session"
            else "entry"
        )
        if self.locator_kind != expected_locator:
            raise ValueError("locator_kind does not match source_kind")
        expected_ref_locator = "entry" if self.locator_kind == "entry" else "seq"
        if evidence_match.group("locator") != expected_ref_locator:
            raise ValueError("evidence_ref locator does not match locator_kind")
        if (
            isinstance(self.ordinal, bool)
            or not isinstance(self.ordinal, int)
            or self.ordinal < 0
        ):
            raise ValueError("ordinal must be a non-negative integer")
        if (
            isinstance(self.source_record_count, bool)
            or not isinstance(self.source_record_count, int)
            or self.source_record_count <= 0
        ):
            raise ValueError("source_record_count must be a positive integer")
        if self.ordinal >= self.source_record_count:
            raise ValueError("ordinal must be inside source record range")
        if int(evidence_match.group("ordinal")) != self.ordinal:
            raise ValueError("evidence_ref ordinal does not match ordinal")
        _require_rfc3339(self.observed_from, name="observed_from")
        if self.observed_to is not None:
            _require_rfc3339(self.observed_to, name="observed_to")
        if self.source_kind in {"har_capture", "webcopy_snapshot"}:
            if _HAR_SOURCE_ID_RE.fullmatch(self.source_id) is None:
                raise ValueError(
                    "entry-based trace requires canonical source_id"
                )
        elif _PROMOTED_SOURCE_ID_RE.fullmatch(self.source_id) is None:
            raise ValueError("promoted session trace requires canonical source_id")
        if not isinstance(self.privacy, str) or not self.privacy:
            raise ValueError("privacy must be non-empty")
        if not isinstance(self.provenance_policy, str) or not self.provenance_policy:
            raise ValueError("provenance_policy must be non-empty")
        if self.raw_source_committed is not False:
            raise ValueError("trace sources must not claim raw bytes are committed")
        if self.source_kind in {"har_capture", "webcopy_snapshot"}:
            if (
                not isinstance(self.source_sha256, str)
                or _SHA256_RE.fullmatch(self.source_sha256) is None
            ):
                raise ValueError(
                    "entry-based trace requires source_sha256"
                )
            if self.runtime_session_id is not None:
                raise ValueError(
                    "entry-based trace cannot carry runtime_session_id"
                )
        else:
            if self.source_sha256 is not None:
                raise ValueError("promoted session trace cannot invent source_sha256")
            if (
                not isinstance(self.runtime_session_id, str)
                or _UUID7_RE.fullmatch(self.runtime_session_id) is None
            ):
                raise ValueError("promoted session trace requires runtime UUIDv7")


@dataclass(frozen=True, slots=True)
class EvidenceTraceResult:
    trace: EvidenceTrace | None


@dataclass(frozen=True, slots=True)
class _TraceSource:
    source_id: str
    source_kind: str
    record_count: int
    raw_source_committed: bool
    source_sha256: str | None
    runtime_session_id: str | None
    observed_from: str
    observed_to: str | None
    privacy: str
    provenance_policy: str


def _mapping(value: object, *, source: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AssetError(f"invalid provenance asset {source}: expected object")
    return value


def _text(value: object, *, source: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AssetError(f"invalid provenance asset {source}: {field} must be text")
    return value


def _non_negative_int(value: object, *, source: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AssetError(
            f"invalid provenance asset {source}: {field} must be non-negative integer"
        )
    return value


def _positive_int(value: object, *, source: str, field: str) -> int:
    resolved = _non_negative_int(value, source=source, field=field)
    if resolved == 0:
        raise AssetError(
            f"invalid provenance asset {source}: {field} must be positive"
        )
    return resolved


def _instant(value: object, *, source: str, field: str) -> str:
    text = _text(value, source=source, field=field)
    try:
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssetError(
            f"invalid provenance asset {source}: {field} must be RFC3339"
        ) from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise AssetError(
            f"invalid provenance asset {source}: {field} requires timezone"
        )
    return text


def _load_json(path: Path, *, root: Path) -> object:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AssetError("required provenance asset is unavailable") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise AssetError("required provenance asset is outside repository boundary")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise AssetError("required provenance asset cannot be read") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetError("required provenance asset is invalid JSON") from exc


def _har_sources(root: Path) -> dict[str, _TraceSource]:
    source_root = (root / "knowledge" / "sources").resolve(strict=False)
    captures_path = source_root / "captures.json"
    document = _mapping(
        _load_json(captures_path, root=root),
        source="knowledge/sources/captures.json",
    )
    if document.get("schema_version") != "2.0":
        raise DataIntegrityError("capture provenance registry version is incompatible")
    if set(document) != {"schema_version", "generated_at", "policy", "captures"}:
        raise DataIntegrityError("capture provenance registry has unexpected fields")
    provenance_policy = _text(
        document.get("policy"),
        source="knowledge/sources/captures.json",
        field="policy",
    )
    captures = document.get("captures")
    if not isinstance(captures, list):
        raise AssetError("capture provenance registry must contain captures array")

    manifests_by_filename: dict[str, Mapping[str, Any]] = {}
    manifest_source_ids: set[str] = set()
    try:
        manifest_paths = sorted(source_root.glob("*.har.json"))
    except OSError as exc:
        raise AssetError("capture provenance manifests are unavailable") from exc

    for path in manifest_paths:
        label = f"knowledge/sources/{path.name}"
        manifest = _mapping(_load_json(path, root=root), source=label)
        source_id = _text(manifest.get("id"), source=label, field="id")
        filename = _text(
            manifest.get("source_filename"),
            source=label,
            field="source_filename",
        )
        if _HAR_SOURCE_ID_RE.fullmatch(source_id) is None:
            raise DataIntegrityError("capture manifest has invalid canonical source id")
        if manifest.get("kind") != "har_capture":
            raise DataIntegrityError("capture manifest has invalid source kind")
        if manifest.get("source_file_committed") is not False:
            raise DataIntegrityError(
                "capture manifest must not claim raw HAR is committed"
            )
        if filename in manifests_by_filename or source_id in manifest_source_ids:
            raise DataIntegrityError("capture manifests contain duplicate identity")
        manifests_by_filename[filename] = manifest
        manifest_source_ids.add(source_id)

    sources: dict[str, _TraceSource] = {}
    for index, raw_capture in enumerate(captures):
        label = f"knowledge/sources/captures.json#capture-{index}"
        capture = _mapping(raw_capture, source=label)
        source_id = _text(capture.get("source_id"), source=label, field="source_id")
        filename = _text(capture.get("file_name"), source=label, field="file_name")
        sha256 = _text(capture.get("sha256"), source=label, field="sha256")
        if _HAR_SOURCE_ID_RE.fullmatch(source_id) is None:
            raise DataIntegrityError("capture registry has invalid canonical source id")
        if _SHA256_RE.fullmatch(sha256) is None:
            raise DataIntegrityError("capture registry has invalid SHA-256")
        entries = _positive_int(capture.get("entries"), source=label, field="entries")
        bytes_count = _non_negative_int(
            capture.get("bytes"),
            source=label,
            field="bytes",
        )
        observed_from = _instant(
            capture.get("started_at"),
            source=label,
            field="started_at",
        )
        observed_to = _instant(
            capture.get("ended_at"),
            source=label,
            field="ended_at",
        )
        if source_id in sources:
            raise DataIntegrityError("capture registry contains duplicate source id")

        manifest = manifests_by_filename.get(filename)
        if manifest is None:
            raise DataIntegrityError("capture registry has no matching source manifest")
        expected_pairs = (
            ("source_id", source_id, manifest.get("id")),
            ("file_name", filename, manifest.get("source_filename")),
            ("sha256", sha256, manifest.get("sha256")),
            ("bytes", bytes_count, manifest.get("size_bytes")),
            ("entries", entries, manifest.get("entries")),
            ("started_at", observed_from, manifest.get("captured_from")),
            ("ended_at", observed_to, manifest.get("captured_to")),
        )
        for field, capture_value, manifest_value in expected_pairs:
            if capture_value != manifest_value:
                raise DataIntegrityError(
                    f"capture provenance identity mismatch for {field}"
                )

        privacy = _text(manifest.get("privacy"), source=label, field="privacy")
        sources[source_id] = _TraceSource(
            source_id=source_id,
            source_kind="har_capture",
            record_count=entries,
            raw_source_committed=False,
            source_sha256=sha256,
            runtime_session_id=None,
            observed_from=observed_from,
            observed_to=observed_to,
            privacy=privacy,
            provenance_policy=provenance_policy,
        )
    return sources


def _webcopy_sources(root: Path) -> dict[str, _TraceSource]:
    source_root = (root / "knowledge" / "sources").resolve(strict=False)
    try:
        paths = sorted(source_root.glob("webcopy-*.json"))
    except OSError as exc:
        raise AssetError("webcopy provenance manifests are unavailable") from exc

    sources: dict[str, _TraceSource] = {}
    for path in paths:
        label = f"knowledge/sources/{path.name}"
        value = _mapping(_load_json(path, root=root), source=label)
        expected_fields = {
            "id",
            "kind",
            "host",
            "captured_from",
            "captured_to",
            "origin_index",
            "origin_index_sha256",
            "entry_count",
            "raw_source_committed",
            "privacy",
            "policy",
            "note",
        }
        if set(value) != expected_fields:
            raise DataIntegrityError(
                "webcopy source manifest has unexpected fields"
            )
        source_id = _text(value.get("id"), source=label, field="id")
        if _HAR_SOURCE_ID_RE.fullmatch(source_id) is None:
            raise DataIntegrityError("webcopy source id is invalid")
        if value.get("kind") != "webcopy_snapshot":
            raise DataIntegrityError("webcopy source kind is invalid")
        if source_id in sources:
            raise DataIntegrityError(
                "webcopy source manifests contain duplicate source id"
            )
        if value.get("raw_source_committed") is not False:
            raise DataIntegrityError(
                "webcopy source must not claim raw snapshot is committed"
            )
        source_sha256 = _text(
            value.get("origin_index_sha256"),
            source=label,
            field="origin_index_sha256",
        )
        if _SHA256_RE.fullmatch(source_sha256) is None:
            raise DataIntegrityError(
                "webcopy origin index has invalid SHA-256"
            )
        record_count = _positive_int(
            value.get("entry_count"),
            source=label,
            field="entry_count",
        )
        observed_from = _instant(
            value.get("captured_from"),
            source=label,
            field="captured_from",
        )
        observed_to = _instant(
            value.get("captured_to"),
            source=label,
            field="captured_to",
        )
        privacy = _text(value.get("privacy"), source=label, field="privacy")
        policy = _text(value.get("policy"), source=label, field="policy")
        sources[source_id] = _TraceSource(
            source_id=source_id,
            source_kind="webcopy_snapshot",
            record_count=record_count,
            raw_source_committed=False,
            source_sha256=source_sha256,
            runtime_session_id=None,
            observed_from=observed_from,
            observed_to=observed_to,
            privacy=privacy,
            provenance_policy=policy,
        )
    return sources


def _promoted_sources(root: Path) -> dict[str, _TraceSource]:
    path = root / "knowledge" / "sources" / "promoted-sessions.json"
    document = _mapping(
        _load_json(path, root=root),
        source="knowledge/sources/promoted-sessions.json",
    )
    if document.get("schema_version") != "1.0":
        raise DataIntegrityError("promoted session registry version is incompatible")
    if set(document) != {"schema_version", "policy", "sources"}:
        raise DataIntegrityError("promoted session registry has unexpected fields")
    provenance_policy = _text(document.get("policy"), source=path.name, field="policy")
    values = document.get("sources")
    if not isinstance(values, list):
        raise AssetError("promoted session registry must contain sources array")

    sources: dict[str, _TraceSource] = {}
    for index, raw_value in enumerate(values):
        label = f"knowledge/sources/promoted-sessions.json#source-{index}"
        value = _mapping(raw_value, source=label)
        expected_fields = {
            "source_id",
            "runtime_session_id",
            "started_at",
            "event_count",
            "raw_source_committed",
            "privacy",
        }
        if set(value) != expected_fields:
            raise DataIntegrityError("promoted session source has unexpected fields")
        source_id = _text(value.get("source_id"), source=label, field="source_id")
        if _PROMOTED_SOURCE_ID_RE.fullmatch(source_id) is None:
            raise DataIntegrityError("promoted source id is invalid")
        if source_id in sources:
            raise DataIntegrityError("promoted session registry contains duplicate source id")
        runtime_session_id = _text(
            value.get("runtime_session_id"),
            source=label,
            field="runtime_session_id",
        )
        if _UUID7_RE.fullmatch(runtime_session_id) is None:
            raise DataIntegrityError("promoted source runtime_session_id is not UUIDv7")
        event_count = _positive_int(
            value.get("event_count"),
            source=label,
            field="event_count",
        )
        observed_from = _instant(
            value.get("started_at"),
            source=label,
            field="started_at",
        )
        if value.get("raw_source_committed") is not False:
            raise DataIntegrityError(
                "promoted source must not claim raw evidence is committed"
            )
        privacy = _text(value.get("privacy"), source=label, field="privacy")
        sources[source_id] = _TraceSource(
            source_id=source_id,
            source_kind="promoted_session",
            record_count=event_count,
            raw_source_committed=False,
            source_sha256=None,
            runtime_session_id=runtime_session_id,
            observed_from=observed_from,
            observed_to=None,
            privacy=privacy,
            provenance_policy=provenance_policy,
        )
    return sources


def _source_catalog(context: CoreContext) -> dict[str, _TraceSource]:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    root = context.assets.root
    har_sources = _har_sources(root)
    promoted_sources = _promoted_sources(root)
    webcopy_sources = _webcopy_sources(root)
    all_ids = [
        *har_sources,
        *promoted_sources,
        *webcopy_sources,
    ]
    if len(set(all_ids)) != len(all_ids):
        raise DataIntegrityError(
            "provenance source registries contain duplicate source ids"
        )
    return {
        **har_sources,
        **promoted_sources,
        **webcopy_sources,
    }


def trace_evidence(
    context: CoreContext,
    request: EvidenceTraceRequest,
) -> EvidenceTraceResult:
    if not isinstance(request, EvidenceTraceRequest):
        raise TypeError("request must be EvidenceTraceRequest")
    match = _EVIDENCE_REF_RE.fullmatch(request.evidence_ref)
    assert match is not None
    source_id = match.group("source")
    locator = match.group("locator")
    ordinal = int(match.group("ordinal"))

    source = _source_catalog(context).get(source_id)
    if source is None:
        return EvidenceTraceResult(trace=None)

    expected_locator = (
        "seq"
        if source.source_kind == "promoted_session"
        else "entry"
    )
    if locator != expected_locator or ordinal >= source.record_count:
        return EvidenceTraceResult(trace=None)

    return EvidenceTraceResult(
        trace=EvidenceTrace(
            evidence_ref=request.evidence_ref,
            source_id=source.source_id,
            source_kind=source.source_kind,
            locator_kind="entry" if locator == "entry" else "sequence",
            ordinal=ordinal,
            source_record_count=source.record_count,
            raw_source_committed=source.raw_source_committed,
            source_sha256=source.source_sha256,
            runtime_session_id=source.runtime_session_id,
            observed_from=source.observed_from,
            observed_to=source.observed_to,
            privacy=source.privacy,
            provenance_policy=source.provenance_policy,
        )
    )


__all__ = [
    "EvidenceTrace",
    "EvidenceTraceRequest",
    "EvidenceTraceResult",
    "trace_evidence",
]
