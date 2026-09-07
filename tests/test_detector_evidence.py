from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import tempfile
import unittest

from tools.bizman_detector.evidence import (
    EvidenceFormatError,
    EvidenceIntegrityError,
    EvidenceReader,
    EvidenceStatusError,
)
from tools.bizman_foundation.session import new_uuid7


REPO_ROOT = Path(__file__).resolve().parents[1]


def _manifest(
    session_id: str,
    *,
    status: str = "completed",
    event_files: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "session_id": session_id,
        "started_at": "2026-09-07T12:00:00Z",
        "ended_at": None if status == "running" else "2026-09-07T12:00:01Z",
        "status": status,
        "collector": {"name": "bizman-cdp", "version": "0.test"},
        "browser": {"product": "Chrome/Test", "version": "1"},
        "protocol": {
            "name": "cdp",
            "version": "1.3",
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": list(event_files or []),
        "artifact_count": 0,
        "warnings": [],
    }


def _event(
    session_id: str,
    sequence: int,
    *,
    event_id: str | None = None,
    event_type: str = "fixture.event",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": event_id or new_uuid7(),
        "session_id": session_id,
        "sequence": sequence,
        "observed_at": "2026-09-07T12:00:00Z",
        "monotonic_time": float(sequence),
        "source": "system",
        "event_type": event_type,
        "confidence": "observed",
    }


def _write_json(path: Path, value: object, *, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {"ensure_ascii": False, "sort_keys": True}
    if pretty:
        kwargs["indent"] = 2
    else:
        kwargs["separators"] = (",", ":")
    path.write_text(json.dumps(value, **kwargs) + "\n", encoding="utf-8")


def _encode_events(events: list[object]) -> bytes:
    return b"".join(
        (json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for event in events
    )


def _write_session(
    data_dir: Path,
    *,
    session_id: str | None = None,
    status: str = "completed",
    events: list[object] | None = None,
    event_rel: str | None = None,
    raw_event_bytes: bytes | None = None,
    write_event_file: bool = True,
) -> tuple[str, Path, Path]:
    resolved_session_id = session_id or new_uuid7()
    resolved_events = events if events is not None else [_event(resolved_session_id, 0)]
    resolved_event_rel = event_rel or f"events/2026-09-07/{resolved_session_id}.jsonl"

    if write_event_file:
        event_path = data_dir.joinpath(*PurePosixPath(resolved_event_rel).parts)
        event_path.parent.mkdir(parents=True, exist_ok=True)
        event_path.write_bytes(
            raw_event_bytes if raw_event_bytes is not None else _encode_events(resolved_events)
        )
    else:
        event_path = data_dir / "missing.jsonl"

    manifest_path = data_dir / "sessions" / resolved_session_id / "manifest.json"
    _write_json(
        manifest_path,
        _manifest(
            resolved_session_id,
            status=status,
            event_files=[resolved_event_rel],
        ),
    )
    return resolved_session_id, manifest_path, event_path


def _write_manifest_only(data_dir: Path, *, status: str) -> str:
    session_id = new_uuid7()
    manifest_path = data_dir / "sessions" / session_id / "manifest.json"
    _write_json(manifest_path, _manifest(session_id, status=status, event_files=[]))
    return session_id


class EvidenceManifestTests(unittest.TestCase):
    def test_manifest_schema_directory_identity_and_finalized_status_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            session_id, manifest_path, _ = _write_session(data_dir)
            reader = EvidenceReader(REPO_ROOT, data_dir)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["started_at"] = "not-a-date"
            _write_json(manifest_path, manifest)
            with self.assertRaisesRegex(EvidenceFormatError, "manifest.*schema"):
                reader.inspect(session_id)

            other_id = new_uuid7()
            manifest["started_at"] = "2026-09-07T12:00:00Z"
            manifest["session_id"] = other_id
            _write_json(manifest_path, manifest)
            with self.assertRaisesRegex(EvidenceIntegrityError, "session.*directory"):
                reader.inspect(session_id)

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            running_id, _, _ = _write_session(data_dir, status="running")
            with self.assertRaisesRegex(EvidenceStatusError, "finalized"):
                EvidenceReader(REPO_ROOT, data_dir).inspect(running_id)

    def test_finalized_manifest_requires_ended_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            session_id, manifest_path, _ = _write_session(data_dir)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["ended_at"] = None
            _write_json(manifest_path, manifest)

            with self.assertRaisesRegex(EvidenceStatusError, "ended_at"):
                EvidenceReader(REPO_ROOT, data_dir).inspect(session_id)

    def test_manifest_formatting_does_not_change_canonical_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            session_id, manifest_path, _ = _write_session(data_dir)
            reader = EvidenceReader(REPO_ROOT, data_dir)
            first = reader.inspect(session_id)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            _write_json(manifest_path, manifest, pretty=False)
            second = reader.inspect(session_id)

            self.assertEqual(first.manifest_sha256, second.manifest_sha256)
            self.assertEqual(first.evidence_sha256, second.evidence_sha256)


class EvidencePathTests(unittest.TestCase):
    def test_unsafe_or_missing_event_paths_fail_closed(self):
        unsafe = (
            "/tmp/escape.jsonl",
            "../events/escape.jsonl",
            "sessions/not-events.jsonl",
            "events/not-jsonl.txt",
            "events//noncanonical.jsonl",
            "events/missing.jsonl",
        )
        for event_rel in unsafe:
            with self.subTest(event_rel=event_rel), tempfile.TemporaryDirectory() as tmp:
                data_dir = Path(tmp)
                session_id, _, _ = _write_session(
                    data_dir,
                    event_rel=event_rel,
                    write_event_file=False,
                )
                with self.assertRaises(EvidenceFormatError):
                    EvidenceReader(REPO_ROOT, data_dir).inspect(session_id)

    def test_symlink_escape_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            outside = root / "outside.jsonl"
            session_id = new_uuid7()
            outside.write_bytes(_encode_events([_event(session_id, 0)]))
            link = data_dir / "events" / "linked.jsonl"
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            manifest_path = data_dir / "sessions" / session_id / "manifest.json"
            _write_json(
                manifest_path,
                _manifest(session_id, event_files=["events/linked.jsonl"]),
            )

            with self.assertRaisesRegex(EvidenceFormatError, "escapes data directory"):
                EvidenceReader(REPO_ROOT, data_dir).inspect(session_id)


class EvidenceStreamTests(unittest.TestCase):
    def test_stream_rejects_oversize_utf8_json_schema_session_sequence_and_duplicate_errors(self):
        cases: list[tuple[str, bytes | None, list[object] | None, int | None]] = []

        session_id = new_uuid7()
        cases.append(("invalid-utf8", b"\xff\n", None, None))
        cases.append(("malformed-json", b'{"schema_version":\n', None, None))
        cases.append(("non-object", b"[]\n", None, None))
        cases.append(("invalid-schema", _encode_events([{"schema_version": "1.0"}]), None, None))
        cases.append(("wrong-session", None, [_event(new_uuid7(), 0)], None))
        cases.append(("sequence-gap", None, [_event(session_id, 0), _event(session_id, 2)], None))
        duplicate_id = new_uuid7()
        cases.append(
            (
                "duplicate-event-id",
                None,
                [_event(session_id, 0, event_id=duplicate_id), _event(session_id, 1, event_id=duplicate_id)],
                None,
            )
        )
        cases.append(("oversize-line", None, [_event(session_id, 0)], 64))

        for name, raw_bytes, events, line_limit in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                data_dir = Path(tmp)
                current_id = session_id if name in {"sequence-gap", "duplicate-event-id", "oversize-line"} else new_uuid7()
                resolved_events = events
                if name == "wrong-session":
                    resolved_events = [_event(new_uuid7(), 0)]
                _write_session(
                    data_dir,
                    session_id=current_id,
                    events=resolved_events or [],
                    raw_event_bytes=raw_bytes,
                )
                kwargs = {"max_event_line_bytes": line_limit} if line_limit is not None else {}
                with self.assertRaises((EvidenceFormatError, EvidenceIntegrityError)):
                    EvidenceReader(REPO_ROOT, data_dir, **kwargs).inspect(current_id)

    def test_event_byte_mutation_changes_evidence_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            session_id, _, event_path = _write_session(data_dir)
            reader = EvidenceReader(REPO_ROOT, data_dir)
            first = reader.inspect(session_id)

            event = json.loads(event_path.read_text(encoding="utf-8"))
            event["event_type"] = "fixture.changed"
            event_path.write_bytes(_encode_events([event]))
            second = reader.inspect(session_id)

            self.assertEqual(first.manifest_sha256, second.manifest_sha256)
            self.assertNotEqual(first.evidence_sha256, second.evidence_sha256)

    def test_iter_events_revalidates_after_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            session_id, _, event_path = _write_session(data_dir)
            reader = EvidenceReader(REPO_ROOT, data_dir)
            reader.inspect(session_id)

            event = json.loads(event_path.read_text(encoding="utf-8"))
            event["sequence"] = 4
            event_path.write_bytes(_encode_events([event]))

            with self.assertRaisesRegex(EvidenceIntegrityError, "sequence"):
                list(reader.iter_events(session_id))


class EvidenceArtifactTests(unittest.TestCase):
    @staticmethod
    def _artifact_path(data_dir: Path, digest: str) -> Path:
        return data_dir / "artifacts" / "sha256" / digest[:2] / digest

    def test_verified_artifact_recomputes_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            payload = b"sanitized-body"
            digest = hashlib.sha256(payload).hexdigest()
            path = self._artifact_path(data_dir, digest)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

            reader = EvidenceReader(REPO_ROOT, data_dir)
            self.assertEqual(reader.read_verified_artifact(f"sha256:{digest}"), payload)

            path.write_bytes(b"tampered")
            with self.assertRaisesRegex(EvidenceIntegrityError, "digest"):
                reader.read_verified_artifact(f"sha256:{digest}")

    def test_artifact_ref_and_size_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            reader = EvidenceReader(REPO_ROOT, data_dir, max_artifact_bytes=4)

            for ref in (
                "sha256:../escape",
                "SHA256:" + "a" * 64,
                "sha256:" + "A" * 64,
                "sha256:" + "a" * 63,
            ):
                with self.subTest(ref=ref), self.assertRaises(EvidenceFormatError):
                    reader.read_verified_artifact(ref)

            payload = b"12345"
            digest = hashlib.sha256(payload).hexdigest()
            path = self._artifact_path(data_dir, digest)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            with self.assertRaisesRegex(EvidenceIntegrityError, "size"):
                reader.read_verified_artifact(f"sha256:{digest}")


class EvidenceDiscoveryTests(unittest.TestCase):
    def test_iter_finalized_returns_only_completed_and_cancelled_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            completed = _write_manifest_only(data_dir, status="completed")
            cancelled = _write_manifest_only(data_dir, status="cancelled")
            _write_manifest_only(data_dir, status="failed")
            _write_manifest_only(data_dir, status="running")

            identities = list(EvidenceReader(REPO_ROOT, data_dir).iter_finalized())
            self.assertEqual(
                [item.session_id for item in identities],
                sorted((completed, cancelled)),
            )
            self.assertEqual({item.status for item in identities}, {"completed", "cancelled"})


if __name__ == "__main__":
    unittest.main()
