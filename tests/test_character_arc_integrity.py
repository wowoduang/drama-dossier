import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from character_arc_integrity import validate_arc
from dossier_v3 import empty_dossier, validate


class CharacterArcIntegrityTests(unittest.TestCase):
    def project(self, enabled):
        return {
            "clips": [{
                "clip_id": "C1",
                "episode_id": "EP01",
                "enabled": enabled,
                "event_ids": ["E1"],
                "character_state_ids": ["S1"],
                "character_state_change_ids": ["CH1"]
            }],
            "character_state_context": {
                "states": [{"episode_id": "EP01", "state_id": "S1", "event_ids": ["E1"]}],
                "changes": [{"episode_id": "EP01", "change_id": "CH1"}]
            }
        }

    def plan(self):
        return {
            "arc_id": "ARC1",
            "milestones": [{
                "milestone_id": "M1",
                "episode_id": "EP01",
                "label": "初始状态与转折",
                "required_state_ids": ["S1"],
                "required_change_ids": ["CH1"],
                "required_event_ids": ["E1"]
            }]
        }

    def test_disabled_clip_does_not_count_as_milestone_coverage(self):
        result = validate_arc(self.project(False), self.plan())
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_milestones"][0]["status"], "MISSING")

    def test_enabled_clip_counts_as_milestone_coverage(self):
        result = validate_arc(self.project(True), self.plan())
        self.assertTrue(result["complete"])
        self.assertEqual(result["passed_milestones"], 1)

    def test_unknown_plan_field_is_rejected(self):
        plan = self.plan()
        plan["milestones"][0]["required_state_id"] = "S1"
        with self.assertRaisesRegex(ValueError, "未知字段"):
            validate_arc(self.project(True), plan)

    def test_unknown_clip_state_reference_field_is_not_silently_used(self):
        project = self.project(True)
        project["clips"][0]["character_state_id"] = ["S1"]
        # The compiler ignores unknown project clip metadata, so the required
        # plural ID remains missing rather than silently accepting the typo.
        project["clips"][0].pop("character_state_ids")
        result = validate_arc(project, self.plan())
        self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
