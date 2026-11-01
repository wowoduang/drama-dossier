import json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from dossier_v3 import empty_dossier,save_atomic
from series_memory import init_db,import_dossier,connect,build_memory
from series_visibility import declare_visible

class SeriesVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'s.sqlite'
        init_db(self.db,'S1','visibility test')
        d=empty_dossier('EP001','第1集',60,'v1')
        d['evidence']=[{'evidence_id':'EVD1','type':'VIS','source':'ep.mp4','start_sec':1,'end_sec':2,'raw':'','observation':'人物身份画面','quality':'high','locator':'frame','review_status':'PASS'}, {'evidence_id':'EVD2','type':'SUB','source':'ep.srt','start_sec':3,'end_sec':4,'raw':'无关对白','observation':'','quality':'high','locator':'subtitle','review_status':'PASS'}]
        d['characters']=[{'character_id':'CHR1','canonical_name':'甲','aliases':[],'identity_status':'confirmed','evidence_ids':['EVD1'],'review_status':'PASS'}]
        d['events']=[{'event_id':'EVT1','scene_id':None,'playback_time':1.5,'actor':'CHR1','action':'揭示身份','patient':None,'recipient':None,'target':None,'modality':'actual','polarity':'positive','quote':'我就是甲','evidence_ids':['EVD1'],'certainty':'F1_DIRECT','review_status':'PASS'}]
        save_atomic(d,self.root/'ep.json');import_dossier(self.db,'S1',self.root/'ep.json')
        d2=empty_dossier('EP002','第2集',60,'v1');d2['evidence']=[{'evidence_id':'EVD9','type':'VIS','source':'ep2.mp4','start_sec':1,'end_sec':2,'raw':'','observation':'后续画面','quality':'high','locator':'frame','review_status':'PASS'}]
        save_atomic(d2,self.root/'ep2.json');import_dossier(self.db,'S1',self.root/'ep2.json')
        c=connect(self.db)
        try:self.gid=c.execute('SELECT global_character_id FROM character_mentions WHERE series_id=?',( 'S1',)).fetchone()[0]
        finally:c.close()
    def tearDown(self):self.tmp.cleanup()
    def test_visibility_declaration_requires_entity_related_evidence(self):
        with self.assertRaisesRegex(ValueError,'直接关联'):
            declare_visible(self.db,'S1','character',self.gid,'EP001',['EP001:EVD2'],'无关证据不能证明身份已知')
    def test_valid_visibility_declaration_is_audited(self):
        out=declare_visible(self.db,'S1','character',self.gid,'EP001',['EP001:EVD1'],'原片在第1集明确揭示身份','tester')
        self.assertEqual(out['review_status'],'PASS')
        c=connect(self.db)
        try:
            row=c.execute('SELECT effective_episode_number FROM series_visibility WHERE series_id=? AND entity_id=?',( 'S1',self.gid)).fetchone()
            audit=c.execute('SELECT COUNT(*) FROM visibility_audit WHERE entity_id=?',(self.gid,)).fetchone()[0]
        finally:c.close()
        self.assertEqual(row[0],1);self.assertEqual(audit,1)
        c=connect(self.db)
        try:memory=build_memory(c,'S1')
        finally:c.close()
        self.assertEqual(memory['series_visibility'][0]['entity_id'],self.gid)
        self.assertEqual(len(memory['visibility_audit']),1)
    def test_visibility_cannot_use_future_episode_evidence(self):
        with self.assertRaisesRegex(ValueError,'不能晚于声明集数'):
            declare_visible(self.db,'S1','character',self.gid,'EP001',['EP002:EVD9'],'未来证据不能证明第1集已知')
    def test_visibility_requires_pass_evidence(self):
        from dossier_v3 import load,save_atomic
        d=load(self.root/'ep.json')
        d['evidence'][0]['review_status']='UNREVIEWED'
        save_atomic(d,self.root/'ep-unreviewed.json')
        import_dossier(self.db,'S1',self.root/'ep-unreviewed.json',replace=True)
        with self.assertRaisesRegex(ValueError,'PASS'):
            declare_visible(self.db,'S1','character',self.gid,'EP001',['EP001:EVD1'],'未审核证据不能建立可见性')

if __name__=='__main__':unittest.main()
