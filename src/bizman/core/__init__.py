"""Stable application-facing BizMan core boundary."""

from bizman.changes.runner import DetectorRunSummary
from bizman.core.assets import AssetId, RepositoryAssets
from bizman.core.collection import CollectionRequest, CollectionResult, collect
from bizman.core.context import CoreContext
from bizman.core.detection import DetectionRequest, detect_changes
from bizman.core.errors import (
    AssetError,
    BizManError,
    ConfigurationError,
    ContractMismatchError,
    DataIntegrityError,
    OperationError,
)
from bizman.core.time import SystemUtcClock, UtcClock
from bizman.core.validation import ValidationResult, validate_repository


__all__ = [
    "AssetError",
    "AssetId",
    "BizManError",
    "CollectionRequest",
    "CollectionResult",
    "ConfigurationError",
    "ContractMismatchError",
    "CoreContext",
    "DataIntegrityError",
    "DetectionRequest",
    "DetectorRunSummary",
    "OperationError",
    "RepositoryAssets",
    "SystemUtcClock",
    "UtcClock",
    "ValidationResult",
    "collect",
    "detect_changes",
    "validate_repository",
]
