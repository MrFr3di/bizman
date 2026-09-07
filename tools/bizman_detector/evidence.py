from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from tools.bizman_detector.session_status import EvidenceSessionStatus
from tools.bizman_foundation.fingerprint import canonical_sha256


MAX_EVENT_LINE_BYTES = 1_048_576
MAX_ARTIFACT_BYTES = 4_194_304
_MAX_MANIFEST_BYTES = 1_048_576
_IN_MEMORY_EVENT_IDS = 8_192
_FINALIZED_STATUSES = frozenset({"completed", "cancelled"})
_SESSION_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_ARTIFACT_REF_RE = re.compile(r"sha256:([0-9a-f]{64})")


class EvidenceError(ValueError):
    """Base class for invalid or corrupted immutable detector evidence."""


class EvidenceFormatError(EvidenceError):
    """Evidence layout or JSON/schema shape is invalid."""


class EvidenceIntegrityError(EvidenceError):
    """Evidence bytes disagree with their declared identity or ordering."""


class EvidenceStatusError(EvidenceError):
    """A session is not in a detector-readable finalized state."""


@dataclass(frozen=True, slots=True)
class EvidenceIdentity:
    session_id: str
    manifest_sha256: str
    evidence_sha256: str
    started_at: str
    ended_at: str
    status: str


class _UniqueEventIds:
    """Exact duplicate detection with bounded in-memory growth.

    Small sessions stay entirely in memory. Larger sessions spill UUID strings
    into an ephemeral SQLite primary-key table so uniqueness remains exact
    without retaining an unbounded Python set.
    """

    def __init__(self, memory_limit: int = _IN_MEMORY_EVENT_IDS) -> None:
        self._memory_limit = memory_limit
        self._memory: set[str] = set()
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> "_UniqueEventIds":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _spill(self) -> None:
        if self._connection is not None:
            return
        self._temp_dir = tempfile.TemporaryDirectory(prefix="bizman-evidence-ids-")
        database = Path(self._temp_dir.name) / "event-ids.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("CREATE TABLE ids (id TEXT PRIMARY KEY) WITHOUT ROWID")
        connection.executemany(
            "INSERT INTO ids(id) VALUES (?)",
            ((event_id,) for event_id in self._memory),
        )
        self._memory.clear()
        self._connection = connection

    def add(self, event_id: str) -> bool:
        connection = self._connection
        if connection is None:
            if event_id in self._memory:
                return False
            if len(self._memory) < self._memory_limit:
                self._memory.add(event_id)
                return True
            self._spill()
            connection = self._connection
            assert connection is not None

        try:
            connection.execute("INSERT INTO ids(id) VALUES (?)", (event_id,))
        except sqlite3.IntegrityError:
            return False
        return True

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None
        self._memory.clear()


def _load_schema(path: Path) -> Mapping[str, Any]:
    label = path.as_posix()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvidenceFormatError(f"missing detector schema {label}: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceFormatError(f"invalid detector schema {label}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise EvidenceFormatError(f"invalid detector schema {label}: root must be an object")
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as exc:
        raise EvidenceFormatError(f"invalid detector schema {label}: {exc.message}") from exc
    return value


def _require_positive_limit(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class EvidenceReader:
    """Fail-closed streaming reader for finalized sanitized collector sessions."""

    def __init__(
        self,
        repo_root: Path,
        data_dir: Path,
        *,
        max_event_line_bytes: int = MAX_EVENT_LINE_BYTES,
        max_artifact_bytes: int = MAX_ARTIFACT_BYTES,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.data_dir = Path(data_dir).expanduser().resolve(strict=False)
        self.max_event_line_bytes = _require_positive_limit(
            max_event_line_bytes, name="max_event_line_bytes"
        )
        self.max_artifact_bytes = _require_positive_limit(
            max_artifact_bytes, name="max_artifact_bytes"
        )

        format_checker = FormatChecker()
        manifest_schema = _load_schema(self.repo_root / "schemas/session-manifest.schema.json")
        event_schema = _load_schema(self.repo_root / "schemas/event.schema.json")
        self._manifest_validator = Draft202012Validator(
            manifest_schema,
            format_checker=format_checker,
        )
        self._event_validator = Draft202012Validator(
            event_schema,
            format_checker=format_checker,
        )

    @staticmethod
    def _validate_session_id(session_id: object) -> str:
        if not isinstance(session_id, str) or _SESSION_ID_RE.fullmatch(session_id) is None:
            raise EvidenceFormatError(f"invalid session id: {session_id!r}")
        return session_id

    def _session_directory(self, session_id: str) -> Path:
        sessions_root = (self.data_dir / "sessions").resolve(strict=False)
        candidate = sessions_root / session_id
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise EvidenceFormatError(f"missing session directory for {session_id}: {exc}") from exc
        if not resolved.is_relative_to(sessions_root):
            raise EvidenceFormatError(f"session directory for {session_id} escapes data directory")
        if not resolved.is_dir():
            raise EvidenceFormatError(f"session directory for {session_id} is not a directory")
        if resolved.name != session_id:
            raise EvidenceIntegrityError(
                f"session directory identity {resolved.name!r} does not match {session_id!r}"
            )
        return resolved

    def _read_manifest(
        self,
        session_id: object,
        *,
        require_finalized: bool,
    ) -> dict[str, Any]:
        resolved_session_id = self._validate_session_id(session_id)
        session_dir = self._session_directory(resolved_session_id)
        manifest_candidate = session_dir / "manifest.json"
        try:
            manifest_path = manifest_candidate.resolve(strict=True)
        except OSError as exc:
            raise EvidenceFormatError(
                f"missing manifest for session {resolved_session_id}: {exc}"
            ) from exc
        if manifest_path.parent != session_dir or not manifest_path.is_file():
            raise EvidenceFormatError(
                f"manifest for session {resolved_session_id} is not a regular session file"
            )
        try:
            size = manifest_path.stat().st_size
        except OSError as exc:
            raise EvidenceFormatError(
                f"cannot stat manifest for session {resolved_session_id}: {exc}"
            ) from exc
        if size > _MAX_MANIFEST_BYTES:
            raise EvidenceFormatError(
                f"manifest for session {resolved_session_id} exceeds size limit"
            )
        try:
            raw = manifest_path.read_bytes()
        except OSError as exc:
            raise EvidenceFormatError(
                f"cannot read manifest for session {resolved_session_id}: {exc}"
            ) from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceFormatError(
                f"manifest for session {resolved_session_id}: invalid UTF-8/JSON: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise EvidenceFormatError(
                f"manifest for session {resolved_session_id}: schema root must be an object"
            )
        try:
            self._manifest_validator.validate(value)
        except ValidationError as exc:
            raise EvidenceFormatError(
                f"manifest for session {resolved_session_id}: schema validation failed: {exc.message}"
            ) from exc

        manifest_session_id = value.get("session_id")
        if manifest_session_id != resolved_session_id:
            raise EvidenceIntegrityError(
                f"session directory {resolved_session_id!r} contains manifest for "
                f"{manifest_session_id!r}"
            )

        status = value.get("status")
        if require_finalized and status not in _FINALIZED_STATUSES:
            raise EvidenceStatusError(
                f"session {resolved_session_id} is not finalized for detector reads: {status!r}"
            )
        if status in _FINALIZED_STATUSES and not isinstance(value.get("ended_at"), str):
            raise EvidenceStatusError(
                f"finalized session {resolved_session_id} must record ended_at"
            )
        return value

    def _resolve_event_file(self, event_rel: object) -> tuple[str, Path]:
        if not isinstance(event_rel, str) or not event_rel:
            raise EvidenceFormatError("event file path must be a non-empty POSIX string")
        if "\\" in event_rel:
            raise EvidenceFormatError(f"event file path is not POSIX-style: {event_rel!r}")
        pure = PurePosixPath(event_rel)
        if pure.is_absolute() or not pure.parts:
            raise EvidenceFormatError(f"event file path must be relative: {event_rel!r}")
        if pure.as_posix() != event_rel or ".." in pure.parts:
            raise EvidenceFormatError(f"event file path is non-canonical or unsafe: {event_rel!r}")
        if pure.parts[0] != "events":
            raise EvidenceFormatError(f"event file must reside under events/: {event_rel!r}")
        if pure.suffix != ".jsonl":
            raise EvidenceFormatError(f"event file must use .jsonl: {event_rel!r}")

        candidate = self.data_dir.joinpath(*pure.parts)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise EvidenceFormatError(f"missing event file {event_rel!r}: {exc}") from exc
        data_root = self.data_dir.resolve(strict=False)
        events_root = (data_root / "events").resolve(strict=False)
        if not resolved.is_relative_to(data_root):
            raise EvidenceFormatError(f"event file {event_rel!r} escapes data directory")
        if not resolved.is_relative_to(events_root):
            raise EvidenceFormatError(f"event file {event_rel!r} escapes events directory")
        if not resolved.is_file():
            raise EvidenceFormatError(f"event file {event_rel!r} is not a regular file")
        return event_rel, resolved

    def _validate_event(
        self,
        value: object,
        *,
        session_id: str,
        source: str,
        expected_sequence: int,
        ids: _UniqueEventIds,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise EvidenceFormatError(f"{source}: event must be a JSON object")
        try:
            self._event_validator.validate(value)
        except ValidationError as exc:
            raise EvidenceFormatError(
                f"{source}: event schema validation failed: {exc.message}"
            ) from exc
        if value.get("session_id") != session_id:
            raise EvidenceIntegrityError(
                f"{source}: event session_id does not match session {session_id}"
            )
        sequence = value.get("sequence")
        if sequence != expected_sequence:
            raise EvidenceIntegrityError(
                f"{source}: sequence {sequence!r} != expected {expected_sequence}"
            )
        event_id = value.get("event_id")
        assert isinstance(event_id, str)
        if not ids.add(event_id):
            raise EvidenceIntegrityError(f"{source}: duplicate event_id {event_id}")
        return value

    def _iter_validated_events(
        self,
        manifest: Mapping[str, Any],
        *,
        file_hashes: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        session_id = str(manifest["session_id"])
        event_files = manifest.get("event_files")
        assert isinstance(event_files, list)
        expected_sequence = 0

        with _UniqueEventIds() as ids:
            for raw_rel in event_files:
                event_rel, event_path = self._resolve_event_file(raw_rel)
                digest = hashlib.sha256()
                total_bytes = 0
                line_number = 0
                try:
                    handle = event_path.open("rb")
                except OSError as exc:
                    raise EvidenceFormatError(
                        f"cannot open event file {event_rel!r}: {exc}"
                    ) from exc
                with handle:
                    while True:
                        raw_line = handle.readline(self.max_event_line_bytes + 1)
                        if not raw_line:
                            break
                        line_number += 1
                        if len(raw_line) > self.max_event_line_bytes:
                            raise EvidenceIntegrityError(
                                f"{event_rel}:{line_number}: event line exceeds "
                                f"{self.max_event_line_bytes} bytes"
                            )
                        digest.update(raw_line)
                        total_bytes += len(raw_line)
                        try:
                            text = raw_line.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise EvidenceFormatError(
                                f"{event_rel}:{line_number}: invalid UTF-8"
                            ) from exc
                        try:
                            value = json.loads(text)
                        except json.JSONDecodeError as exc:
                            raise EvidenceFormatError(
                                f"{event_rel}:{line_number}: invalid JSON: {exc.msg}"
                            ) from exc
                        source = f"{event_rel}:{line_number}"
                        event = self._validate_event(
                            value,
                            session_id=session_id,
                            source=source,
                            expected_sequence=expected_sequence,
                            ids=ids,
                        )
                        expected_sequence += 1
                        yield event
                if file_hashes is not None:
                    file_hashes.append(
                        {
                            "path": event_rel,
                            "sha256": digest.hexdigest(),
                            "bytes": total_bytes,
                        }
                    )

    @staticmethod
    def _identity_from_hashes(
        manifest: Mapping[str, Any],
        file_hashes: list[dict[str, Any]],
    ) -> EvidenceIdentity:
        manifest_sha256 = canonical_sha256(manifest)
        evidence_sha256 = canonical_sha256(
            {
                "manifest_sha256": manifest_sha256,
                "event_files": file_hashes,
            }
        )
        ended_at = manifest.get("ended_at")
        assert isinstance(ended_at, str)
        return EvidenceIdentity(
            session_id=str(manifest["session_id"]),
            manifest_sha256=manifest_sha256,
            evidence_sha256=evidence_sha256,
            started_at=str(manifest["started_at"]),
            ended_at=ended_at,
            status=str(manifest["status"]),
        )

    def _inspect_manifest(self, manifest: Mapping[str, Any]) -> EvidenceIdentity:
        file_hashes: list[dict[str, Any]] = []
        for _ in self._iter_validated_events(manifest, file_hashes=file_hashes):
            pass
        return self._identity_from_hashes(manifest, file_hashes)

    def inspect(self, session_id: str) -> EvidenceIdentity:
        manifest = self._read_manifest(session_id, require_finalized=True)
        return self._inspect_manifest(manifest)

    def iter_events(
        self,
        session_id: str,
        *,
        expected_identity: EvidenceIdentity | None = None,
    ) -> Iterator[Mapping[str, Any]]:
        manifest = self._read_manifest(session_id, require_finalized=True)
        if expected_identity is not None:
            if not isinstance(expected_identity, EvidenceIdentity):
                raise TypeError("expected_identity must be EvidenceIdentity or None")
            if expected_identity.session_id != session_id:
                raise EvidenceIntegrityError(
                    f"expected evidence session {expected_identity.session_id!r} "
                    f"does not match requested session {session_id!r}"
                )
            manifest_sha256 = canonical_sha256(manifest)
            if manifest_sha256 != expected_identity.manifest_sha256:
                raise EvidenceIntegrityError(
                    f"session {session_id} manifest no longer matches expected identity"
                )

        file_hashes: list[dict[str, Any]] = []
        yield from self._iter_validated_events(manifest, file_hashes=file_hashes)
        if expected_identity is not None:
            actual_identity = self._identity_from_hashes(manifest, file_hashes)
            if actual_identity != expected_identity:
                raise EvidenceIntegrityError(
                    f"session {session_id} event bytes no longer match expected identity"
                )

    def read_verified_artifact(self, ref: str) -> bytes:
        if not isinstance(ref, str):
            raise EvidenceFormatError(f"invalid artifact reference: {ref!r}")
        match = _ARTIFACT_REF_RE.fullmatch(ref)
        if match is None:
            raise EvidenceFormatError(f"invalid artifact reference: {ref!r}")
        digest = match.group(1)
        artifact_root = (self.data_dir / "artifacts" / "sha256").resolve(strict=False)
        candidate = artifact_root / digest[:2] / digest
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise EvidenceFormatError(f"missing artifact {ref}: {exc}") from exc
        if not resolved.is_relative_to(artifact_root):
            raise EvidenceFormatError(f"artifact {ref} escapes CAS directory")
        if not resolved.is_file():
            raise EvidenceFormatError(f"artifact {ref} is not a regular file")
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            raise EvidenceFormatError(f"cannot stat artifact {ref}: {exc}") from exc
        if size > self.max_artifact_bytes:
            raise EvidenceIntegrityError(
                f"artifact {ref} size {size} exceeds {self.max_artifact_bytes} bytes"
            )
        try:
            with resolved.open("rb") as handle:
                payload = handle.read(self.max_artifact_bytes + 1)
        except OSError as exc:
            raise EvidenceFormatError(f"cannot read artifact {ref}: {exc}") from exc
        if len(payload) > self.max_artifact_bytes:
            raise EvidenceIntegrityError(
                f"artifact {ref} size exceeds {self.max_artifact_bytes} bytes"
            )
        actual = hashlib.sha256(payload).hexdigest()
        if actual != digest:
            raise EvidenceIntegrityError(
                f"artifact {ref} digest mismatch: actual sha256:{actual}"
            )
        return payload

    def iter_session_statuses(
        self,
        selected: tuple[str, ...] = (),
    ) -> Iterator[EvidenceSessionStatus]:
        """Yield schema-validated session metadata without reading event files."""

        if selected:
            session_ids = sorted({self._validate_session_id(item) for item in selected})
        else:
            sessions_root = self.data_dir / "sessions"
            if not sessions_root.exists():
                return
            if not sessions_root.is_dir():
                raise EvidenceFormatError("sessions root is not a directory")
            session_ids: list[str] = []
            for path in sessions_root.iterdir():
                if not path.is_dir():
                    continue
                session_ids.append(self._validate_session_id(path.name))
            session_ids.sort()

        for session_id in session_ids:
            manifest = self._read_manifest(session_id, require_finalized=False)
            ended_at = manifest.get("ended_at")
            yield EvidenceSessionStatus(
                session_id=session_id,
                status=str(manifest["status"]),
                started_at=str(manifest["started_at"]),
                ended_at=ended_at if isinstance(ended_at, str) else None,
            )

    def iter_finalized(
        self,
        selected: tuple[str, ...] = (),
    ) -> Iterator[EvidenceIdentity]:
        if selected:
            session_ids = sorted({self._validate_session_id(item) for item in selected})
        else:
            sessions_root = self.data_dir / "sessions"
            if not sessions_root.exists():
                return
            if not sessions_root.is_dir():
                raise EvidenceFormatError("sessions root is not a directory")
            session_ids: list[str] = []
            for path in sessions_root.iterdir():
                if not path.is_dir():
                    continue
                session_ids.append(self._validate_session_id(path.name))
            session_ids.sort()

        for session_id in session_ids:
            manifest = self._read_manifest(session_id, require_finalized=False)
            if manifest.get("status") not in _FINALIZED_STATUSES:
                continue
            yield self._inspect_manifest(manifest)


__all__ = [
    "EvidenceError",
    "EvidenceFormatError",
    "EvidenceIdentity",
    "EvidenceIntegrityError",
    "EvidenceReader",
    "EvidenceSessionStatus",
    "EvidenceStatusError",
    "MAX_ARTIFACT_BYTES",
    "MAX_EVENT_LINE_BYTES",
]
