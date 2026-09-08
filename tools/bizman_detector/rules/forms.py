from __future__ import annotations

from tools.bizman_detector.model import RuleDescriptor


FORM_RULES: tuple[RuleDescriptor, ...] = (
    RuleDescriptor("BM-FORM-001", 1, "form.signature_new"),
    RuleDescriptor("BM-FORM-002", 1, "form.field_added"),
)


__all__ = ["FORM_RULES"]
