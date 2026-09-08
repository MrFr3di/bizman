from __future__ import annotations

from types import SimpleNamespace
import unittest

from tools.bizman_detector.evidence import EvidenceIdentity
from tools.bizman_detector.model import DiffFact, MatchState
from tools.bizman_detector.runner import DetectorRunner


EARLIER = "01991c7d-a400-7000-8000-000000000021"
LATER = "01991c7d-a400-7000-8000-000000000022"
EVENT_ID = "01991c7d-a400-7000-8000-000000000023"


def _identity(session_id: str, started_at: str, digest_char: str) -> EvidenceIdentity:
    return EvidenceIdentity(
        session_id=session_id,
        manifest_sha256=digest_char * 64,
        evidence_sha256=digest_char * 64,
        started_at=started_at,
        ended_at="2026-09-08T01:00:00Z",
        status="completed",
    )


class _Reader:
    def __init__(self) -> None:
        self.identities = {
            EARLIER: _identity(EARLIER, "2026-09-08T00:30:00+02:00", "a"),
            LATER: _identity(LATER, "2026-09-07T23:00:00+00:00", "b"),
        }

    def iter_session_statuses(self, selected=()):
        yield SimpleNamespace(session_id=LATER, status="completed")
        yield SimpleNamespace(session_id=EARLIER, status="completed")

    def inspect(self, session_id: str):
        return self.identities[session_id]


class _Extractor:
    def __init__(self) -> None:
        self.order: list[str] = []

    def extract(self, identity: EvidenceIdentity):
        self.order.append(identity.session_id)
        return identity


class _Diff:
    def compare(self, observations):
        return (
            DiffFact(
                state=MatchState.KNOWN,
                kind="endpoint.known",
                subject=(("path", "/known"),),
                evidence_event_ids=(EVENT_ID,),
            ),
        )


class _Rules:
    def apply(self, facts):
        return ()


class _Builder:
    def build(self, identity, profile, findings):
        raise AssertionError("no findings should build a bundle")


class RunnerRfc3339OrderingTests(unittest.TestCase):
    def test_sessions_sort_by_actual_instant_not_timestamp_text(self) -> None:
        reader = _Reader()
        extractor = _Extractor()
        runner = DetectorRunner(
            profile=SimpleNamespace(sha256="c" * 64, baseline_sha256="d" * 64),
            reader=reader,
            extractor=extractor,
            semantic_diff=_Diff(),
            rule_engine=_Rules(),
            bundle_builder=_Builder(),
            state=None,
            materializer=None,
            dry_run=True,
        )

        runner.run()

        self.assertEqual(extractor.order, [EARLIER, LATER])


if __name__ == "__main__":
    unittest.main()
