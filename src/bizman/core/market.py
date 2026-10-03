"""Path-free Core reads over the append-only market observation store.

This module mirrors :mod:`bizman.core.current_read`: callers pass a
:class:`~bizman.core.context.CoreContext` and a frozen request DTO, never a
store or a filesystem path, and every storage failure is translated into the
stable :mod:`bizman.core.errors` vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import re
import sqlite3

from bizman.core.context import CoreContext
from bizman.core.errors import (
    ConfigurationError,
    ContractMismatchError,
    DataIntegrityError,
    OperationError,
)
from bizman.market import (
    SURFACES,
    MarketContractError,
    MarketProjectionEntry,
    MarketStore,
    MarketStoreCompatibilityError,
    MarketStoreIntegrityError,
    MarketStoreOperationError,
    build_market_projection,
)


_MARKET_UNAVAILABLE = (
    "Market observations are unavailable; collect market evidence before "
    "using market reads"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _require_sha256(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
    return value


def _non_negative_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _require_surface(value: object) -> str:
    text = _require_text(value, name="surface")
    if text not in SURFACES:
        raise ValueError("surface must be a known market surface")
    return text


@dataclass(frozen=True, slots=True)
class MarketProjectionRequest:
    """Request the latest-observation market projection."""

    surface: str | None = None

    def __post_init__(self) -> None:
        if self.surface is not None:
            object.__setattr__(
                self,
                "surface",
                _require_surface(self.surface),
            )


@dataclass(frozen=True, slots=True)
class MarketProjectionResult:
    """Path-free result over one market projection."""

    surface: str | None
    entry_count: int
    observation_count: int
    projection_fingerprint: str
    entries: tuple[MarketProjectionEntry, ...]

    def __post_init__(self) -> None:
        if self.surface is not None:
            object.__setattr__(
                self,
                "surface",
                _require_surface(self.surface),
            )
        _non_negative_int(self.entry_count, name="entry_count")
        _non_negative_int(self.observation_count, name="observation_count")
        _require_sha256(
            self.projection_fingerprint,
            name="projection_fingerprint",
        )
        entries = tuple(self.entries)
        if not all(
            isinstance(entry, MarketProjectionEntry) for entry in entries
        ):
            raise TypeError(
                "entries must contain only MarketProjectionEntry values"
            )
        if len(entries) != self.entry_count:
            raise ValueError("entry_count must match the number of entries")
        object.__setattr__(self, "entries", entries)


@contextmanager
def _market_store(context: CoreContext) -> Iterator[MarketStore | None]:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    store: MarketStore | None = None
    try:
        store = MarketStore.open_read_only_if_exists(
            context.data_dir / "market" / "market.sqlite3"
        )
        yield store
    except MarketContractError as exc:
        raise DataIntegrityError(
            "Market projection failed integrity validation"
        ) from exc
    except MarketStoreCompatibilityError as exc:
        raise ContractMismatchError("Market contract is incompatible") from exc
    except MarketStoreIntegrityError as exc:
        raise DataIntegrityError("Market failed integrity validation") from exc
    except MarketStoreOperationError as exc:
        raise OperationError("Market read operation failed") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OperationError("Market read operation failed") from exc
    finally:
        if store is not None:
            store.close()


def market_projection(
    context: CoreContext,
    request: MarketProjectionRequest,
) -> MarketProjectionResult:
    """Read the deterministic latest-observation market projection."""

    if not isinstance(request, MarketProjectionRequest):
        raise TypeError("request must be MarketProjectionRequest")
    with _market_store(context) as store:
        if store is None:
            raise ConfigurationError(_MARKET_UNAVAILABLE)
        projection = build_market_projection(
            store.iter_observations(surface=request.surface)
        )
    return MarketProjectionResult(
        surface=request.surface,
        entry_count=len(projection.entries),
        observation_count=projection.observation_count,
        projection_fingerprint=projection.projection_fingerprint,
        entries=projection.entries,
    )


__all__ = [
    "MarketProjectionRequest",
    "MarketProjectionResult",
    "market_projection",
]
