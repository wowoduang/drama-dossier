import json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from dossier_v3 import empty_dossier,save_atomic
from series_memory import init_db,import_dossier,connect
from series_visibility import declare_visible
from series_search import search_series,make_expansion_prompt,resolve_cutoff

class SeriesSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'s.sqlite'
        init_db(self.db,'S1','测试剧')
        self._add('EP001','林远',['小林'],'救下同伴','我来救你', 'PASS')
        self._add('EP010','林远',['阿远'],'与同伴争执','你骗了我', 'PASS')
        self._add('EP020','黑衣人',['林远真实身份'],'揭晓秘密身份','原来你就是林远', 'PASS')
        self._add('EP030','林远',['后期称号'],'最终复仇','我终于报仇了', 'PASS')
    def tearDown(self):self.tmp.cleanup()
    def _add(self,ep,name,aliases,action,quote,status):
        d=empty_dossier(ep,ep,60,'v1')
        cid='CHR_'+ep
        d['characters']=[{'character_id':cid,'canonical_name':name,'aliases':aliases,'identity_status':'confirmed','evidence_ids':[],'review_status':'PASS'}]
        d['events']=[{'event_id':'EVT_'+ep,'scene_id':None,'playback_time':10,'actor':cid,'action':action,'patient':None,'recipient':None,'target':None,'modality':'actual','polarity':'positive','quote':quote,'evidence_ids':[],'certainty':'F1_DIRECT','review_status':status}]
        save_atomic(d,self.root/f'{ep}.json');import_dossier(self.db,'S1',self.root/f'{ep}.json')
    def test_as_of_cutoff_never_returns_future_episode(self):
        r=search_series(self.db,'S1','林远', 'EP010')
        eps={x['episode_id'] for x in r['results']}
        self.assertTrue(eps.issubset({'EP001','EP010'}))
        self.assertEqual(r['spoiler_guard']['future_episodes_excluded'],2)
        self.assertFalse(r['spoiler_guard']['future_episode_ids_exposed'])
    def test_future_only_alias_is_not_searchable_before_reveal(self):
        r=search_series(self.db,'S1','黑衣人','EP010')
        self.assertEqual(r['result_count'],0)
        r2=search_series(self.db,'S1','黑衣人','EP020')
        self.assertGreater(r2['result_count'],0)
    def test_synonym_expansion_improves_recall(self):
        r=search_series(self.db,'S1','复仇','EP030')
        self.assertTrue(any(x['type']=='event' and x['episode_id']=='EP030' for x in r['results']))
    def test_cutoff_is_mandatory(self):
        with self.assertRaises(ValueError):search_series(self.db,'S1','救人',None)
    def test_expansion_prompt_is_cutoff_limited(self):
        out=make_expansion_prompt(self.db,'S1','背叛','EP010',self.root/'prompt.md')
        txt=(self.root/'prompt.md').read_text(encoding='utf-8')
        self.assertEqual(out['as_of']['episode_number'],10)
        self.assertNotIn('EP020',txt);self.assertNotIn('EP030',txt)
    def test_unknown_cutoff_rejected(self):
        c=__import__('series_memory').connect(self.db)
        try:
            with self.assertRaises(ValueError):resolve_cutoff(c,'S1','EP999')
        finally:c.close()
    def test_viewpoint_global_identity_requires_visibility_declaration(self):
        c=connect(self.db)
        try: gid=c.execute("SELECT global_character_id FROM character_mentions WHERE series_id='S1' AND episode_id='EP001'").fetchone()[0]
        finally:c.close()
        with self.assertRaisesRegex(ValueError,'尚未声明'):
            search_series(self.db,'S1','救人','EP010',viewpoint_character=gid)
        # A valid declaration must use evidence attached to that entity.
        from dossier_v3 import empty_dossier,save_atomic
        d=empty_dossier('EP001','EP001',60,'v1')
        d['evidence']=[{'evidence_id':'EV_VIEW','type':'VIS','source':'EP001.mp4','start_sec':1,'end_sec':2,'raw':'','observation':'确认人物身份','quality':'high','locator':'frame','review_status':'PASS'}]
        d['characters']=[{'character_id':'CHR_EP001','canonical_name':'林远','aliases':['小林'],'identity_status':'confirmed','evidence_ids':['EV_VIEW'],'review_status':'PASS'}]
        d['events']=[{'event_id':'EV_EP001','scene_id':None,'playback_time':1.5,'actor':'CHR_EP001','action':'确认身份','patient':None,'recipient':None,'target':None,'modality':'actual','polarity':'positive','quote':'我是林远','evidence_ids':['EV_VIEW'],'certainty':'F1_DIRECT','review_status':'PASS'}]
        save_atomic(d,self.root/'EP001.json')
        # The fixture originally has no evidence; import a replacement with identity evidence.
        import_dossier(self.db,'S1',self.root/'EP001.json',replace=True)
        declare_visible(self.db,'S1','character',gid,'EP001',['EP001:EV_VIEW'],'第1集画面明确揭示人物身份')
        result=search_series(self.db,'S1','救人','EP010',viewpoint_character=gid)
        self.assertEqual(result['viewpoint_character'],gid)

if __name__=='__main__':unittest.main()
