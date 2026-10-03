from __future__ import annotations

import math
import unittest

from bizman.foundation.fingerprint import canonical_sha256
from bizman.market.model import (
    MAX_HTML_CHARS,
    MAX_ROWS,
    SCHEMA,
    SURFACE_RETAILMARKET_CITY,
    SURFACE_RETAILPRICES_GROUP,
    SURFACE_VENDORS,
    MarketContractError,
    RetailMarketCityPage,
    RetailMarketGroupRow,
    RetailPriceCityRow,
    RetailPriceGroupPage,
    VendorCatalogPage,
    VendorProductRow,
    market_page_fingerprint,
    market_page_semantics,
    market_page_surface,
)
from bizman.market.parsers import parse_market_page


CITY_URL = "https://bizmania.ru/analitics/retailmarket/?city=25"
PRICE_URL = "https://bizmania.ru/analitics/retailprices/?retailgroup=1"
VENDORS_URL = "https://bizmania.ru/analitics/vendors/"


def _city_cells(
    *,
    group_id: int = 1,
    city_id: int = 25,
    name: str = "Группа 1",
    money: str = "819.94 млн p.",
    delta: str = "+79.5%",
    share: str = "13.73%",
    markup: str = "173%",
    level: str = "67.74%",
    image_href: str | None = None,
    name_href: str | None = None,
    dialog_href: str | None = None,
    competition: str = (
        '<img src="/img/retailcompet/3.png" width="16" height="16" '
        'title="Средняя конкуренция" alt="Средняя конкуренция">'
    ),
    bar: str | None = None,
) -> list[str]:
    href = f"/analitics/retailmarket/?city={city_id}&retailgroup={group_id}"
    if image_href is None:
        image_href = href
    if name_href is None:
        name_href = href
    if dialog_href is None:
        dialog_href = (
            "javascript:dialog('/analitics/categorysales/"
            f"?city={city_id}&category={group_id}')"
        )
    if bar is None:
        bar = (
            f'<span class="color-bar green" style="width:60px" '
            f'title="Уровень цен: {level}"><span style="width:41px"></span></span>'
        )
    return [
        f'<td width="1%"><a href="{image_href}">'
        '<img src="/img/products/food.gif" title="Еда" border="0"></a></td>',
        f'<td><a href="{name_href}">{name}</a></td>',
        f'<td align="right" nowrap>{money}</td>',
        f'<td width="1%"><a href="{dialog_href}">'
        '<img src="/img/graph.png" border="0"></a></td>',
        f'<td align="center"><font class="grn">{delta}</font></td>',
        f'<td align="center" nowrap>{share}</td>',
        f'<td align="right" width="70" nowrap>{markup}</td>',
        f'<td align="center" width="1%">{competition}</td>',
        f"<td>{bar}</td>",
    ]


def _row_html(cells: list[str], *, row_class: str | None = "tblr") -> str:
    class_attr = f' class="{row_class}"' if row_class else ""
    return f"<tr{class_attr}>" + "".join(cells) + "</tr>"


def _city_header(cells: list[str] | None = None) -> str:
    if cells is None:
        cells = [
            '<td class="tblh1" colspan="2" align="center">Группа</td>',
            '<td class="tblh" colspan="2" align="center">Объём</td>',
            '<td class="tblh" align="center">+/-</td>',
            '<td class="tblh" align="center">Доля</td>',
            '<td class="tblh" align="center">Ср. наценка</td>',
            '<td class="tblh" align="center">Конкуренция</td>',
            '<td class="tblh" align="center">Уровень цен</td>',
        ]
    return '<tr class="tblhr">' + "".join(cells) + "</tr>"


def _city_table(rows: str, *, header: str | None = None) -> str:
    if header is None:
        header = _city_header()
    return (
        '<table class="datatable" cellpadding="5" cellspacing="0" '
        f'border="0" width="100%"><thead>{header}</thead><tbody>{rows}</tbody></table>'
    )


def _city_page(rows: str, *, header: str | None = None, extra: str = "") -> str:
    return (
        "<!doctype html><html><body><div>Отчёт</div>"
        f"{_city_table(rows, header=header)}{extra}</body></html>"
    )


def _city_summary() -> str:
    return (
        '<tr class="tbltotal"><td class="bold" colspan="2" align="right">Итого:</td>'
        '<td class="bold nowrap" align="right">0 p.</td><td/>'
        '<td class="bold" align="right" colspan="2">Ваша доля:</td>'
        '<td colspan="2" class="bold nowrap">0 p.</td><td/></tr>'
    )


def _price_cells(
    index: int,
    *,
    city_id: int = 43,
    name: str = "Город 43",
    price: str = "2529.622672",
    quality: str = "3.121386539935728",
    share: str = "",
    price_id: str | None = None,
    quality_id: str | None = None,
    city_href: str | None = None,
    price_divs: tuple[str, ...] | None = None,
    quality_divs: tuple[str, ...] | None = None,
    formatted_price: str = "2 530 p.",
    formatted_quality: str = "3.12",
    nested_divs: bool = False,
) -> list[str]:
    if city_href is None:
        city_href = f"/city/?id={city_id}"
    if price_divs is None:
        identifier = price_id if price_id is not None else f"basketPrice[{index}]"
        price_divs = (
            f'<div id="{identifier}" style="display:none">{price}</div>',
        )
    if quality_divs is None:
        identifier = quality_id if quality_id is not None else f"basketQuality[{index}]"
        quality_divs = (
            f'<div id="{identifier}" style="display:none">{quality}</div>',
        )
    values = "".join(price_divs) + "".join(quality_divs)
    if nested_divs:
        city_cell = f'<a href="{city_href}">{name}{values}</a>'
    else:
        city_cell = f'<a href="{city_href}">{name}</a>{values}'
    return [
        '<td class="thincolumn" width="1%">'
        '<img src="/img/flag/europe.gif" width="16" height="12" '
        'title="Европа" alt="Европа" class="flag"></td>',
        f'<td style="white-space: nowrap;">{city_cell}</td>',
        f'<td align="center">{share}</td>',
        f'<td align="right" nowrap>{formatted_price}</td>',
        f'<td align="center" nowrap>{formatted_quality}</td>',
    ]


def _price_header(cells: list[str] | None = None) -> str:
    if cells is None:
        cells = [
            '<td class="tblh1" colspan="2" align="center">Город</td>',
            '<td class="tblh" align="center">Ваша доля</td>',
            '<td class="tblh" align="center" nowrap>'
            '<img src="/img/sdown.gif" class="tblsi">'
            '<a href="/analitics/retailprices/?retailgroup=1&amp;sort=basket_price" '
            'class="tblhl">Нормированная цена</a></td>',
            '<td class="tblh" align="center" nowrap>'
            '<img src="/img/sdown.gif" class="tblsi">'
            '<a href="/analitics/retailprices/?retailgroup=1&amp;sort=basket_quality" '
            'class="tblhl">Среднее качество</a></td>',
        ]
    return '<tr class="tblhr">' + "".join(cells) + "</tr>"


def _price_footer() -> str:
    return (
        '<tfoot><tr class="tbltotal bold"><td colspan="3" align="right">Среднее:</td>'
        '<td id="basketAveragePrice" align="right"/><td id="basketAverageQuality" align="center"/>'
        '<td allow="admin:develop"/></tr></tfoot>'
    )


def _price_page(
    rows: str,
    *,
    header: str | None = None,
    footer: str | None = None,
) -> str:
    if header is None:
        header = _price_header()
    if footer is None:
        footer = _price_footer()
    return (
        "<!doctype html><html><body>"
        '<table class="datatable" cellpadding="5" cellspacing="0" '
        f'border="0" width="450"><thead>{header}</thead><tbody>{rows}</tbody>'
        f"{footer}</table></body></html>"
    )


def _vendor_section(name: str, product_ids: tuple[int, ...]) -> str:
    heading = f'<tr><td colspan="2"><h3>{name}</h3></td></tr>'
    links = "".join(
        '<div style="display:flex">'
        f'<a href="/analitics/vendors/?product={product_id}">'
        '<img src="/img/products/car.gif" width="32" height="32" '
        'title="Товар" alt="Товар"></a></div>'
        for product_id in product_ids
    )
    products = (
        '<tr><td class="hidecompact">'
        '<img src="/img/nil.gif" width="40" height="1"></td>'
        f"<td>{links}</td></tr>"
    )
    return heading + products


def _vendors_table(sections: str) -> str:
    return (
        '<table cellpadding="2" cellspacing="0" border="0" width="100%">'
        f"{sections}</table>"
    )


def _vendors_page(sections: str, *, extra: str = "") -> str:
    return (
        "<!doctype html><html><body><div>Поставщики</div>"
        f"{_vendors_table(sections)}{extra}</body></html>"
    )


class RetailMarketCityPageTests(unittest.TestCase):
    def test_city_page_parses_rows_and_fingerprint(self):
        rows = _row_html(_city_cells()) + _row_html(
            _city_cells(
                group_id=14,
                name="Группа 14",
                money="1.12 млрд p.",
                delta="+61.8%",
                share="18.68%",
                markup="177%",
                level="76.14%",
            )
        )
        page = parse_market_page(_city_page(rows), url=CITY_URL)
        self.assertIsInstance(page, RetailMarketCityPage)
        self.assertEqual(page.city_id, 25)
        self.assertEqual(market_page_surface(page), SURFACE_RETAILMARKET_CITY)
        self.assertEqual(page.schema, SCHEMA)
        self.assertEqual(len(page.rows), 2)
        first = page.rows[0]
        self.assertEqual(first.retail_group_id, 1)
        self.assertEqual(first.group_name, "Группа 1")
        self.assertEqual(first.sales_volume_rub, 819_940_000)
        self.assertEqual(first.delta_percent, 79.5)
        self.assertEqual(first.share_percent, 13.73)
        self.assertEqual(first.avg_markup_percent, 173.0)
        self.assertEqual(first.price_level_percent, 67.74)
        second = page.rows[1]
        self.assertEqual(second.retail_group_id, 14)
        self.assertEqual(second.sales_volume_rub, 1_120_000_000)
        self.assertEqual(
            market_page_fingerprint(page),
            canonical_sha256(market_page_semantics(page)),
        )

    def test_city_page_skips_summary_rows_and_keeps_signed_delta(self):
        rows = _row_html(
            _city_cells(group_id=7, name="Группа 7", delta="-4.4%", markup="0%")
        )
        page = parse_market_page(
            _city_page(rows + _city_summary()),
            url=CITY_URL,
        )
        self.assertEqual(len(page.rows), 1)
        self.assertEqual(page.rows[0].delta_percent, -4.4)
        self.assertEqual(page.rows[0].avg_markup_percent, 0.0)

    def test_city_page_accepts_space_and_nbsp_thousands_separators(self):
        rows = _row_html(_city_cells(money="1&nbsp;234.5 тыс p."))
        page = parse_market_page(_city_page(rows), url=CITY_URL)
        self.assertEqual(page.rows[0].sales_volume_rub, 1_234_500)

    def test_city_page_rejects_missing_table(self):
        with self.assertRaises(MarketContractError):
            parse_market_page("<html><body><p>нет таблицы</p></body></html>", url=CITY_URL)

    def test_city_page_rejects_ambiguous_tables(self):
        first = _city_table(_row_html(_city_cells()))
        second = _city_table(_row_html(_city_cells(group_id=2, name="Группа 2")))
        html = f"<html><body>{first}{second}</body></html>"
        with self.assertRaises(MarketContractError):
            parse_market_page(html, url=CITY_URL)

    def test_city_page_ignores_non_department_datatables(self):
        lookalike_header = _city_header(_city_header_cells()[:6])
        lookalike = (
            '<table class="datatable" cellpadding="5" cellspacing="0" '
            'border="0" width="450"><thead>'
            f"{lookalike_header}</thead><tbody>"
            '<tr><td width="1%"></td><td>Город</td><td>1</td>'
            "<td>2</td><td>3</td><td>4</td></tr>"
            "</tbody></table>"
        )
        html = _city_page(_row_html(_city_cells()), extra=lookalike)
        page = parse_market_page(html, url=CITY_URL)
        self.assertEqual(len(page.rows), 1)

    def test_city_page_rejects_unexpected_header_shapes(self):
        cases = {
            "six-cells": _city_header(_city_header_cells()[:6]),
            "wrong-identity-class": _city_header(
                [
                    '<td class="tblh" colspan="2">Группа</td>',
                    *_city_header_cells()[1:],
                ]
            ),
            "missing-colspan": _city_header(
                [
                    '<td class="tblh1">Группа</td>',
                    *_city_header_cells()[1:],
                ]
            ),
        }
        for label, header in cases.items():
            with self.subTest(label=label):
                html = _city_page(_row_html(_city_cells()), header=header)
                with self.assertRaises(MarketContractError):
                    parse_market_page(html, url=CITY_URL)

    def test_city_page_rejects_unexpected_cell_count(self):
        cells = _city_cells()
        html = _city_page(_row_html(cells[:8]))
        with self.assertRaises(MarketContractError):
            parse_market_page(html, url=CITY_URL)

    def test_city_page_rejects_row_with_no_cells(self):
        html = _city_page("<tr></tr>")
        with self.assertRaises(MarketContractError):
            parse_market_page(html, url=CITY_URL)

    def test_city_page_rejects_summary_lookalike_with_anchor(self):
        lookalike = (
            '<tr><td colspan="2" align="right">'
            '<a href="/analitics/retailmarket/?city=25&retailgroup=1">x</a></td>'
            '<td>1</td><td>2</td><td>3</td><td>4</td><td>5</td></tr>'
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(lookalike), url=CITY_URL)

    def test_city_page_rejects_duplicate_group_identities(self):
        rows = _row_html(_city_cells()) + _row_html(
            _city_cells(name="Группа 1 снова")
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(rows), url=CITY_URL)

    def test_city_page_rejects_duplicate_anchors(self):
        cells = _city_cells()
        cells[0] = (
            '<td width="1%">'
            '<a href="/analitics/retailmarket/?city=25&retailgroup=1">'
            '<img src="/img/products/food.gif"></a>'
            '<a href="/analitics/retailmarket/?city=25&retailgroup=1">'
            '<img src="/img/products/food.gif"></a></td>'
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(_row_html(cells)), url=CITY_URL)

    def test_city_page_rejects_disagreeing_links(self):
        cells = _city_cells(
            name_href="/analitics/retailmarket/?city=25&retailgroup=2"
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(_row_html(cells)), url=CITY_URL)

    def test_city_page_rejects_wrong_city_link(self):
        cells = _city_cells(
            image_href="/analitics/retailmarket/?city=26&retailgroup=1",
            name_href="/analitics/retailmarket/?city=26&retailgroup=1",
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(_row_html(cells)), url=CITY_URL)

    def test_city_page_rejects_unexpected_category_link(self):
        cells = _city_cells(
            dialog_href=(
                "javascript:dialog('/analitics/categorysales/?city=25&category=9')"
            )
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(_row_html(cells)), url=CITY_URL)

    def test_city_page_rejects_missing_price_level_bar(self):
        cells = _city_cells(bar='<span class="other" title="Уровень цен: 10%"></span>')
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(_row_html(cells)), url=CITY_URL)

    def test_city_page_rejects_ambiguous_price_level_bar(self):
        bar = (
            '<span class="color-bar green" title="Уровень цен: 10%"></span>'
            '<span class="color-bar green" title="Уровень цен: 20%"></span>'
        )
        cells = _city_cells(bar=bar)
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(_row_html(cells)), url=CITY_URL)

    def test_city_page_rejects_missing_competition_title(self):
        cells = _city_cells(competition='<img src="/img/retailcompet/3.png">')
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(_row_html(cells)), url=CITY_URL)

    def test_city_page_rejects_malformed_money(self):
        for money in (
            "819,94 млн p.",
            "819.94 p.",
            "819.94 млн",
            "abc млн p.",
            "819.94 трлн p.",
            "1.0000005 тыс p.",
        ):
            with self.subTest(money=money):
                rows = _row_html(_city_cells(money=money))
                with self.assertRaises(MarketContractError):
                    parse_market_page(_city_page(rows), url=CITY_URL)

    def test_city_page_rejects_malformed_percent(self):
        for delta in ("79.5", "abc%", "1e2%", ".5%", "5.%", "1 000%", "%"):
            with self.subTest(delta=delta):
                rows = _row_html(_city_cells(delta=delta))
                with self.assertRaises(MarketContractError):
                    parse_market_page(_city_page(rows), url=CITY_URL)

    def test_city_page_rejects_missing_group_rows(self):
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(""), url=CITY_URL)

    def test_city_page_rejects_more_than_max_rows(self):
        rows = "".join(
            _row_html(
                _city_cells(
                    group_id=index + 1,
                    name=f"Группа {index + 1}",
                )
            )
            for index in range(MAX_ROWS + 1)
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_city_page(rows), url=CITY_URL)


def _city_header_cells() -> list[str]:
    return [
        '<td class="tblh1" colspan="2" align="center">Группа</td>',
        '<td class="tblh" colspan="2" align="center">Объём</td>',
        '<td class="tblh" align="center">+/-</td>',
        '<td class="tblh" align="center">Доля</td>',
        '<td class="tblh" align="center">Ср. наценка</td>',
        '<td class="tblh" align="center">Конкуренция</td>',
        '<td class="tblh" align="center">Уровень цен</td>',
    ]


class RetailPriceGroupPageTests(unittest.TestCase):
    def test_price_page_parses_rows_without_share(self):
        rows = _row_html(
            _price_cells(0, city_id=43),
            row_class="tblr",
        ) + _row_html(
            _price_cells(
                1,
                city_id=18,
                name="Город 18",
                price="2404.638645",
                quality="3.160650457731355",
            ),
            row_class="tblgr",
        )
        page = parse_market_page(_price_page(rows), url=PRICE_URL)
        self.assertIsInstance(page, RetailPriceGroupPage)
        self.assertEqual(page.retail_group_id, 1)
        self.assertEqual(market_page_surface(page), SURFACE_RETAILPRICES_GROUP)
        self.assertEqual(len(page.rows), 2)
        first = page.rows[0]
        self.assertEqual(first.city_id, 43)
        self.assertEqual(first.city_name, "Город 43")
        self.assertEqual(first.normalized_price, 2529.622672)
        self.assertEqual(first.avg_quality, 3.121386539935728)
        self.assertIsNone(first.your_share_percent)
        second = page.rows[1]
        self.assertEqual(second.city_id, 18)
        self.assertEqual(second.normalized_price, 2404.638645)
        self.assertEqual(second.avg_quality, 3.160650457731355)

    def test_price_page_parses_share_percent(self):
        rows = _row_html(
            _price_cells(
                0,
                city_id=25,
                name="Город 25",
                price="94802.883471",
                quality="2.815371018032872",
                share="5.41%",
            )
        )
        page = parse_market_page(_price_page(rows), url=PRICE_URL)
        self.assertEqual(page.rows[0].your_share_percent, 5.41)

    def test_price_page_parses_basket_divs_nested_in_anchor(self):
        rows = _row_html(_price_cells(0, nested_divs=True))
        page = parse_market_page(_price_page(rows), url=PRICE_URL)
        self.assertEqual(page.rows[0].city_id, 43)
        self.assertEqual(page.rows[0].city_name, "Город 43")
        self.assertEqual(page.rows[0].normalized_price, 2529.622672)
        self.assertEqual(page.rows[0].avg_quality, 3.121386539935728)

    def test_price_page_skips_summary_footer(self):
        rows = _row_html(_price_cells(0))
        page = parse_market_page(_price_page(rows), url=PRICE_URL)
        self.assertEqual(len(page.rows), 1)

    def test_price_page_rejects_missing_or_ambiguous_tables(self):
        cases = {
            "missing": "<html><body><p>нет</p></body></html>",
            "ambiguous": (
                "<html><body>"
                + '<table class="datatable"><tbody>'
                + _row_html(_price_cells(0))
                + "</tbody></table>"
                + '<table class="datatable"><tbody>'
                + _row_html(_price_cells(0))
                + "</tbody></table></body></html>"
            ),
        }
        for label, html in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(MarketContractError):
                    parse_market_page(html, url=PRICE_URL)

    def test_price_page_rejects_unexpected_header(self):
        header = _price_header(
            [
                '<td class="tblh" colspan="2">Город</td>',
                *_price_header_cells()[1:],
            ]
        )
        rows = _row_html(_price_cells(0))
        with self.assertRaises(MarketContractError):
            parse_market_page(_price_page(rows, header=header), url=PRICE_URL)

    def test_price_page_rejects_unexpected_cell_count(self):
        cells = _price_cells(0)
        with self.assertRaises(MarketContractError):
            parse_market_page(_price_page(_row_html(cells[:4])), url=PRICE_URL)

    def test_price_page_rejects_wrong_basket_div_index(self):
        cells = _price_cells(0, price_id="basketPrice[9]")
        with self.assertRaises(MarketContractError):
            parse_market_page(_price_page(_row_html(cells)), url=PRICE_URL)

    def test_price_page_rejects_missing_or_duplicate_basket_divs(self):
        cases = {
            "missing-price": _price_cells(0, price_divs=()),
            "duplicate-quality": _price_cells(
                0,
                quality_divs=(
                    '<div id="basketQuality[0]">3.1</div>',
                    '<div id="basketQuality[0]">3.2</div>',
                ),
            ),
        }
        for label, cells in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(MarketContractError):
                    parse_market_page(
                        _price_page(_row_html(cells)),
                        url=PRICE_URL,
                    )

    def test_price_page_rejects_empty_city_name(self):
        cells = _price_cells(0, name="")
        with self.assertRaises(MarketContractError):
            parse_market_page(_price_page(_row_html(cells)), url=PRICE_URL)

    def test_price_page_rejects_malformed_raw_numbers(self):
        for price, quality in (
            ("2,5", "3.12"),
            ("abc", "3.12"),
            ("1e3", "3.12"),
            ("2529.622672", ""),
            ("", "3.12"),
        ):
            with self.subTest(price=price, quality=quality):
                cells = _price_cells(0, price=price, quality=quality)
                with self.assertRaises(MarketContractError):
                    parse_market_page(
                        _price_page(_row_html(cells)),
                        url=PRICE_URL,
                    )

    def test_price_page_rejects_malformed_city_link(self):
        cells = _price_cells(0, city_href="/city/?id=043")
        with self.assertRaises(MarketContractError):
            parse_market_page(_price_page(_row_html(cells)), url=PRICE_URL)

    def test_price_page_rejects_missing_formatted_columns(self):
        for formatted_price, formatted_quality in (
            ("", "3.12"),
            ("2 530 p.", ""),
        ):
            with self.subTest(price=formatted_price, quality=formatted_quality):
                cells = _price_cells(
                    0,
                    formatted_price=formatted_price,
                    formatted_quality=formatted_quality,
                )
                with self.assertRaises(MarketContractError):
                    parse_market_page(
                        _price_page(_row_html(cells)),
                        url=PRICE_URL,
                    )

    def test_price_page_rejects_duplicate_city_identities(self):
        rows = _row_html(_price_cells(0, city_id=43)) + _row_html(
            _price_cells(1, city_id=43, name="Город 43 снова"),
            row_class="tblgr",
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_price_page(rows), url=PRICE_URL)

    def test_price_page_rejects_malformed_share(self):
        for share in ("5.41", "abc%", "-5.41%"):
            with self.subTest(share=share):
                rows = _row_html(_price_cells(0, share=share))
                with self.assertRaises(MarketContractError):
                    parse_market_page(_price_page(rows), url=PRICE_URL)

    def test_price_page_rejects_missing_city_rows(self):
        with self.assertRaises(MarketContractError):
            parse_market_page(_price_page(""), url=PRICE_URL)


def _price_header_cells() -> list[str]:
    return [
        '<td class="tblh1" colspan="2" align="center">Город</td>',
        '<td class="tblh" align="center">Ваша доля</td>',
        '<td class="tblh" align="center" nowrap>Нормированная цена</td>',
        '<td class="tblh" align="center" nowrap>Среднее качество</td>',
    ]


class VendorCatalogPageTests(unittest.TestCase):
    def test_vendors_page_parses_sections_in_document_order(self):
        sections = _vendor_section("Группа 1", (159, 422)) + _vendor_section(
            "Группа 2",
            (398,),
        )
        page = parse_market_page(_vendors_page(sections), url=VENDORS_URL)
        self.assertIsInstance(page, VendorCatalogPage)
        self.assertEqual(market_page_surface(page), SURFACE_VENDORS)
        self.assertEqual(
            [row.product_numeric_id for row in page.rows],
            [159, 422, 398],
        )
        self.assertEqual(
            [row.group_name for row in page.rows],
            ["Группа 1", "Группа 1", "Группа 2"],
        )

    def test_vendors_page_rejects_missing_or_ambiguous_tables(self):
        cases = {
            "missing": "<html><body><p>нет</p></body></html>",
            "ambiguous": (
                "<html><body>"
                + _vendors_table(_vendor_section("Группа 1", (1,)))
                + _vendors_table(_vendor_section("Группа 2", (2,)))
                + "</body></html>"
            ),
        }
        for label, html in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(MarketContractError):
                    parse_market_page(html, url=VENDORS_URL)

    def test_vendors_page_rejects_product_before_heading(self):
        sections = (
            '<tr><td class="hidecompact"></td><td>'
            '<a href="/analitics/vendors/?product=159">'
            '<img src="/img/products/car.gif"></a></td></tr>'
            + _vendor_section("Группа 1", (422,))
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_vendors_page(sections), url=VENDORS_URL)

    def test_vendors_page_rejects_product_inside_heading_row(self):
        sections = (
            '<tr><td colspan="2"><h3>Группа 1</h3>'
            '<a href="/analitics/vendors/?product=159">'
            '<img src="/img/products/car.gif"></a></td></tr>'
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_vendors_page(sections), url=VENDORS_URL)

    def test_vendors_page_rejects_duplicate_product_identities(self):
        sections = _vendor_section("Группа 1", (159,)) + _vendor_section(
            "Группа 2",
            (159,),
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_vendors_page(sections), url=VENDORS_URL)

    def test_vendors_page_rejects_empty_heading(self):
        sections = _vendor_section("", (159,))
        with self.assertRaises(MarketContractError):
            parse_market_page(_vendors_page(sections), url=VENDORS_URL)

    def test_vendors_page_rejects_malformed_product_link(self):
        sections = (
            '<tr><td colspan="2"><h3>Группа 1</h3></td></tr>'
            '<tr><td class="hidecompact"></td><td>'
            '<a href="/analitics/vendors/?product=159">'
            '<img src="/img/products/car.gif"></a>'
            '<a href="/analitics/vendors/?product=abc">'
            '<img src="/img/products/car.gif"></a></td></tr>'
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_vendors_page(sections), url=VENDORS_URL)

    def test_vendors_page_rejects_more_than_max_rows(self):
        sections = _vendor_section(
            "Группа 1",
            tuple(range(1, MAX_ROWS + 2)),
        )
        with self.assertRaises(MarketContractError):
            parse_market_page(_vendors_page(sections), url=VENDORS_URL)


class RequestUrlValidationTests(unittest.TestCase):
    def _assert_rejected(self, url: str, html: str = "<html></html>"):
        with self.assertRaises(MarketContractError):
            parse_market_page(html, url=url)

    def test_rejects_non_string_and_oversized_html(self):
        with self.assertRaises(MarketContractError):
            parse_market_page(b"<html></html>", url=VENDORS_URL)  # type: ignore[arg-type]
        with self.assertRaises(MarketContractError):
            parse_market_page("x" * (MAX_HTML_CHARS + 1), url=VENDORS_URL)

    def test_rejects_foreign_scheme_host_port_credentials_and_fragment(self):
        urls = (
            "http://bizmania.ru/analitics/vendors/",
            "https://example.com/analitics/vendors/",
            "https://bizmania.ru:443/analitics/vendors/",
            "https://bizmania.ru:abc/analitics/vendors/",
            "https://user:pass@bizmania.ru/analitics/vendors/",
            "https://bizmania.ru/analitics/vendors/#top",
            "https://bizmania.ru/units/shop/?id=33670&tab=goods",
        )
        for url in urls:
            with self.subTest(url=url):
                self._assert_rejected(url)

    def test_rejects_unexpected_city_query(self):
        urls = (
            "https://bizmania.ru/analitics/retailmarket/",
            "https://bizmania.ru/analitics/retailmarket/?retailgroup=1",
            "https://bizmania.ru/analitics/retailmarket/?city=25&foo=1",
            "https://bizmania.ru/analitics/retailmarket/?city=25&city=25",
            "https://bizmania.ru/analitics/retailmarket/?%63ity=25",
            "https://bizmania.ru/analitics/retailmarket/?city=025",
            "https://bizmania.ru/analitics/retailmarket/?city=0",
            "https://bizmania.ru/analitics/retailmarket/?city=abc",
            "https://bizmania.ru/analitics/retailmarket/?city=9223372036854775808",
            "https://bizmania.ru/analitics/retailmarket/?city",
        )
        for url in urls:
            with self.subTest(url=url):
                self._assert_rejected(url)

    def test_rejects_unexpected_price_query(self):
        urls = (
            "https://bizmania.ru/analitics/retailprices/",
            "https://bizmania.ru/analitics/retailprices/?city=25",
            "https://bizmania.ru/analitics/retailprices/?retailgroup=1&retailgroup=2",
            "https://bizmania.ru/analitics/retailprices/?retailgroup=-1",
            "https://bizmania.ru/analitics/retailprices/?%72etailgroup=1",
        )
        for url in urls:
            with self.subTest(url=url):
                self._assert_rejected(url)

    def test_rejects_vendors_query(self):
        for url in (
            "https://bizmania.ru/analitics/vendors/?product=1",
            "https://bizmania.ru/analitics/vendors/?foo=1",
        ):
            with self.subTest(url=url):
                self._assert_rejected(url)


class MarketModelTests(unittest.TestCase):
    def _market_row(self, **overrides) -> RetailMarketGroupRow:
        values: dict[str, object] = {
            "retail_group_id": 1,
            "group_name": "  Группа 1  ",
            "sales_volume_rub": 0,
            "delta_percent": -4.4,
            "share_percent": 0.0,
            "avg_markup_percent": 0.0,
            "price_level_percent": 0.0,
        }
        values.update(overrides)
        return RetailMarketGroupRow(**values)  # type: ignore[arg-type]

    def test_row_validation(self):
        row = self._market_row()
        self.assertEqual(row.group_name, "Группа 1")
        self.assertEqual(row.delta_percent, -4.4)
        cases = {
            "bool-id": {"retail_group_id": True},
            "zero-id": {"retail_group_id": 0},
            "negative-sales": {"sales_volume_rub": -1},
            "empty-name": {"group_name": "   "},
            "nan-delta": {"delta_percent": math.nan},
            "infinite-share": {"share_percent": math.inf},
            "negative-markup": {"avg_markup_percent": -0.1},
            "negative-level": {"price_level_percent": -1.0},
        }
        for label, overrides in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(MarketContractError):
                    self._market_row(**overrides)

    def test_price_and_vendor_row_validation(self):
        row = RetailPriceCityRow(
            city_id=43,
            city_name=" Город 43 ",
            your_share_percent=None,
            normalized_price=2529.622672,
            avg_quality=3.121386539935728,
        )
        self.assertEqual(row.city_name, "Город 43")
        with self.assertRaises(MarketContractError):
            RetailPriceCityRow(
                city_id=43,
                city_name="Город 43",
                your_share_percent=-0.1,
                normalized_price=1.0,
                avg_quality=1.0,
            )
        with self.assertRaises(MarketContractError):
            RetailPriceCityRow(
                city_id=43,
                city_name="Город 43",
                your_share_percent=None,
                normalized_price=math.nan,
                avg_quality=1.0,
            )
        with self.assertRaises(MarketContractError):
            VendorProductRow(product_numeric_id=0, group_name="Группа")
        with self.assertRaises(MarketContractError):
            VendorProductRow(product_numeric_id=1, group_name="")

    def test_page_row_collection_validation(self):
        row = self._market_row()
        with self.assertRaises(MarketContractError):
            RetailMarketCityPage(city_id=25, rows=[row])  # type: ignore[arg-type]
        with self.assertRaises(MarketContractError):
            RetailMarketCityPage(city_id=25, rows=())
        with self.assertRaises(MarketContractError):
            RetailMarketCityPage(
                city_id=25,
                rows=(row, "nope"),  # type: ignore[arg-type]
            )
        with self.assertRaises(MarketContractError):
            RetailMarketCityPage(city_id=25, rows=(row, self._market_row()))
        with self.assertRaises(MarketContractError):
            RetailMarketCityPage(
                city_id=25,
                rows=tuple(
                    self._market_row(retail_group_id=index + 1)
                    for index in range(MAX_ROWS + 1)
                ),
            )

    def test_semantics_and_fingerprint_are_canonical(self):
        city_row = self._market_row()
        city_page = RetailMarketCityPage(city_id=25, rows=(city_row,))
        city_semantics = market_page_semantics(city_page)
        self.assertEqual(
            list(city_semantics),
            ["schema", "surface", "city_id", "rows"],
        )
        self.assertEqual(city_semantics["schema"], SCHEMA)
        self.assertEqual(city_semantics["city_id"], 25)
        self.assertEqual(
            list(city_semantics["rows"][0]),
            [
                "retail_group_id",
                "group_name",
                "sales_volume_rub",
                "delta_percent",
                "share_percent",
                "avg_markup_percent",
                "price_level_percent",
            ],
        )
        self.assertEqual(
            market_page_fingerprint(city_page),
            canonical_sha256(city_semantics),
        )

        price_page = RetailPriceGroupPage(
            retail_group_id=1,
            rows=(
                RetailPriceCityRow(
                    city_id=43,
                    city_name="Город 43",
                    your_share_percent=None,
                    normalized_price=2529.622672,
                    avg_quality=3.121386539935728,
                ),
            ),
        )
        self.assertEqual(
            list(market_page_semantics(price_page)),
            ["schema", "surface", "retail_group_id", "rows"],
        )
        self.assertEqual(
            market_page_fingerprint(price_page),
            canonical_sha256(market_page_semantics(price_page)),
        )

        vendor_page = VendorCatalogPage(
            rows=(VendorProductRow(product_numeric_id=159, group_name="Группа 1"),)
        )
        self.assertEqual(
            list(market_page_semantics(vendor_page)),
            ["schema", "surface", "rows"],
        )
        self.assertEqual(
            market_page_fingerprint(vendor_page),
            canonical_sha256(market_page_semantics(vendor_page)),
        )
        self.assertNotEqual(
            market_page_fingerprint(city_page),
            market_page_fingerprint(price_page),
        )

    def test_market_helpers_reject_foreign_values(self):
        with self.assertRaises(TypeError):
            market_page_surface("retailmarket.city")
        with self.assertRaises(TypeError):
            market_page_semantics(object())
        with self.assertRaises(TypeError):
            market_page_fingerprint(None)


if __name__ == "__main__":
    unittest.main()
