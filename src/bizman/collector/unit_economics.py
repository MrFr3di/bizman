"""Transient goods-page HTML to typed unit-economics artifact extraction.

The extractor consumes a response body in memory and emits only canonical
typed-artifact bytes. It never persists raw HTML, labels, arbitrary attributes
or unrelated hidden values. An unparseable data row fails the whole page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import math
import re
from urllib.parse import parse_qsl, urlsplit

from bizman.foundation.unit_economics import (
    MAX_ROWS,
    UnitEconomicsContractError,
    UnitEconomicsPage,
    UnitEconomicsRow,
    unit_economics_payload,
)


_GOODS_PATH = "/units/shop/"
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
_HEADER_CLASS_TOKEN = "tblh"
_DATA_ROW_ID_RE = re.compile(r"^pr([0-9]+)$", re.ASCII)
_INPUT_NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[([0-9]+)\]$")
_INTEGER_TOKEN_RE = re.compile(r"[0-9][0-9 ]*")
_MONETARY_TOKEN_RE = re.compile(r"[0-9][0-9 ]*[0-9]|[0-9]")
_FLOAT_TOKEN_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")


class UnitEconomicsExtractionError(ValueError):
    """A captured goods page cannot be turned into a typed artifact."""


class UnitEconomicsStructureError(UnitEconomicsExtractionError):
    """The goods page structure no longer matches the frozen contract."""


class UnitEconomicsOverflowError(UnitEconomicsExtractionError):
    """The goods page exposes more rows than the contract allows."""


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


class _GoodsTreeParser(HTMLParser):
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


def _node_text(node: _Element) -> str:
    parts: list[str] = []

    def visit(current: _Element) -> None:
        if current.suppressed:
            return
        parts.extend(current.text_parts)
        for child in current.children:
            visit(child)

    visit(node)
    # Collapse all Unicode whitespace, including non-breaking variants, so the
    # only remaining group separator is an ordinary space.
    return " ".join(" ".join(parts).split())


def _class_tokens(node: _Element) -> tuple[str, ...]:
    value = _attr(node, "class")
    if value is None:
        return ()
    return tuple(token.casefold() for token in value.split())


def _goods_table(root: _Element) -> _Element:
    candidates = [
        node
        for node in _iter_nodes(root)
        if node.tag == "table" and not node.suppressed and _attr(node, "id") == "goods"
    ]
    if not candidates:
        raise UnitEconomicsStructureError("goods table is missing")
    if len(candidates) != 1:
        raise UnitEconomicsStructureError("goods table is ambiguous")
    table = candidates[0]
    if table.ambiguous or not table.closed:
        raise UnitEconomicsStructureError("goods table is ambiguous or incomplete")
    return table


def _single_link(cell: _Element, *, label: str) -> _Element:
    links = [
        node
        for node in _iter_nodes(cell)
        if node.tag == "a" and not node.suppressed
    ]
    if len(links) != 1:
        raise UnitEconomicsStructureError(
            f"goods {label} cell must expose exactly one link"
        )
    link = links[0]
    if link.ambiguous or not link.closed:
        raise UnitEconomicsStructureError(
            f"goods {label} link is ambiguous or incomplete"
        )
    return link


def _product_numeric_id(href: str, *, unit_id: str) -> int:
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in href):
        raise UnitEconomicsStructureError("goods product link is malformed")
    if "%" in href or "#" in href:
        raise UnitEconomicsStructureError("goods product link is not canonical")
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or parts.path != _GOODS_PATH:
        raise UnitEconomicsStructureError("goods product link is outside the surface")
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise UnitEconomicsStructureError(
            "goods product link query is malformed"
        ) from exc
    if len(pairs) != 3 or {key for key, _value in pairs} != {"id", "tab", "product"}:
        raise UnitEconomicsStructureError("goods product link query is unexpected")
    values = dict(pairs)
    if values["id"] != unit_id or values["tab"] != "goods":
        raise UnitEconomicsStructureError("goods product link context differs")
    product = values["product"]
    if not product.isascii() or not product.isdigit() or product.startswith("0"):
        raise UnitEconomicsStructureError("goods product id is invalid")
    if len(product) > 19 or int(product) > _MAX_SIGNED_INT64:
        raise UnitEconomicsStructureError("goods product id exceeds the signed 64-bit range")
    return int(product)


def _leading_integer(text: str) -> int:
    match = _INTEGER_TOKEN_RE.match(text)
    if match is None:
        raise UnitEconomicsStructureError("goods row integer field is missing")
    digits = match.group(0).replace(" ", "")
    if not digits or not digits.isascii() or not digits.isdigit():
        raise UnitEconomicsStructureError("goods row integer field is invalid")
    value = int(digits)
    if value > _MAX_SIGNED_INT64:
        raise UnitEconomicsStructureError(
            "goods row integer field exceeds the signed 64-bit range"
        )
    return value


def _leading_quality(text: str) -> float:
    match = _FLOAT_TOKEN_RE.match(text)
    if match is None:
        raise UnitEconomicsStructureError("goods row quality field is missing")
    value = float(match.group(0))
    if not math.isfinite(value) or value < 0 or value > _MAX_SIGNED_INT64:
        raise UnitEconomicsStructureError("goods row quality field is invalid")
    return value


def _monetary_pair(text: str) -> tuple[int, int]:
    matches = list(_MONETARY_TOKEN_RE.finditer(text))
    if len(matches) != 2:
        raise UnitEconomicsStructureError(
            "goods revenue/profit field must expose exactly two integers"
        )
    values = []
    for match in matches:
        prefix = text[: match.start()].rstrip()
        if prefix.endswith(("-", "\u2212")):
            raise UnitEconomicsStructureError(
                "goods revenue/profit field must be non-negative"
            )
        digits = match.group(0).replace(" ", "")
        if not digits.isascii() or not digits.isdigit():
            raise UnitEconomicsStructureError(
                "goods revenue/profit field is invalid"
            )
        value = int(digits)
        if value > _MAX_SIGNED_INT64:
            raise UnitEconomicsStructureError(
                "goods revenue/profit field exceeds the signed 64-bit range"
            )
        values.append(value)
    return values[0], values[1]


def _input_value(cell: _Element, base_name: str) -> str:
    matches: list[_Element] = []
    for node in _iter_nodes(cell):
        if node.tag != "input" or node.suppressed:
            continue
        name = _attr(node, "name")
        if not isinstance(name, str):
            continue
        match = _INPUT_NAME_RE.fullmatch(name)
        if match is not None and match.group(1) == base_name:
            matches.append(node)
    if len(matches) != 1:
        raise UnitEconomicsStructureError(
            f"goods row must expose exactly one {base_name}[] input"
        )
    value = _attr(matches[0], "value")
    if not isinstance(value, str):
        raise UnitEconomicsStructureError(
            f"goods {base_name}[] input is missing its value"
        )
    return value


def _parse_input_integer(value: str, base_name: str) -> int:
    normalized = value.replace("\u00a0", " ").strip()
    digits = normalized.replace(" ", "")
    if not digits or not digits.isascii() or not digits.isdigit():
        raise UnitEconomicsStructureError(
            f"goods {base_name}[] input is not a non-negative integer"
        )
    number = int(digits)
    if number > _MAX_SIGNED_INT64:
        raise UnitEconomicsStructureError(
            f"goods {base_name}[] input exceeds the signed 64-bit range"
        )
    return number


def _input_integer(
    cell: _Element, base_name: str, *, empty_value: int | None = None
) -> int:
    value = _input_value(cell, base_name)
    if not value.replace("\u00a0", " ").strip():
        if empty_value is None:
            raise UnitEconomicsStructureError(
                f"goods {base_name}[] input is not a non-negative integer"
            )
        # An unselected supplier keeps the hidden vendorPrice input empty; the
        # frozen contract represents that as a zero supply cost.
        return empty_value
    return _parse_input_integer(value, base_name)


def _is_header_row(row: _Element, cells: list[_Element]) -> bool:
    if _HEADER_CLASS_TOKEN in _class_tokens(row):
        return True
    return any(_HEADER_CLASS_TOKEN in _class_tokens(cell) for cell in cells)


def _row_product_id(row: _Element, unit_id: str) -> int:
    if row.ambiguous or not row.closed:
        raise UnitEconomicsStructureError("goods row is ambiguous or incomplete")
    cells = [
        child
        for child in row.children
        if child.tag == "td" and not child.suppressed
    ]
    if len(cells) < 15:
        raise UnitEconomicsStructureError("goods data row has too few cells")
    for cell in cells[:15]:
        if cell.ambiguous or not cell.closed:
            raise UnitEconomicsStructureError("goods data cell is ambiguous or incomplete")
    first = _single_link(cells[0], label="image")
    second = _single_link(cells[1], label="name")
    first_href = _attr(first, "href")
    second_href = _attr(second, "href")
    if not isinstance(first_href, str) or not isinstance(second_href, str):
        raise UnitEconomicsStructureError("goods row link is missing its href")
    first_id = _product_numeric_id(first_href, unit_id=unit_id)
    second_id = _product_numeric_id(second_href, unit_id=unit_id)
    if first_id != second_id:
        raise UnitEconomicsStructureError("goods row link identities disagree")
    row_id = _attr(row, "id")
    if row_id is not None:
        match = _DATA_ROW_ID_RE.fullmatch(row_id)
        if match is None or int(match.group(1)) != first_id:
            raise UnitEconomicsStructureError("goods row id disagrees with its links")
    return first_id


def _row_values(row: _Element, product_numeric_id: int) -> UnitEconomicsRow:
    cells = [
        child
        for child in row.children
        if child.tag == "td" and not child.suppressed
    ]
    revenue, profit = _monetary_pair(_node_text(cells[3]))
    return UnitEconomicsRow(
        product_numeric_id=product_numeric_id,
        revenue=revenue,
        profit=profit,
        stock_qty=_leading_integer(_node_text(cells[5])),
        stock_quality=_leading_quality(_node_text(cells[6])),
        our_price=_input_integer(cells[9], "price"),
        city_quality=_leading_quality(_node_text(cells[10])),
        city_price=_leading_integer(_node_text(cells[11])),
        sales_volume=_leading_integer(_node_text(cells[12])),
        supply_qty=_input_integer(cells[14], "purchaseQuantity"),
        # td7 ("себест.") is deliberately not projected; supply_cost is the
        # selected supplier price from the hidden vendorPrice[N] value.
        supply_cost=_input_integer(cells[14], "vendorPrice", empty_value=0),
    )


def _goods_rows(table: _Element, unit_id: str) -> tuple[UnitEconomicsRow, ...]:
    rows: list[UnitEconomicsRow] = []
    seen: set[int] = set()
    for row in table.children:
        if row.tag != "tr" or row.suppressed:
            continue
        cells = [
            child
            for child in row.children
            if child.tag == "td" and not child.suppressed
        ]
        if not cells:
            if row_id := _attr(row, "id"):
                if _DATA_ROW_ID_RE.fullmatch(row_id) is not None:
                    raise UnitEconomicsStructureError("goods data row has no cells")
            continue
        if _is_header_row(row, cells):
            continue
        if row.ambiguous or not row.closed:
            raise UnitEconomicsStructureError("goods row is ambiguous or incomplete")
        if len(rows) >= MAX_ROWS:
            raise UnitEconomicsOverflowError(
                f"goods page exceeds the {MAX_ROWS} row limit"
            )
        product_numeric_id = _row_product_id(row, unit_id)
        if product_numeric_id in seen:
            raise UnitEconomicsStructureError(
                "goods page contains duplicate product identities"
            )
        seen.add(product_numeric_id)
        rows.append(_row_values(row, product_numeric_id))
    if not rows:
        raise UnitEconomicsStructureError("goods table exposes no product rows")
    return tuple(rows)


def extract_unit_economics_payload(html: bytes, *, unit_id: str) -> bytes:
    """Return canonical unit-economics artifact bytes or raise an extractor error."""

    if not isinstance(html, bytes):
        raise TypeError("html must be bytes")
    if (
        not isinstance(unit_id, str)
        or not unit_id
        or not unit_id.isascii()
        or not unit_id.isdigit()
        or unit_id.startswith("0")
        or int(unit_id) > _MAX_SIGNED_INT64
    ):
        raise UnitEconomicsStructureError(
            "goods extraction requires a canonical positive unit id"
        )
    try:
        text = html.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnitEconomicsStructureError("goods response is not UTF-8") from exc
    parser = _GoodsTreeParser()
    parser.feed(text)
    parser.close()
    table = _goods_table(parser.root)
    try:
        page = UnitEconomicsPage(
            unit_id=unit_id,
            coverage="observed",
            rows=_goods_rows(table, unit_id),
        )
        return unit_economics_payload(page)
    except UnitEconomicsContractError as exc:
        raise UnitEconomicsStructureError(
            "goods page cannot be represented by the frozen artifact contract"
        ) from exc


__all__ = [
    "UnitEconomicsExtractionError",
    "UnitEconomicsOverflowError",
    "UnitEconomicsStructureError",
    "extract_unit_economics_payload",
]
