import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from batch_media_scan import run_batch


class BatchMediaScanTests(unittest.TestCase):
    def test_batch_resume_and_changed_source_guard(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            videos = root / "videos"
            output = root / "scan"
            videos.mkdir()
            video = videos / "EP001.mp4"
            video.write_bytes(b"video-version-1")

            def fake_scan(video_path, subtitle, threshold, audio_window, frames_dir, max_frames, sample_interval):
                return {"source_video": str(video_path), "cue_count": 3, "frame_manifest": [], "media": {"duration_sec": 12}}

            with patch("batch_media_scan.build_scan", side_effect=fake_scan), patch("batch_media_scan.write_ai_review_prompt"):
                first = run_batch(videos, output)
                second = run_batch(videos, output)
                self.assertEqual(first["done"], 1)
                self.assertEqual(second["skipped"], 1)
                video.write_bytes(b"video-version-2")
                guarded = run_batch(videos, output)
                self.assertEqual(guarded["changed_needs_replace"], 1)
                guarded_again = run_batch(videos, output)
                self.assertEqual(guarded_again["changed_needs_replace"], 1)
                replaced = run_batch(videos, output, replace=True)
                self.assertEqual(replaced["done"], 1)

            manifest = json.loads((output / "batch-scan-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["items"][0]["status"], "DONE")


if __name__ == "__main__":
    unittest.main()
