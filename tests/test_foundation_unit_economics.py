from __future__ import annotations

import json
import unittest

from bizman.foundation.fingerprint import canonical_json_bytes
from bizman.foundation.unit_economics import (
    CONTRACT_VERSION,
    MAX_ROWS,
    ROW_FIELDS,
    SCHEMA,
    UnitEconomicsContractError,
    UnitEconomicsPage,
    UnitEconomicsRow,
    parse_unit_economics_payload,
    unit_economics_payload,
    unit_economics_semantic_fingerprint,
)


EXPECTED_ROW_FIELDS = (
    "product_numeric_id",
    "revenue",
    "profit",
    "stock_qty",
    "stock_quality",
    "our_price",
    "city_quality",
    "city_price",
    "sales_volume",
    "supply_qty",
    "supply_cost",
)
UNIT_ID = "33670"


def _row_dict(number: int, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "product_numeric_id": number,
        "revenue": 1000 + number,
        "profit": 100 + number,
        "stock_qty": 10 + number,
        "stock_quality": 3.5,
        "our_price": 150 + number,
        "city_quality": 4.25,
        "city_price": 160 + number,
        "sales_volume": number,
        "supply_qty": 7 + number,
        "supply_cost": 120 + number,
    }
    value.update(overrides)
    return value


def _row(number: int, **overrides: object) -> UnitEconomicsRow:
    return UnitEconomicsRow(**_row_dict(number, **overrides))


def _payload(
    *rows: dict[str, object],
    unit_id: str = UNIT_ID,
    schema: str = SCHEMA,
    contract_version: object = CONTRACT_VERSION,
    coverage: str = "observed",
) -> bytes:
    return canonical_json_bytes(
        {
            "schema": schema,
            "contract_version": contract_version,
            "unit_id": unit_id,
            "coverage": coverage,
            "rows": list(rows),
        }
    )


class UnitEconomicsContractTests(unittest.TestCase):
    def test_field_contract_is_exactly_frozen(self):
        self.assertEqual(ROW_FIELDS, EXPECTED_ROW_FIELDS)
        self.assertEqual(SCHEMA, "bizman.unit-economics.v1")
        self.assertEqual(CONTRACT_VERSION, 1)
        self.assertNotIn("vendor_cost", ROW_FIELDS)

    def test_round_trip_is_canonical_and_preserves_values(self):
        raw = _payload(_row_dict(11), _row_dict(12))
        page = parse_unit_economics_payload(raw)
        self.assertEqual(page.unit_id, UNIT_ID)
        self.assertEqual(page.coverage, "observed")
        self.assertEqual(
            [row.product_numeric_id for row in page.rows],
            [11, 12],
        )
        self.assertEqual(page.rows[0].revenue, 1011)
        self.assertEqual(page.rows[0].supply_cost, 131)
        self.assertEqual(page.rows[0].stock_quality, 3.5)
        self.assertEqual(unit_economics_payload(page), raw)

    def test_rows_must_be_unique_positive_int64_products(self):
        for number in (0, -1, 1 << 63, True):
            with self.subTest(number=number):
                with self.assertRaises(UnitEconomicsContractError):
                    UnitEconomicsRow(**_row_dict(1, product_numeric_id=number))

        with self.assertRaisesRegex(
            UnitEconomicsContractError,
            "duplicate product_numeric_id",
        ):
            UnitEconomicsPage(
                unit_id=UNIT_ID,
                coverage="observed",
                rows=(_row(11), _row(11)),
            )

        with self.assertRaisesRegex(
            UnitEconomicsContractError,
            "duplicate product_numeric_id",
        ):
            parse_unit_economics_payload(_payload(_row_dict(11), _row_dict(11)))

    def test_integer_fields_reject_floats_booleans_and_negatives(self):
        for field_name in (
            "revenue",
            "profit",
            "stock_qty",
            "our_price",
            "city_price",
            "sales_volume",
            "supply_qty",
            "supply_cost",
        ):
            for invalid in (-1, 2.5, True):
                with self.subTest(field=field_name, invalid=invalid):
                    with self.assertRaises(UnitEconomicsContractError):
                        UnitEconomicsRow(
                            **_row_dict(11, **{field_name: invalid})
                        )
                    with self.assertRaises(UnitEconomicsContractError):
                        parse_unit_economics_payload(
                            _payload(_row_dict(11, **{field_name: invalid}))
                        )

    def test_integer_fields_reject_overflow(self):
        for field_name in ("revenue", "stock_qty", "supply_cost"):
            with self.subTest(field=field_name):
                with self.assertRaises(UnitEconomicsContractError):
                    parse_unit_economics_payload(
                        _payload(_row_dict(11, **{field_name: 1 << 63}))
                    )

    def test_quality_fields_reject_negative_and_non_numeric(self):
        for invalid in (-0.5, True, "3.5"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(UnitEconomicsContractError):
                    parse_unit_economics_payload(
                        _payload(_row_dict(11, stock_quality=invalid))
                    )

    def test_validator_rejects_duplicate_json_keys(self):
        raw = _payload(_row_dict(11))
        duplicated = raw.replace(
            b'"revenue":',
            b'"revenue":0,"revenue":',
            1,
        )
        with self.assertRaisesRegex(
            UnitEconomicsContractError,
            "duplicate JSON key",
        ):
            parse_unit_economics_payload(duplicated)

    def test_validator_rejects_missing_and_extra_fields(self):
        base = json.loads(_payload(_row_dict(11)).decode("utf-8"))

        missing_row = json.loads(json.dumps(base))
        del missing_row["rows"][0]["profit"]
        with self.assertRaises(UnitEconomicsContractError):
            parse_unit_economics_payload(canonical_json_bytes(missing_row))

        extra_row = json.loads(json.dumps(base))
        extra_row["rows"][0]["vendor_cost"] = 0
        with self.assertRaises(UnitEconomicsContractError):
            parse_unit_economics_payload(canonical_json_bytes(extra_row))

        extra_top = json.loads(json.dumps(base))
        extra_top["source"] = "cdp.network"
        with self.assertRaises(UnitEconomicsContractError):
            parse_unit_economics_payload(canonical_json_bytes(extra_top))

        missing_top = json.loads(json.dumps(base))
        del missing_top["coverage"]
        with self.assertRaises(UnitEconomicsContractError):
            parse_unit_economics_payload(canonical_json_bytes(missing_top))

    def test_validator_rejects_schema_version_and_coverage_mismatch(self):
        cases = (
            {"schema": "bizman.unit-economics.v2"},
            {"contract_version": 2},
            {"contract_version": True},
            {"coverage": "complete"},
            {"unit_id": "0"},
            {"unit_id": "01"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(UnitEconomicsContractError):
                    parse_unit_economics_payload(_payload(_row_dict(11), **overrides))

    def test_validator_rejects_non_canonical_or_trailing_bytes(self):
        raw = _payload(_row_dict(11))
        non_canonical = raw.replace(b"{", b"{ ", 1)
        with self.assertRaisesRegex(
            UnitEconomicsContractError,
            "canonically encoded",
        ):
            parse_unit_economics_payload(non_canonical)

        with self.assertRaises(UnitEconomicsContractError):
            parse_unit_economics_payload(raw + b"{}")

        with self.assertRaises(UnitEconomicsContractError):
            parse_unit_economics_payload(raw + b" ")

        with self.assertRaises(UnitEconomicsContractError):
            parse_unit_economics_payload(b"\xff\xfe")

        with self.assertRaises(UnitEconomicsContractError):
            parse_unit_economics_payload(b"[]")

    def test_row_limit_is_a_contract_limit(self):
        rows = [_row_dict(number) for number in range(1, MAX_ROWS + 1)]
        page = parse_unit_economics_payload(_payload(*rows))
        self.assertEqual(len(page.rows), MAX_ROWS)

        too_many = [_row_dict(number) for number in range(1, MAX_ROWS + 2)]
        with self.assertRaisesRegex(UnitEconomicsContractError, "row limit"):
            parse_unit_economics_payload(_payload(*too_many))

    def test_semantic_fingerprint_is_stable_hex(self):
        first = unit_economics_semantic_fingerprint()
        self.assertEqual(first, unit_economics_semantic_fingerprint())
        self.assertRegex(first, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
