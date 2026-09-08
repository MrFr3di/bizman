from __future__ import annotations

from bizman.core.context import CoreContext
from bizman.core.errors import AssetError
from bizman.foundation.validation import ValidationResult
from bizman.foundation.validation import validate_repository as _validate_repository


def validate_repository(context: CoreContext) -> ValidationResult:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    try:
        return _validate_repository(context.assets.root)
    except OSError as exc:
        raise AssetError("repository validation assets are unavailable") from exc


__all__ = ["ValidationResult", "validate_repository"]
