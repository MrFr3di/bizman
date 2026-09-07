import tempfile
import unittest
from pathlib import Path

from tools.ci.download_chrome_for_testing import _restore_linux_executable_bits


class ChromeForTestingExtractionTests(unittest.TestCase):
    def test_linux_runtime_binaries_become_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "chrome-linux64"
            runtime.mkdir()
            binaries = [
                runtime / "chrome",
                runtime / "chrome_crashpad_handler",
                runtime / "chrome_sandbox",
            ]
            unrelated = runtime / "resources.pak"
            for path in [*binaries, unrelated]:
                path.write_bytes(b"fixture")
                path.chmod(0o644)

            _restore_linux_executable_bits(root)

            for path in binaries:
                self.assertNotEqual(path.stat().st_mode & 0o111, 0, path.name)
            self.assertEqual(unrelated.stat().st_mode & 0o111, 0)


if __name__ == "__main__":
    unittest.main()
