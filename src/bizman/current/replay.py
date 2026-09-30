from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Mapping

from bizman.changes.profile import compile_default_analysis_profile
from bizman.current.company_units import (
    CompanyRosterProjection,
    CompanyUnitsArtifactError,
    CompanyUnitsParserIncompatible,
    project_company_roster_event,
)
from bizman.current.model import (
    PROJECTION_NAME,
    PROJECTION_VERSION,
    CompanyState,
    CurrentProjectionSpec,
    CurrentStateSnapshot,
    ReplaySession,
    UnitState,
    build_current_snapshot,
)
from bizman.current.state import (
    APPLICATION_ID,
    USER_VERSION,
    CurrentStateIntegrityError,
    CurrentStateOperationError,
    CurrentStateStore,
)
from bizman.foundation.redaction import RedactionPolicy
from bizman.sessions.evidence import EvidenceIdentity, EvidenceReader


def _instant(value: str) -> datetime:
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise CurrentStateIntegrityError(
            "evidence timestamp is not valid RFC3339"
        ) from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise CurrentStateIntegrityError(
            "evidence timestamp must include timezone"
        )
    return instant.astimezone(UTC)


def _ordered_identities(
    reader: EvidenceReader,
) -> tuple[EvidenceIdentity, ...]:
    identities = tuple(reader.iter_finalized())
    for identity in identities:
        if not isinstance(identity, EvidenceIdentity):
            raise TypeError("EvidenceReader must yield EvidenceIdentity values")
    return tuple(
        sorted(
            identities,
            key=lambda item: (
                _instant(item.started_at),
                item.session_id,
            ),
        )
    )


def _verified_event_sequence(event: Mapping[str, object], expected: int) -> int:
    sequence = event.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise TypeError("validated evidence sequence must be an integer")
    if sequence != expected:
        raise CurrentStateIntegrityError(
            "validated evidence sequence is not contiguous"
        )
    return sequence


def _roster_projection_or_stale(
    reader: EvidenceReader,
    event: Mapping[str, object],
) -> tuple[CompanyRosterProjection | None, bool]:
    try:
        return project_company_roster_event(reader, event), False
    except CompanyUnitsParserIncompatible:
        return None, True
    except CompanyUnitsArtifactError as exc:
        raise CurrentStateIntegrityError(
            "company/unit evidence artifact violates Current State contract"
        ) from exc


def _replay_session(
    reader: EvidenceReader,
    identity: EvidenceIdentity,
    *,
    companies: dict[str, CompanyState],
    units: dict[str, UnitState],
) -> tuple[ReplaySession, bool]:
    event_count = 0
    last_sequence: int | None = None
    parser_incompatible = False
    for event in reader.iter_events(
        identity.session_id,
        expected_identity=identity,
    ):
        if not isinstance(event, Mapping):
            raise TypeError("EvidenceReader event must be a mapping")
        sequence = _verified_event_sequence(event, event_count)
        projection, incompatible = _roster_projection_or_stale(reader, event)
        parser_incompatible = parser_incompatible or incompatible

        if projection is not None:
            companies[projection.company.company_id] = projection.company
            for unit in projection.units:
                units[unit.unit_id] = unit

        event_count += 1
        last_sequence = sequence

    try:
        replay = ReplaySession(
            started_at=identity.started_at,
            session_id=identity.session_id,
            manifest_sha256=identity.manifest_sha256,
            evidence_sha256=identity.evidence_sha256,
            ended_at=identity.ended_at,
            status=identity.status,
            event_count=event_count,
            last_sequence=last_sequence,
        )
    except ValueError as exc:
        raise CurrentStateIntegrityError(
            "evidence identity is incompatible with Current State replay"
        ) from exc
    return replay, parser_incompatible


def build_replay_snapshot(
    repo_root: Path,
    data_dir: Path,
    redaction: RedactionPolicy,
    *,
    projection_name: str = PROJECTION_NAME,
    projection_version: int = PROJECTION_VERSION,
) -> CurrentStateSnapshot:
    root = Path(repo_root).expanduser().resolve()
    data = Path(data_dir).expanduser().resolve(strict=False)
    if not isinstance(redaction, RedactionPolicy):
        raise TypeError("redaction must be RedactionPolicy")

    profile_compilation = compile_default_analysis_profile(root, redaction)
    spec = CurrentProjectionSpec(
        analysis_profile_sha256=profile_compilation.profile.sha256,
        projection_name=projection_name,
        projection_version=projection_version,
    )
    reader = EvidenceReader(root, data)
    companies: dict[str, CompanyState] = {}
    units: dict[str, UnitState] = {}
    sessions: list[ReplaySession] = []
    parser_incompatible = False

    for identity in _ordered_identities(reader):
        replay, incompatible = _replay_session(
            reader,
            identity,
            companies=companies,
            units=units,
        )
        sessions.append(replay)
        parser_incompatible = parser_incompatible or incompatible

    return build_current_snapshot(
        spec,
        tuple(sessions),
        companies=tuple(
            sorted(companies.values(), key=lambda item: item.company_id)
        ),
        units=tuple(sorted(units.values(), key=lambda item: item.unit_id)),
        status="stale" if parser_incompatible else "ready",
        stale_reason=(
            "company_units_parser_v1_incompatible"
            if parser_incompatible
            else None
        ),
    )


def _is_rebuildable_older_state(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        # Keep explicit SQL transactions compatible with Python 3.11 and 3.14.
        connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        try:
            application_id_row = connection.execute(
                "PRAGMA application_id"
            ).fetchone()
            user_version_row = connection.execute(
                "PRAGMA user_version"
            ).fetchone()
            if application_id_row is None or user_version_row is None:
                return False
            application_id_raw = next(iter(application_id_row), None)
            user_version_raw = next(iter(user_version_row), None)
            if application_id_raw is None or user_version_raw is None:
                return False
            application_id = int(application_id_raw)
            user_version = int(user_version_raw)
            tables = {
                str(name)
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            strict = {
                str(name): int(is_strict)
                for name, is_strict in connection.execute(
                    "SELECT name, strict FROM pragma_table_list "
                    "WHERE name IN ('projection_meta', 'replayed_session')"
                )
            }
            projection_columns = tuple(
                str(name)
                for (name,) in connection.execute(
                    "SELECT name FROM pragma_table_info('projection_meta')"
                )
            )
            replay_columns = tuple(
                str(name)
                for (name,) in connection.execute(
                    "SELECT name FROM pragma_table_info('replayed_session')"
                )
            )
        finally:
            connection.close()
    except sqlite3.Error:
        return False

    return (
        application_id == APPLICATION_ID
        and user_version == USER_VERSION - 1
        and tables == {"projection_meta", "replayed_session"}
        and strict == {"projection_meta": 1, "replayed_session": 1}
        and projection_columns
        == (
            "singleton",
            "projection_name",
            "projection_version",
            "analysis_profile_sha256",
            "input_fingerprint",
            "state_fingerprint",
            "status",
            "stale_reason",
            "session_count",
            "last_session_id",
            "last_sequence",
        )
        and replay_columns
        == (
            "session_id",
            "manifest_sha256",
            "evidence_sha256",
            "started_at",
            "ended_at",
            "status",
            "event_count",
            "last_sequence",
        )
    )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        directory_fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def _best_effort_remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _replace_rebuildable_older_state(
    path: Path,
    snapshot: CurrentStateSnapshot,
) -> CurrentStateSnapshot:
    older_sidecars = tuple(
        path.with_name(path.name + suffix)
        for suffix in ("-wal", "-shm")
    )
    if any(sidecar.exists() for sidecar in older_sidecars):
        raise CurrentStateOperationError(
            "cannot replace older Current State while SQLite sidecars exist"
        )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.p4b-",
            suffix=".sqlite3",
            dir=path.parent,
        )
        os.close(descriptor)
    except OSError as exc:
        raise CurrentStateOperationError(
            "cannot stage Current State schema replacement"
        ) from exc

    temporary = Path(temporary_name)
    try:
        with CurrentStateStore.open_rw(temporary) as store:
            store.replace_snapshot(snapshot)
            staged = store.snapshot()
        if staged != snapshot:
            raise CurrentStateIntegrityError(
                "staged Current State does not match requested snapshot"
            )

        try:
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())

            os.replace(temporary, path)
            _fsync_directory(path.parent)
        except OSError as exc:
            raise CurrentStateOperationError(
                "cannot atomically replace older Current State schema"
            ) from exc

        verified_store = CurrentStateStore.open_read_only_if_exists(path)
        if verified_store is None:
            raise CurrentStateIntegrityError(
                "Current State disappeared after schema replacement"
            )
        with verified_store:
            persisted = verified_store.snapshot()
        if persisted != snapshot:
            raise CurrentStateIntegrityError(
                "replaced Current State does not match requested snapshot"
            )
        return persisted
    finally:
        _best_effort_remove(temporary)
        _best_effort_remove(temporary.with_name(temporary.name + "-wal"))
        _best_effort_remove(temporary.with_name(temporary.name + "-shm"))


def rebuild_current_state(
    repo_root: Path,
    data_dir: Path,
    redaction: RedactionPolicy,
    *,
    projection_name: str = PROJECTION_NAME,
    projection_version: int = PROJECTION_VERSION,
) -> CurrentStateSnapshot:
    data = Path(data_dir).expanduser().resolve(strict=False)
    snapshot = build_replay_snapshot(
        repo_root,
        data,
        redaction,
        projection_name=projection_name,
        projection_version=projection_version,
    )
    state_path = data / "state" / "current.sqlite3"
    if _is_rebuildable_older_state(state_path):
        return _replace_rebuildable_older_state(state_path, snapshot)

    with CurrentStateStore.open_rw(state_path) as store:
        store.replace_snapshot(snapshot)
        persisted = store.snapshot()
    if persisted is None:
        raise RuntimeError("Current State disappeared after committed replay")
    return persisted


__all__ = [
    "build_replay_snapshot",
    "rebuild_current_state",
]
