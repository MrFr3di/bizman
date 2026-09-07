from __future__ import annotations

import unittest

from tools.bizman_detector.model import DiffFact, MatchState, RuleDescriptor
from tools.bizman_detector.rules import RULE_DESCRIPTORS, RuleEngine


def _fact(
    kind: str,
    *,
    state: MatchState = MatchState.NOVEL,
    path: str = "/x",
    delta=(),
    evidence=("event-1",),
) -> DiffFact:
    return DiffFact(
        state=state,
        kind=kind,
        subject=(("path", path),),
        delta=delta,
        evidence_event_ids=evidence,
    )


class RuleCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RuleEngine()

    def test_http_fixture_has_exact_rule_id_set(self):
        findings = self.engine.apply(
            (
                _fact("endpoint.new"),
                _fact("endpoint.method_added", delta=(("method", "PATCH"),)),
                _fact("endpoint.query_key_added", delta=(("added_keys", ("q",)),)),
                _fact("endpoint.status_added", delta=(("status", 418),)),
            )
        )
        self.assertEqual(
            {finding.rule_id for finding in findings},
            {"BM-HTTP-001", "BM-HTTP-002", "BM-HTTP-003", "BM-HTTP-004"},
        )

    def test_form_operation_relation_fixtures_have_exact_rule_id_sets(self):
        forms = self.engine.apply(
            (
                _fact("form.signature_new"),
                _fact("form.field_added", delta=(("added_fields", ("x",)),)),
            )
        )
        self.assertEqual(
            {finding.rule_id for finding in forms},
            {"BM-FORM-001", "BM-FORM-002"},
        )

        operations = self.engine.apply(
            (
                _fact("operation.new_signature"),
                _fact("operation.query_key_added", delta=(("added_keys", ("q",)),)),
                _fact("operation.body_key_added", delta=(("added_keys", ("b",)),)),
            )
        )
        self.assertEqual(
            {finding.rule_id for finding in operations},
            {"BM-OP-001", "BM-OP-002", "BM-OP-003"},
        )

        relations = self.engine.apply(
            (
                _fact("action_http.request_family_new"),
                _fact("action_http.path_conflict", state=MatchState.CONFLICT),
            )
        )
        self.assertEqual(
            {finding.rule_id for finding in relations},
            {"BM-REL-001", "BM-REL-002"},
        )

    def test_indeterminate_and_known_facts_never_promote(self):
        findings = self.engine.apply(
            (
                _fact("operation.indeterminate", state=MatchState.INDETERMINATE),
                _fact("endpoint.known", state=MatchState.KNOWN),
                _fact("relation.temporal_only", state=MatchState.INDETERMINATE),
            )
        )
        self.assertEqual(findings, ())

    def test_unmapped_promotable_fact_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "no registered rule"):
            self.engine.apply((_fact("future.promotable_kind"),))


class FindingIdentityTests(unittest.TestCase):
    def test_change_identity_excludes_evidence_ids_and_merges_duplicates(self):
        engine = RuleEngine()
        first = _fact("endpoint.new", evidence=("event-b",))
        second = _fact("endpoint.new", evidence=("event-a", "event-b"))
        findings = engine.apply((first, second))

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence_event_ids, ("event-a", "event-b"))
        single = engine.apply((first,))[0]
        self.assertEqual(findings[0].change_id, single.change_id)
        self.assertRegex(findings[0].change_id, r"^chg\.[0-9a-f]{64}$")

    def test_change_identity_changes_with_rule_version_subject_or_delta(self):
        fact = _fact("endpoint.new")
        current = RuleEngine().apply((fact,))[0]

        versioned_descriptors = tuple(
            RuleDescriptor(item.rule_id, 2 if item.rule_id == "BM-HTTP-001" else item.version, item.kind)
            for item in RULE_DESCRIPTORS
        )
        versioned = RuleEngine(versioned_descriptors).apply((fact,))[0]
        subject_changed = RuleEngine().apply((_fact("endpoint.new", path="/y"),))[0]
        delta_changed = RuleEngine().apply(
            (_fact("endpoint.new", delta=(("shape", "different"),)),)
        )[0]

        self.assertNotEqual(current.change_id, versioned.change_id)
        self.assertNotEqual(current.change_id, subject_changed.change_id)
        self.assertNotEqual(current.change_id, delta_changed.change_id)

    def test_change_identity_includes_normalization_and_extraction_versions(self):
        fact = _fact("endpoint.new")
        base = RuleEngine(normalization_version=1, extraction_version=1).apply((fact,))[0]
        normalization = RuleEngine(normalization_version=2, extraction_version=1).apply((fact,))[0]
        extraction = RuleEngine(normalization_version=1, extraction_version=2).apply((fact,))[0]
        self.assertNotEqual(base.change_id, normalization.change_id)
        self.assertNotEqual(base.change_id, extraction.change_id)


if __name__ == "__main__":
    unittest.main()
