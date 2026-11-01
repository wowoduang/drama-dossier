import copy,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from dossier_v3 import empty_dossier,validate,migrate_to_v34,add_review

class KnowledgeSchemaTests(unittest.TestCase):
    def test_knowledge_state_validates_and_is_reviewable(self):
        d=empty_dossier('EP001','测试',60,'v1')
        d['evidence']=[{'evidence_id':'EVD1','type':'SUB','source':'srt','start_sec':1,'end_sec':2,'raw':'我知道','observation':'','quality':'high','locator':'cue','review_status':'PASS'}]
        d['characters']=[{'character_id':'CHR1','canonical_name':'甲','aliases':[],'identity_status':'confirmed','evidence_ids':['EVD1'],'review_status':'PASS'}]
        d['events']=[{'event_id':'EVT1','scene_id':None,'playback_time':1.5,'actor':'CHR1','action':'得知秘密','patient':None,'recipient':None,'target':None,'modality':'actual','polarity':'positive','quote':'我知道','evidence_ids':['EVD1'],'certainty':'F1_DIRECT','review_status':'PASS'}]
        d['knowledge_states']=[{'knowledge_id':'KNW1','subject_type':'CHARACTER','character_id':'CHR1','proposition':'秘密身份','state':'KNOWS','playback_time':1.5,'event_id':'EVT1','evidence_ids':['EVD1'],'review_status':'UNREVIEWED'}]
        self.assertEqual(validate(d),[])
        add_review(d,'KNW1','PASS','核对字幕和画面',['EVD1'])
        self.assertEqual(d['knowledge_states'][0]['review_status'],'PASS')
        self.assertEqual(validate(d),[])

    def test_invalid_knowledge_subject_rejected(self):
        d=empty_dossier('EP001','测试',60,'v1')
        d['knowledge_states']=[{'knowledge_id':'KNW1','subject_type':'CHARACTER','character_id':'MISSING','proposition':'秘密','state':'KNOWS','playback_time':1,'evidence_ids':[],'review_status':'UNREVIEWED'}]
        self.assertTrue(any('character_id 人物不存在' in x for x in validate(d)))

    def test_old_v34_dossier_migrates_without_inventing_knowledge(self):
        d=empty_dossier('EP001','测试',60,'v1');d['schema_version']='3.4.0';d.pop('knowledge_states')
        self.assertEqual(validate(d),[])
        migrated=migrate_to_v34(d)
        self.assertEqual(migrated['schema_version'],'3.5.0')
        self.assertEqual(migrated['knowledge_states'],[])

if __name__=='__main__':unittest.main()
