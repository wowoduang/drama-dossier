import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from dedupe_clips import compare_hash_sequences,detect_duplicates,text_similarity,overlap_ratio

class ClipDedupeTests(unittest.TestCase):
    def test_same_visual_sequence_is_candidate_not_deleted(self):
        project={'project_type':'cross_episode_compilation','title':'demo','clips':[
            {'clip_id':'A','episode_id':'EP001','source_video':'/missing/a.mp4','start_sec':1,'end_sec':4,'title':'张三发现背叛','reason':'发现李四背叛'},
            {'clip_id':'B','episode_id':'EP008','source_video':'/missing/b.mp4','start_sec':10,'end_sec':13,'title':'张三发现背叛','reason':'发现李四背叛'}]}
        report=detect_duplicates(project,hash_cache={'A':[0,0,0],'B':[0,0,0]})
        self.assertEqual(report['duplicate_pair_count'],1)
        self.assertEqual(report['pairs'][0]['action'],'REVIEW_MANUALLY')
        self.assertFalse(report['policy']['automatic_removal'])
    def test_same_source_overlap_detected_without_frame_hash(self):
        project={'clips':[{'clip_id':'A','source_video':'/tmp/source.mp4','start_sec':1,'end_sec':5,'title':'one'}, {'clip_id':'B','source_video':'/tmp/source.mp4','start_sec':1.5,'end_sec':4.5,'title':'two'}]}
        r=detect_duplicates(project,hash_cache={'A':[],'B':[]})
        self.assertEqual(r['duplicate_pair_count'],1)
        self.assertEqual(r['pairs'][0]['confidence'],'HIGH')
    def test_different_sequences_not_flagged(self):
        project={'clips':[{'clip_id':'A','source_video':'/missing/a','start_sec':1,'end_sec':4,'title':'救人'}, {'clip_id':'B','source_video':'/missing/b','start_sec':1,'end_sec':4,'title':'告别'}]}
        r=detect_duplicates(project,hash_cache={'A':[0,0,0],'B':[2**64-1,2**64-1,2**64-1]})
        self.assertEqual(r['duplicate_pair_count'],0)
    def test_chinese_text_similarity(self):
        self.assertGreater(text_similarity('张三发现李四背叛','张三发现李四背叛了自己'),.4)
    def test_hash_sequence_reports_distinct_sample_matches(self):
        x=compare_hash_sequences([0,0,0],[0,0,0])
        self.assertEqual(x['matched_frames'],3)

if __name__=='__main__':unittest.main()
