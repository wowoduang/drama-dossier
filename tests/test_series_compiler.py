import json
import sys
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from dossier_v3 import empty_dossier,save_atomic
from series_memory import init_db,import_dossier,link_characters
from series_compiler import compile_project
from render_series_project import build_command,load_project

class SeriesCompilerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'series.sqlite'
        self.videos={}
        for ep in ('EP001','EP002'):
            v=self.root/f'{ep}.mp4';v.write_bytes(b'fake video placeholder');self.videos[ep]=str(v)
        self.sources=self.root/'sources.json';self.sources.write_text(json.dumps(self.videos),encoding='utf-8')
        init_db(self.db,'SER01','测试全剧')
        for n,ep,cid,eid,action in [(1,'EP001','CHR1','EVT1','初次救下伙伴'),(2,'EP002','CHR9','EVT9','最终再次救下伙伴')]:
            d=empty_dossier(ep,f'第{n}集',120,'master')
            d['evidence']=[{'evidence_id':f'EVD{n}','type':'SUB','source':f'{ep}.srt','start_sec':20.0,'end_sec':22.0,'raw':'我不会放弃你','observation':'','quality':'high','locator':'cue','review_status':'PASS'}]
            d['characters']=[{'character_id':cid,'canonical_name':'林远','aliases':['阿远'],'identity_status':'confirmed','evidence_ids':[f'EVD{n}'],'review_status':'PASS'},{'character_id':('CHR2' if n==1 else 'CHR8'),'canonical_name':'苏宁','aliases':['小宁'],'identity_status':'confirmed','evidence_ids':[f'EVD{n}'],'review_status':'PASS'}]
            d['events']=[{'event_id':eid,'scene_id':None,'playback_time':21.0,'story_time':'','actor':cid,'action':action,'patient':('CHR2' if n==1 else 'CHR8'),'recipient':None,'target':None,'modality':'actual','polarity':'positive','quote':'我不会放弃你','evidence_ids':[f'EVD{n}'],'certainty':'F1_DIRECT','highlight_dimensions':{'importance':90,'emotion':85,'conflict':70,'twist':50,'suspense':50,'visual':70,'standalone':80},'context_required':'medium','review_status':'PASS'}]
            d['storylines']=[{'storyline_id':f'STL{n}','title':'守护关系线','summary':'跨集守护伙伴','character_ids':[cid,('CHR2' if n==1 else 'CHR8')],'event_ids':[eid],'status':'ACTIVE','evidence_ids':[f'EVD{n}'],'review_status':'PASS'}]
            d['threads']=[{'thread_id':f'TH{n}','description':'失踪信件伏笔','first_event':eid,'status':'OPEN','evidence_ids':[f'EVD{n}'],'resolution_evidence_ids':[],'review_status':'PASS'}]
            d['highlights']=[{'highlight_id':f'HL{n}','start_sec':18.0,'end_sec':27.0,'title':action,'reason':'已核实高光','event_ids':[eid],'storyline_ids':[],'evidence_ids':[f'EVD{n}'],'dimensions':{'importance':90,'emotion':85,'conflict':70,'twist':50,'suspense':50,'visual':70,'standalone':80},'total_score':80,'score_profile':'5m','score_source':'human','context_required':'medium','review_status':'PASS'}]
            path=self.root/f'{ep}.json';save_atomic(d,path);import_dossier(self.db,'SER01',path)
        link_characters(self.db,'SER01','EP001:CHR1','EP002:CHR9','same character after checking evidence','test')
        link_characters(self.db,'SER01','EP001:CHR2','EP002:CHR8','same partner after checking evidence','test')
        from series_memory import link_storylines,link_threads
        link_storylines(self.db,'SER01','EP001:STL1','EP002:STL2','same storyline after review','test')
        link_threads(self.db,'SER01','EP001:TH1','EP002:TH2','same foreshadowing after review','test')

    def tearDown(self):self.tmp.cleanup()

    def test_compile_cross_episode_character_project(self):
        from series_memory import connect
        c=connect(self.db)
        try:gid=c.execute('SELECT global_character_id FROM character_mentions WHERE series_id=? AND episode_id=?',('SER01','EP001')).fetchone()[0]
        finally:c.close()
        out=self.root/'edit.json'
        p=compile_project(self.db,'SER01','character',gid,self.sources,out,preset='5m')
        self.assertEqual(len(p['clips']),2)
        self.assertEqual([x['episode_id'] for x in p['clips']],['EP001','EP002'])
        self.assertTrue(all(x['source_video'] and x['source_basename'] for x in p['clips']))
        self.assertTrue(out.exists())

    def test_default_excludes_unreviewed(self):
        from series_memory import connect
        c=connect(self.db)
        try:
            c.execute("UPDATE events SET review_status='UNREVIEWED' WHERE episode_id='EP002'");c.commit()
            gid=c.execute('SELECT global_character_id FROM character_mentions WHERE episode_id=?',('EP001',)).fetchone()[0]
        finally:c.close()
        p=compile_project(self.db,'SER01','character',gid,self.sources,self.root/'edit.json')
        self.assertEqual(len(p['clips']),1)
        self.assertEqual(p['clips'][0]['episode_id'],'EP001')

    def test_include_unreviewed_is_explicit(self):
        from series_memory import connect
        c=connect(self.db)
        try:
            c.execute("UPDATE events SET review_status='UNREVIEWED' WHERE episode_id='EP002'");c.commit()
            gid=c.execute('SELECT global_character_id FROM character_mentions WHERE episode_id=?',('EP001',)).fetchone()[0]
        finally:c.close()
        p=compile_project(self.db,'SER01','character',gid,self.sources,self.root/'edit.json',reviewed_only=False)
        self.assertEqual(len(p['clips']),2)
        self.assertEqual(p['clips'][1]['event_review_status'],'UNREVIEWED')

    def test_missing_episode_source_is_reported_not_silently_hidden(self):
        from series_memory import connect
        c=connect(self.db)
        try:gid=c.execute('SELECT global_character_id FROM character_mentions WHERE episode_id=?',('EP001',)).fetchone()[0]
        finally:c.close()
        sources=self.root/'partial.json';sources.write_text(json.dumps({'EP001':self.videos['EP001']}),encoding='utf-8')
        p=compile_project(self.db,'SER01','character',gid,sources,self.root/'partial-edit.json')
        self.assertEqual(len(p['compile_report']['missing_sources']),1)
        self.assertEqual(len(p['clips']),1)


    def test_compile_pair_relationship_project(self):
        from series_memory import connect
        c=connect(self.db)
        try:
            ids=[r[0] for r in c.execute("SELECT global_character_id FROM character_mentions WHERE series_id='SER01' AND episode_id='EP001' ORDER BY local_character_id")]
        finally:c.close()
        p=compile_project(self.db,'SER01','pair',','.join(ids),self.sources,self.root/'pair.json')
        self.assertEqual(len(p['clips']),2)
        self.assertEqual(p['selector']['type'],'pair')
        self.assertEqual(p['clips'][0]['character_names'],['林远','苏宁'])


    def test_compile_storyline_and_thread_projects(self):
        from series_memory import connect
        c=connect(self.db)
        try:
            line=c.execute("SELECT global_storyline_id FROM storyline_mentions WHERE series_id='SER01' AND episode_id='EP001'").fetchone()[0]
            thread=c.execute("SELECT global_thread_id FROM thread_mentions WHERE series_id='SER01' AND episode_id='EP001'").fetchone()[0]
        finally:c.close()
        lp=compile_project(self.db,'SER01','storyline',line,self.sources,self.root/'line.json')
        tp=compile_project(self.db,'SER01','thread',thread,self.sources,self.root/'thread.json')
        self.assertEqual(len(lp['clips']),2)
        self.assertEqual(len(tp['clips']),2)
        self.assertTrue(all(x['thread_description']=='失踪信件伏笔' for x in tp['clips']))

    def test_target_duration_selects_whole_clips_and_keeps_chronology(self):
        from series_memory import connect
        c=connect(self.db)
        try:gid=c.execute("SELECT global_character_id FROM character_mentions WHERE series_id='SER01' AND episode_id='EP001' AND local_character_id='CHR1'").fetchone()[0]
        finally:c.close()
        p=compile_project(self.db,'SER01','character',gid,self.sources,self.root/'short.json',target_sec=10)
        self.assertLessEqual(p['estimated_duration_sec'],10)
        self.assertEqual(len(p['clips']),1)
        self.assertTrue(p['compile_report']['target_respected'])


    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg/FFprobe not installed')
    def test_end_to_end_series_database_to_multisource_mp4(self):
        for i,ep in enumerate(('EP001','EP002')):
            v=self.root/f'{ep}.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',f'color=c={"red" if i==0 else "blue"}:s=320x240:r=25:d=30','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=30','-shortest','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(v)],check=True)
            self.videos[ep]=str(v)
        self.sources.write_text(json.dumps(self.videos),encoding='utf-8')
        from series_memory import connect
        c=connect(self.db)
        try:gid=c.execute("SELECT global_character_id FROM character_mentions WHERE series_id='SER01' AND episode_id='EP001' AND local_character_id='CHR1'").fetchone()[0]
        finally:c.close()
        edit=self.root/'compiled.json'
        compile_project(self.db,'SER01','character',gid,self.sources,edit,preset='5m')
        project=load_project(edit);self.assertEqual(len(project['clips']),2)
        out=self.root/'series-cut.mp4';cmd,flt=build_command(project,out,width=320,height=240,fps=25)
        script=self.root/'filter.ffscript';script.write_text(flt,encoding='utf-8')
        subprocess.run([str(script) if x=='FILTER_FILE' else x for x in cmd],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        info=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(out)],capture_output=True,text=True, encoding='utf-8', errors='replace',check=True)
        meta=json.loads(info.stdout)
        self.assertAlmostEqual(float(meta['format']['duration']),18.0,delta=.5)
        self.assertEqual({x['codec_type'] for x in meta['streams']},{'video','audio'})

    def test_invalid_selector_fails(self):
        with self.assertRaises(ValueError):compile_project(self.db,'SER01','character','NOPE',self.sources,self.root/'x.json')

if __name__=='__main__':unittest.main()
