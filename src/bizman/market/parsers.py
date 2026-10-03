"""Deterministic HTML parsers for the frozen read-only market surfaces.

Each parser consumes a decoded first-party response body and emits only frozen
typed observations. Unsupported routes, ambiguous markup, unexpected row
shapes and malformed numbers fail the whole page; nothing is partially
projected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re
from urllib.parse import parse_qsl, urlsplit

from bizman.market.model import (
    MAX_HTML_CHARS,
    MAX_ROWS,
    MarketContractError,
    RetailMarketCityPage,
    RetailMarketGroupRow,
    RetailPriceCityRow,
    RetailPriceGroupPage,
    SURFACE_RETAILMARKET_CITY,
    SURFACE_RETAILPRICES_GROUP,
    SURFACE_VENDORS,
    VendorCatalogPage,
    VendorProductRow,
)


_HOST = "bizmania.ru"
_CITY_PATH = "/analitics/retailmarket/"
_PRICE_PATH = "/analitics/retailprices/"
_VENDORS_PATH = "/analitics/vendors/"
_MAX_SIGNED_INT64 = (1 << 63) - 1
_SUPPRESSED_TAGS = frozenset(
    {"script", "style", "template", "noscript", "svg", "iframe", "select"}
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
_MONEY_RE = re.compile(
    r"^(?P<number>[0-9]+(?: [0-9]+)*(?:\.[0-9]+)?) "
    r"(?P<unit>тыс|млн|млрд) p\.$"
)
_MONEY_MULTIPLIERS = {
    "тыс": Decimal(1000),
    "млн": Decimal(1_000_000),
    "млрд": Decimal(1_000_000_000),
}
_PERCENT_RE = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?%$")
_RAW_NUMBER_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_RETAILMARKET_LINK_RE = re.compile(
    r"^/analitics/retailmarket/\?city=([0-9]+)&retailgroup=([0-9]+)$"
)
_CATEGORY_LINK_RE = re.compile(
    r"^javascript:dialog\("
    r"'/analitics/categorysales/\?city=([0-9]+)&category=([0-9]+)'"
    r"\)$"
)
_CITY_LINK_RE = re.compile(r"^/city/\?id=([0-9]+)$")
_VENDOR_PRODUCT_RE = re.compile(r"^/analitics/vendors/\?product=([0-9]+)$")
_PRICE_LEVEL_PREFIX = "Уровень цен:"


@dataclass(slots=True)
class _Element:
    tag: str
    attrs: tuple[tuple[str, str | None], ...]
    suppressed: bool = False
    ambiguous: bool = False
    closed: bool = False
    text_parts: list[str] = field(default_factory=list)
    children: list["_Element"] = field(default_factory=list)


def _duplicate_attribute_names(attrs: list[tuple[str, str | None]]) -> bool:
    seen: set[str] = set()
    for key, _value in attrs:
        normalized = key.casefold()
        if normalized in seen:
            return True
        seen.add(normalized)
    return False


class _MarketTreeParser(HTMLParser):
    """Small structural tree with active-content suppression and optional tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("", ())
        self._stack: list[_Element] = [self.root]

    def _open(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        self_closing: bool,
    ) -> _Element:
        normalized = tag.casefold()
        parent = self._stack[-1]
        node = _Element(
            tag=normalized,
            attrs=tuple(attrs),
            suppressed=parent.suppressed or normalized in _SUPPRESSED_TAGS,
            ambiguous=parent.ambiguous or _duplicate_attribute_names(attrs),
            closed=self_closing or normalized in _VOID_TAGS,
        )
        parent.children.append(node)
        return node

    def _close_open(self, tags: frozenset[str]) -> None:
        while len(self._stack) > 1 and self._stack[-1].tag in tags:
            self._stack[-1].closed = True
            self._stack.pop()

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        normalized = tag.casefold()
        if normalized in {"td", "th"}:
            self._close_open(frozenset({"td", "th"}))
        elif normalized == "tr":
            self._close_open(frozenset({"td", "th", "tr"}))
        node = self._open(tag, attrs, self_closing=False)
        if not node.closed:
            self._stack.append(node)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._open(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == normalized:
                self._stack[index].closed = True
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self._stack[-1].suppressed:
            return
        if data:
            self._stack[-1].text_parts.append(data)


def _iter_nodes(node: _Element):
    pending = list(reversed(node.children))
    while pending:
        child = pending.pop()
        yield child
        pending.extend(reversed(child.children))


def _attr(node: _Element, name: str) -> str | None:
    values = [
        value
        for key, value in node.attrs
        if key.casefold() == name
    ]
    if len(values) != 1:
        return None
    return values[0]


def _class_tokens(node: _Element) -> tuple[str, ...]:
    value = _attr(node, "class")
    if value is None:
        return ()
    return tuple(token.casefold() for token in value.split())


def _node_text(node: _Element, *, exclude_ids: frozenset[str] = frozenset()) -> str:
    parts: list[str] = []

    def visit(current: _Element) -> None:
        if current.suppressed:
            return
        if current.tag == "div" and _attr(current, "id") in exclude_ids:
            return
        parts.extend(current.text_parts)
        for child in current.children:
            visit(child)

    visit(node)
    # Collapse all Unicode whitespace, including non-breaking variants, so the
    # only remaining separator is an ordinary space.
    return " ".join(" ".join(parts).split())


def _direct_cells(row: _Element) -> list[_Element]:
    return [
        child
        for child in row.children
        if child.tag == "td" and not child.suppressed
    ]


def _table_rows(table: _Element) -> list[_Element]:
    rows: list[_Element] = []

    def visit(node: _Element) -> None:
        for child in node.children:
            if child.suppressed or child.tag == "table":
                continue
            if child.tag == "tr":
                rows.append(child)
            visit(child)

    visit(table)
    return rows


def _summary_row(row: _Element, cells: list[_Element]) -> bool:
    first = cells[0]
    if first.tag != "td":
        return False
    colspan = _attr(first, "colspan")
    if colspan is None or not colspan.isascii() or not colspan.isdigit():
        return False
    if int(colspan) < 2:
        return False
    return not any(
        node.tag == "a" and _attr(node, "href") is not None
        for node in _iter_nodes(row)
    )


def _single_anchor(node: _Element, *, name: str) -> _Element:
    anchors = [
        child
        for child in _iter_nodes(node)
        if child.tag == "a" and not child.suppressed
    ]
    if len(anchors) != 1:
        raise MarketContractError(f"{name} cell must expose exactly one anchor")
    anchor = anchors[0]
    if anchor.ambiguous or not anchor.closed:
        raise MarketContractError(f"{name} anchor is ambiguous or incomplete")
    return anchor


def _anchor_href(node: _Element, *, name: str) -> str:
    href = _attr(node, "href")
    if not isinstance(href, str) or not href:
        raise MarketContractError(f"{name} anchor is missing its href")
    return href


def _single_titled_image(node: _Element, *, name: str) -> _Element:
    images = [
        child
        for child in _iter_nodes(node)
        if child.tag == "img" and not child.suppressed
    ]
    if len(images) != 1:
        raise MarketContractError(f"{name} cell must expose exactly one image")
    image = images[0]
    if image.ambiguous:
        raise MarketContractError(f"{name} image is ambiguous")
    title = _attr(image, "title")
    if not isinstance(title, str) or not title.strip():
        raise MarketContractError(f"{name} image is missing its title")
    return image


def _canonical_id(value: str, *, name: str) -> int:
    if not value.isascii() or not value.isdigit() or value.startswith("0"):
        raise MarketContractError(f"{name} must be a canonical positive identifier")
    if len(value) > 19 or int(value) > _MAX_SIGNED_INT64:
        raise MarketContractError(f"{name} exceeds the signed 64-bit range")
    return int(value)


def _parse_money(value: str, *, name: str) -> int:
    match = _MONEY_RE.fullmatch(value)
    if match is None:
        raise MarketContractError(f"{name} is not a canonical money amount")
    digits = match.group("number").replace(" ", "")
    try:
        amount = Decimal(digits) * _MONEY_MULTIPLIERS[match.group("unit")]
    except InvalidOperation as exc:
        raise MarketContractError(f"{name} is not a canonical money amount") from exc
    if amount != amount.to_integral_value():
        raise MarketContractError(f"{name} is not an integral ruble amount")
    return int(amount)


def _parse_percent(value: str, *, name: str) -> float:
    if _PERCENT_RE.fullmatch(value) is None:
        raise MarketContractError(f"{name} is not a canonical percentage")
    return float(value[:-1])


def _parse_raw_number(value: str, *, name: str) -> float:
    if _RAW_NUMBER_RE.fullmatch(value) is None:
        raise MarketContractError(f"{name} is not a canonical number")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise MarketContractError(f"{name} is not a canonical number") from exc
    return float(number)


def _parse_price_level(cell: _Element) -> float:
    bars = [
        child
        for child in _iter_nodes(cell)
        if child.tag == "span" and not child.suppressed and "color-bar" in _class_tokens(child)
    ]
    if len(bars) != 1:
        raise MarketContractError("retail market price level bar is missing or ambiguous")
    bar = bars[0]
    if bar.ambiguous or not bar.closed:
        raise MarketContractError("retail market price level bar is ambiguous or incomplete")
    title = _attr(bar, "title")
    if not isinstance(title, str):
        raise MarketContractError("retail market price level bar is missing its title")
    collapsed = " ".join(title.split())
    if not collapsed.startswith(_PRICE_LEVEL_PREFIX):
        raise MarketContractError("retail market price level bar title is unexpected")
    remainder = collapsed[len(_PRICE_LEVEL_PREFIX) :].strip()
    return _parse_percent(remainder, name="price_level_percent")


def _parse_query(query: str, *, name: str) -> list[tuple[str, str]]:
    for segment in query.split("&"):
        if "%" in segment.split("=", 1)[0]:
            raise MarketContractError(f"{name} query contains a percent-encoded key")
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise MarketContractError(f"{name} query is malformed") from exc
    keys = [key for key, _value in pairs]
    if len(set(keys)) != len(keys):
        raise MarketContractError(f"{name} query repeats a key")
    return pairs


def _parse_target(url: object) -> tuple[str, int | None]:
    if not isinstance(url, str):
        raise MarketContractError("market page url must be a string")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise MarketContractError("market page url is malformed") from exc
    if parts.scheme != "https" or parts.hostname != _HOST:
        raise MarketContractError("market page url must target https://bizmania.ru")
    if parts.username is not None or parts.password is not None:
        raise MarketContractError("market page url must not carry credentials")
    if port is not None:
        raise MarketContractError("market page url must not carry a port")
    if parts.fragment:
        raise MarketContractError("market page url must not carry a fragment")
    if parts.path == _CITY_PATH:
        pairs = _parse_query(parts.query, name="retailmarket")
        if len(pairs) != 1 or pairs[0][0] != "city":
            raise MarketContractError("retailmarket url must carry exactly city")
        city_id = _canonical_id(pairs[0][1], name="city")
        return SURFACE_RETAILMARKET_CITY, city_id
    if parts.path == _PRICE_PATH:
        pairs = _parse_query(parts.query, name="retailprices")
        if len(pairs) != 1 or pairs[0][0] != "retailgroup":
            raise MarketContractError(
                "retailprices url must carry exactly retailgroup"
            )
        retail_group_id = _canonical_id(pairs[0][1], name="retailgroup")
        return SURFACE_RETAILPRICES_GROUP, retail_group_id
    if parts.path == _VENDORS_PATH:
        pairs = _parse_query(parts.query, name="vendors")
        if pairs:
            raise MarketContractError("vendors url must not carry a query")
        return SURFACE_VENDORS, None
    raise MarketContractError("market page url is outside the frozen surfaces")


def _retailmarket_header_shape(cells: list[_Element]) -> bool:
    if len(cells) != 7:
        return False
    if (
        cells[0].tag != "td"
        or _attr(cells[0], "class") != "tblh1"
        or _attr(cells[0], "colspan") != "2"
    ):
        return False
    if (
        cells[1].tag != "td"
        or _attr(cells[1], "class") != "tblh"
        or _attr(cells[1], "colspan") != "2"
    ):
        return False
    return all(
        cell.tag == "td" and _attr(cell, "class") == "tblh" for cell in cells[2:]
    )


def _retailmarket_table(root: _Element) -> _Element:
    candidates = []
    for table in _iter_nodes(root):
        if table.tag != "table" or table.suppressed:
            continue
        if "datatable" not in _class_tokens(table):
            continue
        rows = _table_rows(table)
        if not rows:
            continue
        if _retailmarket_header_shape(_direct_cells(rows[0])):
            candidates.append(table)
    if not candidates:
        raise MarketContractError("retail market table is missing")
    if len(candidates) != 1:
        raise MarketContractError("retail market table is ambiguous")
    table = candidates[0]
    if table.ambiguous or not table.closed:
        raise MarketContractError("retail market table is ambiguous or incomplete")
    return table


def _retailmarket_header(rows: list[_Element]) -> None:
    if not _retailmarket_header_shape(_direct_cells(rows[0])):
        raise MarketContractError("retail market header is unexpected")


def _retailmarket_row(
    cells: list[_Element],
    *,
    city_id: int,
) -> RetailMarketGroupRow:
    image_link = _single_anchor(cells[0], name="image")
    name_link = _single_anchor(cells[1], name="name")
    image_href = _anchor_href(image_link, name="image")
    name_href = _anchor_href(name_link, name="name")
    if image_href != name_href:
        raise MarketContractError("retail market group links disagree")
    match = _RETAILMARKET_LINK_RE.fullmatch(image_href)
    if match is None:
        raise MarketContractError("retail market group link is malformed")
    if _canonical_id(match.group(1), name="city") != city_id:
        raise MarketContractError("retail market group link city differs")
    retail_group_id = _canonical_id(match.group(2), name="retailgroup")
    group_name = _node_text(name_link)
    if not group_name:
        raise MarketContractError("retail market group name is empty")
    dialog_link = _single_anchor(cells[3], name="category")
    dialog_href = _anchor_href(dialog_link, name="category")
    dialog_match = _CATEGORY_LINK_RE.fullmatch(dialog_href)
    if dialog_match is None:
        raise MarketContractError("retail market category link is malformed")
    if _canonical_id(dialog_match.group(1), name="city") != city_id:
        raise MarketContractError("retail market category link city differs")
    if _canonical_id(dialog_match.group(2), name="category") != retail_group_id:
        raise MarketContractError("retail market category link group differs")
    delta_percent = _parse_percent(_node_text(cells[4]), name="delta_percent")
    share_percent = _parse_percent(_node_text(cells[5]), name="share_percent")
    avg_markup_percent = _parse_percent(
        _node_text(cells[6]),
        name="avg_markup_percent",
    )
    _single_titled_image(cells[7], name="competition")
    price_level_percent = _parse_price_level(cells[8])
    return RetailMarketGroupRow(
        retail_group_id=retail_group_id,
        group_name=group_name,
        sales_volume_rub=_parse_money(_node_text(cells[2]), name="sales_volume_rub"),
        delta_percent=delta_percent,
        share_percent=share_percent,
        avg_markup_percent=avg_markup_percent,
        price_level_percent=price_level_percent,
    )


def _parse_retailmarket_city(root: _Element, city_id: int) -> RetailMarketCityPage:
    table = _retailmarket_table(root)
    rows = _table_rows(table)
    if not rows:
        raise MarketContractError("retail market table exposes no rows")
    _retailmarket_header(rows)
    parsed: list[RetailMarketGroupRow] = []
    seen: set[int] = set()
    for row in rows[1:]:
        cells = _direct_cells(row)
        if not cells:
            raise MarketContractError("retail market row exposes no cells")
        if _summary_row(row, cells):
            continue
        if len(cells) != 9:
            raise MarketContractError("retail market row must expose nine cells")
        if len(parsed) >= MAX_ROWS:
            raise MarketContractError(
                f"retail market page exceeds the {MAX_ROWS} row limit"
            )
        parsed_row = _retailmarket_row(cells, city_id=city_id)
        if parsed_row.retail_group_id in seen:
            raise MarketContractError(
                "retail market page repeats a retail group identity"
            )
        seen.add(parsed_row.retail_group_id)
        parsed.append(parsed_row)
    if not parsed:
        raise MarketContractError("retail market table exposes no group rows")
    return RetailMarketCityPage(city_id=city_id, rows=tuple(parsed))


def _retailprices_table(root: _Element) -> _Element:
    candidates = [
        table
        for table in _iter_nodes(root)
        if table.tag == "table"
        and not table.suppressed
        and "datatable" in _class_tokens(table)
    ]
    if not candidates:
        raise MarketContractError("retail price table is missing")
    if len(candidates) != 1:
        raise MarketContractError("retail price table is ambiguous")
    table = candidates[0]
    if table.ambiguous or not table.closed:
        raise MarketContractError("retail price table is ambiguous or incomplete")
    return table


def _retailprice_identity(
    cell: _Element,
    *,
    index: int,
) -> tuple[int, str, float, float]:
    anchor = _single_anchor(cell, name="city")
    href = _anchor_href(anchor, name="city")
    match = _CITY_LINK_RE.fullmatch(href)
    if match is None:
        raise MarketContractError("retail price city link is malformed")
    city_id = _canonical_id(match.group(1), name="city")
    price_id = f"basketPrice[{index}]"
    quality_id = f"basketQuality[{index}]"
    price_cells = [
        node
        for node in _iter_nodes(cell)
        if node.tag == "div" and _attr(node, "id") == price_id
    ]
    quality_cells = [
        node
        for node in _iter_nodes(cell)
        if node.tag == "div" and _attr(node, "id") == quality_id
    ]
    if len(price_cells) != 1 or len(quality_cells) != 1:
        raise MarketContractError(
            "retail price city row must expose one basket price and quality value"
        )
    normalized_price = _parse_raw_number(
        _node_text(price_cells[0]),
        name="normalized_price",
    )
    avg_quality = _parse_raw_number(
        _node_text(quality_cells[0]),
        name="avg_quality",
    )
    city_name = _node_text(anchor, exclude_ids=frozenset({price_id, quality_id}))
    if not city_name:
        raise MarketContractError("retail price city name is empty")
    return city_id, city_name, normalized_price, avg_quality


def _parse_retailprices_group(
    root: _Element,
    retail_group_id: int,
) -> RetailPriceGroupPage:
    table = _retailprices_table(root)
    rows = _table_rows(table)
    if not rows:
        raise MarketContractError("retail price table exposes no rows")
    header_cells = _direct_cells(rows[0])
    if (
        len(header_cells) != 4
        or header_cells[0].tag != "td"
        or _attr(header_cells[0], "class") != "tblh1"
        or _attr(header_cells[0], "colspan") != "2"
    ):
        raise MarketContractError("retail price header is unexpected")
    parsed: list[RetailPriceCityRow] = []
    seen: set[int] = set()
    index = 0
    for row in rows[1:]:
        cells = _direct_cells(row)
        if not cells:
            raise MarketContractError("retail price row exposes no cells")
        if _summary_row(row, cells):
            continue
        if len(cells) != 5:
            raise MarketContractError("retail price row must expose five cells")
        if len(parsed) >= MAX_ROWS:
            raise MarketContractError(
                f"retail price page exceeds the {MAX_ROWS} row limit"
            )
        city_id, city_name, normalized_price, avg_quality = _retailprice_identity(
            cells[1],
            index=index,
        )
        share_text = _node_text(cells[2])
        your_share_percent = (
            None
            if not share_text
            else _parse_percent(share_text, name="your_share_percent")
        )
        if not _node_text(cells[3]) or not _node_text(cells[4]):
            raise MarketContractError(
                "retail price formatted price and quality must be present"
            )
        parsed_row = RetailPriceCityRow(
            city_id=city_id,
            city_name=city_name,
            your_share_percent=your_share_percent,
            normalized_price=normalized_price,
            avg_quality=avg_quality,
        )
        if parsed_row.city_id in seen:
            raise MarketContractError("retail price page repeats a city identity")
        seen.add(parsed_row.city_id)
        parsed.append(parsed_row)
        index += 1
    if not parsed:
        raise MarketContractError("retail price table exposes no city rows")
    return RetailPriceGroupPage(
        retail_group_id=retail_group_id,
        rows=tuple(parsed),
    )


def _vendor_product_links(row: _Element) -> list[_Element]:
    links: list[_Element] = []
    for node in _iter_nodes(row):
        if node.tag != "a" or node.suppressed:
            continue
        hrefs = [value for key, value in node.attrs if key.casefold() == "href"]
        if not hrefs:
            continue
        if node.ambiguous or not node.closed or len(hrefs) != 1:
            raise MarketContractError("vendor anchor is ambiguous or incomplete")
        href = hrefs[0]
        if not isinstance(href, str) or not href.startswith(_VENDORS_PATH):
            continue
        if _VENDOR_PRODUCT_RE.fullmatch(href) is None:
            raise MarketContractError("vendor product link is malformed")
        links.append(node)
    return links


def _vendor_product_id(node: _Element) -> int:
    href = _anchor_href(node, name="vendor product")
    match = _VENDOR_PRODUCT_RE.fullmatch(href)
    if match is None:
        raise MarketContractError("vendor product link is malformed")
    return _canonical_id(match.group(1), name="product")


def _vendors_table(root: _Element) -> _Element:
    candidates = []
    for table in _iter_nodes(root):
        if table.tag != "table" or table.suppressed:
            continue
        if any(
            _VENDOR_PRODUCT_RE.fullmatch(_attr(node, "href") or "") is not None
            for node in _iter_nodes(table)
            if node.tag == "a" and not node.suppressed
        ):
            candidates.append(table)
    if not candidates:
        raise MarketContractError("vendor table is missing")
    if len(candidates) != 1:
        raise MarketContractError("vendor table is ambiguous")
    table = candidates[0]
    if table.ambiguous or not table.closed:
        raise MarketContractError("vendor table is ambiguous or incomplete")
    return table


def _parse_vendors(root: _Element) -> VendorCatalogPage:
    table = _vendors_table(root)
    parsed: list[VendorProductRow] = []
    seen: set[int] = set()
    current_group: str | None = None
    for row in _table_rows(table):
        headings = [
            node
            for node in _iter_nodes(row)
            if node.tag == "h3" and not node.suppressed
        ]
        if headings:
            if len(headings) != 1:
                raise MarketContractError("vendor group heading is ambiguous")
            group_name = _node_text(headings[0])
            if not group_name:
                raise MarketContractError("vendor group name is empty")
            if _vendor_product_links(row):
                raise MarketContractError(
                    "vendor group heading row must not expose a product link"
                )
            current_group = group_name
            continue
        for link in _vendor_product_links(row):
            if current_group is None:
                raise MarketContractError(
                    "vendor product link precedes its group heading"
                )
            if len(parsed) >= MAX_ROWS:
                raise MarketContractError(
                    f"vendor page exceeds the {MAX_ROWS} row limit"
                )
            product_numeric_id = _vendor_product_id(link)
            if product_numeric_id in seen:
                raise MarketContractError("vendor page repeats a product identity")
            seen.add(product_numeric_id)
            parsed.append(
                VendorProductRow(
                    product_numeric_id=product_numeric_id,
                    group_name=current_group,
                )
            )
    if not parsed:
        raise MarketContractError("vendor table exposes no product rows")
    return VendorCatalogPage(rows=tuple(parsed))


def parse_market_page(
    html: str,
    *,
    url: str,
) -> RetailMarketCityPage | RetailPriceGroupPage | VendorCatalogPage:
    """Parse one frozen market surface body selected by its exact request url."""

    if not isinstance(html, str):
        raise MarketContractError("market page html must be a string")
    if len(html) > MAX_HTML_CHARS:
        raise MarketContractError(
            f"market page exceeds the {MAX_HTML_CHARS} character limit"
        )
    surface, request_id = _parse_target(url)
    parser = _MarketTreeParser()
    parser.feed(html)
    parser.close()
    if surface == SURFACE_RETAILMARKET_CITY:
        assert request_id is not None
        return _parse_retailmarket_city(parser.root, request_id)
    if surface == SURFACE_RETAILPRICES_GROUP:
        assert request_id is not None
        return _parse_retailprices_group(parser.root, request_id)
    return _parse_vendors(parser.root)


__all__ = [
    "parse_market_page",
]
