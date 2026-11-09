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
        self.assertGreaterEqual(out["clips"][0]["boundary_suggestion"]["suggested_start_sec"],9.5)
        self.assertLessEqual(out["clips"][0]["boundary_suggestion"]["suggested_end_sec"],15.0)

    def test_anchor_padding_changes_boundaries_and_keeps_original(self):
        project={"clips":[{"clip_id":"C1","episode_id":"E1","start_sec":0,"end_sec":20,"event_time_sec":10}]}
        result=suggest_boundaries(project,{})
        suggestion=result['boundary_suggestions'][0]
        self.assertEqual((suggestion['suggested_start_sec'],suggestion['suggested_end_sec']),(8.8,11.8))
        self.assertEqual((project['clips'][0]['start_sec'],project['clips'][0]['end_sec']),(0,20))
        other=suggest_boundaries(project,{},pre_roll=3,post_roll=4)['boundary_suggestions'][0]
        self.assertEqual((other['suggested_start_sec'],other['suggested_end_sec']),(7,14))

    def test_subtitles_and_state_evidence_are_retained(self):
        project={'clips':[{'clip_id':'C1','episode_id':'E1','start_sec':0,'end_sec':20,'event_time_sec':10,'state_evidence_ranges':[{'start_sec':4,'end_sec':6}]}]}
        cues={'E1':{'cues':[{'type':'subtitle','start_sec':2,'end_sec':3},{'type':'subtitle','start_sec':15,'end_sec':17}]}}
        s=suggest_boundaries(project,cues)['boundary_suggestions'][0]
        self.assertLessEqual(s['suggested_start_sec'],2);self.assertGreaterEqual(s['suggested_end_sec'],17)

    def test_boundary_inside_subtitle_is_pinned_and_flagged(self):
        project={'clips':[{'clip_id':'C1','episode_id':'E1','start_sec':10,'end_sec':15}]}
        cues={'E1':{'cues':[{'type':'subtitle','start_sec':9,'end_sec':11},{'type':'subtitle','start_sec':14,'end_sec':16}]}}
        s=suggest_boundaries(project,cues)['boundary_suggestions'][0]
        self.assertEqual((s['suggested_start_sec'],s['suggested_end_sec']),(10,15))
        self.assertIn('BOUNDARY_PINNED_REVIEW',s['risk_flags'])

    def test_rejects_invalid_anchor_and_state_evidence(self):
        p={'clips':[{'clip_id':'C1','start_sec':1,'end_sec':5,'event_time_sec':6}]}
        with self.assertRaises(ValueError):suggest_boundaries(p,{})
        p['clips'][0].update(event_time_sec=3,state_evidence_ranges=[{'start_sec':0,'end_sec':2}])
        with self.assertRaises(ValueError):suggest_boundaries(p,{})

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

