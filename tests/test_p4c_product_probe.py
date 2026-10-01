from __future__ import annotations

import unittest

from bizman.collector.product_probe import (
    ProductProbeCandidateOverflowError,
    ProductProbeRoute,
    ProductProbeRouteError,
    inspect_product_identity_candidates,
)


class ProductProbeRouteTests(unittest.TestCase):
    def test_accepts_exact_shop_goods_route(self) -> None:
        route = ProductProbeRoute.parse(
            "https://bizmania.ru/units/shop/?id=33670&tab=goods",
            approved_hosts=("bizmania.ru",),
        )
        self.assertEqual(route.unit_id, 33670)
        self.assertEqual(route.surface, "shop.goods")
        self.assertEqual(route.normalized_path, "/units/shop/")
        self.assertEqual(route.normalized_query, (("id", "33670"), ("tab", "goods")))

    def test_rejects_noncanonical_or_ambiguous_route_inputs(self) -> None:
        rejected = (
            "http://bizmania.ru/units/shop/?id=33670&tab=goods",
            "https://user@bizmania.ru/units/shop/?id=33670&tab=goods",
            "https://bizmania.ru/units/shop/?id=33670&tab=goods#fragment",
            "https://bizmania.ru/units/shop/?id=33670&id=33671&tab=goods",
            "https://bizmania.ru/units/shop/?id=33670&tab=goods&extra=1",
            "https://bizmania.ru/units/shop/?id=0&tab=goods",
            "https://bizmania.ru/units/shop/?id=+33670&tab=goods",
            "https://bizmania.ru/units/shop/?id=３３６７０&tab=goods",
            "https://bizmania.ru/units/shop/?%69d=33670&tab=goods",
            "https://bizmania.ru/units/shop/?id=33670&t%61b=goods",
            "https://bizmania.ru/units/service/?id=33670&tab=goods",
            "https://bizmania.ru/units/shop/?id=33670&tab=supply",
            " https://bizmania.ru/units/shop/?id=33670&tab=goods",
            "\thttps://bizmania.ru/units/shop/?id=33670&tab=goods",
            "https://bizmania.ru/units/shop/?id=33670&tab=goods\n",
        )
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaises(ProductProbeRouteError):
                    ProductProbeRoute.parse(url, approved_hosts=("bizmania.ru",))

    def test_rejects_unapproved_or_subdomain_host(self) -> None:
        for url in (
            "https://evil.example/units/shop/?id=33670&tab=goods",
            "https://www.bizmania.ru/units/shop/?id=33670&tab=goods",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ProductProbeRouteError):
                    ProductProbeRoute.parse(url, approved_hosts=("bizmania.ru",))


class ProductIdentityInspectionTests(unittest.TestCase):
    def test_reports_structural_candidates_without_promoting_labels_or_ordinals(self) -> None:
        html = b"""
        <table>
          <tr class="product-row">
            <td><a href="/products/?id=42">Antiseptic</a></td>
            <td><input type="hidden" name="csrf" value="TOP_SECRET"></td>
          </tr>
          <tr class="product-row">
            <td><a href="/products/?id=77">Healing cream</a></td>
          </tr>
        </table>
        """
        report = inspect_product_identity_candidates(html, max_candidates=16)
        self.assertEqual(
            [(item.tag, item.attribute, item.numeric_query_ids) for item in report],
            [
                ("a", "href", (42,)),
                ("a", "href", (77,)),
            ],
        )
        self.assertNotIn("TOP_SECRET", repr(report))
        self.assertNotIn("Antiseptic", repr(report))
        self.assertNotIn("Healing cream", repr(report))

    def test_inactive_or_hidden_subtrees_are_not_candidates(self) -> None:
        html = b"""
        <template><a href="/products/?id=41">template</a></template>
        <div hidden><a href="/products/?id=42">hidden</a></div>
        <div aria-hidden="true"><a href="/products/?id=43">aria</a></div>
        <svg><a href="/products/?id=44">svg</a></svg>
        <a href="/products/?%69d=45">encoded key</a>
        <a href="/products/?id=46&extra=1">extra query</a>
        """
        self.assertEqual(inspect_product_identity_candidates(html), ())

    def test_hidden_form_values_and_arbitrary_data_attributes_are_not_candidates(self) -> None:
        html = b"""
        <div data-product-id="42">
          <input type="hidden" name="product_id" value="42">
          <span id="77">Visible label</span>
        </div>
        """
        self.assertEqual(inspect_product_identity_candidates(html), ())

    def test_duplicate_attributes_fail_closed_for_candidate_subtrees(self) -> None:
        html = b"""
        <a href="/products/?id=41" href="/products/?id=42">ambiguous href</a>
        <div style="display:none" style="">
          <a href="/products/?id=43">ambiguous style</a>
        </div>
        <a href="/products/?id=44">visible</a>
        """
        report = inspect_product_identity_candidates(html)
        self.assertEqual(
            [item.numeric_query_ids for item in report],
            [(44,)],
        )

    def test_malformed_hidden_nesting_does_not_release_suppression_early(self) -> None:
        html = b"""
        <div hidden>
          <span><a href="/products/?id=41">hidden</a>
        </div>
        <a href="/products/?id=42">visible</a>
        """
        report = inspect_product_identity_candidates(html)
        self.assertEqual(
            [item.numeric_query_ids for item in report],
            [(42,)],
        )

    def test_hidden_void_element_does_not_suppress_following_candidate(self) -> None:
        html = (
            b'<input type="hidden" value="secret">'
            b'<a href="/products/?id=47">x</a>'
        )
        report = inspect_product_identity_candidates(html)
        self.assertEqual(report[0].numeric_query_ids, (47,))

    def test_candidate_overflow_fails_closed_instead_of_truncating(self) -> None:
        html = (
            "<div>"
            + "".join(f'<a href="/products/?id={index + 1}">x</a>' for index in range(20))
            + "</div>"
        ).encode()
        with self.assertRaises(ProductProbeCandidateOverflowError):
            inspect_product_identity_candidates(html, max_candidates=4)


if __name__ == "__main__":
    unittest.main()
