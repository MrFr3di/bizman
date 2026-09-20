from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class AssetId(StrEnum):
    REDACTION_POLICY = "redaction-policy"
    EVENT_SCHEMA = "event-schema"
    SESSION_MANIFEST_SCHEMA = "session-manifest-schema"
    PROMOTION_BUNDLE_SCHEMA = "promotion-bundle-schema"
    KNOWLEDGE_ROOT = "knowledge-root"


_ASSET_PATHS: dict[AssetId, Path] = {
    AssetId.REDACTION_POLICY: Path("config/redaction-policy.json"),
    AssetId.EVENT_SCHEMA: Path("schemas/event.schema.json"),
    AssetId.SESSION_MANIFEST_SCHEMA: Path("schemas/session-manifest.schema.json"),
    AssetId.PROMOTION_BUNDLE_SCHEMA: Path("schemas/promotion-bundle.schema.json"),
    AssetId.KNOWLEDGE_ROOT: Path("knowledge"),
}


@dataclass(frozen=True, slots=True)
class RepositoryAssets:
    root: Path

    def __post_init__(self) -> None:
        root = Path(self.root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(root)
        object.__setattr__(self, "root", root)
        for asset_id in AssetId:
            self._resolve(asset_id)

    def path(self, asset_id: AssetId) -> Path:
        if not isinstance(asset_id, AssetId):
            raise TypeError("asset_id must be an AssetId")
        return self._resolve(asset_id)

    def _resolve(self, asset_id: AssetId) -> Path:
        candidate = (self.root / _ASSET_PATHS[asset_id]).resolve(strict=True)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"asset escapes repository root: {asset_id.value}") from exc

        if asset_id is AssetId.KNOWLEDGE_ROOT:
            if not candidate.is_dir():
                raise NotADirectoryError(candidate)
        elif not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate


__all__ = ["AssetId", "RepositoryAssets"]
