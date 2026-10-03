from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from bizman.collector.network import (
    FirstPartyPolicy,
    NetworkNormalizer,
    ResponseBodyCaptureError,
)
from bizman.collector.storage import ArtifactStore
from bizman.foundation.fingerprint import canonical_sha256
from bizman.foundation.redaction import RedactionPolicy
from bizman.foundation.unit_economics import (
    RESPONSE_BODY_KIND,
    parse_unit_economics_payload,
    unit_economics_payload,
)


SESSION_ID = "01991c7d-a400-7000-8000-000000000401"
UNIT_ID = "33670"
_LEGACY_FINGERPRINT_KEYS = (
    "source",
    "event_type",
    "target_id",
    "frame_id",
    "loader_id",
    "request_id",
    "redirect_index",
    "method",
    "url_path",
    "status_code",
    "query",
    "initiator_type",
    "has_user_gesture",
    "request_body_ref",
    "response_body_ref",
    "websocket_opcode",
    "error_text",
)


def _header_rows() -> str:
    row = (
        '<tr class="tblh">'
        + "".join('<td class="tblh">Заголовок</td>' for _ in range(16))
        + "</tr>"
    )
    return row + row


def _goods_row_html(
    *,
    unit: str = UNIT_ID,
    index: int = 0,
    product: int = 42,
    revenue: str = "1 234",
    profit: str = "321",
    stock_qty: str = "1 000",
    stock_quality: str = "4.5 (3.0)",
    our_price: str = "150",
    city_quality: str = "3.75",
    city_price: str = "160",
    sales_volume: str = "12",
    supply_qty: str = "7",
    supplier_price: str | None = "120",
    supplier_input: bool = True,
    label_canary: str = "SYNTHETIC_LABEL_CANARY",
    attribute_canary: str = "SYNTHETIC_ATTRIBUTE_CANARY",
    link_product: int | None = None,
    purchase_input: bool = True,
) -> str:
    link_id = product if link_product is None else link_product
    href = f"/units/shop/?id={unit}&tab=goods&product={link_id}"
    if supplier_input:
        vendor_value = "" if supplier_price is None else supplier_price
        vendor_input = (
            f'<input type="hidden" name="vendorPrice[{index}]" '
            f'value="{vendor_value}">'
        )
    else:
        vendor_input = ""
    purchase = (
        f'<input name="purchaseQuantity[{index}]" value="{supply_qty}">'
        if purchase_input
        else ""
    )
    cells = (
        f'<td><a href="{href}"><img src="/i/{product}.png" '
        f'alt="{attribute_canary}"></a>'
        f'<input type="hidden" name="product[0]" value="{product}"></td>',
        f'<td><a href="{href}">{label_canary}</a></td>',
        '<td><div class="progress" title="47%"></div></td>',
        f'<td>{revenue} p. {profit} p. '
        '<span title="Рентабельность: 25%"></span></td>',
        "<td>12%</td>",
        f"<td>{stock_qty}</td>",
        f"<td>{stock_quality}</td>",
        "<td>777 p. (555 p.)</td>",
        '<td><a href="#graph">graph</a></td>',
        f'<td><input name="price[{index}]" value="{our_price}">'
        '<input type="hidden" name="price_currency" value="RUB"></td>',
        f"<td>{city_quality}</td>",
        f"<td>{city_price}</td>",
        f"<td>{sales_volume}</td>",
        '<td><a href="#dialog">dialog</a></td>',
        f"<td>{purchase}{vendor_input}</td>",
        '<td><a href="#buy">buy</a></td>',
    )
    assert len(cells) == 16
    return f'<tr id="pr{product}">' + "".join(cells) + "</tr>"


def _goods_html(*rows: str, unit: str = UNIT_ID) -> str:
    return (
        "<!doctype html><html><body>"
        f'<table id="goods">{_header_rows()}{"".join(rows)}</table>'
        "</body></html>"
    )


class GoodsSurfaceCaptureTests(unittest.TestCase):
    def _normalizer(
        self,
        root: Path,
        *,
        max_response_body_bytes: int = 4 * 1024 * 1024,
    ) -> NetworkNormalizer:
        return NetworkNormalizer(
            session_id=SESSION_ID,
            first_party=FirstPartyPolicy(("bizmania.ru",)),
            redaction=RedactionPolicy.default(),
            artifacts=ArtifactStore(root),
            max_response_body_bytes=max_response_body_bytes,
        )

    def _prime(
        self,
        normalizer: NetworkNormalizer,
        request_id: str,
        url: str,
        *,
        method: str = "GET",
        status: int = 200,
        mime_type: str = "text/html",
        target_id: str = "t1",
    ) -> None:
        request = normalizer.normalize(
            method="Network.requestWillBeSent",
            params={
                "requestId": request_id,
                "timestamp": 1.0,
                "request": {
                    "url": url,
                    "method": method,
                    "headers": {},
                },
            },
            target_id=target_id,
        )
        self.assertIsNotNone(request)
        response = normalizer.normalize(
            method="Network.responseReceived",
            params={
                "requestId": request_id,
                "timestamp": 1.1,
                "response": {
                    "url": url,
                    "status": status,
                    "mimeType": mime_type,
                    "headers": {},
                },
            },
            target_id=target_id,
        )
        self.assertIsNotNone(response)

    def test_goods_candidate_accepts_exact_route_in_any_query_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            self._prime(
                normalizer,
                "r1",
                "https://bizmania.ru/units/shop/?tab=goods&id=33670",
            )
            self.assertTrue(
                normalizer.response_body_capture_candidate("r1", target_id="t1")
            )

    def test_goods_candidate_accepts_http_only_for_loopback_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = NetworkNormalizer(
                session_id=SESSION_ID,
                first_party=FirstPartyPolicy(("127.0.0.1",)),
                redaction=RedactionPolicy.default(),
                artifacts=ArtifactStore(Path(tmp)),
            )
            self._prime(
                normalizer,
                "r-loopback",
                "http://127.0.0.1:8765/units/shop/?id=33670&tab=goods",
            )
            self.assertTrue(
                normalizer.response_body_capture_candidate(
                    "r-loopback", target_id="t1"
                )
            )

    def test_goods_candidate_rejects_extra_keys_and_foreign_surfaces(self):
        rejected = (
            "https://bizmania.ru/units/shop/?id=33670&tab=goods&product=42",
            "https://bizmania.ru/units/shop/?id=33670&tab=goods&p=2",
            "https://bizmania.ru/units/shop/?id=33670&tab=supply",
            "https://bizmania.ru/units/shop/?id=33670&tab=goods&id=33671",
            "https://bizmania.ru/units/shop/?id=0&tab=goods",
            "https://bizmania.ru/units/shop/?id=33670&tab=Goods",
            "http://bizmania.ru/units/shop/?id=33670&tab=goods",
        )
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            for index, url in enumerate(rejected, start=1):
                with self.subTest(url=url):
                    request_id = f"r{index}"
                    self._prime(normalizer, request_id, url)
                    self.assertFalse(
                        normalizer.response_body_capture_candidate(
                            request_id, target_id="t1"
                        )
                    )

    def test_goods_candidate_rejects_non_get_non_200_and_non_html(self):
        cases = (
            {"method": "POST"},
            {"status": 500},
            {"status": 204},
            {"mime_type": "application/json"},
            {"mime_type": "text/plain"},
        )
        url = "https://bizmania.ru/units/shop/?id=33670&tab=goods"
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            for index, overrides in enumerate(cases, start=1):
                with self.subTest(overrides=overrides):
                    request_id = f"r{index}"
                    self._prime(normalizer, request_id, url, **overrides)
                    self.assertFalse(
                        normalizer.response_body_capture_candidate(
                            request_id, target_id="t1"
                        )
                    )

    def test_goods_capture_persists_typed_artifact_and_kind_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            self._prime(
                normalizer,
                "r1",
                "https://bizmania.ru/units/shop/?id=33670&tab=goods",
            )
            html = _goods_html(
                _goods_row_html(index=0, product=42),
                _goods_row_html(
                    index=1,
                    product=43,
                    revenue="2 000",
                    profit="400",
                    stock_qty="2",
                    stock_quality="1.5 (1.0)",
                    our_price="200",
                    city_quality="2.25",
                    city_price="210",
                    sales_volume="1 500",
                    supply_qty="9",
                    supplier_price="90",
                ),
            )
            event = normalizer.normalize_response_body(
                request_id="r1",
                body=html,
                base64_encoded=False,
                params={"requestId": "r1", "timestamp": 1.2},
                target_id="t1",
            )
            self.assertIsNotNone(event)
            assert event is not None
            self.assertEqual(event["event_type"], "http.response_body")
            self.assertEqual(event["url_path"], "/units/shop/")
            self.assertEqual(event["query"], {"id": ["33670"], "tab": ["goods"]})
            self.assertEqual(event["response_body_kind"], RESPONSE_BODY_KIND)

            ref = event["response_body_ref"]
            self.assertIsInstance(ref, str)
            payload = normalizer.artifacts.read_bytes(ref)
            page = parse_unit_economics_payload(payload)
            self.assertEqual(unit_economics_payload(page), payload)
            self.assertEqual(page.unit_id, "33670")
            self.assertEqual(
                [row.product_numeric_id for row in page.rows],
                [42, 43],
            )
            first = page.rows[0]
            self.assertEqual(first.revenue, 1234)
            self.assertEqual(first.profit, 321)
            self.assertEqual(first.stock_qty, 1000)
            self.assertEqual(first.stock_quality, 4.5)
            self.assertEqual(first.our_price, 150)
            self.assertEqual(first.city_quality, 3.75)
            self.assertEqual(first.city_price, 160)
            self.assertEqual(first.sales_volume, 12)
            self.assertEqual(first.supply_qty, 7)
            self.assertEqual(first.supply_cost, 120)
            second = page.rows[1]
            self.assertEqual(second.revenue, 2000)
            self.assertEqual(second.sales_volume, 1500)
            self.assertEqual(second.supply_cost, 90)

    def test_unselected_supplier_is_zero_supply_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            self._prime(
                normalizer,
                "r1",
                "https://bizmania.ru/units/shop/?id=33670&tab=goods",
            )
            event = normalizer.normalize_response_body(
                request_id="r1",
                body=_goods_html(_goods_row_html(product=42, supplier_price=None)),
                base64_encoded=False,
                params={"requestId": "r1", "timestamp": 1.2},
                target_id="t1",
            )
            assert event is not None
            page = parse_unit_economics_payload(
                normalizer.artifacts.read_bytes(event["response_body_ref"])
            )
            self.assertEqual(page.rows[0].supply_cost, 0)

    def test_extraction_failure_persists_no_artifact_or_event(self):
        broken_pages = (
            _goods_html(
                _goods_row_html(product=42),
                _goods_row_html(product=43, link_product=44),
            ),
            _goods_html(_goods_row_html(product=42, purchase_input=False)),
            _goods_html(_goods_row_html(product=42, supplier_input=False)),
            _goods_html(_goods_row_html(product=42, profit="-321")),
        )
        for html in broken_pages:
            with self.subTest(html=html[:64]), tempfile.TemporaryDirectory() as tmp:
                normalizer = self._normalizer(Path(tmp))
                self._prime(
                    normalizer,
                    "r1",
                    "https://bizmania.ru/units/shop/?id=33670&tab=goods",
                )
                before = normalizer.artifacts.count
                with self.assertRaises(ResponseBodyCaptureError):
                    normalizer.normalize_response_body(
                        request_id="r1",
                        body=html,
                        base64_encoded=False,
                        params={"requestId": "r1", "timestamp": 1.2},
                        target_id="t1",
                    )
                self.assertEqual(normalizer.artifacts.count, before)

    def test_goods_artifacts_are_content_addressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            html = _goods_html(_goods_row_html(product=42))
            refs = []
            for index in (1, 2):
                request_id = f"r{index}"
                self._prime(
                    normalizer,
                    request_id,
                    "https://bizmania.ru/units/shop/?id=33670&tab=goods",
                )
                event = normalizer.normalize_response_body(
                    request_id=request_id,
                    body=html,
                    base64_encoded=False,
                    params={"requestId": request_id, "timestamp": 1.2},
                    target_id="t1",
                )
                assert event is not None
                refs.append(event["response_body_ref"])
            self.assertEqual(refs[0], refs[1])

    def test_roster_event_fingerprint_ignores_the_kind_field(self):
        url = "https://bizmania.ru/company/?id=13393&tab=units"
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            self._prime(normalizer, "r1", url)
            event = normalizer.normalize_response_body(
                request_id="r1",
                body=(
                    "<html><body><div>Предприятия</div>"
                    "<table><tr><th>Город</th><th>Предприятие</th>"
                    "<th>Уровень</th></tr>"
                    "<tr><td>Анкара</td><td>Детский магазин #33670</td>"
                    "<td>1</td></tr></table></body></html>"
                ),
                base64_encoded=False,
                params={"requestId": "r1", "timestamp": 1.2},
                target_id="t1",
            )
            assert event is not None
            self.assertNotIn("response_body_kind", event)
            legacy_semantic = {
                key: event.get(key) for key in _LEGACY_FINGERPRINT_KEYS
            }
            self.assertEqual(event["fingerprint"], canonical_sha256(legacy_semantic))

    def test_goods_event_fingerprint_binds_the_kind_field(self):
        url = "https://bizmania.ru/units/shop/?id=33670&tab=goods"
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            self._prime(normalizer, "r1", url)
            event = normalizer.normalize_response_body(
                request_id="r1",
                body=_goods_html(_goods_row_html(product=42)),
                base64_encoded=False,
                params={"requestId": "r1", "timestamp": 1.2},
                target_id="t1",
            )
            assert event is not None
            self.assertNotEqual(
                event["fingerprint"],
                canonical_sha256(
                    {key: event.get(key) for key in _LEGACY_FINGERPRINT_KEYS}
                ),
            )
            semantic = {key: event.get(key) for key in _LEGACY_FINGERPRINT_KEYS}
            semantic["response_body_kind"] = event["response_body_kind"]
            self.assertEqual(event["fingerprint"], canonical_sha256(semantic))

    def test_goods_artifact_never_contains_raw_markup_or_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            normalizer = self._normalizer(Path(tmp))
            self._prime(
                normalizer,
                "r1",
                "https://bizmania.ru/units/shop/?id=33670&tab=goods",
            )
            event = normalizer.normalize_response_body(
                request_id="r1",
                body=_goods_html(
                    _goods_row_html(
                        product=42,
                        label_canary="SYNTHETIC_LABEL_CANARY",
                        attribute_canary="SYNTHETIC_ATTRIBUTE_CANARY",
                    )
                ),
                base64_encoded=False,
                params={"requestId": "r1", "timestamp": 1.2},
                target_id="t1",
            )
            assert event is not None
            payload = normalizer.artifacts.read_bytes(event["response_body_ref"])
            for forbidden in (
                b"SYNTHETIC_LABEL_CANARY",
                b"SYNTHETIC_ATTRIBUTE_CANARY",
                b"<table",
                b"purchaseQuantity",
                b"vendorPrice",
            ):
                self.assertNotIn(forbidden, payload)
            parsed = json.loads(payload.decode("utf-8"))
            self.assertEqual(
                set(parsed),
                {"schema", "contract_version", "unit_id", "coverage", "rows"},
            )


if __name__ == "__main__":
    unittest.main()
