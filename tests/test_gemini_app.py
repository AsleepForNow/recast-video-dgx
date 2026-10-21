"""Local only: exercise manual Gemini packet and collection with synthetic data, never call Gemini."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gemini_app
from recast import frames, probe, video


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg + ffprobe required")
class GeminiHandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-n", "-f", "lavfi",
            "-i", "testsrc=size=64x64:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(self.source)
        ], check=True)
        # Input existence and SHA are all prepare requires; image decoding belongs to Gemini App.
        self.image = self.root / "identity.png"
        self.image.write_bytes(b"synthetic-test-image")
        review = {"passed": True, "reviewer": "synthetic-test", "at": "2026-10-21T00:00:00Z",
                  "evidence": "automated test fixture (not a real human visual review)"}
        self.task = {
            "source":str(self.source), "start":0, "end":48, "segment_id":"S001",
            "version":"v001", "source_actor":"test source", "target_character":"test character",
            "action_notes":"test sample motion", "reference_images":[str(self.image)],
            "action_review":review, "reference_review":review
        }
        self.task_path = self.root / "task.json"
        self.task_path.write_text(json.dumps(self.task), encoding="utf-8")

    def test_prepare_and_collect_synthetic_clip(self):
        packet = self.root / "packet"
        gemini_app.prepare(type("Args", (), {"task":str(self.task_path), "output":str(packet)})())
        data = json.loads((packet/"handoff.json").read_text())
        self.assertEqual(data["backend"], "gemini-app-manual")
        self.assertEqual(data["state"], "awaiting_user_upload_and_download")
        self.assertFalse(data["visual_acceptance"])
        self.assertEqual(frames(video(probe(packet/"inputs/segment_for_gemini.mp4"))), 48)
        # Test fixture uses the packet's own clip as simulated download.
        mapped = self.root / "mapped.mp4"
        gemini_app.collect(type("Args", (), {
            "packet":str(packet),
            "input":str(packet/"inputs/segment_for_gemini.mp4"),
            "output":str(mapped)})())
        self.assertEqual(frames(video(probe(mapped))), 48)
        evidence = json.loads(Path(str(mapped)+".gemini.json").read_text())
        self.assertFalse(evidence["visual_acceptance"])
        with self.assertRaises(ValueError):
            gemini_app.collect(type("Args", (), {
                "packet":str(packet), "input":str(mapped), "output":str(mapped)})())

    def test_review_gate(self):
        self.task["action_review"]["passed"] = False
        self.task_path.write_text(json.dumps(self.task), encoding="utf-8")
        with self.assertRaises(ValueError):
            gemini_app.prepare(type("Args", (), {"task":str(self.task_path),
                                                 "output":str(self.root/"packet")})())


if __name__ == "__main__":
    unittest.main()
