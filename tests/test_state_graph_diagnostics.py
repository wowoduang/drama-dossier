import unittest
from character_state_model import state_graph_issues

class StateGraphDiagnosticsTests(unittest.TestCase):
    def test_detects_transition_fork(self):
        data = {
            "character_states": [
                {"state_id":"S1","character_id":"A","dimension":"goal","aspect":"main","target_character_id":None,"playback_time":1,"value":"wait"},
                {"state_id":"S2","character_id":"A","dimension":"goal","aspect":"main","target_character_id":None,"playback_time":2,"value":"search"},
                {"state_id":"S3","character_id":"A","dimension":"goal","aspect":"main","target_character_id":None,"playback_time":3,"value":"leave"}
            ],
            "character_state_changes": [
                {"change_id":"C1","from_state_id":"S1","to_state_id":"S2"},
                {"change_id":"C2","from_state_id":"S1","to_state_id":"S3"}
            ]
        }
        codes = {x["code"] for x in state_graph_issues(data)}
        self.assertIn("STATE_TRANSITION_FORK", codes)

    def test_detects_overlapping_conflicting_state_validity(self):
        data = {
            "character_states": [
                {"state_id":"S1","character_id":"A","dimension":"goal","aspect":"main","target_character_id":None,"playback_time":1,"valid_until_sec":10,"value":"wait"},
                {"state_id":"S2","character_id":"A","dimension":"goal","aspect":"main","target_character_id":None,"playback_time":5,"valid_until_sec":None,"value":"escape"}
            ],
            "character_state_changes": []
        }
        codes = {x["code"] for x in state_graph_issues(data)}
        self.assertIn("OVERLAPPING_STATE_LIFECYCLES", codes)

if __name__ == "__main__":
    unittest.main()
