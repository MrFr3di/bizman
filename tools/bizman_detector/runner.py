from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.bizman_detector.baseline import BaselineCompiler, build_analysis_profile
from tools.bizman_detector.diff import SemanticDiff
from tools.bizman_detector.evidence import EvidenceIdentity, EvidenceReader
from tools.bizman_detector.extract import ObservationExtractor
from tools.bizman_detector.model import AnalysisProfile, DiffFact, MatchState
from tools.bizman_detector.promotion import PromotionBundleBuilder, PromotionMaterializer
from tools.bizman_detector.rules import RULE_DESCRIPTORS, RuleEngine
from tools.bizman_detector.state import DetectorState, TransactionResult
from tools.bizman_foundation.redaction import RedactionPolicy


_FINALIZED_STATUSES = frozenset({"completed", "cancelled"})
_FAILED_STATUS = "failed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DetectorRunSummary:
    analysis_profile_sha256: str
    baseline_sha256: str
    dry_run: bool
    sessions_discovered: int
    sessions_processed: int
    sessions_checkpointed: int
    sessions_failed_skipped: int
    sessions_unfinalized_skipped: int
    evidence_sha256s: tuple[str, ...]
    fact_counts: dict[str, int]
    first_seen_change_ids: tuple[str, ...]
    repeated_change_ids: tuple[str, ...]
    materialized_bundle_count: int
    pending_bundle_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_profile_sha256": self.analysis_profile_sha256,
            "baseline_sha256": self.baseline_sha256,
            "dry_run": self.dry_run,
            "sessions_discovered": self.sessions_discovered,
            "sessions_processed": self.sessions_processed,
            "sessions_checkpointed": self.sessions_checkpointed,
            "sessions_failed_skipped": self.sessions_failed_skipped,
            "sessions_unfinalized_skipped": self.sessions_unfinalized_skipped,
            "evidence_sha256s": list(self.evidence_sha256s),
            "fact_counts": dict(self.fact_counts),
            "first_seen_change_ids": list(self.first_seen_change_ids),
            "repeated_change_ids": list(self.repeated_change_ids),
            "materialized_bundle_count": self.materialized_bundle_count,
            "pending_bundle_count": self.pending_bundle_count,
        }


class DetectorRunner:
    """Deterministic coordinator for evidence replay, state and promotions."""

    def __init__(
        self,
        *,
        profile: AnalysisProfile | Any,
        reader: EvidenceReader | Any,
        extractor: ObservationExtractor | Any,
        semantic_diff: SemanticDiff | Any,
        rule_engine: RuleEngine | Any,
        bundle_builder: PromotionBundleBuilder | Any,
        state: DetectorState | Any | None,
        materializer: PromotionMaterializer | Any | None,
        selected: tuple[str, ...] = (),
        dry_run: bool = False,
        clock: Callable[[], str] = _utc_now,
        owns_state: bool = False,
    ) -> None:
        if not isinstance(selected, tuple):
            selected = tuple(selected)
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not dry_run and (state is None or materializer is None):
            raise ValueError("normal detector run requires writable state and materializer")
        if dry_run and materializer is not None:
            raise ValueError("dry-run must not have a promotion materializer")
        self.profile = profile
        self.reader = reader
        self.extractor = extractor
        self.semantic_diff = semantic_diff
        self.rule_engine = rule_engine
        self.bundle_builder = bundle_builder
        self.state = state
        self.materializer = materializer
        self.selected = selected
        self.dry_run = dry_run
        self.clock = clock
        self._owns_state = owns_state
        self._closed = False

    @classmethod
    def from_paths(
        cls,
        *,
        repo_root: Path,
        data_dir: Path,
        redaction: RedactionPolicy,
        selected: tuple[str, ...] = (),
        dry_run: bool = False,
        clock: Callable[[], str] = _utc_now,
    ) -> "DetectorRunner":
        root = Path(repo_root).expanduser().resolve()
        data = Path(data_dir).expanduser().resolve(strict=False)
        if not isinstance(redaction, RedactionPolicy):
            raise TypeError("redaction must be RedactionPolicy")

        compilation = BaselineCompiler.compile(root, redaction)
        profile = build_analysis_profile(compilation, RULE_DESCRIPTORS)
        reader = EvidenceReader(root, data)
        extractor = ObservationExtractor(compilation.contract, reader, redaction)
        semantic_diff = SemanticDiff(compilation.contract)
        rule_engine = RuleEngine(
            RULE_DESCRIPTORS,
            normalization_version=profile.normalization_version,
            extraction_version=profile.extraction_version,
        )
        schema_path = root / "schemas" / "promotion-bundle.schema.json"
        bundle_builder = PromotionBundleBuilder(schema_path)
        state_path = data / "detector" / "state.sqlite3"

        if dry_run:
            state = DetectorState.open_read_only_if_exists(state_path)
            materializer = None
        else:
            state = DetectorState.open_rw(state_path)
            materializer = PromotionMaterializer(data, state, schema_path)

        return cls(
            profile=profile,
            reader=reader,
            extractor=extractor,
            semantic_diff=semantic_diff,
            rule_engine=rule_engine,
            bundle_builder=bundle_builder,
            state=state,
            materializer=materializer,
            selected=tuple(selected),
            dry_run=dry_run,
            clock=clock,
            owns_state=True,
        )

    def close(self) -> None:
        if self._closed:
            return
        if self._owns_state and self.state is not None:
            self.state.close()
        self._closed = True

    def __enter__(self) -> "DetectorRunner":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _session_statuses(self) -> tuple[Any, ...]:
        return tuple(self.reader.iter_session_statuses(self.selected))

    def _existing_change_ids(self, change_ids: tuple[str, ...]) -> frozenset[str]:
        if not change_ids or self.state is None:
            return frozenset()
        return frozenset(self.state.existing_change_ids(self.profile.sha256, change_ids))

    @staticmethod
    def _count_facts(facts: Iterable[DiffFact], counts: dict[str, int]) -> None:
        for fact in facts:
            if not isinstance(fact, DiffFact):
                raise TypeError("semantic diff must return DiffFact values")
            key = fact.state.value
            if key not in counts:
                raise ValueError(f"unsupported diff state {key!r}")
            counts[key] += 1

    def run(self) -> DetectorRunSummary:
        fact_counts = {state.value: 0 for state in MatchState}
        first_seen: set[str] = set()
        repeated: set[str] = set()
        evidence_hashes: list[str] = []
        materialized_paths: set[Path] = set()
        sessions_processed = 0
        sessions_checkpointed = 0
        failed_skipped = 0
        unfinalized_skipped = 0

        if not self.dry_run:
            assert self.materializer is not None
            recovered = self.materializer.materialize_pending(materialized_at=self.clock())
            materialized_paths.update(Path(path) for path in recovered)

        statuses = self._session_statuses()
        finalized_ids: list[str] = []
        for status in statuses:
            state = str(status.status)
            if state in _FINALIZED_STATUSES:
                finalized_ids.append(str(status.session_id))
            elif state == _FAILED_STATUS:
                failed_skipped += 1
            else:
                unfinalized_skipped += 1

        identities = [self.reader.inspect(session_id) for session_id in finalized_ids]
        identities.sort(key=lambda identity: (identity.started_at, identity.session_id))

        for identity in identities:
            if not isinstance(identity, EvidenceIdentity):
                raise TypeError("reader.inspect must return EvidenceIdentity")
            evidence_hashes.append(identity.evidence_sha256)
            observations = self.extractor.extract(identity)
            facts = tuple(self.semantic_diff.compare(observations))
            self._count_facts(facts, fact_counts)
            findings = tuple(self.rule_engine.apply(facts))
            finding_ids = tuple(sorted({finding.change_id for finding in findings}))

            if self.dry_run:
                existing = self._existing_change_ids(finding_ids)
                repeated.update(existing)
                unseen = tuple(
                    finding for finding in findings if finding.change_id not in existing
                )
                first_seen.update(finding.change_id for finding in unseen)
                if unseen:
                    # Full schema/privacy validation remains part of dry-run, but
                    # the canonical payload stays in memory and never enters state.
                    self.bundle_builder.build(identity, self.profile, unseen)
                sessions_processed += 1
                continue

            assert self.state is not None
            result: TransactionResult = self.state.process_session_transaction(
                identity=identity,
                profile=self.profile,
                findings=findings,
                outbox_factory=self.bundle_builder.build,
                processed_at=self.clock(),
            )
            if result.processed:
                sessions_processed += 1
                first_for_session = frozenset(result.first_seen_change_ids)
                first_seen.update(first_for_session)
                repeated.update(
                    change_id for change_id in finding_ids if change_id not in first_for_session
                )
            else:
                sessions_checkpointed += 1
                repeated.update(finding_ids)

            assert self.materializer is not None
            materialized = self.materializer.materialize_pending(materialized_at=self.clock())
            materialized_paths.update(Path(path) for path in materialized)

        pending_bundle_count = 0
        if self.state is not None:
            pending_bundle_count = len(self.state.pending_outbox())

        return DetectorRunSummary(
            analysis_profile_sha256=str(self.profile.sha256),
            baseline_sha256=str(self.profile.baseline_sha256),
            dry_run=self.dry_run,
            sessions_discovered=len(statuses),
            sessions_processed=sessions_processed,
            sessions_checkpointed=sessions_checkpointed,
            sessions_failed_skipped=failed_skipped,
            sessions_unfinalized_skipped=unfinalized_skipped,
            evidence_sha256s=tuple(evidence_hashes),
            fact_counts=fact_counts,
            first_seen_change_ids=tuple(sorted(first_seen)),
            repeated_change_ids=tuple(sorted(repeated)),
            materialized_bundle_count=len(materialized_paths),
            pending_bundle_count=pending_bundle_count,
        )


__all__ = ["DetectorRunSummary", "DetectorRunner"]
