from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bizman.changes.baseline import (
    BaselineCompilation,
    BaselineCompiler,
    build_analysis_profile,
)
from bizman.changes.model import AnalysisProfile
from bizman.changes.rules import RULE_DESCRIPTORS
from bizman.foundation.redaction import RedactionPolicy


@dataclass(frozen=True, slots=True)
class AnalysisProfileCompilation:
    baseline: BaselineCompilation
    profile: AnalysisProfile


def compile_default_analysis_profile(
    repo_root: Path,
    redaction: RedactionPolicy,
) -> AnalysisProfileCompilation:
    if not isinstance(redaction, RedactionPolicy):
        raise TypeError("redaction must be RedactionPolicy")
    baseline = BaselineCompiler.compile(
        Path(repo_root).expanduser().resolve(),
        redaction,
    )
    profile = build_analysis_profile(baseline, RULE_DESCRIPTORS)
    return AnalysisProfileCompilation(
        baseline=baseline,
        profile=profile,
    )


__all__ = [
    "AnalysisProfileCompilation",
    "compile_default_analysis_profile",
]
