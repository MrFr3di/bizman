from __future__ import annotations

from collections.abc import Iterable

from bizman.changes.model import (
    DiffFact,
    EndpointFamily,
    EndpointMethodContract,
    FormObservation,
    FormSignature,
    HttpObservation,
    MatchState,
    ObservationSet,
    OperationSignature,
    RelationObservation,
    RuntimeContract,
    SemanticFields,
    SemanticValue,
)


_SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_PROMOTABLE_CORRELATION = frozenset({"strong", "probable"})


class SemanticContractError(ValueError):
    """Observed semantics cannot be interpreted by the current detector contract."""


def _fields(**values: SemanticValue) -> SemanticFields:
    return tuple(sorted(values.items()))


def _evidence(*event_ids: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in event_ids if value))


def _fact(
    state: MatchState,
    kind: str,
    *,
    subject: SemanticFields,
    delta: SemanticFields = (),
    evidence: tuple[str, ...] = (),
) -> DiffFact:
    return DiffFact(
        state=state,
        kind=kind,
        subject=subject,
        delta=delta,
        evidence_event_ids=evidence,
    )


class SemanticDiff:
    """Pure semantic comparison of value-free observations against RuntimeContract."""

    def __init__(self, contract: RuntimeContract) -> None:
        if not isinstance(contract, RuntimeContract):
            raise TypeError("contract must be RuntimeContract")
        self.contract = contract
        self._endpoints = {family.path_pattern: family for family in contract.endpoints}
        self._forms: dict[tuple[str, str], tuple[FormSignature, ...]] = {}
        for signature in contract.forms:
            self._forms.setdefault((signature.method, signature.action_path), ())
            self._forms[(signature.method, signature.action_path)] += (signature,)
        self._operations: dict[tuple[str, str], tuple[OperationSignature, ...]] = {}
        for signature in contract.operations:
            key = (signature.method, signature.path_pattern)
            self._operations.setdefault(key, ())
            self._operations[key] += (signature,)
        self._known_action_request_families = frozenset(
            (family.method, family.path_pattern) for family in contract.actions
        )

    @staticmethod
    def _observation_path(observation: HttpObservation) -> str:
        return observation.canonical_path_pattern or observation.literal_path

    @staticmethod
    def _method_contract(
        family: EndpointFamily,
        method: str,
    ) -> EndpointMethodContract | None:
        return next((item for item in family.methods if item.method == method), None)

    def _endpoint_facts(self, observation: HttpObservation) -> list[DiffFact]:
        path = self._observation_path(observation)
        family = self._endpoints.get(path)
        request_evidence = _evidence(observation.request_event_id)
        if family is None:
            return [
                _fact(
                    MatchState.NOVEL,
                    "endpoint.new",
                    subject=_fields(path=path),
                    evidence=request_evidence,
                )
            ]

        method_contract = self._method_contract(family, observation.method)
        if method_contract is None:
            return [
                _fact(
                    MatchState.NOVEL,
                    "endpoint.method_added",
                    subject=_fields(path=path),
                    delta=_fields(method=observation.method),
                    evidence=request_evidence,
                )
            ]

        facts: list[DiffFact] = []
        known_query_keys = {
            key
            for variant in method_contract.variants
            for key in variant.query_keys
        }
        added_query_keys = tuple(
            sorted(set(observation.query_keys).difference(known_query_keys))
        )
        if added_query_keys:
            facts.append(
                _fact(
                    MatchState.NOVEL,
                    "endpoint.query_key_added",
                    subject=_fields(method=observation.method, path=path),
                    delta=_fields(added_keys=added_query_keys),
                    evidence=request_evidence,
                )
            )

        if observation.status is not None and observation.status not in method_contract.statuses:
            facts.append(
                _fact(
                    MatchState.NOVEL,
                    "endpoint.status_added",
                    subject=_fields(method=observation.method, path=path),
                    delta=_fields(status=observation.status),
                    evidence=_evidence(
                        observation.request_event_id,
                        observation.response_event_id,
                    ),
                )
            )

        if not facts:
            facts.append(
                _fact(
                    MatchState.KNOWN,
                    "endpoint.known",
                    subject=_fields(method=observation.method, path=path),
                    evidence=request_evidence,
                )
            )
        return facts

    def _form_facts(self, observation: FormObservation) -> list[DiffFact]:
        key = (observation.method, observation.action_path)
        signatures = self._forms.get(key, ())
        subject = _fields(method=observation.method, path=observation.action_path)
        evidence = _evidence(observation.action_event_id)
        observed = set(observation.field_names)
        if not signatures:
            return [
                _fact(
                    MatchState.NOVEL,
                    "form.signature_new",
                    subject=subject,
                    delta=_fields(fields=observation.field_names),
                    evidence=evidence,
                )
            ]

        known_sets = [set(signature.field_names) for signature in signatures]
        if any(observed.issubset(known) for known in known_sets):
            return [
                _fact(
                    MatchState.KNOWN,
                    "form.known",
                    subject=subject,
                    evidence=evidence,
                )
            ]

        additive = [known for known in known_sets if known.issubset(observed)]
        if additive:
            best_size = max(len(known) for known in additive)
            closest = [known for known in additive if len(known) == best_size]
            deltas = {
                tuple(sorted(observed.difference(known))) for known in closest
            }
            if len(deltas) == 1:
                added_fields = next(iter(deltas))
                return [
                    _fact(
                        MatchState.NOVEL,
                        "form.field_added",
                        subject=subject,
                        delta=_fields(added_fields=added_fields),
                        evidence=evidence,
                    )
                ]

        return [
            _fact(
                MatchState.NOVEL,
                "form.signature_new",
                subject=subject,
                delta=_fields(fields=observation.field_names),
                evidence=evidence,
            )
        ]

    def _operation_facts(self, observation: HttpObservation) -> list[DiffFact]:
        if observation.method in _SAFE_HTTP_METHODS:
            return []

        path = self._observation_path(observation)
        subject = _fields(method=observation.method, path=path)
        evidence = _evidence(observation.request_event_id)
        if observation.body_keys is None:
            return [
                _fact(
                    MatchState.INDETERMINATE,
                    "operation.indeterminate",
                    subject=subject,
                    delta=_fields(reason="body_keys_unknown"),
                    evidence=evidence,
                )
            ]

        signatures = self._operations.get((observation.method, path), ())
        if not signatures:
            return [
                _fact(
                    MatchState.NOVEL,
                    "operation.new_signature",
                    subject=subject,
                    delta=_fields(
                        body_keys=observation.body_keys,
                        query_keys=observation.query_keys,
                    ),
                    evidence=evidence,
                )
            ]

        observed_query = set(observation.query_keys)
        observed_body = set(observation.body_keys)
        for signature in signatures:
            if (
                tuple(signature.query_keys) == observation.query_keys
                and tuple(signature.body_keys) == observation.body_keys
            ):
                return [
                    _fact(
                        MatchState.KNOWN,
                        "operation.known",
                        subject=subject,
                        evidence=evidence,
                    )
                ]

        additive: list[tuple[int, OperationSignature, tuple[str, ...], tuple[str, ...]]] = []
        for signature in signatures:
            baseline_query = set(signature.query_keys)
            baseline_body = set(signature.body_keys)
            if not baseline_query.issubset(observed_query):
                continue
            if not baseline_body.issubset(observed_body):
                continue
            query_delta = tuple(sorted(observed_query.difference(baseline_query)))
            body_delta = tuple(sorted(observed_body.difference(baseline_body)))
            additive.append(
                (len(query_delta) + len(body_delta), signature, query_delta, body_delta)
            )

        if additive:
            minimum = min(item[0] for item in additive)
            closest = [item for item in additive if item[0] == minimum]
            if len(closest) == 1:
                _, _, query_delta, body_delta = closest[0]
                facts: list[DiffFact] = []
                if query_delta:
                    facts.append(
                        _fact(
                            MatchState.NOVEL,
                            "operation.query_key_added",
                            subject=subject,
                            delta=_fields(added_keys=query_delta),
                            evidence=evidence,
                        )
                    )
                if body_delta:
                    facts.append(
                        _fact(
                            MatchState.NOVEL,
                            "operation.body_key_added",
                            subject=subject,
                            delta=_fields(added_keys=body_delta),
                            evidence=evidence,
                        )
                    )
                if facts:
                    return facts

        return [
            _fact(
                MatchState.NOVEL,
                "operation.new_signature",
                subject=subject,
                delta=_fields(
                    body_keys=observation.body_keys,
                    query_keys=observation.query_keys,
                ),
                evidence=evidence,
            )
        ]

    def _relation_facts(self, observation: RelationObservation) -> list[DiffFact]:
        request_path = observation.request_path_pattern or observation.request_literal_path
        evidence = _evidence(observation.action_event_id, observation.request_event_id)
        subject = _fields(
            action_method=observation.action_method,
            action_path=observation.action_path,
            request_method=observation.request_method,
            request_path=request_path,
        )
        if observation.correlation_status == "temporal-only":
            return [
                _fact(
                    MatchState.INDETERMINATE,
                    "relation.temporal_only",
                    subject=subject,
                    evidence=evidence,
                )
            ]
        if observation.correlation_status not in _PROMOTABLE_CORRELATION:
            raise SemanticContractError(
                f"unsupported correlation status for semantic diff: "
                f"{observation.correlation_status!r}"
            )

        facts: list[DiffFact] = []
        request_family = (observation.request_method, request_path)
        if (
            observation.request_method not in _SAFE_HTTP_METHODS
            and request_family not in self._known_action_request_families
        ):
            facts.append(
                _fact(
                    MatchState.NOVEL,
                    "action_http.request_family_new",
                    subject=_fields(
                        method=observation.request_method,
                        path=request_path,
                    ),
                    delta=_fields(correlation=observation.correlation_status),
                    evidence=evidence,
                )
            )

        if (
            observation.action_path is not None
            and observation.action_path != request_path
        ):
            facts.append(
                _fact(
                    MatchState.CONFLICT,
                    "action_http.path_conflict",
                    subject=subject,
                    delta=_fields(correlation=observation.correlation_status),
                    evidence=evidence,
                )
            )

        if not facts:
            facts.append(
                _fact(
                    MatchState.KNOWN,
                    "relation.known",
                    subject=subject,
                    evidence=evidence,
                )
            )
        return facts

    def compare(self, observations: ObservationSet) -> tuple[DiffFact, ...]:
        if not isinstance(observations, ObservationSet):
            raise TypeError("observations must be ObservationSet")
        facts: list[DiffFact] = []
        for observation in observations.http:
            facts.extend(self._endpoint_facts(observation))
            facts.extend(self._operation_facts(observation))
        for observation in observations.forms:
            facts.extend(self._form_facts(observation))
        for observation in observations.relations:
            facts.extend(self._relation_facts(observation))
        return tuple(facts)


__all__ = ["SemanticContractError", "SemanticDiff"]
