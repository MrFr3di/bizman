from __future__ import annotations

from dataclasses import dataclass

from websockets.exceptions import WebSocketException

from bizman.collector.cdp import CdpError
from bizman.collector.runtime import run_collection
from bizman.core.assets import AssetId
from bizman.core.context import CoreContext
from bizman.core.errors import AssetError, ConfigurationError, OperationError
from bizman.foundation.redaction import load_redaction_policy


_EXPECTED_OPERATION_FAILURES = (CdpError, OSError, WebSocketException)


@dataclass(frozen=True, slots=True)
class CollectionRequest:
    endpoint: str = "http://127.0.0.1:9222"
    hosts: tuple[str, ...] = ("bizmania.ru",)
    event_queue_size: int = 8192

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, str) or not self.endpoint:
            raise TypeError("endpoint must be a non-empty string")
        if not isinstance(self.hosts, tuple) or not self.hosts:
            raise TypeError("hosts must be a non-empty tuple of strings")
        if not all(isinstance(host, str) and host for host in self.hosts):
            raise TypeError("hosts must contain only non-empty strings")
        if (
            isinstance(self.event_queue_size, bool)
            or not isinstance(self.event_queue_size, int)
            or self.event_queue_size <= 0
        ):
            raise ValueError("event_queue_size must be a positive integer")


@dataclass(frozen=True, slots=True)
class CollectionResult:
    session_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id:
            raise TypeError("session_id must be a non-empty string")


async def collect(context: CoreContext, request: CollectionRequest) -> CollectionResult:
    if not isinstance(context, CoreContext):
        raise TypeError("context must be CoreContext")
    if not isinstance(request, CollectionRequest):
        raise TypeError("request must be CollectionRequest")

    try:
        redaction = load_redaction_policy(context.assets.path(AssetId.REDACTION_POLICY))
    except (OSError, ValueError) as exc:
        raise AssetError("redaction policy is unavailable or invalid") from exc

    try:
        session_id = await run_collection(
            endpoint=request.endpoint,
            data_dir=context.data_dir,
            hosts=request.hosts,
            redaction_policy=redaction,
            event_queue_size=request.event_queue_size,
        )
    except ValueError as exc:
        raise ConfigurationError("collection request is invalid") from exc
    except BaseExceptionGroup as exc:
        _, unexpected = exc.split(_EXPECTED_OPERATION_FAILURES)
        if unexpected is not None:
            raise
        raise OperationError("collection operation failed") from exc
    except _EXPECTED_OPERATION_FAILURES as exc:
        raise OperationError("collection operation failed") from exc

    return CollectionResult(session_id=session_id)


__all__ = ["CollectionRequest", "CollectionResult", "collect"]
