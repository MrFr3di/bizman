"""Deterministic offline change detection for sanitized BizMan evidence."""

from bizman.changes.model import ChangeSummary
from bizman.changes.query import ChangeSummaryReader

CONTRACT_SCHEMA_VERSION = 1
NORMALIZATION_VERSION = 1
EXTRACTION_VERSION = 1
PROMOTION_SCHEMA_VERSION = 1

__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "NORMALIZATION_VERSION",
    "EXTRACTION_VERSION",
    "PROMOTION_SCHEMA_VERSION",
    "ChangeSummary",
    "ChangeSummaryReader",
]
