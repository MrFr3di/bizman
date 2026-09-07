import json
import tempfile
import unittest
from pathlib import Path

from tools.bizman_collector.storage import SessionWriter


SESSION_ID = "01991c7d-a400-7000-8000-000000000002"


def _manifest() -> dict:
    return {
        "schema_version": "1.0",
        "session_id": SESSION_ID,
        "started_at": "2026-09-07T00:00:00Z",
        "ended_at": None,
        "status": "running",
        "collector": {"name": "bizman-cdp", "version": "test"},
        "browser": {"product": "Chrome", "version": "test"},
        "protocol": {
            "name": "cdp",
            "version": "1.3",
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": [],
        "artifact_count": 0,
        "warnings": [],
    }


def _event(sequence: int) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": f"01991c7d-a400-7000-8000-{sequence:012d}",
        "session_id": SESSION_ID,
        "sequence": sequence,
        "observed_at": "2026-09-07T00:00:00Z",
        "monotonic_time": float(sequence),
        "source": "cdp.network",
        "event_type": "http.request",
        "confidence": "observed",
        "request_id": f"r{sequence}",
        "method": "GET",
        "url_path": "/test",
    }


class SessionWriterBatchingTests(unittest.TestCase):
    def test_flush_threshold_must_be_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                SessionWriter(
                    Path(tmp),
                    _manifest(),
                    flush_every_events=0,
                )

    def test_batch_boundary_flushes_events_before_finalize(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SessionWriter(
                Path(tmp),
                _manifest(),
                flush_every_events=2,
            )
            writer.append_event(_event(0))
            writer.append_event(_event(1))

            lines = writer.event_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual([json.loads(line)["sequence"] for line in lines], [0, 1])
            writer.finalize()

    def test_finalize_flushes_partial_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = SessionWriter(
                Path(tmp),
                _manifest(),
                flush_every_events=64,
            )
            for sequence in range(3):
                writer.append_event(_event(sequence))
            writer.finalize()

            lines = writer.event_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual([json.loads(line)["sequence"] for line in lines], [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
