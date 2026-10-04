from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import parse_qsl, urlsplit


_MAX_SIGNED_INT64 = (1 << 63) - 1
_MAX_LINK_CANDIDATES = 256
_MAX_INPUT_CANDIDATES = 512
_MAX_QUERY_FIELDS = 16
_SUPPLY_PATH = "/units/shop/"
_SUPPRESSED_TAGS = frozenset(
    {"script", "style", "template", "noscript", "svg", "iframe"}
)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_HIDDEN_STYLE_TOKENS = (
    "display:none",
    "visibility:hidden",
    "visibility:collapse",
)
_QUERY_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$", re.ASCII)
_INPUT_NAME_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_.-]{0,63})(?:\[([0-9]+)\])?$",
    re.ASCII,
)
_NUMERIC_QUERY_KEYS = frozenset({"id", "product", "vendor", "unit", "supplier"})


class SupplyProbeError(ValueError):
    """Base class for research-only supply evidence probe failures."""


class SupplyProbeRouteError(SupplyProbeError):
    """The requested URL is outside the exact research supply surface."""


class SupplyProbeStructureError(SupplyProbeError):
    """Structural metadata is malformed or ambiguous."""


class SupplyProbeCandidateOverflowError(SupplyProbeError):
    """Bounded inspection cannot prove that every candidate was observed."""


def _contains_forbidden_url_character(value: str) -> bool:
    return any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)


def _has_duplicate_attribute_names(attrs: list[tuple[str, str | None]]) -> bool:
    seen: set[str] = set()
    for key, _value in attrs:
        normalized = key.casefold()
        if normalized in seen:
            return True
        seen.add(normalized)
    return False


def _hidden(attrs: list[tuple[str, str | None]]) -> bool:
    lowered = {key.casefold(): value for key, value in attrs}
    if "hidden" in lowered or "inert" in lowered:
        return True
    aria_hidden = lowered.get("aria-hidden")
    if isinstance(aria_hidden, str) and aria_hidden.strip().casefold() == "true":
        return True
    style = lowered.get("style")
    if isinstance(style, str):
        compact = "".join(style.casefold().split())
        return any(token in compact for token in _HIDDEN_STYLE_TOKENS)
    return False


def _positive_decimal(value: str) -> int | None:
    if (
        not value
        or not value.isascii()
        or not value.isdigit()
        or value.startswith("0")
        or len(value) > 19
    ):
        return None
    number = int(value)
    if number > _MAX_SIGNED_INT64:
        return None
    return number


@dataclass(frozen=True, slots=True)
class SupplyProbeRoute:
    unit_id: int
    surface: str
    normalized_path: str
    normalized_query: tuple[tuple[str, str], ...]

    @classmethod
    def parse(
        cls,
        url: str,
        *,
        approved_hosts: tuple[str, ...],
    ) -> "SupplyProbeRoute":
        if not isinstance(url, str) or not url or _contains_forbidden_url_character(url):
            raise SupplyProbeRouteError("URL is outside the approved P4-D supply surface")
        try:
            parts = urlsplit(url)
            hostname = parts.hostname
            port = parts.port
        except ValueError as exc:
            raise SupplyProbeRouteError(
                "URL is outside the approved P4-D supply surface"
            ) from exc
        if (
            parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or port is not None
            or parts.fragment
            or hostname not in approved_hosts
            or parts.path != _SUPPLY_PATH
            or "%" in parts.path
            or "%" in parts.query
        ):
            raise SupplyProbeRouteError("URL is outside the approved P4-D supply surface")
        try:
            pairs = parse_qsl(
                parts.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=_MAX_QUERY_FIELDS,
            )
        except ValueError as exc:
            raise SupplyProbeRouteError("supply query is malformed") from exc
        if len(pairs) != 2 or {key for key, _value in pairs} != {"id", "tab"}:
            raise SupplyProbeRouteError(
                "supply query must contain exactly one id and one tab"
            )
        values = dict(pairs)
        unit_id = _positive_decimal(values["id"])
        if unit_id is None or values["tab"] != "supply":
            raise SupplyProbeRouteError("invalid unit id or supply surface")
        return cls(
            unit_id=unit_id,
            surface="shop.supply",
            normalized_path=_SUPPLY_PATH,
            normalized_query=(("id", values["id"]), ("tab", "supply")),
        )


@dataclass(frozen=True, slots=True)
class SupplyLinkCandidate:
    path: str
    query_keys: tuple[str, ...]
    numeric_query_fields: tuple[tuple[str, int], ...]
    in_row: bool
    in_form: bool
    row_slot: int | None
    form_slot: int | None


@dataclass(frozen=True, slots=True)
class SupplyInputCandidate:
    name: str
    index: int | None
    in_row: bool
    in_form: bool
    row_slot: int | None
    form_slot: int | None


@dataclass(frozen=True, slots=True)
class SupplyStructureReport:
    links: tuple[SupplyLinkCandidate, ...]
    inputs: tuple[SupplyInputCandidate, ...]


@dataclass(slots=True)
class _OpenNode:
    tag: str
    suppressed: bool
    row_slot: int | None
    form_slot: int | None


class _SupplyStructureParser(HTMLParser):
    def __init__(
        self,
        *,
        max_links: int,
        max_inputs: int,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.max_links = max_links
        self.max_inputs = max_inputs
        self.links: list[SupplyLinkCandidate] = []
        self.inputs: list[SupplyInputCandidate] = []
        self._stack: list[_OpenNode] = []
        self._suppressed_depth = 0
        self._next_row_slot = 0
        self._next_form_slot = 0

    def _pop_from(self, index: int) -> None:
        closing = self._stack[index:]
        del self._stack[index:]
        self._suppressed_depth -= sum(
            1 for node in closing if node.suppressed
        )

    def _close_optional_before_start(self, tag: str) -> None:
        targets: frozenset[str]
        if tag in {"td", "th"}:
            targets = frozenset({"td", "th"})
        elif tag == "tr":
            targets = frozenset({"tr"})
        else:
            return
        for index in range(len(self._stack) - 1, -1, -1):
            node = self._stack[index]
            if node.tag in targets:
                self._pop_from(index)
                return
            if node.tag == "form":
                raise SupplyProbeStructureError(
                    "optional table closure crosses an open form"
                )
            if node.tag == "table":
                return

    def _context(self) -> tuple[int | None, int | None]:
        row_slot: int | None = None
        form_slot: int | None = None
        for node in reversed(self._stack):
            if node.suppressed:
                continue
            if row_slot is None and node.tag == "tr":
                row_slot = node.row_slot
            if form_slot is None and node.tag == "form":
                form_slot = node.form_slot
            if row_slot is not None and form_slot is not None:
                break
        return row_slot, form_slot

    def assert_structurally_complete(self) -> None:
        if any(
            node.tag in {"form", "table", "tr", "td", "th"}
            for node in self._stack
        ):
            raise SupplyProbeStructureError(
                "supply structural container is incomplete"
            )

    def _append_link(self, attrs: list[tuple[str, str | None]]) -> None:
        hrefs = [
            value
            for key, value in attrs
            if key.casefold() == "href" and isinstance(value, str)
        ]
        if len(hrefs) != 1:
            if hrefs:
                raise SupplyProbeStructureError("supply link href is ambiguous")
            return
        href = hrefs[0]
        if _contains_forbidden_url_character(href) or len(href) > 2048:
            raise SupplyProbeStructureError("supply link is malformed")
        try:
            parts = urlsplit(href)
        except ValueError as exc:
            raise SupplyProbeStructureError("supply link is malformed") from exc
        if parts.scheme or parts.netloc or parts.fragment:
            return
        if not parts.path.startswith("/"):
            if parts.query:
                raise SupplyProbeStructureError(
                    "relative supply link cannot be resolved safely"
                )
            return
        if (
            "%" in parts.path
            or "%" in parts.query
            or "\\" in parts.path
            or "//" in parts.path
            or len(parts.path) > 256
            or any(segment in {".", ".."} for segment in parts.path.split("/"))
        ):
            raise SupplyProbeStructureError(
                "supply link path/query encoding is ambiguous"
            )
        try:
            pairs = parse_qsl(
                parts.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=_MAX_QUERY_FIELDS,
            )
        except ValueError as exc:
            raise SupplyProbeStructureError(
                "supply link query is malformed"
            ) from exc
        keys = [key for key, _value in pairs]
        if (
            len(keys) != len(set(keys))
            or not all(_QUERY_KEY_RE.fullmatch(key) is not None for key in keys)
        ):
            raise SupplyProbeStructureError("supply link query is ambiguous")
        if not keys:
            return

        numeric: list[tuple[str, int]] = []
        for key, value in pairs:
            if key not in _NUMERIC_QUERY_KEYS:
                continue
            parsed = _positive_decimal(value)
            if parsed is not None:
                numeric.append((key, parsed))

        if len(self.links) >= self.max_links:
            raise SupplyProbeCandidateOverflowError(
                "supply link candidates exceed the bounded research limit"
            )
        row_slot, form_slot = self._context()
        self.links.append(
            SupplyLinkCandidate(
                path=parts.path,
                query_keys=tuple(sorted(keys)),
                numeric_query_fields=tuple(sorted(numeric)),
                in_row=row_slot is not None,
                in_form=form_slot is not None,
                row_slot=row_slot,
                form_slot=form_slot,
            )
        )

    def _append_input(self, attrs: list[tuple[str, str | None]]) -> None:
        names = [
            value
            for key, value in attrs
            if key.casefold() == "name" and isinstance(value, str)
        ]
        if len(names) != 1:
            if names:
                raise SupplyProbeStructureError("supply input name is ambiguous")
            return
        match = _INPUT_NAME_RE.fullmatch(names[0])
        if match is None:
            raise SupplyProbeStructureError(
                "supply input name shape is unsupported"
            )
        index_text = match.group(2)
        index: int | None = None
        if index_text is not None:
            if (
                len(index_text) > 19
                or (len(index_text) > 1 and index_text.startswith("0"))
            ):
                raise SupplyProbeStructureError(
                    "supply input index is non-canonical"
                )
            index = int(index_text)
            if index > _MAX_SIGNED_INT64:
                raise SupplyProbeStructureError("supply input index is out of range")

        if len(self.inputs) >= self.max_inputs:
            raise SupplyProbeCandidateOverflowError(
                "supply input candidates exceed the bounded research limit"
            )
        row_slot, form_slot = self._context()
        self.inputs.append(
            SupplyInputCandidate(
                name=match.group(1),
                index=index,
                in_row=row_slot is not None,
                in_form=form_slot is not None,
                row_slot=row_slot,
                form_slot=form_slot,
            )
        )

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized = tag.casefold()
        self._close_optional_before_start(normalized)
        duplicate = _has_duplicate_attribute_names(attrs)
        if duplicate:
            raise SupplyProbeStructureError("duplicate HTML attributes are ambiguous")

        suppressed = (
            self._suppressed_depth > 0
            or normalized in _SUPPRESSED_TAGS
            or _hidden(attrs)
        )
        if (
            not suppressed
            and normalized == "form"
            and any(
                node.tag == "form" and not node.suppressed
                for node in self._stack
            )
        ):
            raise SupplyProbeStructureError(
                "nested supply forms are ambiguous"
            )

        row_slot: int | None = None
        form_slot: int | None = None
        if not suppressed and normalized == "tr":
            row_slot = self._next_row_slot
            self._next_row_slot += 1
        if not suppressed and normalized == "form":
            form_slot = self._next_form_slot
            self._next_form_slot += 1

        is_void = normalized in _VOID_TAGS
        if not is_void:
            self._stack.append(
                _OpenNode(
                    normalized,
                    suppressed,
                    row_slot,
                    form_slot,
                )
            )
            if suppressed:
                self._suppressed_depth += 1

        if suppressed:
            return
        if normalized == "a":
            self._append_link(attrs)
        elif normalized == "input":
            self._append_input(attrs)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized = tag.casefold()
        if normalized in _VOID_TAGS:
            self.handle_starttag(tag, attrs)
            return
        self.handle_starttag(tag, attrs)
        if self._stack and self._stack[-1].tag == normalized:
            node = self._stack.pop()
            if node.suppressed:
                self._suppressed_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].tag != normalized:
                continue
            self._pop_from(index)
            return


def inspect_supply_structure(
    html: bytes,
    *,
    max_links: int = _MAX_LINK_CANDIDATES,
    max_inputs: int = _MAX_INPUT_CANDIDATES,
) -> SupplyStructureReport:
    """Inspect a local supply page without retaining text or input values."""

    if not isinstance(html, bytes):
        raise TypeError("html must be bytes")
    for value, name, ceiling in (
        (max_links, "max_links", _MAX_LINK_CANDIDATES),
        (max_inputs, "max_inputs", _MAX_INPUT_CANDIDATES),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if not 1 <= value <= ceiling:
            raise ValueError(f"{name} must be between 1 and {ceiling}")

    decoded = html.decode("utf-8", errors="strict")
    parser = _SupplyStructureParser(
        max_links=max_links,
        max_inputs=max_inputs,
    )
    parser.feed(decoded)
    parser.close()
    parser.assert_structurally_complete()
    return SupplyStructureReport(
        links=tuple(parser.links),
        inputs=tuple(parser.inputs),
    )


__all__ = [
    "SupplyInputCandidate",
    "SupplyLinkCandidate",
    "SupplyProbeCandidateOverflowError",
    "SupplyProbeError",
    "SupplyProbeRoute",
    "SupplyProbeRouteError",
    "SupplyProbeStructureError",
    "SupplyStructureReport",
    "inspect_supply_structure",
]
