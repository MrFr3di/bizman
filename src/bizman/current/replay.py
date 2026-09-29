from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from bizman.changes.profile import compile_default_analysis_profile
from bizman.current.model import (
    PROJECTION_NAME,
    PROJECTION_VERSION,
    CurrentProjectionSpec,
    CurrentStateSnapshot,
    ReplaySession,
    build_current_snapshot,
)
from bizman.current.state import CurrentStateIntegrityError, CurrentStateStore
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
) -> ReplaySession:
    event_count = 0
    last_sequence: int | None = None
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
        event_count += 1
        last_sequence = sequence

    try:
        return ReplaySession(
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
    sessions = tuple(
        _replay_session(reader, identity)
        for identity in _ordered_identities(reader)
    )
    return build_current_snapshot(spec, sessions)


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
