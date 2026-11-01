import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from edit_quality import suggest_boundaries, continuity_qc

class EditQualityTests(unittest.TestCase):
    def test_boundary_suggestions_are_unreviewed_and_trace_clip(self):
        project={"clips":[{"clip_id":"C1","episode_id":"EP01","start_sec":9.5,"end_sec":15.0,"event_time_sec":12.0}]}
        cues={"EP01":{"cues":[
            {"type":"shot_change","time_sec":10.0},
            {"type":"subtitle","start_sec":10.2,"end_sec":11.0},
            {"type":"subtitle","start_sec":12.0,"end_sec":13.0},
            {"type":"silence","time_sec":14.0}
        ]}}
        out=suggest_boundaries(project,cues)
        self.assertEqual(out["clips"][0]["boundary_suggestion"]["review_status"],"UNREVIEWED")
        self.assertEqual(out["boundary_engine_version"],"3.9.0")
        self.assertLessEqual(out["clips"][0]["boundary_suggestion"]["suggested_start_sec"],9.5+1e-6)
        self.assertGreaterEqual(out["clips"][0]["boundary_suggestion"]["suggested_end_sec"],15.0-1e-6)

    def test_qc_blocks_invalid_range(self):
        out=continuity_qc({"clips":[{"clip_id":"C1","start_sec":4,"end_sec":2}]})
        self.assertFalse(out["release_ready"])
        self.assertEqual(out["release_blockers"],1)

    def test_qc_warns_unreviewed_boundary(self):
        out=continuity_qc({"clips":[{"clip_id":"C1","start_sec":0,"end_sec":2,"boundary_suggestion":{"review_status":"UNREVIEWED"}}]})
        self.assertTrue(out["release_ready"])
        self.assertIn("BOUNDARY_NOT_REVIEWED",[x["code"] for x in out["issues"]])

    def test_qc_detects_overlap(self):
        out=continuity_qc({"clips":[
            {"clip_id":"C1","source_video":"a.mp4","start_sec":0,"end_sec":3},
            {"clip_id":"C2","source_video":"a.mp4","start_sec":2,"end_sec":4}
        ]})
        self.assertIn("SOURCE_OVERLAP",[x["code"] for x in out["issues"]])

if __name__=="__main__": unittest.main()
