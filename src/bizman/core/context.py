from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bizman.core.assets import RepositoryAssets
from bizman.core.time import UtcClock


@dataclass(frozen=True, slots=True)
class CoreContext:
    assets: RepositoryAssets
    data_dir: Path
    clock: UtcClock

    def __post_init__(self) -> None:
        if not isinstance(self.assets, RepositoryAssets):
            raise TypeError("assets must be RepositoryAssets")
        if not isinstance(self.clock, UtcClock):
            raise TypeError("clock must satisfy UtcClock")
        data_dir = Path(self.data_dir).expanduser().resolve(strict=False)
        object.__setattr__(self, "data_dir", data_dir)


__all__ = ["CoreContext"]
