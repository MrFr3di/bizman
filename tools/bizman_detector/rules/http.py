from __future__ import annotations

from tools.bizman_detector.model import RuleDescriptor


HTTP_RULES: tuple[RuleDescriptor, ...] = (
    RuleDescriptor("BM-HTTP-001", 1, "endpoint.new"),
    RuleDescriptor("BM-HTTP-002", 1, "endpoint.method_added"),
    RuleDescriptor("BM-HTTP-003", 1, "endpoint.query_key_added"),
    RuleDescriptor("BM-HTTP-004", 1, "endpoint.status_added"),
)


__all__ = ["HTTP_RULES"]
