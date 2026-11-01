import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dossier_v3 import (
    SCHEMA_VERSION,
    add_review,
    empty_dossier,
    import_scene_candidates,
    load,
    markdown_report,
    migrate_to_v33,
    migrate_to_v34,
    save_atomic,
    unresolved,
    validate,
)
from highlight_engine import generate_highlights, select_project, weighted_score
from render_project import build_filter, enabled_clips
from scene_candidates import cuts_to_segments
from arc_candidates import generate_arcs, _event_dims
from multimodal_scan import sample_times, load_subtitles, subtitle_cues, audio_cues
from import_ai_review import import_review


class DossierTests(unittest.TestCase):
    def sample(self):
        d = empty_dossier("E01", "测试集", 120, "cut-A")
        d["evidence"] = [
            {
                "evidence_id": "EVD1",
                "type": "SUB",
                "source": "sub.srt",
                "start_sec": 1,
                "end_sec": 2,
                "raw": "我走了",
                "quality": "high",
                "review_status": "PASS",
            }
        ]
        d["characters"] = [
            {"character_id": "CHR1", "canonical_name": "甲", "aliases": [], "identity_status": "confirmed", "evidence_ids": ["EVD1"], "review_status": "PASS"},
            {"character_id": "CHR2", "canonical_name": "乙", "aliases": [], "identity_status": "confirmed", "evidence_ids": [], "review_status": "PASS"},
        ]
        d["scenes"] = [
            {"scene_id": "SCN1", "playback_start": 0, "playback_end": 10, "boundary_confidence": "probable", "evidence_ids": ["EVD1"], "review_status": "PASS"}
        ]
        d["events"] = [
            {
                "event_id": "EVT1",
                "scene_id": "SCN1",
                "playback_time": 1,
                "actor": "CHR1",
                "action": "揭露真相并拒绝命令",
                "patient": "CHR2",
                "recipient": None,
                "target": None,
                "modality": "actual",
                "polarity": "positive",
                "quote": "我走了",
                "evidence_ids": ["EVD1"],
                "certainty": "F1_DIRECT",
                "highlight_dimensions": {"importance": 90, "emotion": 70, "conflict": 80, "twist": 90, "suspense": 75, "visual": 60, "standalone": 65},
                "context_required": "low",
                "review_status": "UNREVIEWED",
            }
        ]
        d["storylines"] = [
            {
                "storyline_id": "STL1",
                "title": "真相线",
                "summary": "甲揭露真相。",
                "character_ids": ["CHR1", "CHR2"],
                "event_ids": ["EVT1"],
                "status": "ACTIVE",
                "evidence_ids": ["EVD1"],
                "review_status": "PASS",
            }
        ]
        return d

    def test_valid_document(self):
        self.assertEqual(validate(self.sample()), [])

    def test_missing_reference_detected(self):
        d = self.sample()
        d["events"][0]["actor"] = "NOPE"
        self.assertTrue(any("人物不存在" in x for x in validate(d)))

    def test_invalid_enum_detected(self):
        d = self.sample()
        d["events"][0]["modality"] = "maybe"
        self.assertTrue(any("modality" in x for x in validate(d)))

    def test_storyline_reference_detected(self):
        d = self.sample()
        d["storylines"][0]["event_ids"] = ["NOPE"]
        self.assertTrue(any("storylines[0].event_ids" in x for x in validate(d)))

    def test_highlight_dimension_range_detected(self):
        d = self.sample()
        d["highlights"] = [{
            "highlight_id": "HL1", "start_sec": 0, "end_sec": 2, "event_ids": ["EVT1"],
            "storyline_ids": ["STL1"], "evidence_ids": ["EVD1"], "dimensions": {"importance": 101},
            "total_score": 50, "context_required": "low", "review_status": "UNREVIEWED"
        }]
        self.assertTrue(any("importance" in x for x in validate(d)))

    def test_review_is_audited_and_keeps_id(self):
        d = self.sample()
        old = copy.deepcopy(d["events"][0])
        add_review(d, "EVT1", "PASS", "画面与字幕互证", ["EVD1"], {"action": "把信交给乙"}, "reviewer")
        self.assertEqual(d["events"][0]["event_id"], "EVT1")
        self.assertEqual(d["events"][0]["action"], "把信交给乙")
        self.assertEqual(d["events"][0]["review_status"], "PASS")
        self.assertEqual(d["review_history"][0]["old_value"], old)
        self.assertEqual(d["review_history"][0]["collection"], "events")
        self.assertEqual(validate(d), [])

    def test_review_updates_highlight(self):
        d = self.sample()
        generate_highlights(d)
        hid = d["highlights"][0]["highlight_id"]
        add_review(d, hid, "PASS", "回看原片确认边界", ["EVD1"])
        self.assertEqual(d["highlights"][0]["review_status"], "PASS")
        self.assertEqual(validate(d), [])

    def test_review_requires_reason(self):
        d = self.sample()
        with self.assertRaises(ValueError):
            add_review(d, "EVT1", "PASS", "", ["EVD1"])

    def test_rekey_rejected(self):
        d = self.sample()
        with self.assertRaises(ValueError):
            add_review(d, "EVT1", "PASS", "bad", [], {"event_id": "EVT2"})

    def test_atomic_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "d.json"
            save_atomic(self.sample(), p)
            self.assertEqual(validate(load(p)), [])

    def test_report_is_view(self):
        self.assertIn("甲", markdown_report(self.sample()))

    def test_unresolved_tracks_all_reviewable_entities(self):
        d = self.sample()
        d["characters"][0]["review_status"] = "NEED_EVIDENCE"
        todo = unresolved(d)
        self.assertIn(("characters", "CHR1", "NEED_EVIDENCE"), todo)
        self.assertIn(("events", "EVT1", "UNREVIEWED"), todo)

    def test_migrate_v32_adds_v34_collections(self):
        d = self.sample()
        d["schema_version"] = "3.2.0"
        d.pop("storylines")
        d.pop("highlights")
        out = migrate_to_v33(d)
        self.assertEqual(out["schema_version"], SCHEMA_VERSION)
        self.assertEqual(out["storylines"], [])
        self.assertEqual(out["highlights"], [])
        self.assertEqual(validate(out), [])

    def test_import_scene_candidates(self):
        d = self.sample()
        payload = {"method": "test", "segments": [{"start_sec": 10, "end_sec": 20, "score": 0.8}]}
        n = import_scene_candidates(d, payload)
        self.assertEqual(n, 1)
        self.assertEqual(d["scenes"][-1]["boundary_confidence"], "uncertain")
        self.assertEqual(d["scenes"][-1]["review_status"], "UNREVIEWED")
        self.assertEqual(validate(d), [])

    def test_import_scene_candidates_deduplicates(self):
        d = self.sample()
        payload = {"segments": [{"start_sec": 0, "end_sec": 10}]}
        self.assertEqual(import_scene_candidates(d, payload), 0)

    def test_cut_segments_are_contiguous(self):
        segs = cuts_to_segments([{"time_sec": 2.0, "score": 0.9}, {"time_sec": 5.0, "score": 0.8}], 8.0)
        self.assertEqual([(s["start_sec"], s["end_sec"]) for s in segs], [(0.0, 2.0), (2.0, 5.0), (5.0, 8.0)])
        self.assertTrue(all(s["boundary_confidence"] == "uncertain" for s in segs))

    def test_generate_highlights_is_explainable(self):
        d = self.sample()
        self.assertEqual(generate_highlights(d, "60s"), 1)
        h = d["highlights"][0]
        self.assertEqual(h["event_ids"], ["EVT1"])
        self.assertEqual(h["storyline_ids"], ["STL1"])
        self.assertEqual(h["score_source"], "event.highlight_dimensions")
        self.assertGreater(h["total_score"], 70)
        self.assertEqual(validate(d), [])

    def test_weighted_score_changes_by_preset(self):
        dims = {"importance": 100, "emotion": 0, "conflict": 0, "twist": 0, "suspense": 0, "visual": 0, "standalone": 100}
        self.assertNotEqual(weighted_score(dims, "30s"), weighted_score(dims, "5m"))

    def test_select_project_uses_highlights(self):
        d = self.sample()
        generate_highlights(d, "60s")
        project = select_project(d, "60s", "episode.mp4")
        self.assertEqual(project["source_video"], "episode.mp4")
        self.assertEqual(len(project["clips"]), 1)
        self.assertGreater(project["estimated_duration_sec"], 0)

    def test_render_filter_audio_and_video(self):
        project = {"clips": [{"clip_id": "CLP_001", "start_sec": 1, "end_sec": 2, "enabled": True}]}
        clips = enabled_clips(project)
        self.assertIn("concat=n=1:v=1:a=1", build_filter(clips, True))
        self.assertIn("concat=n=1:v=1:a=0", build_filter(clips, False))


    def test_multimodal_sample_times_cover_duration(self):
        self.assertEqual(sample_times(25, 10), [0.0, 10.0, 20.0, 24.95])
        self.assertEqual(sample_times(0, 10), [])

    def test_audio_peak_does_not_claim_emotion(self):
        event = {"action": "人物进入房间", "quote": "", "highlight_dimensions": {}}
        baseline = _event_dims([event], [], 0)
        loud = _event_dims([event], [{"cue_type": "audio_peak", "score": 1.0}], 0)
        self.assertEqual(baseline["emotion"], loud["emotion"])
        self.assertEqual(baseline["importance"], loud["importance"])

    def test_generate_arc_contains_distinct_setup_peak_reaction_payoff(self):
        d = self.sample()
        d["episode"]["duration_sec"] = 90
        d["events"][0]["playback_time"] = 10
        d["events"][0]["action"] = "甲揭露关键真相"
        d["events"].append({
            "event_id": "EVT_SETUP", "scene_id": "SCN1", "playback_time": 5,
            "actor": "CHR1", "action": "甲提出疑问", "patient": "CHR2", "recipient": None, "target": None,
            "modality": "actual", "polarity": "positive", "quote": "你早就知道？",
            "evidence_ids": ["EVD1"], "certainty": "F1_DIRECT", "review_status": "PASS"
        })
        d["events"].append({
            "event_id": "EVT_REACTION", "scene_id": "SCN1", "playback_time": 13,
            "actor": "CHR2", "action": "乙震惊后退", "patient": None, "recipient": None, "target": None,
            "modality": "actual", "polarity": "positive", "quote": "不可能。",
            "evidence_ids": ["EVD1"], "certainty": "F1_DIRECT", "review_status": "PASS"
        })
        d["events"].append({
            "event_id": "EVT_PAYOFF", "scene_id": "SCN1", "playback_time": 30,
            "actor": "CHR1", "action": "甲拿出信件作为证明", "patient": "CHR2", "recipient": None, "target": None,
            "modality": "actual", "polarity": "positive", "quote": "证据在这里。",
            "evidence_ids": ["EVD1"], "certainty": "F1_DIRECT", "review_status": "PASS"
        })
        d["storylines"][0]["event_ids"] = ["EVT_SETUP", "EVT1", "EVT_REACTION", "EVT_PAYOFF"]
        d["storylines"][0]["review_status"] = "PASS"
        d["events"][0]["review_status"] = "PASS"
        d["causal_edges"].append({
            "cause_id": "CAU_ARC", "from_event": "EVT1", "to_event": "EVT_PAYOFF",
            "type": "REVEALS", "reasoning": "后续证据揭示同一事件的结果。",
            "evidence_ids": ["EVD1"], "counter_evidence_ids": [], "review_status": "PASS"
        })
        scan = {"media": {"duration_sec": 90}, "cues": [{
            "cue_id": "CUE_PEAK", "cue_type": "subtitle_keyword", "start_sec": 9.5,
            "end_sec": 10.5, "score": .9, "source": "test", "confidence": "low", "details": {}
        }]}
        self.assertGreaterEqual(generate_arcs(d, scan, max_arcs=20), 1)
        arc = next(a for a in d["story_arcs"] if a.get("anchor_event_id") == "EVT1")
        roles = {seg["role"] for seg in arc["segments"]}
        self.assertTrue({"setup", "peak", "reaction", "payoff"}.issubset(roles))
        payoff = next(seg for seg in arc["segments"] if seg["role"] == "payoff")
        self.assertIn("EVT_PAYOFF", payoff["event_ids"])
        self.assertEqual(validate(d), [])

    def test_generate_arcs_is_idempotent(self):
        d = self.sample()
        scan = {"media": {"duration_sec": 120}, "cues": [{
            "cue_id": "CUE1", "cue_type": "subtitle_keyword", "start_sec": 1,
            "end_sec": 2, "score": .8, "source": "test", "confidence": "low", "details": {}
        }]}
        first = generate_arcs(d, scan, max_arcs=10)
        second = generate_arcs(d, scan, max_arcs=10)
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0)

    def test_import_ai_review_adds_unreviewed_visual_evidence_and_event(self):
        d = self.sample()
        review = {
            "observations": [{
                "start_sec": 3.0, "end_sec": 5.0,
                "frame_refs": ["frame_00003.000_uniform_sample.jpg"],
                "visual_description": "甲将一封信放在桌面上。",
                "confidence": "medium", "continuity_notes": "需回看前后镜头。"
            }],
            "event_candidates": [{
                "playback_time": 4.0, "action": "甲将信放在桌面上",
                "actor": "CHR1", "patient": None, "recipient": None, "target": None,
                "modality": "actual", "polarity": "positive", "certainty": "I2_PLAUSIBLE",
                "observation_index": 0
            }]
        }
        counts = import_review(d, review)
        self.assertEqual(counts["observations"], 1)
        self.assertEqual(counts["events"], 1)
        self.assertEqual(d["evidence"][-1]["type"], "VIS")
        self.assertEqual(d["evidence"][-1]["review_status"], "UNREVIEWED")
        self.assertEqual(d["events"][-1]["review_status"], "UNREVIEWED")
        self.assertEqual(validate(d), [])

    def test_import_ai_review_rejects_unknown_character_transactionally(self):
        d = self.sample()
        before = copy.deepcopy(d)
        review = {
            "observations": [{"start_sec": 1, "end_sec": 2, "visual_description": "有人拿起信件。"}],
            "event_candidates": [{"playback_time": 1.5, "action": "拿起信件", "actor": "CHR_UNKNOWN", "observation_index": 0}]
        }
        with self.assertRaises(ValueError):
            import_review(d, review)
        self.assertEqual(d, before)

    def test_arc_validation_rejects_unknown_segment_role(self):
        d = self.sample()
        d["story_arcs"] = [{
            "arc_id": "ARC_BAD", "start_sec": 1, "end_sec": 3, "anchor_sec": 2,
            "segments": [{"role": "climaxish", "start_sec": 1, "end_sec": 2, "event_ids": ["EVT1"], "cue_ids": []}],
            "event_ids": ["EVT1"], "storyline_ids": ["STL1"], "evidence_ids": ["EVD1"], "cue_ids": [],
            "dimensions": {}, "context_required": "high", "review_status": "UNREVIEWED"
        }]
        self.assertTrue(any("role 无效" in x for x in validate(d)))


    def test_later_unrelated_event_is_not_a_payoff(self):
        d = self.sample()
        d["episode"]["duration_sec"] = 100
        d["events"][0]["playback_time"] = 10
        d["events"].append({
            "event_id": "EVT_OTHER", "scene_id": "SCN1", "playback_time": 30,
            "actor": "CHR2", "action": "乙在另一条故事线上离开", "patient": None,
            "recipient": None, "target": None, "modality": "actual", "polarity": "positive",
            "quote": "我走了。", "evidence_ids": ["EVD1"], "certainty": "I2_PLAUSIBLE", "review_status": "PASS"
        })
        d["storylines"][0]["event_ids"] = ["EVT1"]
        scan = {"media": {"duration_sec": 100}, "cues": [{
            "cue_id": "CUE_PEAK", "cue_type": "subtitle_keyword", "start_sec": 9.5,
            "end_sec": 10.5, "score": .9, "source": "test", "confidence": "low", "details": {}
        }]}
        generate_arcs(d, scan, max_arcs=10)
        arc = next(a for a in d["story_arcs"] if a.get("anchor_event_id") == "EVT1")
        self.assertNotIn("EVT_OTHER", arc["event_ids"])
        self.assertIn("payoff", arc["missing_roles"])

    def test_import_ai_review_is_transactional_on_bad_late_scene(self):
        d = self.sample()
        before = copy.deepcopy(d)
        review = {
            "observations": [{"start_sec": 1, "end_sec": 2, "visual_description": "有人拿起信件。"}],
            "event_candidates": [{"playback_time": 1.5, "action": "拿起信件", "actor": None, "observation_index": 0}],
            "scene_candidates": {"segments": "invalid"}
        }
        with self.assertRaises(ValueError):
            import_review(d, review)
        self.assertEqual(d, before)


    def test_every_transcript_line_becomes_alignment_cue_not_emotion_claim(self):
        cues = subtitle_cues([
            {"start_sec": 1.0, "end_sec": 2.0, "text": "我回来了。"},
            {"start_sec": 3.0, "end_sec": 4.0, "text": "你竟然背叛了我！"},
        ])
        transcript = [c for c in cues if c["cue_type"] == "transcript_segment"]
        emphasis = [c for c in cues if c["cue_type"] == "subtitle_keyword"]
        self.assertEqual(len(transcript), 2)
        self.assertEqual(len(emphasis), 1)
        self.assertTrue(all(c["confidence"] == "low" for c in cues))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_audio_silence_is_a_low_confidence_locator(self):
        with tempfile.TemporaryDirectory() as td:
            video = Path(td) / "silence.mp4"
            cmd = [
                shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=8",
                "-f", "lavfi", "-i", r"aevalsrc=if(lt(t\,2)\,0.3*sin(2*PI*440*t)\,if(gt(t\,6)\,0.3*sin(2*PI*440*t)\,0)):s=8000:d=8",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            cues, meta = audio_cues(video, .5)
            silence = [c for c in cues if c["cue_type"] == "audio_silence"]
            self.assertTrue(meta["available"])
            self.assertTrue(any(1.5 <= c["start_sec"] <= 2.5 and 5.5 <= c["end_sec"] <= 6.5 for c in silence))
            self.assertTrue(all(c["confidence"] == "low" for c in silence))


    def test_migrate_v33_preserves_facts_and_adds_multimodal_collections(self):
        legacy = self.sample()
        legacy["schema_version"] = "3.3.0"
        legacy.pop("media_cues", None)
        legacy.pop("story_arcs", None)
        original_events = copy.deepcopy(legacy["events"])
        migrated = migrate_to_v34(legacy)
        self.assertEqual(migrated["schema_version"], SCHEMA_VERSION)
        self.assertEqual(migrated["events"], original_events)
        self.assertEqual(migrated["media_cues"], [])
        self.assertEqual(migrated["story_arcs"], [])
        self.assertEqual(validate(migrated), [])


    def test_edit_project_preserves_story_arc_provenance(self):
        d = self.sample()
        d["story_arcs"] = [{"arc_id": "ARC_SOURCE", "start_sec": 1, "end_sec": 8, "anchor_sec": 4,
                            "segments": [], "event_ids": ["EVT1"], "storyline_ids": ["STL1"],
                            "evidence_ids": ["EVD1"], "cue_ids": [], "dimensions": {},
                            "context_required": "medium", "review_status": "UNREVIEWED"}]
        d["highlights"] = [{
            "highlight_id": "HL_ARC", "start_sec": 1, "end_sec": 8, "title": "弧线高光",
            "reason": "由剧情弧线候选转入", "event_ids": ["EVT1"], "storyline_ids": ["STL1"],
            "evidence_ids": ["EVD1"], "dimensions": {"importance": 80, "emotion": 60, "conflict": 70, "twist": 50, "suspense": 50, "visual": 35, "standalone": 40},
            "total_score": 60, "context_required": "medium", "review_status": "UNREVIEWED",
            "source_arc_id": "ARC_SOURCE", "story_arc_ids": ["ARC_SOURCE"]
        }]
        project = select_project(d, "30s")
        self.assertEqual(project["clips"][0]["story_arc_ids"], ["ARC_SOURCE"])


    def test_duplicate_same_time_event_candidates_do_not_duplicate_arcs(self):
        d = self.sample()
        d["events"][0]["playback_time"] = 10
        d["events"][0]["action"] = "甲揭露关键真相"
        duplicate = copy.deepcopy(d["events"][0])
        duplicate["event_id"] = "EVT_DUP"
        duplicate["evidence_ids"] = ["EVD1"]
        duplicate["review_status"] = "UNREVIEWED"
        d["events"].append(duplicate)
        d["storylines"][0]["event_ids"] = ["EVT1", "EVT_DUP"]
        scan = {"media": {"duration_sec": 120}, "cues": [{
            "cue_id": "CUE_PEAK", "cue_type": "subtitle_keyword", "start_sec": 9.5,
            "end_sec": 10.5, "score": .9, "source": "test", "confidence": "low", "details": {}
        }]}
        generate_arcs(d, scan, max_arcs=10)
        anchored = [a for a in d["story_arcs"] if a.get("anchor_sec") == 10]
        self.assertEqual(len(anchored), 1)


    def test_review_story_arc_updates_semantic_status(self):
        d = self.sample()
        d["story_arcs"] = [{"arc_id": "ARC_REVIEW", "start_sec": 1, "end_sec": 8, "anchor_sec": 4,
                            "segments": [], "event_ids": ["EVT1"], "storyline_ids": ["STL1"],
                            "evidence_ids": ["EVD1"], "cue_ids": [], "dimensions": {},
                            "context_required": "medium", "semantic_status": "NEEDS_CHATGPT_REVIEW",
                            "review_status": "UNREVIEWED"}]
        add_review(d, "ARC_REVIEW", "PASS", "逐段回看原片，四段叙事关系成立。", ["EVD1"])
        self.assertEqual(d["story_arcs"][0]["semantic_status"], "HUMAN_CONFIRMED")
        self.assertEqual(validate(d), [])


if __name__ == "__main__":
    unittest.main()
