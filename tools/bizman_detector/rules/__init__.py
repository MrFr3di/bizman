from __future__ import annotations

from tools.bizman_detector.model import RuleDescriptor


RULE_DESCRIPTORS: tuple[RuleDescriptor, ...] = (
    RuleDescriptor("BM-HTTP-001", 1, "endpoint.new"),
    RuleDescriptor("BM-HTTP-002", 1, "endpoint.method_added"),
    RuleDescriptor("BM-HTTP-003", 1, "endpoint.query_key_added"),
    RuleDescriptor("BM-HTTP-004", 1, "endpoint.status_added"),
    RuleDescriptor("BM-FORM-001", 1, "form.signature_new"),
    RuleDescriptor("BM-FORM-002", 1, "form.field_added"),
    RuleDescriptor("BM-OP-001", 1, "operation.new_signature"),
    RuleDescriptor("BM-OP-002", 1, "operation.query_key_added"),
    RuleDescriptor("BM-OP-003", 1, "operation.body_key_added"),
    RuleDescriptor("BM-REL-001", 1, "action_http.request_family_new"),
    RuleDescriptor("BM-REL-002", 1, "action_http.path_conflict"),
)


__all__ = ["RULE_DESCRIPTORS"]
