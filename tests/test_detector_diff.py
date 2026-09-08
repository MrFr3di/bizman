from __future__ import annotations

import unittest

from tools.bizman_detector.diff import SemanticDiff
from tools.bizman_detector.model import (
    ActionRequestFamily,
    EndpointFamily,
    EndpointMethodContract,
    EndpointVariant,
    FormObservation,
    FormSignature,
    HttpObservation,
    MatchState,
    ObservationSet,
    OperationSignature,
    RelationObservation,
    RuntimeContract,
)


def _http(
    *,
    method: str = "POST",
    literal_path: str = "/units/42/save",
    pattern: str | None = "/units/{id}/save",
    query: tuple[str, ...] = ("id",),
    status: int | None = 200,
    body: tuple[str, ...] | None = ("name", "price"),
    request_id: str = "req-1",
    response_id: str | None = "res-1",
) -> HttpObservation:
    return HttpObservation(
        method=method,
        literal_path=literal_path,
        canonical_path_pattern=pattern,
        query_keys=query,
        status=status,
        body_keys=body,
        request_event_id=request_id,
        response_event_id=response_id,
    )


def _contract(*, operations: tuple[OperationSignature, ...] | None = None) -> RuntimeContract:
    if operations is None:
        operations = (
            OperationSignature(
                method="POST",
                path_pattern="/units/{id}/save",
                query_keys=("id",),
                body_keys=("name", "price"),
                statuses=(200,),
            ),
        )
    return RuntimeContract(
        contract_schema_version=1,
        normalization_version=1,
        endpoints=(
            EndpointFamily(
                path_pattern="/units/{id}/save",
                methods=(
                    EndpointMethodContract(
                        method="POST",
                        variants=(
                            EndpointVariant(query_keys=("id",), status=200),
                            EndpointVariant(query_keys=("id", "mode"), status=302),
                        ),
                    ),
                    EndpointMethodContract(
                        method="GET",
                        variants=(EndpointVariant(query_keys=(), status=200),),
                    ),
                ),
            ),
        ),
        forms=(
            FormSignature(
                method="POST",
                action_path="/units/{id}/save",
                field_names=("name", "price"),
            ),
            FormSignature(
                method="POST",
                action_path="/units/{id}/save",
                field_names=("name", "price", "stock"),
            ),
        ),
        operations=operations,
        actions=(
            ActionRequestFamily(
                action_id="unit.save",
                method="POST",
                path_pattern="/units/{id}/save",
                field_names=("name", "price"),
                query_key_sets=(("id",),),
                statuses=(200,),
            ),
        ),
    )


def _kinds(facts):
    return {fact.kind for fact in facts}


class EndpointDiffTests(unittest.TestCase):
    def test_known_endpoint_and_query_subset_do_not_create_novelty(self):
        facts = SemanticDiff(_contract()).compare(
            ObservationSet(http=(_http(query=(), body=None),), forms=(), relations=())
        )
        endpoint = [fact for fact in facts if fact.kind.startswith("endpoint.")]
        self.assertTrue(any(fact.state is MatchState.KNOWN for fact in endpoint))
        self.assertFalse(any(fact.state is MatchState.NOVEL for fact in endpoint))

    def test_new_path_method_query_key_and_status_are_distinct_facts(self):
        diff = SemanticDiff(_contract())
        new_path = diff.compare(
            ObservationSet(
                http=(
                    _http(
                        literal_path="/brand-new",
                        pattern=None,
                        query=(),
                        body=None,
                        status=None,
                    ),
                ),
                forms=(),
                relations=(),
            )
        )
        self.assertIn("endpoint.new", _kinds(new_path))

        method = diff.compare(
            ObservationSet(
                http=(_http(method="PATCH", query=("id",), body=None),),
                forms=(),
                relations=(),
            )
        )
        self.assertIn("endpoint.method_added", _kinds(method))

        query = diff.compare(
            ObservationSet(
                http=(_http(query=("id", "novel"), body=None),),
                forms=(),
                relations=(),
            )
        )
        query_fact = next(f for f in query if f.kind == "endpoint.query_key_added")
        self.assertEqual(dict(query_fact.delta)["added_keys"], ("novel",))

        status = diff.compare(
            ObservationSet(
                http=(_http(status=418, body=None),), forms=(), relations=()
            )
        )
        status_fact = next(f for f in status if f.kind == "endpoint.status_added")
        self.assertEqual(dict(status_fact.delta)["status"], 418)


class FormDiffTests(unittest.TestCase):
    def test_known_subset_additive_fields_and_incompatible_signature(self):
        diff = SemanticDiff(_contract())
        known = diff.compare(
            ObservationSet(
                http=(),
                forms=(
                    FormObservation(
                        method="POST",
                        action_path="/units/{id}/save",
                        field_names=("name",),
                        action_event_id="a1",
                    ),
                ),
                relations=(),
            )
        )
        self.assertIn("form.known", _kinds(known))

        additive = diff.compare(
            ObservationSet(
                http=(),
                forms=(
                    FormObservation(
                        method="POST",
                        action_path="/units/{id}/save",
                        field_names=("name", "price", "stock", "warehouse"),
                        action_event_id="a2",
                    ),
                ),
                relations=(),
            )
        )
        fact = next(f for f in additive if f.kind == "form.field_added")
        self.assertEqual(dict(fact.delta)["added_fields"], ("warehouse",))

        incompatible = diff.compare(
            ObservationSet(
                http=(),
                forms=(
                    FormObservation(
                        method="POST",
                        action_path="/units/{id}/save",
                        field_names=("totally_new",),
                        action_event_id="a3",
                    ),
                ),
                relations=(),
            )
        )
        self.assertIn("form.signature_new", _kinds(incompatible))


class OperationDiffTests(unittest.TestCase):
    def test_exact_known_and_unknown_body_is_indeterminate(self):
        diff = SemanticDiff(_contract())
        known = diff.compare(
            ObservationSet(http=(_http(),), forms=(), relations=())
        )
        self.assertIn("operation.known", _kinds(known))

        unknown = diff.compare(
            ObservationSet(http=(_http(body=None),), forms=(), relations=())
        )
        op_facts = [fact for fact in unknown if fact.kind.startswith("operation.")]
        self.assertEqual([fact.kind for fact in op_facts], ["operation.indeterminate"])
        self.assertIs(op_facts[0].state, MatchState.INDETERMINATE)

    def test_unique_closest_signature_emits_additive_query_and_body_deltas(self):
        diff = SemanticDiff(_contract())
        facts = diff.compare(
            ObservationSet(
                http=(
                    _http(
                        query=("id", "scope"),
                        body=("name", "price", "stock"),
                    ),
                ),
                forms=(),
                relations=(),
            )
        )
        self.assertIn("operation.query_key_added", _kinds(facts))
        self.assertIn("operation.body_key_added", _kinds(facts))

    def test_ambiguous_closest_signature_degrades_to_generic_novelty(self):
        operations = (
            OperationSignature(
                method="POST",
                path_pattern="/units/{id}/save",
                query_keys=("id", "a"),
                body_keys=("x",),
                statuses=(200,),
            ),
            OperationSignature(
                method="POST",
                path_pattern="/units/{id}/save",
                query_keys=("id",),
                body_keys=("x", "b"),
                statuses=(200,),
            ),
        )
        facts = SemanticDiff(_contract(operations=operations)).compare(
            ObservationSet(
                http=(
                    _http(
                        query=("id", "a"),
                        body=("x", "b"),
                    ),
                ),
                forms=(),
                relations=(),
            )
        )
        op_novel = [f for f in facts if f.state is MatchState.NOVEL and f.kind.startswith("operation.")]
        self.assertEqual([f.kind for f in op_novel], ["operation.new_signature"])


class RelationDiffTests(unittest.TestCase):
    def test_unseen_write_family_and_path_conflict_are_separate(self):
        diff = SemanticDiff(_contract())
        unseen = RelationObservation(
            correlation_status="strong",
            action_event_id="a1",
            request_event_id="r1",
            action_method="POST",
            action_path="/units/{id}/archive",
            request_method="POST",
            request_literal_path="/units/42/archive",
            request_path_pattern=None,
        )
        facts = diff.compare(ObservationSet(http=(), forms=(), relations=(unseen,)))
        self.assertIn("action_http.request_family_new", _kinds(facts))

        conflict = RelationObservation(
            correlation_status="probable",
            action_event_id="a2",
            request_event_id="r2",
            action_method="POST",
            action_path="/different/form",
            request_method="POST",
            request_literal_path="/units/42/save",
            request_path_pattern="/units/{id}/save",
        )
        facts = diff.compare(ObservationSet(http=(), forms=(), relations=(conflict,)))
        conflict_fact = next(f for f in facts if f.kind == "action_http.path_conflict")
        self.assertIs(conflict_fact.state, MatchState.CONFLICT)

    def test_temporal_only_never_becomes_promotable_relation_novelty(self):
        relation = RelationObservation(
            correlation_status="temporal-only",
            action_event_id="a1",
            request_event_id="r1",
            action_method="POST",
            action_path="/new/write",
            request_method="POST",
            request_literal_path="/new/write",
            request_path_pattern=None,
        )
        facts = SemanticDiff(_contract()).compare(
            ObservationSet(http=(), forms=(), relations=(relation,))
        )
        self.assertNotIn("action_http.request_family_new", _kinds(facts))
        self.assertNotIn("action_http.path_conflict", _kinds(facts))


if __name__ == "__main__":
    unittest.main()
