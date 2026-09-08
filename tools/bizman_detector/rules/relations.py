from __future__ import annotations

from tools.bizman_detector.model import RuleDescriptor


RELATION_RULES: tuple[RuleDescriptor, ...] = (
    RuleDescriptor("BM-REL-001", 1, "action_http.request_family_new"),
    RuleDescriptor("BM-REL-002", 1, "action_http.path_conflict"),
)


__all__ = ["RELATION_RULES"]
