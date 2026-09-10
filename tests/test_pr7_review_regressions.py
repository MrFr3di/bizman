from __future__ import annotations

import argparse
import asyncio
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_ID = "01991c7d-a400-7000-8000-000000000001"
EVENT_ID = "01991c7d-a400-7000-8000-000000000011"


def _write_minimal_evidence(data_dir: Path) -> tuple[Path, dict[str, object]]:
    event = {
        "schema_version": "1.0",
        "event_id": EVENT_ID,
        "session_id": SESSION_ID,
        "sequence": 0,
        "observed_at": "2026-09-07T12:00:00Z",
        "monotonic_time": 0.0,
        "source": "system",
        "event_type": "fixture.event",
        "confidence": "observed",
    }
    event_rel = f"events/2026-09-07/{SESSION_ID}.jsonl"
    event_path = data_dir / event_rel
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text(
        json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0",
        "session_id": SESSION_ID,
        "started_at": "2026-09-07T12:00:00Z",
        "ended_at": "2026-09-07T12:00:01Z",
        "status": "completed",
        "collector": {"name": "bizman-cdp", "version": "0.test"},
        "browser": {"product": "Chrome/Test", "version": "1"},
        "protocol": {
            "name": "cdp",
            "version": "1.3",
            "sha256": None,
            "artifact_ref": None,
        },
        "event_files": [event_rel],
        "artifact_count": 0,
        "warnings": [],
    }
    manifest_path = data_dir / "sessions" / SESSION_ID / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return event_path, event


def _minimal_validation_root(root: Path) -> None:
    (root / "knowledge/sources").mkdir(parents=True)
    (root / "schemas").mkdir()


class CodeRabbitReviewRegressionTests(unittest.TestCase):
    def test_expected_identity_is_verified_before_first_event_is_yielded(self):
        from bizman.sessions.evidence import EvidenceIntegrityError, EvidenceReader

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            event_path, event = _write_minimal_evidence(data_dir)
            reader = EvidenceReader(REPO_ROOT, data_dir)
            identity = reader.inspect(SESSION_ID)

            changed = dict(event)
            changed["event_type"] = "fixture.changed"
            event_path.write_text(
                json.dumps(changed, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            stream = reader.iter_events(SESSION_ID, expected_identity=identity)
            with self.assertRaisesRegex(EvidenceIntegrityError, "bytes"):
                next(stream)

    def test_grouped_cdp_failure_is_translated_at_core_boundary(self):
        from bizman.collector.cdp import CdpError
        from bizman.core import CollectionRequest, CoreContext, OperationError, RepositoryAssets
        from bizman.core.collection import collect
        from bizman.core.time import SystemUtcClock

        with tempfile.TemporaryDirectory() as tmp:
            context = CoreContext(
                assets=RepositoryAssets(REPO_ROOT),
                data_dir=Path(tmp) / "BizManData",
                clock=SystemUtcClock(),
            )
            failure = ExceptionGroup("collector task group", [CdpError("transport failed")])
            with patch(
                "bizman.core.collection.run_collection",
                AsyncMock(side_effect=failure),
            ):
                with self.assertRaises(OperationError) as raised:
                    asyncio.run(collect(context, CollectionRequest()))
            self.assertIs(raised.exception.__cause__, failure)

    def test_non_utf8_cdp_binary_frame_is_normalized(self):
        from bizman.collector import cdp

        class BinaryWebSocket:
            def __aiter__(self):
                async def messages():
                    yield b"\xff"

                return messages()

            async def close(self) -> None:
                return None

        async def consume() -> None:
            async for _ in cdp._WebSocketTransport(BinaryWebSocket()):
                pass

        with self.assertRaises(cdp.CdpError):
            asyncio.run(consume())

    def test_semantic_diff_uses_detector_contract_error_for_unsupported_status(self):
        from bizman.changes.diff import SemanticContractError, SemanticDiff
        from bizman.changes.model import ObservationSet, RelationObservation, RuntimeContract

        contract = RuntimeContract(
            contract_schema_version=1,
            normalization_version=1,
            endpoints=(),
            forms=(),
            operations=(),
            actions=(),
        )
        observations = ObservationSet(
            http=(),
            forms=(),
            relations=(
                RelationObservation(
                    correlation_status="exact",
                    action_event_id=EVENT_ID,
                    request_event_id="01991c7d-a400-7000-8000-000000000012",
                    action_method="POST",
                    action_path="/known/",
                    request_method="POST",
                    request_literal_path="/known/",
                    request_path_pattern="/known/",
                ),
            ),
        )
        with self.assertRaises(SemanticContractError):
            SemanticDiff(contract).compare(observations)

    def test_promotion_schema_path_is_an_explicit_low_level_dependency(self):
        from bizman.changes.promotion import PromotionBundleBuilder, PromotionMaterializer

        builder_parameter = inspect.signature(PromotionBundleBuilder).parameters["schema_path"]
        materializer_parameter = inspect.signature(PromotionMaterializer).parameters["schema_path"]
        self.assertIs(builder_parameter.default, inspect.Parameter.empty)
        self.assertIs(materializer_parameter.default, inspect.Parameter.empty)

    def test_runner_closes_state_when_materializer_initialization_fails(self):
        from bizman.changes.runner import DetectorRunner
        from bizman.foundation.redaction import RedactionPolicy

        state = MagicMock()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "bizman.changes.runner.DetectorState.open_rw",
            return_value=state,
        ), patch(
            "bizman.changes.runner.PromotionMaterializer",
            side_effect=RuntimeError("bad materializer"),
        ):
            with self.assertRaisesRegex(RuntimeError, "bad materializer"):
                DetectorRunner.from_paths(
                    repo_root=REPO_ROOT,
                    data_dir=Path(tmp) / "BizManData",
                    redaction=RedactionPolicy.default(),
                )
        state.close.assert_called_once_with()

    def test_empty_session_is_rejected_by_argparse(self):
        from bizman.cli.main import main

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit) as raised:
            main(
                [
                    "detect",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-dir",
                    tmp,
                    "--session",
                    "",
                ]
            )
        self.assertEqual(raised.exception.code, 2)

    def test_legacy_validate_parses_process_arguments_when_argv_is_none(self):
        from bizman.cli.validate import legacy_main

        with patch.object(sys, "argv", ["validate_repo.py", "--unknown"]):
            with self.assertRaises(SystemExit) as raised:
                legacy_main(None, repo_root=REPO_ROOT)
        self.assertEqual(raised.exception.code, 2)

    def test_configured_post_data_limit_warns_when_protocol_cannot_apply_it(self):
        from bizman.collector.network import FirstPartyPolicy
        from bizman.collector.targets import TargetOrchestrator

        capabilities = MagicMock()
        capabilities.is_empty = False
        capabilities.command_supports_parameter.return_value = False
        warnings: list[str] = []
        orchestrator = TargetOrchestrator(
            cdp=MagicMock(),
            first_party=FirstPartyPolicy(("bizmania.ru",)),
            capabilities=capabilities,
            max_post_data_size=4096,
            warning_sink=warnings.append,
        )
        self.assertEqual(orchestrator._network_enable_params(), {})
        self.assertTrue(any("cannot be applied" in warning for warning in warnings))

    def test_catalog_dataset_cannot_escape_repository_root(self):
        from bizman.foundation.validation import validate_repository

        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "repo"
            _minimal_validation_root(root)
            (parent / "outside.json").write_text("[]", encoding="utf-8")
            (root / "knowledge/catalog.json").write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "datasets": [
                            {
                                "id": "escape",
                                "path": "../outside.json",
                                "format": "json",
                                "records": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_repository(root)
            self.assertTrue(any("escapes" in error for error in result.errors), result.errors)

    def test_partition_file_cannot_escape_manifest_directory(self):
        from bizman.foundation.validation import validate_repository

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _minimal_validation_root(root)
            items = root / "knowledge/items"
            items.mkdir(parents=True)
            (root / "knowledge/outside.jsonl").write_text('{"id":1}\n', encoding="utf-8")
            (items / "index.json").write_text(
                json.dumps(
                    {
                        "total_records": 1,
                        "parts": [{"file": "../outside.jsonl", "records": 1, "offset": 0}],
                    }
                ),
                encoding="utf-8",
            )
            (root / "knowledge/catalog.json").write_text(
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "datasets": [
                            {
                                "id": "items",
                                "path": "knowledge/items/index.json",
                                "format": "partition-manifest/jsonl",
                                "records": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_repository(root)
            self.assertTrue(any("escapes" in error for error in result.errors), result.errors)

    def test_legacy_collector_package_keeps_version_export(self):
        from bizman.collector import COLLECTOR_VERSION as canonical_version
        from tools.bizman_collector import COLLECTOR_VERSION as legacy_version

        self.assertEqual(legacy_version, canonical_version)

    def test_lint_plan_matches_ci_scope(self):
        plan = (
            REPO_ROOT
            / "docs/superpowers/plans/2026-09-08-python-packaging-core-boundary.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("uv run ruff check src tests tools", plan)
        self.assertIn("uv run ruff check src", plan)

    def test_workflow_glob_already_covers_its_own_definition(self):
        workflow = (REPO_ROOT / ".github/workflows/collector-e2e.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('- ".github/workflows/**"', workflow)


if __name__ == "__main__":
    unittest.main()
