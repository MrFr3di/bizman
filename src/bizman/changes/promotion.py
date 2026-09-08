from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from bizman.changes import PROMOTION_SCHEMA_VERSION
from bizman.sessions.evidence import EvidenceIdentity
from bizman.changes.model import AnalysisProfile, Finding, SemanticFields
from bizman.changes.state import DetectorState, OutboxItem, OutboxPayload
from bizman.foundation.fingerprint import canonical_json_bytes, canonical_sha256
from bizman.foundation.redaction import RedactionPolicy


PROFILE_PREFIX_LENGTH: Final[int] = 16
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHANGE_ID_RE = re.compile(r"^chg\.[0-9a-f]{64}$")
_BUNDLE_ID_RE = re.compile(r"^promotion\.[0-9a-f]{64}$")
_UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_METHOD_RE = re.compile(r"^[A-Z][A-Z0-9!#$%&'*+.^_`|~-]*$")
_FORBIDDEN_SERIALIZED_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "headers",
        "raw_html",
        "websocket_payload",
        "cas_bytes",
        "request_body",
        "response_body",
        "query_value",
        "body_value",
        "form_value",
    }
)
_SENSITIVE_FIELD_POLICY = RedactionPolicy.default()


class PromotionError(RuntimeError):
    """Base class for promotion bundle failures."""


class PromotionIntegrityError(PromotionError):
    """Promotion state, payload identity or materialized bytes are inconsistent."""


class PromotionPrivacyError(PromotionIntegrityError):
    """A value-bearing or sensitive semantic field reached the promotion boundary."""


class PromotionSchemaError(PromotionIntegrityError):
    """A promotion payload does not satisfy the committed Draft 2020-12 schema."""


@dataclass(frozen=True, slots=True)
class _RuleShape:
    rule_id: str
    novelty_class: str
    subject_keys: frozenset[str]
    delta_keys: frozenset[str]


_RULE_SHAPES: Final[dict[str, _RuleShape]] = {
    "endpoint.new": _RuleShape(
        "BM-HTTP-001", "novel", frozenset({"path"}), frozenset()
    ),
    "endpoint.method_added": _RuleShape(
        "BM-HTTP-002", "novel", frozenset({"path"}), frozenset({"method"})
    ),
    "endpoint.query_key_added": _RuleShape(
        "BM-HTTP-003",
        "novel",
        frozenset({"method", "path"}),
        frozenset({"added_keys"}),
    ),
    "endpoint.status_added": _RuleShape(
        "BM-HTTP-004",
        "novel",
        frozenset({"method", "path"}),
        frozenset({"status"}),
    ),
    "form.signature_new": _RuleShape(
        "BM-FORM-001",
        "novel",
        frozenset({"method", "path"}),
        frozenset({"fields"}),
    ),
    "form.field_added": _RuleShape(
        "BM-FORM-002",
        "novel",
        frozenset({"method", "path"}),
        frozenset({"added_fields"}),
    ),
    "operation.new_signature": _RuleShape(
        "BM-OP-001",
        "novel",
        frozenset({"method", "path"}),
        frozenset({"body_keys", "query_keys"}),
    ),
    "operation.query_key_added": _RuleShape(
        "BM-OP-002",
        "novel",
        frozenset({"method", "path"}),
        frozenset({"added_keys"}),
    ),
    "operation.body_key_added": _RuleShape(
        "BM-OP-003",
        "novel",
        frozenset({"method", "path"}),
        frozenset({"added_keys"}),
    ),
    "action_http.request_family_new": _RuleShape(
        "BM-REL-001",
        "novel",
        frozenset({"method", "path"}),
        frozenset({"correlation"}),
    ),
    "action_http.path_conflict": _RuleShape(
        "BM-REL-002",
        "conflict",
        frozenset({"action_method", "action_path", "request_method", "request_path"}),
        frozenset({"correlation"}),
    ),
}


def _default_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "schemas" / "promotion-bundle.schema.json"


def _require_sha256(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise PromotionIntegrityError(f"{name} must be 64 lowercase hexadecimal characters")
    return value


def _require_uuid7(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _UUID7_RE.fullmatch(value) is None:
        raise PromotionIntegrityError(f"{name} must be a canonical UUIDv7")
    return value


def _require_positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PromotionIntegrityError(f"{name} must be a positive integer")
    return value


def _require_method(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _METHOD_RE.fullmatch(value) is None:
        raise PromotionPrivacyError(f"invalid normalized HTTP method {value!r}")
    return value


def _require_path(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.startswith("/"):
        raise PromotionPrivacyError(f"invalid origin-relative path {value!r}")
    if len(value) > 2048 or "?" in value or "#" in value or "\r" in value or "\n" in value:
        raise PromotionPrivacyError("promotion paths must not contain query, fragment or controls")
    return value


def _require_key_sequence(value: object, *, name: str) -> list[str]:
    if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
        raise PromotionPrivacyError(f"{name} must be a normalized tuple of field names")
    if len(value) > 256 or tuple(sorted(set(value))) != value:
        raise PromotionPrivacyError(f"{name} must be sorted, unique and bounded")
    result: list[str] = []
    for item in value:
        if not item or len(item) > 256:
            raise PromotionPrivacyError(f"{name} contains an invalid field name")
        if _SENSITIVE_FIELD_POLICY.should_drop_field(item):
            raise PromotionPrivacyError(f"{name} contains sensitive field name {item!r}")
        result.append(item)
    return result


def _validate_semantic_value(key: str, value: object) -> Any:
    if key in {"method", "request_method"}:
        return _require_method(value)
    if key == "action_method":
        return _require_method(value, nullable=True)
    if key in {"path", "request_path"}:
        return _require_path(value)
    if key == "action_path":
        return _require_path(value, nullable=True)
    if key in {"added_keys", "fields", "added_fields", "body_keys", "query_keys"}:
        return _require_key_sequence(value, name=key)
    if key == "status":
        if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
            raise PromotionPrivacyError("status must be an HTTP status integer")
        return value
    if key == "correlation":
        if value not in {"strong", "probable"}:
            raise PromotionPrivacyError("promoted relation correlation must be strong or probable")
        return value
    raise PromotionPrivacyError(f"unsupported semantic field {key!r}")


def _semantic_object(
    fields: SemanticFields,
    *,
    expected_keys: frozenset[str],
    name: str,
) -> dict[str, Any]:
    if not isinstance(fields, tuple):
        raise PromotionPrivacyError(f"{name} must be immutable SemanticFields")
    keys: list[str] = []
    values: dict[str, Any] = {}
    for item in fields:
        if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
            raise PromotionPrivacyError(f"{name} contains malformed semantic fields")
        key, value = item
        if key in values:
            raise PromotionPrivacyError(f"{name} contains duplicate semantic field {key!r}")
        keys.append(key)
        values[key] = _validate_semantic_value(key, value)
    if frozenset(keys) != expected_keys:
        unexpected = sorted(frozenset(keys).difference(expected_keys))
        missing = sorted(expected_keys.difference(keys))
        detail = f"unexpected={unexpected}, missing={missing}"
        raise PromotionPrivacyError(f"{name} does not match rule semantic shape: {detail}")
    if keys != sorted(keys):
        raise PromotionIntegrityError(f"{name} semantic fields must be canonically sorted")
    return values


def _audit_serialized(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_SERIALIZED_KEYS or normalized.endswith("_value"):
                raise PromotionPrivacyError(f"forbidden value-bearing field at {path}.{key}")
            _audit_serialized(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _audit_serialized(item, path=f"{path}[{index}]")
        return
    if isinstance(value, bytes):
        raise PromotionPrivacyError(f"raw bytes are forbidden at {path}")
    if isinstance(value, str) and "\x00" in value:
        raise PromotionPrivacyError(f"NUL-containing strings are forbidden at {path}")


class _PromotionSchemaValidator:
    def __init__(self, schema_path: Path) -> None:
        self.schema_path = Path(schema_path).expanduser().resolve()
        try:
            with self.schema_path.open("r", encoding="utf-8") as handle:
                schema = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise PromotionSchemaError(
                f"cannot load promotion schema {self.schema_path}: {exc}"
            ) from exc
        if not isinstance(schema, Mapping):
            raise PromotionSchemaError("promotion schema root must be an object")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise PromotionSchemaError("promotion schema is not valid Draft 2020-12") from exc
        self._validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def validate(self, document: Mapping[str, Any]) -> None:
        errors = sorted(
            self._validator.iter_errors(document),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            error = errors[0]
            location = "/".join(str(part) for part in error.absolute_path) or "$"
            raise PromotionSchemaError(
                f"promotion bundle schema validation failed at {location}: {error.message}"
            )


class PromotionBundleBuilder:
    """Build exact canonical value-free outbox payloads from first-seen Findings."""

    def __init__(self, schema_path: Path | None = None) -> None:
        self.schema_path = Path(schema_path) if schema_path is not None else _default_schema_path()
        self._schema = _PromotionSchemaValidator(self.schema_path)

    @staticmethod
    def _finding_document(finding: Finding) -> dict[str, Any]:
        if not isinstance(finding, Finding):
            raise TypeError("findings must contain Finding values")
        if not isinstance(finding.change_id, str) or _CHANGE_ID_RE.fullmatch(finding.change_id) is None:
            raise PromotionIntegrityError("finding change_id must be canonical chg.<sha256>")
        shape = _RULE_SHAPES.get(finding.kind)
        if shape is None:
            raise PromotionPrivacyError(f"unsupported promotable finding kind {finding.kind!r}")
        if finding.rule_id != shape.rule_id:
            raise PromotionIntegrityError(
                f"rule/kind mismatch: {finding.rule_id!r} does not classify {finding.kind!r}"
            )
        _require_positive_int(finding.rule_version, name="rule_version")
        if finding.novelty_class != shape.novelty_class:
            raise PromotionIntegrityError(
                f"invalid novelty_class {finding.novelty_class!r} for {finding.kind!r}"
            )
        subject = _semantic_object(
            finding.subject,
            expected_keys=shape.subject_keys,
            name=f"{finding.kind}.subject",
        )
        delta = _semantic_object(
            finding.delta,
            expected_keys=shape.delta_keys,
            name=f"{finding.kind}.delta",
        )
        event_ids = tuple(sorted(set(finding.evidence_event_ids)))
        if not event_ids:
            raise PromotionIntegrityError("promoted findings require evidence event IDs")
        for event_id in event_ids:
            _require_uuid7(event_id, name="evidence_event_id")

        document = {
            "change_id": finding.change_id,
            "rule_id": finding.rule_id,
            "rule_version": finding.rule_version,
            "kind": finding.kind,
            "novelty_class": finding.novelty_class,
            "evidence_confidence": "observed",
            "subject": subject,
            "delta": delta,
            "evidence_event_ids": list(event_ids),
        }
        _audit_serialized(document)
        return document

    def build(
        self,
        identity: EvidenceIdentity,
        profile: AnalysisProfile,
        findings: tuple[Finding, ...],
    ) -> OutboxPayload:
        if not isinstance(identity, EvidenceIdentity):
            raise TypeError("identity must be EvidenceIdentity")
        if not isinstance(profile, AnalysisProfile):
            raise TypeError("profile must be AnalysisProfile")
        if identity.status not in {"completed", "cancelled"}:
            raise PromotionIntegrityError("promotion requires finalized completed/cancelled evidence")
        _require_uuid7(identity.session_id, name="session_id")
        _require_sha256(identity.evidence_sha256, name="evidence_sha256")
        _require_sha256(identity.manifest_sha256, name="manifest_sha256")
        _require_sha256(profile.sha256, name="analysis_profile_sha256")
        _require_sha256(profile.baseline_sha256, name="baseline_sha256")
        _require_sha256(profile.redaction_policy_sha256, name="redaction_policy_sha256")
        _require_positive_int(profile.normalization_version, name="normalization_version")
        _require_positive_int(profile.extraction_version, name="extraction_version")
        if not isinstance(identity.ended_at, str) or not identity.ended_at:
            raise PromotionIntegrityError("finalized evidence must have ended_at")
        if not isinstance(findings, tuple) or not findings:
            raise PromotionIntegrityError("promotion bundle requires first-seen findings")

        ordered = tuple(sorted(findings, key=lambda item: item.change_id))
        change_ids = [item.change_id for item in ordered]
        if len(set(change_ids)) != len(change_ids):
            raise PromotionIntegrityError("promotion bundle cannot contain duplicate change IDs")
        finding_documents = [self._finding_document(item) for item in ordered]

        payload_without_id: dict[str, Any] = {
            "schema_version": f"{PROMOTION_SCHEMA_VERSION}.0",
            "analysis_profile_sha256": profile.sha256,
            "baseline_sha256": profile.baseline_sha256,
            "redaction_policy_sha256": profile.redaction_policy_sha256,
            "session_id": identity.session_id,
            "evidence_sha256": identity.evidence_sha256,
            "manifest_sha256": identity.manifest_sha256,
            "created_at": identity.ended_at,
            "detector": {
                "normalization_version": profile.normalization_version,
                "extraction_version": profile.extraction_version,
            },
            "findings": finding_documents,
        }
        bundle_id = f"promotion.{canonical_sha256(payload_without_id)}"
        document = dict(payload_without_id)
        document["bundle_id"] = bundle_id
        _audit_serialized(document)
        self._schema.validate(document)
        payload_bytes = canonical_json_bytes(document)
        payload_json = payload_bytes.decode("utf-8")
        return OutboxPayload(
            bundle_id=bundle_id,
            payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
            payload_json=payload_json,
            created_at=identity.ended_at,
        )


class PromotionMaterializer:
    """Crash-safe filesystem materializer for committed promotion outbox rows."""

    def __init__(
        self,
        data_dir: Path,
        state: DetectorState,
        schema_path: Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve(strict=False)
        self.promotions_dir = self.data_dir / "promotions"
        self.state = state
        self.schema_path = Path(schema_path) if schema_path is not None else _default_schema_path()
        self._schema = _PromotionSchemaValidator(self.schema_path)

    @staticmethod
    def _verify_payload(item: OutboxItem, schema: _PromotionSchemaValidator) -> bytes:
        if item.state != "pending":
            raise PromotionIntegrityError(f"materializer received non-pending row {item.bundle_id!r}")
        if _BUNDLE_ID_RE.fullmatch(item.bundle_id) is None:
            raise PromotionIntegrityError("outbox bundle_id is not canonical")
        _require_sha256(item.analysis_profile_sha256, name="analysis_profile_sha256")
        _require_sha256(item.baseline_sha256, name="baseline_sha256")
        _require_uuid7(item.session_id, name="session_id")
        _require_sha256(item.payload_sha256, name="payload_sha256")
        payload_bytes = item.payload_json.encode("utf-8")
        actual_hash = hashlib.sha256(payload_bytes).hexdigest()
        if actual_hash != item.payload_sha256:
            raise PromotionIntegrityError(
                f"outbox payload hash mismatch for {item.bundle_id!r}: {actual_hash}"
            )
        try:
            document = json.loads(item.payload_json)
        except json.JSONDecodeError as exc:
            raise PromotionIntegrityError("outbox payload_json is invalid JSON") from exc
        if not isinstance(document, Mapping):
            raise PromotionIntegrityError("outbox promotion payload root must be an object")
        if canonical_json_bytes(document) != payload_bytes:
            raise PromotionIntegrityError("outbox promotion payload must be canonical JSON")
        _audit_serialized(document)
        schema.validate(document)
        if document.get("bundle_id") != item.bundle_id:
            raise PromotionIntegrityError("outbox bundle_id does not match payload")
        if document.get("analysis_profile_sha256") != item.analysis_profile_sha256:
            raise PromotionIntegrityError("outbox analysis profile does not match payload")
        if document.get("baseline_sha256") != item.baseline_sha256:
            raise PromotionIntegrityError("outbox baseline does not match payload")
        if document.get("session_id") != item.session_id:
            raise PromotionIntegrityError("outbox session does not match payload")
        without_id = dict(document)
        without_id.pop("bundle_id", None)
        expected_bundle_id = f"promotion.{canonical_sha256(without_id)}"
        if expected_bundle_id != item.bundle_id:
            raise PromotionIntegrityError("promotion bundle semantic identity mismatch")
        return payload_bytes

    def _ensure_parent(self, item: OutboxItem) -> Path:
        root = self.promotions_dir
        if root.exists() and root.is_symlink():
            raise PromotionIntegrityError("promotions directory must not be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        profile_dir = root / item.analysis_profile_sha256[:PROFILE_PREFIX_LENGTH]
        if profile_dir.exists() and profile_dir.is_symlink():
            raise PromotionIntegrityError("promotion profile directory must not be a symlink")
        profile_dir.mkdir(exist_ok=True)
        session_dir = profile_dir / item.session_id
        if session_dir.exists() and session_dir.is_symlink():
            raise PromotionIntegrityError("promotion session directory must not be a symlink")
        session_dir.mkdir(exist_ok=True)
        return session_dir

    def path_for(self, item: OutboxItem) -> Path:
        if _BUNDLE_ID_RE.fullmatch(item.bundle_id) is None:
            raise PromotionIntegrityError("outbox bundle_id is not canonical")
        _require_sha256(item.analysis_profile_sha256, name="analysis_profile_sha256")
        _require_uuid7(item.session_id, name="session_id")
        return (
            self.promotions_dir
            / item.analysis_profile_sha256[:PROFILE_PREFIX_LENGTH]
            / item.session_id
            / f"{item.bundle_id}.json"
        )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = getattr(os, "O_RDONLY", 0)
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return
        try:
            try:
                os.fsync(descriptor)
            except OSError:
                pass
        finally:
            os.close(descriptor)

    @staticmethod
    def _assert_existing_bytes(target: Path, expected: bytes) -> None:
        if target.is_symlink() or not target.is_file():
            raise PromotionIntegrityError(f"promotion target is not a regular file: {target}")
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise PromotionIntegrityError(f"cannot stat promotion target {target}") from exc
        if size != len(expected):
            raise PromotionIntegrityError(
                f"existing promotion file has different bytes: {target}"
            )
        try:
            actual = target.read_bytes()
        except OSError as exc:
            raise PromotionIntegrityError(f"cannot read promotion target {target}") from exc
        if actual != expected:
            raise PromotionIntegrityError(
                f"existing promotion file has different bytes: {target}"
            )

    def _materialize_one(self, item: OutboxItem, payload_bytes: bytes) -> Path:
        target = self.path_for(item)
        if target.exists() or target.is_symlink():
            self._assert_existing_bytes(target, payload_bytes)
            return target

        parent = self._ensure_parent(item)
        target = parent / target.name
        if target.exists() or target.is_symlink():
            self._assert_existing_bytes(target, payload_bytes)
            return target

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(payload_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists() or target.is_symlink():
                self._assert_existing_bytes(target, payload_bytes)
                temporary_path.unlink(missing_ok=True)
                temporary_path = None
                return target
            os.replace(temporary_path, target)
            temporary_path = None
            self._fsync_directory(parent)
            self._assert_existing_bytes(target, payload_bytes)
            return target
        except BaseException:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def materialize_pending(self, *, materialized_at: str) -> tuple[Path, ...]:
        if not isinstance(materialized_at, str) or not materialized_at:
            raise PromotionIntegrityError("materialized_at must be a non-empty string")
        materialized: list[Path] = []
        for item in self.state.pending_outbox():
            payload_bytes = self._verify_payload(item, self._schema)
            target = self._materialize_one(item, payload_bytes)
            self.state.mark_materialized(
                item.bundle_id,
                item.payload_sha256,
                materialized_at,
            )
            materialized.append(target)
        return tuple(materialized)


__all__ = [
    "PROFILE_PREFIX_LENGTH",
    "PromotionBundleBuilder",
    "PromotionError",
    "PromotionIntegrityError",
    "PromotionMaterializer",
    "PromotionPrivacyError",
    "PromotionSchemaError",
]
