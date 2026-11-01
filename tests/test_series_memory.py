import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dossier_v3 import empty_dossier, save_atomic
from series_memory import batch_import, export_memory, import_dossier, init_db, link_characters, link_storylines, link_threads, pending_candidates, reject_candidate, search, status, connect, add_cross_causal_link, review_cross_causal_link


class SeriesMemoryTests(unittest.TestCase):
    def make_dossier(self, ep, char_id="CHR1", name="林远", alias="阿远", line_id="STL1", event_id="EVT1", action="保护同伴"):
        d = empty_dossier(ep, f"第{ep[-1]}集", 120, "master")
        d["characters"] = [{"character_id": char_id, "canonical_name": name, "aliases": [alias], "identity_status": "probable", "evidence_ids": [], "review_status": "PASS"}]
        d["events"] = [{"event_id": event_id, "scene_id": None, "playback_time": 22.5, "actor": char_id, "action": action, "patient": None, "recipient": None, "target": None, "modality": "actual", "polarity": "positive", "quote": "我来保护你", "evidence_ids": [], "certainty": "I1_STRONG", "review_status": "PASS"}]
        d["storylines"] = [{"storyline_id": line_id, "title": "守护同伴", "summary": "主角保护伙伴", "character_ids": [char_id], "event_ids": [event_id], "status": "ACTIVE", "evidence_ids": [], "review_status": "PASS"}]
        d["threads"] = [{"thread_id": "TH1" if ep == "EP001" else "TH9", "description": "失踪信件的秘密", "first_event": event_id, "status": "OPEN", "evidence_ids": [], "review_status": "PASS"}]
        return d

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "series.sqlite"
        self.d1 = self.root / "ep001.json"
        self.d2 = self.root / "ep002.json"
        save_atomic(self.make_dossier("EP001"), self.d1)
        save_atomic(self.make_dossier("EP002", char_id="CHR9", line_id="STL9", event_id="EVT9", action="再次保护同伴"), self.d2)
        init_db(self.db, "SER01", "测试连续剧")

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_two_episodes_and_create_candidates(self):
        self.assertEqual(import_dossier(self.db, "SER01", self.d1)["status"], "IMPORTED")
        self.assertEqual(import_dossier(self.db, "SER01", self.d2)["status"], "IMPORTED")
        c = pending_candidates(self.db, "SER01")
        self.assertEqual(len(c["identity_candidates"]), 1)
        self.assertEqual(len(c["storyline_candidates"]), 1)
        self.assertEqual(len(c["thread_candidates"]), 1)
        self.assertEqual(status(self.db, "SER01")["episode_count"], 2)
        self.assertEqual(status(self.db, "SER01")["thread_candidates_pending"], 1)

    def test_import_is_idempotent(self):
        import_dossier(self.db, "SER01", self.d1)
        self.assertEqual(import_dossier(self.db, "SER01", self.d1)["status"], "SKIPPED_UNCHANGED")

    def test_changed_dossier_requires_replace(self):
        import_dossier(self.db, "SER01", self.d1)
        save_atomic(self.make_dossier("EP001", action="改变后的事件"), self.d1)
        with self.assertRaises(ValueError):
            import_dossier(self.db, "SER01", self.d1)
        self.assertEqual(import_dossier(self.db, "SER01", self.d1, replace=True)["status"], "IMPORTED")

    def test_link_character_and_storyline_merges_cross_episode_memory(self):
        import_dossier(self.db, "SER01", self.d1)
        import_dossier(self.db, "SER01", self.d2)
        gid = link_characters(self.db, "SER01", "EP001:CHR1", "EP002:CHR9", "姓名与连续剧情核实为同一人", "tester")
        line_gid = link_storylines(self.db, "SER01", "EP001:STL1", "EP002:STL9", "两集属于同一守护线", "tester")
        thread_gid = link_threads(self.db, "SER01", "EP001:TH1", "EP002:TH9", "两集指向同一封失踪信件", "tester")
        out = export_memory(self.db, "SER01", self.root / "memory.json")
        char = next(x for x in out["characters"] if x["global_character_id"] == gid)
        self.assertEqual(len(char["mentions"]), 2)
        self.assertEqual(len(char["event_timeline"]), 2)
        line = next(x for x in out["storylines"] if x["global_storyline_id"] == line_gid)
        self.assertEqual(len(line["mentions"]), 2)
        thread = next(x for x in out["threads"] if x["global_thread_id"] == thread_gid)
        self.assertEqual(len(thread["mentions"]), 2)
        self.assertTrue((self.root / "memory.json").exists())
        self.assertIn("evidence_index", out)

    def test_replacing_episode_reopens_cross_episode_causal_edges(self):
        from dossier_v3 import load, save_atomic
        for path,ep in ((self.d1,'EP001'),(self.d2,'EP002')):
            d=self.make_dossier(ep)
            d['evidence']=[{'evidence_id':'EVD1','type':'SUB','source':ep+'.srt','start_sec':20,'end_sec':25,'raw':'证据','observation':'','quality':'high','locator':'cue','review_status':'PASS'}]
            d['events'][0]['evidence_ids']=['EVD1']
            save_atomic(d,path); import_dossier(self.db,'SER01',path)
        edge=add_cross_causal_link(self.db,'SER01','EP001:EVT1','EP002:EVT1','CAUSES','跨集动机',['EP001:EVD1','EP002:EVD1'])
        review_cross_causal_link(self.db,'SER01',edge,'PASS','核对原片','tester')
        d=load(self.d2); d['episode']['title']='更新后的集标题'; save_atomic(d,self.d2)
        import_dossier(self.db,'SER01',self.d2,replace=True)
        c=connect(self.db)
        try: edge_status=c.execute('SELECT review_status FROM cross_episode_causal_edges WHERE edge_id=?',(edge,)).fetchone()[0]
        finally:c.close()
        self.assertEqual(edge_status,'NEED_EVIDENCE')

    def test_search_finds_event_and_character(self):
        import_dossier(self.db, "SER01", self.d1)
        hits = search(self.db, "SER01", "保护")
        people = search(self.db, "SER01", "林远")
        self.assertTrue(any(x["type"] == "event" for x in hits))
        self.assertTrue(any(x["type"] == "character" for x in people))
        thread_hits = search(self.db, "SER01", "失踪信件")
        self.assertTrue(any(x["type"] == "thread" for x in thread_hits))


    def test_reject_candidate_is_audited_and_stays_rejected(self):
        import_dossier(self.db, "SER01", self.d1)
        import_dossier(self.db, "SER01", self.d2)
        candidate = pending_candidates(self.db, "SER01")["identity_candidates"][0]
        reject_candidate(self.db, "SER01", candidate["candidate_id"], "identity", "两集为同名不同人", "tester")
        self.assertEqual(len(pending_candidates(self.db, "SER01")["identity_candidates"]), 0)
        memory = export_memory(self.db, "SER01", self.root / "rejected-memory.json")
        self.assertTrue(any(x["action"] == "REJECT_CANDIDATE" and x["reason"] == "两集为同名不同人" for x in memory["audit_log"]))

    def test_batch_import_resumes_without_duplicate(self):
        folder = self.root / "batch"
        folder.mkdir()
        save_atomic(self.make_dossier("EP001"), folder / "one.json")
        save_atomic(self.make_dossier("EP002", char_id="CHR9", line_id="STL9", event_id="EVT9"), folder / "two.json")
        first = batch_import(self.db, "SER01", folder)
        second = batch_import(self.db, "SER01", folder)
        self.assertEqual(first["imported"], 2)
        self.assertEqual(second["skipped"], 2)


if __name__ == "__main__":
    unittest.main()
