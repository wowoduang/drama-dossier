import sys
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from review_cut import run_ffmpeg_with_filter_script


class ReviewCutWindowsCompatibilityTests(unittest.TestCase):
    def test_filter_script_is_closed_before_ffmpeg_opens_it(self):
        observed = {}

        def fake_run(command, **kwargs):
            script_path = command[command.index("-filter_complex_script") + 1]
            # This second open succeeds only after NamedTemporaryFile is closed;
            # it reproduces the Windows sharing restriction.
            observed["content"] = Path(script_path).read_text(encoding="utf-8")
            observed["path"] = script_path
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("review_cut.subprocess.run", side_effect=fake_run):
            run_ffmpeg_with_filter_script(
                ["ffmpeg", "-filter_complex_script", "FILTER_FILE", "output.mp4"],
                "[0:v]null[vout]"
            )

        self.assertEqual(observed["content"], "[0:v]null[vout]")
        self.assertFalse(Path(observed["path"]).exists())

    def test_ffmpeg_failure_preserves_stderr_and_cleans_temp_file(self):
        observed = {}

        def fake_run(command, **kwargs):
            script_path = command[command.index("-filter_complex_script") + 1]
            observed["path"] = script_path
            # Opening it while the mocked child process runs verifies that the
            # writer handle was closed before subprocess invocation.
            self.assertTrue(Path(script_path).read_text(encoding="utf-8"))
            raise subprocess.CalledProcessError(1, command, output="", stderr="Invalid filtergraph")

        with patch("review_cut.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(ValueError, "Invalid filtergraph"):
                run_ffmpeg_with_filter_script(
                    ["ffmpeg", "-filter_complex_script", "FILTER_FILE", "output.mp4"],
                    "[0:v]broken[vout]"
                )

        self.assertFalse(Path(observed["path"]).exists())


if __name__ == "__main__":
    unittest.main()
