from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit


_MAX_SIGNED_INT64 = (1 << 63) - 1
_GOODS_PATH = "/units/shop/"
_PRODUCT_LINK_PATH = "/products/"
_SUPPRESSED_TAGS = frozenset({"script", "style", "template", "noscript", "svg", "iframe"})\n_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})\n_HIDDEN_STYLE_TOKENS = ("display:none", "visibility:hidden", "visibility:collapse")


class ProductProbeRouteError(ValueError):
    """Raised when a research probe URL is outside the frozen C0 surface."""


@dataclass(frozen=True, slots=True)
class ProductProbeRoute:
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
    ) -> ProductProbeRoute:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or parts.port is not None
            or parts.fragment
            or parts.hostname not in approved_hosts
            or parts.path != _GOODS_PATH
            or "%" in parts.path
            or "%" in parts.query
        ):
            raise ProductProbeRouteError("URL is outside the approved P4-C C0 surface")
        try:
            pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise ProductProbeRouteError("query is malformed") from exc
        if len(pairs) != 2 or {key for key, _value in pairs} != {"id", "tab"}:
            raise ProductProbeRouteError("query must contain exactly one id and one tab")
        values = {key: value for key, value in pairs}
        unit_id_text = values["id"]
        if (
            not unit_id_text
            or not unit_id_text.isascii()
            or not unit_id_text.isdigit()
            or unit_id_text.startswith("0")
            or values["tab"] != "goods"
        ):
            raise ProductProbeRouteError("invalid unit id or product surface")
        unit_id = int(unit_id_text)
        if unit_id > _MAX_SIGNED_INT64:
            raise ProductProbeRouteError("unit id exceeds signed 64-bit range")
        return cls(
            unit_id=unit_id,
            surface="shop.goods",
            normalized_path=_GOODS_PATH,
            normalized_query=(("id", unit_id_text), ("tab", "goods")),
        )


@dataclass(frozen=True, slots=True)
class ProductIdentityCandidate:
    tag: str
    attribute: str
    numeric_query_ids: tuple[int, ...]


def _hidden(attrs: list[tuple[str, str | None]]) -> bool:
    lowered = {key.casefold(): value for key, value in attrs}
    if "hidden" in lowered or "inert" in lowered:
        return True
    aria_hidden = lowered.get("aria-hidden")
    if isinstance(aria_hidden, str) and aria_hidden.casefold() == "true":
        return True
    style = lowered.get("style")
    if isinstance(style, str):
        compact = "".join(style.casefold().split())
        return any(token in compact for token in _HIDDEN_STYLE_TOKENS)
    return False


class _IdentityCandidateParser(HTMLParser):
    def __init__(self, *, max_candidates: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_candidates = max_candidates
        self.candidates: list[ProductIdentityCandidate] = []
        self._suppressed_depth = 0
        self._open_suppression: list[tuple[str, bool]] = []\n
    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.casefold()
        suppress_here = (
            self._suppressed_depth > 0
            or normalized_tag in _SUPPRESSED_TAGS
            or _hidden(attrs)
        )
        is_void = normalized_tag in _VOID_TAGS\n        if not is_void:\n            self._open_suppression.append((normalized_tag, suppress_here))\n        if suppress_here:\n            if not is_void:\n                self._suppressed_depth += 1\n            return\n        if len(self.candidates) >= self.max_candidates or normalized_tag != "a":
            return
        href = next(
            (value for key, value in attrs if key.casefold() == "href"),
            None,
        )
        if not isinstance(href, str):
            return
        parts = urlsplit(href)
        if (
            parts.scheme
            or parts.netloc
            or parts.fragment
            or parts.path != _PRODUCT_LINK_PATH
            or "%" in parts.path
            or "%" in parts.query
        ):
            return
        try:
            pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            return
        if len(pairs) != 1 or pairs[0][0] != "id":
            return
        value = pairs[0][1]
        if (
            not value
            or not value.isascii()
            or not value.isdigit()
            or value.startswith("0")
        ):
            return
        numeric_id = int(value)
        if numeric_id > _MAX_SIGNED_INT64:
            return
        self.candidates.append(
            ProductIdentityCandidate(
                tag="a",
                attribute="href",
                numeric_query_ids=(numeric_id,),
            )
        )

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.casefold()\n        if normalized_tag in _VOID_TAGS:\n            self.handle_starttag(tag, attrs)\n            return\n        before = len(self._open_suppression)\n        self.handle_starttag(tag, attrs)\n        if len(self._open_suppression) > before:\n            _opened_tag, suppressed = self._open_suppression.pop()\n            if suppressed:\n                self._suppressed_depth -= 1\n
    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()\n        for index in range(len(self._open_suppression) - 1, -1, -1):\n            if self._open_suppression[index][0] != normalized_tag:\n                continue\n            closing = self._open_suppression[index:]\n            del self._open_suppression[index:]\n            self._suppressed_depth -= sum(1 for _name, suppressed in closing if suppressed)\n            return\n

def inspect_product_identity_candidates(
    html: bytes,
    *,
    max_candidates: int = 64,
) -> tuple[ProductIdentityCandidate, ...]:
    """Inspect raw HTML transiently and return only bounded structural ID candidates."""

    if not isinstance(html, bytes):
        raise TypeError("html must be bytes")
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
        raise TypeError("max_candidates must be an integer")
    if not 1 <= max_candidates <= 512:
        raise ValueError("max_candidates must be between 1 and 512")
    parser = _IdentityCandidateParser(max_candidates=max_candidates)
    parser.feed(html.decode("utf-8", errors="strict"))
    parser.close()
    return tuple(parser.candidates)
