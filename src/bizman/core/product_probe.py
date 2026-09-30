from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bizman.collector.product_probe import (
    ProductIdentityCandidate,
    ProductProbeRoute,
    inspect_product_identity_candidates,
)
from bizman.core.context import CoreContext
from bizman.core.errors import OperationError


_MAX_PROBE_BODY_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProductEvidenceProbeRequest:
    source_url: str
    body_file: Path

    def __post_init__(self) -> None:
        if not isinstance(self.source_url, str) or not self.source_url:
            raise TypeError("source_url must be a non-empty string")
        object.__setattr__(self, "body_file", Path(self.body_file))


@dataclass(frozen=True, slots=True)
class ProductEvidenceProbeResult:
    unit_id: int
    surface: str
    candidates: tuple[ProductIdentityCandidate, ...]


def probe_product_evidence(
    context: CoreContext,
    request: ProductEvidenceProbeRequest,
) -> ProductEvidenceProbeResult:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    if not isinstance(request, ProductEvidenceProbeRequest):
        raise TypeError("request must be ProductEvidenceProbeRequest")
    try:
        route = ProductProbeRoute.parse(
            request.source_url,
            approved_hosts=("bizmania.ru",),
        )
        with request.body_file.open("rb") as stream:\n            body = stream.read(_MAX_PROBE_BODY_BYTES + 1)\n        if len(body) > _MAX_PROBE_BODY_BYTES:\n            raise ValueError("probe body exceeds the research size limit")\n        candidates = inspect_product_identity_candidates(body)
    except (OSError, UnicodeError, ValueError) as exc:
        raise OperationError(
            f"product evidence probe failed: {type(exc).__name__}"
        ) from exc
    return ProductEvidenceProbeResult(
        unit_id=route.unit_id,
        surface=route.surface,
        candidates=candidates,
    )


__all__ = [
    "ProductEvidenceProbeRequest",
    "ProductEvidenceProbeResult",
    "probe_product_evidence",
]
