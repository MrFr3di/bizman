from __future__ import annotations

from dataclasses import dataclass

from bizman.collector.supply_probe import (
    SupplyInputCandidate,
    SupplyLinkCandidate,
    SupplyProbeRoute,
    inspect_supply_structure,
)
from bizman.core.context import CoreContext
from bizman.core.errors import OperationError


_MAX_PROBE_BODY_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SupplyEvidenceProbeRequest:
    source_url: str
    body: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.source_url, str) or not self.source_url:
            raise TypeError("source_url must be a non-empty string")
        if not isinstance(self.body, bytes):
            raise TypeError("body must be bytes")
        if len(self.body) > _MAX_PROBE_BODY_BYTES:
            raise ValueError("body exceeds the research size limit")


@dataclass(frozen=True, slots=True)
class SupplyEvidenceProbeResult:
    unit_id: int
    surface: str
    links: tuple[SupplyLinkCandidate, ...]
    inputs: tuple[SupplyInputCandidate, ...]


def probe_supply_evidence(
    context: CoreContext,
    request: SupplyEvidenceProbeRequest,
) -> SupplyEvidenceProbeResult:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    if not isinstance(request, SupplyEvidenceProbeRequest):
        raise TypeError("request must be SupplyEvidenceProbeRequest")
    try:
        route = SupplyProbeRoute.parse(
            request.source_url,
            approved_hosts=("bizmania.ru",),
        )
        report = inspect_supply_structure(request.body)
    except (UnicodeError, ValueError) as exc:
        raise OperationError(
            f"supply evidence probe failed: {type(exc).__name__}"
        ) from exc
    return SupplyEvidenceProbeResult(
        unit_id=route.unit_id,
        surface=route.surface,
        links=report.links,
        inputs=report.inputs,
    )


__all__ = [
    "SupplyEvidenceProbeRequest",
    "SupplyEvidenceProbeResult",
    "probe_supply_evidence",
]
