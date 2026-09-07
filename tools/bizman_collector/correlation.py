from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Any

from tools.bizman_collector.events import CollectorClock, EventSequencer
from tools.bizman_foundation.fingerprint import canonical_sha256
from tools.bizman_foundation.session import new_uuid7


@dataclass(frozen=True, slots=True)
class _Candidate:
    action: dict[str, Any]
    request: dict[str, Any]
    score: float
    delta_ms: float
    signals: tuple[str, ...]
    has_causal_signal: bool


class ActionHttpCorrelator:
    """Correlate immutable actions with immutable first-party HTTP requests.

    Source observations are expected in strictly increasing session ``sequence``
    order, which is guaranteed by the collector-wide ``EventSequencer``. That
    monotonic contract provides exact replay rejection with O(1) dedupe memory.
    Candidate state itself is bounded by ``max_recent`` and the time window.
    Heuristic links never use the reserved ``exact`` status.
    """

    def __init__(
        self,
        *,
        session_id: str,
        sequencer: EventSequencer,
        clock: CollectorClock,
        window_seconds: float = 2.0,
        max_recent: int = 4096,
    ) -> None:
        if not isinstance(window_seconds, (int, float)) or isinstance(window_seconds, bool):
            raise TypeError("window_seconds must be numeric")
        if not math.isfinite(float(window_seconds)) or float(window_seconds) <= 0:
            raise ValueError("window_seconds must be positive and finite")
        if not isinstance(max_recent, int) or isinstance(max_recent, bool) or max_recent <= 0:
            raise ValueError("max_recent must be a positive integer")

        self.session_id = session_id
        self.sequencer = sequencer
        self.clock = clock
        self.window_seconds = float(window_seconds)
        self.max_recent = max_recent
        self._actions: deque[dict[str, Any]] = deque()
        self._requests: deque[dict[str, Any]] = deque()
        self._last_source_sequence = -1
        self._max_seen_time = 0.0

    @staticmethod
    def _event_time(event: dict[str, Any]) -> float | None:
        value = event.get("monotonic_time")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            return None
        return numeric

    @staticmethod
    def _event_id(event: dict[str, Any]) -> str | None:
        value = event.get("event_id")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _event_sequence(event: dict[str, Any]) -> int | None:
        value = event.get("sequence")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    @staticmethod
    def _is_action(event: dict[str, Any]) -> bool:
        return event.get("source") in {"dom.action", "bas.action"} and event.get(
            "event_type"
        ) in {"dom.action", "bas.action"}

    @staticmethod
    def _is_request(event: dict[str, Any]) -> bool:
        return event.get("source") == "cdp.network" and event.get(
            "event_type"
        ) == "http.request"

    def _remember(self, collection: deque[dict[str, Any]], event: dict[str, Any]) -> None:
        event_id = self._event_id(event)
        if event_id is None:
            return
        if any(self._event_id(item) == event_id for item in collection):
            return
        collection.append(event)
        while len(collection) > self.max_recent:
            collection.popleft()

    def _remove_request(self, request: dict[str, Any]) -> None:
        request_id = self._event_id(request)
        if request_id is None:
            return
        for item in tuple(self._requests):
            if self._event_id(item) == request_id:
                self._requests.remove(item)
                return

    def _prune(self, event_time: float) -> None:
        self._max_seen_time = max(self._max_seen_time, event_time)
        cutoff = self._max_seen_time - self.window_seconds
        while self._actions:
            timestamp = self._event_time(self._actions[0])
            if timestamp is not None and timestamp < cutoff:
                self._actions.popleft()
                continue
            break
        while self._requests:
            timestamp = self._event_time(self._requests[0])
            if timestamp is not None and timestamp < cutoff:
                self._requests.popleft()
                continue
            break

    def _score(
        self,
        action: dict[str, Any],
        request: dict[str, Any],
    ) -> _Candidate | None:
        action_id = self._event_id(action)
        request_id = self._event_id(request)
        action_time = self._event_time(action)
        request_time = self._event_time(request)
        if (
            action_id is None
            or request_id is None
            or action_time is None
            or request_time is None
        ):
            return None

        action_target = action.get("target_id")
        request_target = request.get("target_id")
        if (
            not isinstance(action_target, str)
            or not action_target
            or action_target != request_target
        ):
            return None

        action_frame = action.get("frame_id")
        request_frame = request.get("frame_id")
        if (
            isinstance(action_frame, str)
            and action_frame
            and isinstance(request_frame, str)
            and request_frame
            and action_frame != request_frame
        ):
            return None

        delta_seconds = abs(float(request_time) - float(action_time))
        if delta_seconds > self.window_seconds:
            return None

        score = 0.0
        signals: list[str] = []

        if (
            isinstance(action_frame, str)
            and action_frame
            and isinstance(request_frame, str)
            and action_frame == request_frame
        ):
            score += 0.25
            signals.append("same-frame")

        delta_ms = delta_seconds * 1000.0
        if delta_ms <= 150.0:
            score += 0.30
            signals.append("within-150ms")
        elif delta_ms <= 500.0:
            score += 0.22
            signals.append("within-500ms")
        elif delta_ms <= 1500.0:
            score += 0.10
            signals.append("within-1500ms")

        form_path = action.get("form_action_path")
        request_path = request.get("url_path")
        path_match = (
            isinstance(form_path, str)
            and form_path
            and isinstance(request_path, str)
            and request_path == form_path
        )
        if path_match:
            score += 0.25
            signals.append("form-path-match")

        form_method = action.get("form_method")
        request_method = request.get("method")
        method_match = (
            isinstance(form_method, str)
            and form_method
            and isinstance(request_method, str)
            and request_method.upper() == form_method.upper()
        )
        if method_match:
            score += 0.15
            signals.append("method-match")

        user_gesture = request.get("has_user_gesture") is True
        if user_gesture:
            score += 0.10
            signals.append("request-user-gesture")

        trusted = action.get("is_trusted") is True
        if trusted:
            score += 0.05
            signals.append("trusted-action")

        submit_structural = (
            action.get("action_kind") == "submit" and path_match and method_match
        )
        if submit_structural:
            score += 0.05
            signals.append("submit-structural")

        score = min(score, 1.0)
        # A method match is supporting evidence, not a causal signal by itself:
        # many unrelated requests in a page share GET/POST. A path match or an
        # explicit request user-gesture signal is required for probable/strong.
        has_causal_signal = path_match or user_gesture
        return _Candidate(
            action=action,
            request=request,
            score=score,
            delta_ms=delta_ms,
            signals=tuple(sorted(set(signals))),
            has_causal_signal=has_causal_signal,
        )

    @staticmethod
    def _status(candidate: _Candidate) -> str | None:
        if not candidate.has_causal_signal:
            return "temporal-only" if candidate.score >= 0.40 else None
        if candidate.score >= 0.80:
            return "strong"
        if candidate.score >= 0.60:
            return "probable"
        if candidate.score >= 0.40:
            return "temporal-only"
        return None

    def _best_for_request(self, request: dict[str, Any]) -> _Candidate | None:
        candidates = [
            candidate
            for action in self._actions
            if (candidate := self._score(action, request)) is not None
            and self._status(candidate) is not None
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                item.score,
                -item.delta_ms,
                self._event_id(item.action) or "",
            ),
        )

    def _emit(self, candidate: _Candidate) -> dict[str, Any] | None:
        action_id = self._event_id(candidate.action)
        request_id = self._event_id(candidate.request)
        status = self._status(candidate)
        if action_id is None or request_id is None or status is None:
            return None

        event: dict[str, Any] = {
            "schema_version": "1.0",
            "event_id": new_uuid7(),
            "session_id": self.session_id,
            "sequence": self.sequencer.next(),
            "observed_at": self.clock.wall_iso(),
            "monotonic_time": float(self.clock.monotonic()),
            "source": "system",
            "event_type": "correlation.action_http",
            "confidence": "inferred",
            "target_id": candidate.request.get("target_id"),
            "frame_id": candidate.request.get("frame_id"),
            "action_refs": [action_id],
            "action_event_id": action_id,
            "network_event_id": request_id,
            "correlation_status": status,
            "correlation_score": round(candidate.score, 4),
            "delta_ms": round(candidate.delta_ms, 3),
            "signals": list(candidate.signals),
        }
        event["fingerprint"] = canonical_sha256(
            {
                "source": event["source"],
                "event_type": event["event_type"],
                "action_event_id": action_id,
                "network_event_id": request_id,
                "correlation_status": status,
                "correlation_score": event["correlation_score"],
                "signals": event["signals"],
            }
        )
        return event

    def observe(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(event, dict):
            return []
        if not self._is_action(event) and not self._is_request(event):
            return []
        event_time = self._event_time(event)
        event_id = self._event_id(event)
        sequence = self._event_sequence(event)
        if event_time is None or event_id is None or sequence is None:
            return []
        if event.get("session_id") != self.session_id:
            return []
        if sequence <= self._last_source_sequence:
            return []
        self._last_source_sequence = sequence

        self._prune(event_time)

        if self._is_action(event):
            self._remember(self._actions, event)
            links: list[dict[str, Any]] = []
            for pending_request in tuple(self._requests):
                best = self._best_for_request(pending_request)
                if best is None or self._event_id(best.action) != event_id:
                    continue
                link = self._emit(best)
                if link is not None:
                    self._remove_request(pending_request)
                    links.append(link)
            return links

        self._remember(self._requests, event)
        best = self._best_for_request(event)
        if best is None:
            return []
        link = self._emit(best)
        if link is None:
            return []
        self._remove_request(event)
        return [link]
