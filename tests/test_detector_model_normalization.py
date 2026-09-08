import unittest

from tools.bizman_detector.model import MatchState
from tools.bizman_detector.normalization import (
    AmbiguousPathError,
    PathMatcher,
    normalize_field_name,
    normalize_key_set,
    normalize_method,
    normalize_origin_relative_path,
)
from tools.bizman_foundation.redaction import RedactionPolicy


class DetectorSemanticVersionTests(unittest.TestCase):
    def test_match_states_are_explicit(self):
        self.assertEqual(
            {item.value for item in MatchState},
            {"known", "novel", "indeterminate", "conflict"},
        )


class DetectorKeyNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RedactionPolicy.default()

    def test_ascii_indexes_are_normalized(self):
        self.assertEqual(
            normalize_field_name(" product[17] ", self.policy),
            "product[n]",
        )
        self.assertEqual(
            normalize_field_name("selected[397][2]", self.policy),
            "selected[n][n]",
        )

    def test_ascii_numeric_key_becomes_dynamic_class(self):
        self.assertEqual(
            normalize_field_name("12345", self.policy),
            "{numeric-key}",
        )

    def test_non_ascii_decimal_digits_are_not_ascii_numeric_class(self):
        self.assertEqual(
            normalize_field_name("١٢٣", self.policy),
            "١٢٣",
        )

    def test_sensitive_name_is_removed_before_index_normalization(self):
        self.assertIsNone(normalize_field_name("clientSecret[17]", self.policy))
        self.assertIsNone(normalize_field_name(" accessToken ", self.policy))

    def test_key_set_is_redacted_deduplicated_and_sorted(self):
        self.assertEqual(
            normalize_key_set(
                [
                    "z",
                    "product[1]",
                    "product[9]",
                    "clientSecret",
                    "42",
                    "42",
                    "a",
                    None,
                ],
                self.policy,
            ),
            ("a", "product[n]", "z", "{numeric-key}"),
        )


class DetectorMethodNormalizationTests(unittest.TestCase):
    def test_method_is_uppercased(self):
        self.assertEqual(normalize_method("post"), "POST")
        self.assertEqual(normalize_method("m-search"), "M-SEARCH")

    def test_invalid_method_is_rejected(self):
        for value in (None, "", " GET ", "POST NOW", "MéTHOD", True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_method(value)


class DetectorPathNormalizationTests(unittest.TestCase):
    def test_origin_relative_path_preserves_percent_encoding(self):
        self.assertEqual(
            normalize_origin_relative_path("/topic/a%2Fb"),
            "/topic/a%2Fb",
        )

    def test_trailing_slash_is_preserved(self):
        self.assertEqual(normalize_origin_relative_path("/a"), "/a")
        self.assertEqual(normalize_origin_relative_path("/a/"), "/a/")

    def test_non_path_or_url_components_are_rejected(self):
        for value in (
            "https://bizmania.ru/a",
            "//bizmania.ru/a",
            "a/b",
            "/a?x=1",
            "/a#fragment",
            "",
            None,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_origin_relative_path(value)


class DetectorPathMatcherTests(unittest.TestCase):
    def test_exact_literal_outranks_template(self):
        matcher = PathMatcher(("/a/{id}", "/a/b"))
        match = matcher.match("/a/b")
        self.assertTrue(match.matched)
        self.assertEqual(match.path_pattern, "/a/b")
        self.assertTrue(match.exact)

    def test_template_placeholder_matches_exactly_one_segment(self):
        matcher = PathMatcher(("/a/{id}/c",))
        self.assertEqual(matcher.match("/a/123/c").path_pattern, "/a/{id}/c")
        self.assertFalse(matcher.match("/a/123/extra/c").matched)
        self.assertFalse(matcher.match("/a//c").matched)

    def test_more_literal_segments_win(self):
        matcher = PathMatcher(("/{x}/{y}/c", "/a/{id}/c"))
        self.assertEqual(
            matcher.match("/a/b/c").path_pattern,
            "/a/{id}/c",
        )

    def test_tied_distinct_templates_are_ambiguous(self):
        matcher = PathMatcher(("/a/{id}", "/{kind}/b"))
        with self.assertRaises(AmbiguousPathError):
            matcher.match("/a/b")

    def test_trailing_slash_does_not_match_implicitly(self):
        matcher = PathMatcher(("/user/check/message/r/{message_id}",))
        self.assertTrue(matcher.match("/user/check/message/r/313965").matched)
        self.assertFalse(matcher.match("/user/check/message/r/313965/").matched)

    def test_percent_encoded_slash_remains_one_segment(self):
        matcher = PathMatcher(("/topic/{topic}",))
        self.assertEqual(
            matcher.match("/topic/a%2Fb").path_pattern,
            "/topic/{topic}",
        )


if __name__ == "__main__":
    unittest.main()
