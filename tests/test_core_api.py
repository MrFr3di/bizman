from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_ID = "01991c7d-a400-7000-8000-000000000001"


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def now_utc(self) -> datetime:
        return self.value


def _context(data_dir: Path):
    from bizman.core import CoreContext, RepositoryAssets

    return CoreContext(
        assets=RepositoryAssets(REPO_ROOT),
        data_dir=data_dir,
        clock=FixedClock(datetime(2026, 9, 8, 8, 30, tzinfo=UTC)),
    )


class CorePublicApiTests(unittest.TestCase):
    def test_exact_supported_exports(self):
        import bizman.core as core

        self.assertEqual(
            tuple(core.__all__),
            (
                "AssetError",
                "AssetId",
                "BizManError",
                "CollectionRequest",
                "CollectionResult",
                "ConfigurationError",
                "ContractMismatchError",
                "CoreContext",
                "DataIntegrityError",
                "DetectionRequest",
                "DetectorRunSummary",
                "OperationError",
                "RepositoryAssets",
                "SystemUtcClock",
                "UtcClock",
                "ValidationResult",
                "collect",
                "detect_changes",
                "validate_repository",
            ),
        )

    def test_request_and_result_dtos_are_frozen_slotted_and_path_free(self):
        from bizman.core import CollectionRequest, CollectionResult, DetectionRequest

        collection = CollectionRequest(
            endpoint="http://127.0.0.1:9222",
            hosts=("bizmania.ru",),
            event_queue_size=4096,
        )
        detection = DetectionRequest(selected_sessions=(SESSION_ID,), dry_run=True)
        result = CollectionResult(session_id=SESSION_ID)

        self.assertEqual(
            tuple(field.name for field in fields(CollectionRequest)),
            ("endpoint", "hosts", "event_queue_size"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(DetectionRequest)),
            ("selected_sessions", "dry_run"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(CollectionResult)),
            ("session_id",),
        )
        for value in (collection, detection, result):
            self.assertFalse(hasattr(value, "__dict__"))
            with self.assertRaises(FrozenInstanceError):
                value._probe = True  # type: ignore[attr-defined,misc]
        for dto in (CollectionRequest, DetectionRequest, CollectionResult):
            self.assertTrue(
                all("Path" not in str(field.type) for field in fields(dto)),
                f"{dto.__name__} must not expose filesystem paths",
            )


class CoreUseCaseTests(unittest.TestCase):
    def test_detection_dry_run_uses_context_configuration_without_writes(self):
        from bizman.core import DetectionRequest, DetectorRunSummary, detect_changes

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "BizManData"
            summary = detect_changes(
                _context(data_dir),
                DetectionRequest(dry_run=True),
            )

            self.assertIsInstance(summary, DetectorRunSummary)
            self.assertTrue(summary.dry_run)
            self.assertEqual(summary.sessions_discovered, 0)
            self.assertFalse((data_dir / "detector").exists())
            self.assertFalse((data_dir / "promotions").exists())

    def test_repository_validation_uses_context_asset_root(self):
        from bizman.core import ValidationResult, validate_repository

        with tempfile.TemporaryDirectory() as tmp:
            result = validate_repository(_context(Path(tmp) / "BizManData"))

        self.assertIsInstance(result, ValidationResult)
        self.assertTrue(result.ok, result.errors)

    def test_collection_uses_context_data_root_and_typed_request(self):
        from bizman.core import CollectionRequest, CollectionResult, collect
        from bizman.foundation.redaction import RedactionPolicy

        with tempfile.TemporaryDirectory() as tmp:
            context = _context(Path(tmp) / "BizManData")
            request = CollectionRequest(
                endpoint="http://127.0.0.1:9333",
                hosts=("bizmania.ru", "www.bizmania.ru"),
                event_queue_size=2048,
            )
            fake_run = AsyncMock(return_value=SESSION_ID)
            with patch("bizman.core.collection.run_collection", fake_run):
                result = asyncio.run(collect(context, request))

        self.assertEqual(result, CollectionResult(session_id=SESSION_ID))
        kwargs = fake_run.await_args.kwargs
        self.assertEqual(kwargs["endpoint"], request.endpoint)
        self.assertEqual(kwargs["data_dir"], context.data_dir)
        self.assertEqual(kwargs["hosts"], request.hosts)
        self.assertEqual(kwargs["event_queue_size"], request.event_queue_size)
        self.assertIsInstance(kwargs["redaction_policy"], RedactionPolicy)

    def test_expected_asset_failure_is_translated_with_cause(self):
        from bizman.core import AssetError, CollectionRequest, collect

        with tempfile.TemporaryDirectory() as tmp:
            context = _context(Path(tmp) / "BizManData")
            with patch(
                "bizman.core.collection.load_redaction_policy",
                side_effect=ValueError("bad policy"),
            ):
                with self.assertRaises(AssetError) as raised:
                    asyncio.run(collect(context, CollectionRequest()))

        self.assertIsInstance(raised.exception.__cause__, ValueError)

    def test_programming_errors_are_not_blanket_translated(self):
        from bizman.core import DetectionRequest, detect_changes

        with tempfile.TemporaryDirectory() as tmp:
            context = _context(Path(tmp) / "BizManData")
            with patch(
                "bizman.core.detection.DetectorRunner.from_paths",
                side_effect=TypeError("programming defect"),
            ):
                with self.assertRaisesRegex(TypeError, "programming defect"):
                    detect_changes(context, DetectionRequest(dry_run=True))


if __name__ == "__main__":
    unittest.main()
