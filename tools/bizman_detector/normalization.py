from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
import re
from typing import Final

from tools.bizman_detector.model import PathMatch
from tools.bizman_foundation.redaction import RedactionPolicy


_ASCII_INDEX_RE: Final = re.compile(r"\[[0-9]+\]")
_ASCII_NUMERIC_KEY_RE: Final = re.compile(r"[0-9]+")
_PLACEHOLDER_RE: Final = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_HTTP_TOKEN_RE: Final = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


class AmbiguousPathError(ValueError):
    """Raised when equally specific curated templates match one pathname."""


@dataclass(frozen=True, slots=True)
class _Template:
    pattern: str
    segments: tuple[str, ...]
    literal_count: int
    placeholder_count: int

    @property
    def specificity(self) -> tuple[int, int]:
        # More literals are better; for equal length, fewer placeholders are better.
        return (self.literal_count, -self.placeholder_count)


def normalize_field_name(
    name: object,
    redaction: RedactionPolicy,
) -> str | None:
    """Normalize one structural key without ever inspecting its value.

    Semantics v1 intentionally recognizes only ASCII decimal indexes and
    digit-only ASCII dynamic keys so other producers can reproduce the same
    result without Python-specific Unicode decimal behavior.
    """

    if not isinstance(name, str):
        return None
    value = name.strip()
    if not value:
        return None
    if redaction.should_drop_field(value):
        return None
    if _ASCII_NUMERIC_KEY_RE.fullmatch(value):
        return "{numeric-key}"
    return _ASCII_INDEX_RE.sub("[n]", value)


def normalize_key_set(
    names: Iterable[object],
    redaction: RedactionPolicy,
) -> tuple[str, ...]:
    normalized = {
        value
        for name in names
        if (value := normalize_field_name(name, redaction)) is not None
    }
    return tuple(sorted(normalized))


def normalize_method(value: object) -> str:
    """Return an uppercase RFC HTTP-token method without implicit trimming."""

    if not isinstance(value, str) or not value:
        raise ValueError("HTTP method must be a non-empty ASCII token")
    if _HTTP_TOKEN_RE.fullmatch(value) is None:
        raise ValueError("HTTP method must be a non-empty ASCII token")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("HTTP method must be a non-empty ASCII token") from exc
    return value.upper()


def normalize_origin_relative_path(value: object) -> str:
    """Validate a pathname while preserving its exact encoded representation.

    Detector semantics v1 deliberately doesn't percent-decode, Unicode-normalize,
    collapse slashes, or infer trailing-slash equivalence. Query and fragment
    components are rejected rather than silently discarded.
    """

    if not isinstance(value, str) or not value:
        raise ValueError("path must be a non-empty origin-relative pathname")
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError("path must be a non-empty origin-relative pathname")
    if "?" in value or "#" in value:
        raise ValueError("path must not contain query or fragment components")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError("path must not contain ASCII control characters")
    return value


def _segments(path: str) -> tuple[str, ...]:
    if path == "/":
        return ()
    return tuple(path[1:].split("/"))


def _is_placeholder(segment: str) -> bool:
    return _PLACEHOLDER_RE.fullmatch(segment) is not None


class PathMatcher:
    """Match literal and single-segment template paths deterministically.

    Literal matches always win. Templates are bucketed by segment count, then
    ranked by literal-segment count. Equally specific distinct matches are a
    baseline conflict rather than being resolved by input ordering.
    """

    def __init__(self, patterns: Iterable[str]) -> None:
        exact: set[str] = set()
        templates_by_segments: dict[int, list[_Template]] = defaultdict(list)

        for raw_pattern in patterns:
            pattern = normalize_origin_relative_path(raw_pattern)
            segments = _segments(pattern)
            placeholder_count = sum(_is_placeholder(segment) for segment in segments)
            if placeholder_count == 0:
                exact.add(pattern)
                continue

            for segment in segments:
                if segment.startswith("{") or segment.endswith("}"):
                    if not _is_placeholder(segment):
                        raise ValueError(f"invalid path placeholder in {pattern!r}")

            template = _Template(
                pattern=pattern,
                segments=segments,
                literal_count=len(segments) - placeholder_count,
                placeholder_count=placeholder_count,
            )
            templates_by_segments[len(segments)].append(template)

        self._exact = frozenset(exact)
        self._templates = {
            count: tuple(sorted(items, key=lambda item: item.pattern))
            for count, items in templates_by_segments.items()
        }

    @staticmethod
    def _template_matches(template: _Template, path_segments: tuple[str, ...]) -> bool:
        for pattern_segment, actual_segment in zip(template.segments, path_segments, strict=True):
            if _is_placeholder(pattern_segment):
                if not actual_segment:
                    return False
                continue
            if pattern_segment != actual_segment:
                return False
        return True

    def match(self, path: object) -> PathMatch:
        normalized = normalize_origin_relative_path(path)
        if normalized in self._exact:
            return PathMatch(matched=True, path_pattern=normalized, exact=True)

        segments = _segments(normalized)
        candidates = [
            template
            for template in self._templates.get(len(segments), ())
            if self._template_matches(template, segments)
        ]
        if not candidates:
            return PathMatch(matched=False)

        best_specificity = max(candidate.specificity for candidate in candidates)
        best = [
            candidate
            for candidate in candidates
            if candidate.specificity == best_specificity
        ]
        distinct_patterns = {candidate.pattern for candidate in best}
        if len(distinct_patterns) > 1:
            joined = ", ".join(sorted(distinct_patterns))
            raise AmbiguousPathError(
                f"ambiguous path {normalized!r}; equally specific patterns: {joined}"
            )

        winner = best[0]
        return PathMatch(matched=True, path_pattern=winner.pattern, exact=False)


__all__ = [
    "AmbiguousPathError",
    "PathMatcher",
    "normalize_field_name",
    "normalize_key_set",
    "normalize_method",
    "normalize_origin_relative_path",
]
