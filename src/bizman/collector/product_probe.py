from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit


_MAX_SIGNED_INT64 = (1 << 63) - 1
_GOODS_PATH = "/units/shop/"
_PRODUCT_LINK_PATH = "/products/"


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
        ):
            raise ProductProbeRouteError("URL is outside the approved P4-C C0 surface")

        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
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


class _IdentityCandidateParser(HTMLParser):
    def __init__(self, *, max_candidates: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_candidates = max_candidates
        self.candidates: list[ProductIdentityCandidate] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if len(self.candidates) >= self.max_candidates or tag.casefold() != "a":
            return
        href = next(
            (value for key, value in attrs if key.casefold() == "href"),
            None,
        )
        if not isinstance(href, str):
            return
        parts = urlsplit(href)
        if parts.scheme or parts.netloc or parts.fragment or parts.path != _PRODUCT_LINK_PATH:
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
