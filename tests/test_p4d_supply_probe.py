from __future__ import annotations

import unittest

from bizman.collector.supply_probe import (
    SupplyProbeCandidateOverflowError,
    SupplyProbeRoute,
    SupplyProbeRouteError,
    SupplyProbeStructureError,
    inspect_supply_structure,
)


class SupplyProbeRouteTests(unittest.TestCase):
    def test_accepts_exact_shop_supply_route(self) -> None:
        route = SupplyProbeRoute.parse(
            "https://bizmania.ru/units/shop/?id=33676&tab=supply",
            approved_hosts=("bizmania.ru",),
        )
        self.assertEqual(route.unit_id, 33676)
        self.assertEqual(route.surface, "shop.supply")
        self.assertEqual(route.normalized_path, "/units/shop/")
        self.assertEqual(
            route.normalized_query,
            (("id", "33676"), ("tab", "supply")),
        )

    def test_rejects_noncanonical_or_unapproved_routes(self) -> None:
        rejected = (
            "http://bizmania.ru/units/shop/?id=33676&tab=supply",
            "https://www.bizmania.ru/units/shop/?id=33676&tab=supply",
            "https://user@bizmania.ru/units/shop/?id=33676&tab=supply",
            "https://bizmania.ru:443/units/shop/?id=33676&tab=supply",
            "https://bizmania.ru/units/shop/?id=33676&tab=supply#x",
            "https://bizmania.ru/units/shop/?id=33676&tab=goods",
            "https://bizmania.ru/units/shop/?id=33676&tab=supply&product=1",
            "https://bizmania.ru/units/shop/?id=33676&id=1&tab=supply",
            "https://bizmania.ru/units/shop/?id=0&tab=supply",
            "https://bizmania.ru/units/shop/?id=+33676&tab=supply",
            "https://bizmania.ru/units/shop/?%69d=33676&tab=supply",
            " https://bizmania.ru/units/shop/?id=33676&tab=supply",
        )
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaises(SupplyProbeRouteError):
                    SupplyProbeRoute.parse(
                        url,
                        approved_hosts=("bizmania.ru",),
                    )


class SupplyStructureInspectionTests(unittest.TestCase):
    def test_reports_only_bounded_structural_metadata(self) -> None:
        html = b"""
        <form action="/units/shop/" method="post">
          <table>
            <tr>
              <td>
                <a href="/units/vendor/?id=777&product=418&token=TOP_SECRET">
                  SECRET_VENDOR_LABEL
                </a>
              </td>
              <td>
                <input type="hidden" name="vendor[0]" value="777">
                <input name="vendorQuantity[0]" value="25">
                <input name="vendorPrice[0]" value="12345">
              </td>
            </tr>
          </table>
        </form>
        """
        report = inspect_supply_structure(html)
        self.assertEqual(len(report.links), 1)
        link = report.links[0]
        self.assertEqual(link.path, "/units/vendor/")
        self.assertEqual(link.query_keys, ("id", "product", "token"))
        self.assertEqual(
            link.numeric_query_fields,
            (("id", 777), ("product", 418)),
        )
        self.assertTrue(link.in_row)
        self.assertTrue(link.in_form)
        self.assertEqual(link.row_slot, 0)
        self.assertEqual(link.form_slot, 0)

        self.assertEqual(
            [(item.name, item.index) for item in report.inputs],
            [
                ("vendor", 0),
                ("vendorQuantity", 0),
                ("vendorPrice", 0),
            ],
        )
        self.assertTrue(all(item.in_row for item in report.inputs))
        self.assertTrue(all(item.in_form for item in report.inputs))
        self.assertEqual({item.row_slot for item in report.inputs}, {0})
        self.assertEqual({item.form_slot for item in report.inputs}, {0})
        serialized = repr(report)
        self.assertNotIn("TOP_SECRET", serialized)
        self.assertNotIn("SECRET_VENDOR_LABEL", serialized)
        self.assertNotIn("12345", serialized)

    def test_hidden_and_active_content_subtrees_are_ignored(self) -> None:
        html = b"""
        <script><a href="/units/vendor/?id=41">x</a></script>
        <template><input name="vendor[1]" value="42"></template>
        <div hidden><a href="/units/vendor/?id=43">hidden</a></div>
        <div aria-hidden="true"><input name="vendor[2]" value="44"></div>
        <div style="display:none"><input name="vendor[3]" value="45"></div>
        <a href="/units/vendor/?id=46">visible</a>
        <input type="hidden" name="vendor[4]" value="TOP_SECRET">
        """
        report = inspect_supply_structure(html)
        self.assertEqual(
            [item.numeric_query_fields for item in report.links],
            [(("id", 46),)],
        )
        self.assertEqual(
            [(item.name, item.index) for item in report.inputs],
            [("vendor", 4)],
        )
        self.assertNotIn("TOP_SECRET", repr(report))

    def test_external_fragment_and_value_only_links_are_not_reported(self) -> None:
        html = b"""
        <a href="https://evil.example/?id=41">external</a>
        <a href="#secret">fragment</a>
        <a href="/plain/path">no query</a>
        <a href="/units/vendor/?id=43&note=TOP_SECRET">safe shape</a>
        """
        report = inspect_supply_structure(html)
        self.assertEqual(len(report.links), 1)
        self.assertEqual(report.links[0].path, "/units/vendor/")
        self.assertEqual(report.links[0].query_keys, ("id", "note"))
        self.assertEqual(
            report.links[0].numeric_query_fields,
            (("id", 43),),
        )
        self.assertNotIn("TOP_SECRET", repr(report))

    def test_ambiguous_first_party_link_shapes_fail_closed(self) -> None:
        ambiguous = (
            b'<a href="/units/vendor/?%69d=42">encoded query</a>',
            b'<a href="/units/%76endor/?id=42">encoded path</a>',
            b'<a href="/units/../user/?id=42">dot segment</a>',
            b'<a href="/units\\vendor/?id=42">backslash</a>',
            b'<a href="vendor/?id=42">path relative</a>',
        )
        for html in ambiguous:
            with self.subTest(html=html):
                with self.assertRaises(SupplyProbeStructureError):
                    inspect_supply_structure(html)

    def test_duplicate_attributes_and_duplicate_query_keys_fail_closed(self) -> None:
        for html in (
            b'<input name="vendor[0]" name="vendor[1]" value="SECRET">',
            b'<a href="/units/vendor/?id=41" href="/units/vendor/?id=42">x</a>',
            b'<a href="/units/vendor/?id=41&id=42">x</a>',
            b'<tr class="a" class="b"><input name="vendor[0]"></tr>',
        ):
            with self.subTest(html=html):
                with self.assertRaises(SupplyProbeStructureError):
                    inspect_supply_structure(html)

    def test_malformed_hidden_nesting_does_not_release_suppression(self) -> None:
        html = b"""
        <div hidden>
          <span><input name="vendor[1]" value="SECRET">
        </div>
        <input name="vendor[2]" value="SAFE_BUT_NOT_RETAINED">
        """
        report = inspect_supply_structure(html)
        self.assertEqual(
            [(item.name, item.index) for item in report.inputs],
            [("vendor", 2)],
        )

    def test_row_slots_group_candidates_without_becoming_identity(self) -> None:
        html = b"""
        <form action="/units/shop/" method="post">
          <table>
            <tr>
              <td><a href="/units/vendor/?id=701&product=401">one</a></td>
              <td><input name="vendor[0]" value="SECRET_ONE"></td>
            <tr>
              <td><a href="/units/vendor/?id=702&product=402">two</a></td>
              <td><input name="vendor[1]" value="SECRET_TWO"></td>
            </tr>
          </table>
        </form>
        """
        report = inspect_supply_structure(html)
        self.assertEqual(
            [(item.row_slot, item.form_slot) for item in report.links],
            [(0, 0), (1, 0)],
        )
        self.assertEqual(
            [(item.row_slot, item.form_slot) for item in report.inputs],
            [(0, 0), (1, 0)],
        )
        self.assertNotIn("SECRET_ONE", repr(report))
        self.assertNotIn("SECRET_TWO", repr(report))

    def test_malformed_candidate_query_fails_closed(self) -> None:
        with self.assertRaises(SupplyProbeStructureError):
            inspect_supply_structure(
                b'<a href="/units/vendor/?id=41&&product=42">x</a>'
            )

    def test_noncanonical_input_index_fails_closed(self) -> None:
        for html in (
            b'<input name="vendor[01]" value="SECRET">',
            b'<input name="vendor[000]" value="SECRET">',
        ):
            with self.subTest(html=html):
                with self.assertRaises(SupplyProbeStructureError):
                    inspect_supply_structure(html)

    def test_incomplete_structural_container_fails_closed(self) -> None:
        with self.assertRaises(SupplyProbeStructureError):
            inspect_supply_structure(
                b'<table><tr><td><a href="/units/vendor/?id=41">x</a>'
            )

    def test_nested_forms_fail_closed(self) -> None:
        with self.assertRaises(SupplyProbeStructureError):
            inspect_supply_structure(
                b'<form><form><input name="vendor[0]"></form></form>'
            )

    def test_unsupported_input_name_shape_fails_closed(self) -> None:
        with self.assertRaises(SupplyProbeStructureError):
            inspect_supply_structure(
                b'<input name="vendor[item]" value="SECRET">'
            )

    def test_self_closing_optional_cell_does_not_corrupt_context(self) -> None:
        report = inspect_supply_structure(
            b'<table><tr><td/><td><a href="/units/vendor/?id=41">x</a></td></tr></table>'
        )
        self.assertEqual(len(report.links), 1)
        self.assertEqual(report.links[0].row_slot, 0)
        self.assertIsNone(report.links[0].form_slot)

    def test_optional_table_closure_cannot_cross_open_form(self) -> None:
        with self.assertRaises(SupplyProbeStructureError):
            inspect_supply_structure(
                b'<table><tr><td><form><input name="vendor[0]"><td>'
                b'<a href="/units/vendor/?id=41">x</a></td></tr></table></form>'
            )

    def test_disabled_controls_and_subtrees_are_ignored(self) -> None:
        html = b"""
        <fieldset disabled>
          <input name="vendor[1]" value="SECRET_FIELDSET">
        </fieldset>
        <input disabled name="vendor[2]" value="SECRET_INPUT">
        <input name="vendor[3]" value="SAFE_BUT_NOT_RETAINED">
        """
        report = inspect_supply_structure(html)
        self.assertEqual(
            [(item.name, item.index) for item in report.inputs],
            [("vendor", 3)],
        )
        self.assertNotIn("SECRET_", repr(report))

    def test_open_depth_is_bounded(self) -> None:
        html = ("<div>" * 257 + "</div>" * 257).encode()
        with self.assertRaises(SupplyProbeCandidateOverflowError):
            inspect_supply_structure(html)

    def test_incomplete_suppressed_subtree_fails_closed(self) -> None:
        for html in (
            b'<div hidden><input name="vendor[0]" value="SECRET">',
            b'<script><a href="/units/vendor/?id=41">x</a>',
        ):
            with self.subTest(html=html):
                with self.assertRaises(SupplyProbeStructureError):
                    inspect_supply_structure(html)

    def test_candidate_overflow_fails_instead_of_truncating(self) -> None:
        links = "".join(
            f'<a href="/units/vendor/?id={index + 1}">x</a>'
            for index in range(4)
        ).encode()
        with self.assertRaises(SupplyProbeCandidateOverflowError):
            inspect_supply_structure(links, max_links=3)

        inputs = "".join(
            f'<input name="vendor[{index}]" value="SECRET_{index}">'
            for index in range(4)
        ).encode()
        with self.assertRaises(SupplyProbeCandidateOverflowError):
            inspect_supply_structure(inputs, max_inputs=3)


if __name__ == "__main__":
    unittest.main()
