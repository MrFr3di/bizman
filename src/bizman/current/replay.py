from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Mapping

from bizman.changes.profile import compile_default_analysis_profile
from bizman.current.company_units import (
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
        sequence = event.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise TypeError("validated evidence sequence must be an integer")
        if sequence != event_count:
            raise CurrentStateIntegrityError(
                "validated evidence sequence is not contiguous"
            )

        try:
            projection = project_company_roster_event(reader, event)
        except CompanyUnitsParserIncompatible:
            parser_incompatible = True
            projection = None
        except CompanyUnitsArtifactError as exc:
            raise CurrentStateIntegrityError(
                "company/unit evidence artifact violates Current State contract"
            ) from exc

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


def _discard_rebuildable_older_state(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    try:
        if hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL"):
            connection = sqlite3.connect(path, timeout=5.0, autocommit=True)
        else:
            connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        try:
            application_id = int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            )
            user_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            strict = {
                str(row[1]): int(row[5])
                for row in connection.execute("PRAGMA table_list")
                if str(row[1]) in {"projection_meta", "replayed_session"}
            }
        finally:
            connection.close()
    except sqlite3.Error:
        return

    recognized_p4a_v1 = (
        application_id == APPLICATION_ID
        and user_version == USER_VERSION - 1
        and tables == {"projection_meta", "replayed_session"}
        and strict == {"projection_meta": 1, "replayed_session": 1}
    )
    if recognized_p4a_v1:
        path.unlink(missing_ok=True)
        path.with_name(path.name + "-wal").unlink(missing_ok=True)
        path.with_name(path.name + "-shm").unlink(missing_ok=True)


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
    _discard_rebuildable_older_state(state_path)
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
