from __future__ import annotations


class BizManError(Exception):
    """Base class for stable application-facing BizMan failures."""


class ConfigurationError(BizManError):
    """Application configuration is missing or invalid."""


class AssetError(BizManError):
    """Required repository assets are unavailable or invalid."""


class DataIntegrityError(BizManError):
    """Operational evidence or state failed integrity validation."""


class ContractMismatchError(BizManError):
    """Versioned semantic contracts are incompatible."""


class OperationError(BizManError):
    """An expected application operation failed."""


__all__ = [
    "AssetError",
    "BizManError",
    "ConfigurationError",
    "ContractMismatchError",
    "DataIntegrityError",
    "OperationError",
]
