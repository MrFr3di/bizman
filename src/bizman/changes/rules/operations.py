from __future__ import annotations

from bizman.changes.model import RuleDescriptor


OPERATION_RULES: tuple[RuleDescriptor, ...] = (
    RuleDescriptor("BM-OP-001", 1, "operation.new_signature"),
    RuleDescriptor("BM-OP-002", 1, "operation.query_key_added"),
    RuleDescriptor("BM-OP-003", 1, "operation.body_key_added"),
)


__all__ = ["OPERATION_RULES"]
