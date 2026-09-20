from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def now_utc(self) -> datetime:
        return self.value


class CoreTimeTests(unittest.TestCase):
    def _repository_root(self, base: Path) -> Path:
        root = base / "repo"
        (root / "config").mkdir(parents=True)
        (root / "schemas").mkdir()
        (root / "knowledge").mkdir()
        (root / "config/redaction-policy.json").write_text("{}", encoding="utf-8")
        for name in (
            "event.schema.json",
            "session-manifest.schema.json",
            "promotion-bundle.schema.json",
        ):
            (root / "schemas" / name).write_text("{}", encoding="utf-8")
        return root

    def test_system_clock_returns_timezone_aware_utc(self):
        from bizman.core.time import SystemUtcClock

        value = SystemUtcClock().now_utc()
        self.assertIs(value.tzinfo, UTC)
        self.assertEqual(value.utcoffset().total_seconds(), 0)

    def test_core_context_accepts_fixed_clock_and_canonicalizes_data_root(self):
        from bizman.core.assets import RepositoryAssets
        from bizman.core.context import CoreContext
        from bizman.core.time import UtcClock

        fixed = FixedClock(datetime(2026, 9, 8, 7, 0, tzinfo=UTC))
        self.assertIsInstance(fixed, UtcClock)

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            assets = RepositoryAssets(self._repository_root(base))
            data_dir = base / "runtime" / ".." / "BizManData"
            context = CoreContext(assets=assets, data_dir=data_dir, clock=fixed)

            self.assertEqual(context.assets, assets)
            self.assertEqual(context.data_dir, (base / "BizManData").resolve())
            self.assertEqual(context.clock.now_utc(), fixed.value)


if __name__ == "__main__":
    unittest.main()
