from __future__ import annotations

from dataclasses import dataclass

from bizman.core.context import CoreContext
from bizman.core.errors import AssetError
from bizman.foundation.validation import ValidationResult as _ValidationResult
from bizman.foundation.validation import validate_repository as _validate_repository


@dataclass(frozen=True, slots=True)
class ValidationResult:
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    @classmethod
    def from_internal(cls, value: _ValidationResult) -> "ValidationResult":
        if not isinstance(value, _ValidationResult):
            raise TypeError("value must be foundation validation result")
        return cls(errors=tuple(value.errors), warnings=tuple(value.warnings))


def validate_repository(context: CoreContext) -> ValidationResult:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    try:
        result = _validate_repository(context.assets.root)
    except OSError as exc:
        raise AssetError("repository validation assets are unavailable") from exc
    return ValidationResult.from_internal(result)


__all__ = ["ValidationResult", "validate_repository"]
