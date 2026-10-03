"""Read-only experiment framework for verifying uncertain game mechanics."""

from bizman.experiments.delta import (
    classify_direction,
    compute_delta,
    evaluate_experiment,
)
from bizman.experiments.model import (
    DIRECTIONS,
    OUTCOME_STATUSES,
    TRACKED_FIELDS,
    ControlObservation,
    ExperimentAction,
    ExperimentError,
    ExperimentHypothesis,
    ExperimentRef,
    ExperimentResult,
    ObservationDelta,
    ObservationSnapshot,
    UnitProductDelta,
    new_experiment_id,
)


__all__ = [
    "ControlObservation",
    "DIRECTIONS",
    "ExperimentAction",
    "ExperimentError",
    "ExperimentHypothesis",
    "ExperimentRef",
    "ExperimentResult",
    "OUTCOME_STATUSES",
    "ObservationDelta",
    "ObservationSnapshot",
    "TRACKED_FIELDS",
    "UnitProductDelta",
    "classify_direction",
    "compute_delta",
    "evaluate_experiment",
    "new_experiment_id",
]
