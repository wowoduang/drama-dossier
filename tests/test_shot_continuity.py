import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from shot_continuity import candidates, qc

class ShotContinuityTests(unittest.TestCase):
    def setUp(self):
        self.d={"episode":{"episode_id":"EP01"},"events":[{"event_id":"EV1"}],"evidence":[{"evidence_id":"ED1"}]}
        self.mm={"cues":[{"type":"shot_change","time_sec":2.0}]}
    def test_shot_reverse_shot_candidate_requires_reviewed_opposite_gaze(self):
        obs={"shots":[
            {"shot_id":"S1","start_sec":0,"end_sec":2,"visible_character_ids":["A"],"speaker_character_id":"A","gaze_direction":"right","review_status":"PASS","event_ids":["EV1"],"evidence_ids":["ED1"]},
            {"shot_id":"S2","start_sec":2.1,"end_sec":4,"visible_character_ids":["B"],"gaze_direction":"left","action_phase":"listening","review_status":"PASS","event_ids":["EV1"],"evidence_ids":["ED1"]}
        ]}
        out=candidates(self.d,self.mm,obs)
        self.assertTrue(any(x["candidate_type"]=="SHOT_REVERSE_SHOT" for x in out["candidates"]))
        self.assertTrue(out["all_candidates_unreviewed"])
    def test_reaction_shot_candidate(self):
        obs={"shots":[
            {"shot_id":"S1","start_sec":0,"end_sec":1,"visible_character_ids":["A"],"speaker_character_id":"A","review_status":"PASS"},
            {"shot_id":"S2","start_sec":1.1,"end_sec":2,"visible_character_ids":["B"],"action_phase":"reaction","reaction_target_character_id":"A","review_status":"PASS","event_ids":["EV1"]}
        ]}
        out=candidates(self.d,self.mm,obs)
        self.assertTrue(any(x["candidate_type"]=="REACTION_SHOT" for x in out["candidates"]))
    def test_action_continuity_candidate(self):
        obs={"shots":[
            {"shot_id":"S1","start_sec":0,"end_sec":1,"actor_character_id":"A","action_description":"推开木门","action_phase":"start","continuity_status":"continuous"},
            {"shot_id":"S2","start_sec":1.1,"end_sec":2,"actor_character_id":"A","action_description":"推开木门","action_phase":"end","continuity_status":"continuous"}
        ]}
        out=candidates(self.d,self.mm,obs)
        self.assertTrue(any(x["candidate_type"]=="ACTION_CONTINUITY" for x in out["candidates"]))
    def test_invalid_observation_range_rejected(self):
        with self.assertRaises(ValueError):
            candidates(self.d,self.mm,{"shots":[{"start_sec":3,"end_sec":2}]})
    def test_qc_flags_missing_evidence(self):
        out=qc({"candidates":[{"candidate_id":"C1","candidate_type":"REACTION_SHOT","shot_ids":["S1","S2"],"character_ids":[],"evidence_ids":[],"event_ids":[],"review_status":"UNREVIEWED"}]})
        self.assertIn("NO_EVENT_OR_EVIDENCE_LINK",[x["code"] for x in out["issues"]])
if __name__=="__main__":unittest.main()
