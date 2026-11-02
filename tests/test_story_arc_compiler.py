import json, sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from dossier_v3 import empty_dossier, save_atomic, validate, migrate_to_v34
from series_memory import init_db, import_dossier, connect, add_cross_causal_link, review_cross_causal_link, build_memory
from story_arc_compiler import compile_story_arc, make_prompt
from series_visibility import declare_visible

class StoryArcCompilerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.db=self.root/'series.sqlite'
        init_db(self.db,'SER01','认知与背叛'); self.sources={}
        for ep,n in [('EP001',1),('EP002',2),('EP003',3)]:
            video=self.root/f'{ep}.mp4'; video.write_bytes(b'placeholder source'); self.sources[ep]=str(video)
            d=empty_dossier(ep,f'第{n}集',60,'master')
            d['evidence']=[{'evidence_id':f'EVD{n}A','type':'SUB','source':f'{ep}.srt','start_sec':4,'end_sec':8,'raw':'关键对白','observation':'','quality':'high','locator':'subtitle','review_status':'PASS'},
                           {'evidence_id':f'EVD{n}B','type':'VIS','source':f'{ep}.mp4','start_sec':10,'end_sec':14,'raw':'','observation':'画面观察','quality':'high','locator':'frame','review_status':'PASS'}]
            ca='CHR_A' if n==1 else f'CHR_A{n}'; cb='CHR_B' if n==1 else f'CHR_B{n}'
            d['characters']=[{'character_id':ca,'canonical_name':'张三','aliases':['阿三'],'identity_status':'confirmed','evidence_ids':[f'EVD{n}A'],'review_status':'PASS'},
                             {'character_id':cb,'canonical_name':'李四','aliases':['小李'],'identity_status':'confirmed','evidence_ids':[f'EVD{n}A'],'review_status':'PASS'}]
            if n==1: evs=[('EVT_SETUP',5,'张三信任李四',ca,cb),('EVT_SECRET',12,'李四隐瞒真实身份',cb,ca)]
            elif n==2: evs=[('EVT_PEAK',10,'张三发现李四背叛',ca,cb),('EVT_REACT',18,'张三决定复仇',ca,cb)]
            else: evs=[('EVT_PAYOFF',8,'张三揭露李四阴谋',ca,cb)]
            for eid,t,action,actor,patient in evs:
                d['events'].append({'event_id':eid,'scene_id':None,'playback_time':t,'story_time':'','actor':actor,'action':action,'patient':patient,'recipient':None,'target':None,'modality':'actual','polarity':'positive','quote':'我知道真相了','evidence_ids':[f'EVD{n}A',f'EVD{n}B'],'certainty':'F1_DIRECT','highlight_dimensions':{'importance':90,'emotion':80,'conflict':90,'twist':90,'suspense':80,'visual':70,'standalone':70},'context_required':'high','review_status':'PASS'})
            d['storylines']=[{'storyline_id':f'STL{n}','title':'背叛与复仇','summary':'张三与李四的背叛复仇线','character_ids':[ca,cb],'event_ids':[x[0] for x in evs],'status':'ACTIVE','evidence_ids':[f'EVD{n}A'],'review_status':'PASS'}]
            if n==2:
                d['story_arcs']=[{'arc_id':'ARC2','title':'发现背叛','arc_type':'betrayal','narrative_shape':'setup_peak_reaction_payoff','start_sec':10,'end_sec':18,'anchor_sec':10,'anchor_event_id':'EVT_PEAK','segments':[{'role':'peak','start_sec':10,'end_sec':12,'description':'主角发现背叛','event_ids':['EVT_PEAK'],'cue_ids':[]}],'event_ids':['EVT_PEAK','EVT_REACT'],'storyline_ids':['STL2'],'evidence_ids':['EVD2A'],'cue_ids':[],'dimensions':{'importance':90},'total_score':85,'score_source':'human','candidate_source':'dossier_event','semantic_status':'HUMAN_CONFIRMED','context_required':'high','missing_roles':[],'risk_flags':[],'semantic_questions':[],'review_status':'PASS'}]
            if n==1:
                d['causal_edges']=[{'cause_id':'CAU1','from_event':'EVT_SETUP','to_event':'EVT_SECRET','type':'ENABLES','reasoning':'信任使隐瞒得以持续','evidence_ids':['EVD1A'],'counter_evidence_ids':[],'review_status':'PASS'}]
                d['knowledge_states']=[{'knowledge_id':'KNW1','subject_type':'CHARACTER','character_id':ca,'proposition':'李四真实身份','state':'UNAWARE','playback_time':5,'event_id':'EVT_SETUP','evidence_ids':['EVD1A'],'review_status':'PASS'},
                                       {'knowledge_id':'KNW2','subject_type':'VIEWER','character_id':None,'subject_name':'观众','proposition':'李四真实身份','state':'SUSPECTS','playback_time':12,'event_id':'EVT_SECRET','evidence_ids':['EVD1B'],'review_status':'PASS'}]
            elif n==2:
                d['causal_edges']=[{'cause_id':'CAU2','from_event':'EVT_PEAK','to_event':'EVT_REACT','type':'MOTIVATES','reasoning':'发现背叛后决定复仇','evidence_ids':['EVD2A'],'counter_evidence_ids':[],'review_status':'PASS'}]
                d['knowledge_states']=[{'knowledge_id':'KNW3','subject_type':'CHARACTER','character_id':ca,'proposition':'李四真实身份','state':'KNOWS','playback_time':10,'event_id':'EVT_PEAK','evidence_ids':['EVD2A'],'review_status':'PASS'},
                                       {'knowledge_id':'KNW4','subject_type':'VIEWER','character_id':None,'subject_name':'观众','proposition':'李四真实身份','state':'KNOWS','playback_time':11,'event_id':'EVT_PEAK','evidence_ids':['EVD2A'],'review_status':'PASS'},
                                       {'knowledge_id':'KNW6','subject_type':'VIEWER','character_id':None,'subject_name':'观众','proposition':'李四真实身份','state':'UNAWARE','playback_time':9,'event_id':None,'evidence_ids':['EVD2B'],'review_status':'PASS'}]
            else:
                d['knowledge_states']=[{'knowledge_id':'KNW5','subject_type':'CHARACTER','character_id':ca,'proposition':'李四真实身份','state':'KNOWS','playback_time':8,'event_id':'EVT_PAYOFF','evidence_ids':['EVD3A'],'review_status':'PASS'}]
            path=self.root/f'{ep}.json'; save_atomic(d,path); import_dossier(self.db,'SER01',path)
        from series_memory import link_characters,link_storylines
        for ep,cid_a,cid_b,sl in [('EP002','CHR_A2','CHR_B2','STL2'),('EP003','CHR_A3','CHR_B3','STL3')]:
            link_characters(self.db,'SER01','EP001:CHR_A',f'{ep}:{cid_a}','核对身份','test')
            link_characters(self.db,'SER01','EP001:CHR_B',f'{ep}:{cid_b}','核对身份','test')
            link_storylines(self.db,'SER01','EP001:STL1',f'{ep}:{sl}','同一故事线','test')
        self.cross1=add_cross_causal_link(self.db,'SER01','EP001:EVT_SETUP','EP002:EVT_PEAK','CAUSES','信任与隐瞒最终被揭穿',['EP001:EVD1A','EP002:EVD2A'])
        review_cross_causal_link(self.db,'SER01',self.cross1,'PASS','人工回看片段确认','tester')
        self.cross2=add_cross_causal_link(self.db,'SER01','EP002:EVT_REACT','EP003:EVT_PAYOFF','ENABLES','复仇决定促成最终揭露',['EP002:EVD2B','EP003:EVD3A'])
        review_cross_causal_link(self.db,'SER01',self.cross2,'PASS','核实前后剧情与证据','tester')
        c=connect(self.db)
        try:
            self.gid=c.execute("SELECT global_character_id FROM character_mentions WHERE episode_id='EP001' AND local_character_id='CHR_A'").fetchone()[0]
            self.gid_b=c.execute("SELECT global_character_id FROM character_mentions WHERE episode_id='EP001' AND local_character_id='CHR_B'").fetchone()[0]
        finally:c.close()
        self.sources_path=self.root/'sources.json';self.sources_path.write_text(json.dumps(self.sources),encoding='utf-8')

    def tearDown(self): self.tmp.cleanup()

    def plan(self):
        return {'schema_version':'1.0.0','plan_id':'PLAN001','series_id':'SER01','title':'从信任到复仇','goal':'张三发现李四背叛并完成复仇','viewpoint':{'type':'CHARACTER','character_id':self.gid},'sequence_strategy':'CHRONOLOGICAL','required_roles':['setup','peak','reaction','payoff'],'target_duration_sec':300,'review_status':'PASS',
          'segments':[{'segment_id':'SEG1','role':'setup','episode_id':'EP001','event_ids':['EVT_SETUP'],'rationale':'先建立信任','review_status':'PASS'},
                      {'segment_id':'SEG2','role':'peak','episode_id':'EP002','event_ids':['EVT_PEAK'],'rationale':'背叛被发现','review_status':'PASS'},
                      {'segment_id':'SEG3','role':'reaction','episode_id':'EP002','event_ids':['EVT_REACT'],'rationale':'人物决定复仇','review_status':'PASS'},
                      {'segment_id':'SEG4','role':'payoff','episode_id':'EP003','event_ids':['EVT_PAYOFF'],'rationale':'最终揭露阴谋','review_status':'PASS'}],
          'continuity_links':[{'from_event':'EP001:EVT_SETUP','to_event':'EP002:EVT_PEAK','basis':'CROSS_EPISODE_CAUSAL','edge_id':self.cross1},
                              {'from_event':'EP002:EVT_PEAK','to_event':'EP002:EVT_REACT','basis':'LOCAL_CAUSAL','edge_id':'CAU2'},
                              {'from_event':'EP002:EVT_REACT','to_event':'EP003:EVT_PAYOFF','basis':'CROSS_EPISODE_CAUSAL','edge_id':self.cross2}],
          'missing_roles':[],'risk_flags':[]}

    def test_import_perspective_and_story_arc_memory(self):
        c=connect(self.db)
        try:m=build_memory(c,'SER01')
        finally:c.close()
        self.assertEqual(len(m['knowledge_states']),6)
        self.assertEqual(len(m['causal_edges']),2)
        self.assertEqual(len(m['story_arcs']),1)
        self.assertEqual(len(m['cross_episode_causal_edges']),2)

    def test_compile_story_arc_with_knowledge_timeline(self):
        plan=self.plan();pf=self.root/'plan.json';pf.write_text(json.dumps(plan),encoding='utf-8')
        out=compile_story_arc(self.db,self.sources_path,pf,self.root/'project.json')
        self.assertEqual([x['story_arc_role'] for x in out['clips']],['setup','peak','reaction','payoff'])
        self.assertEqual(out['clips'][0]['perspective_knowledge'][0]['state'],'UNAWARE')
        self.assertEqual(out['clips'][1]['perspective_knowledge'][0]['state'],'KNOWS')
        self.assertEqual(out['clips'][1]['viewer_knowledge'][0]['state'],'UNAWARE')
        self.assertEqual(out['clips'][1]['knowledge_gap_flags'][0]['type'],'CHARACTER_AHEAD_OF_VIEWER')
        self.assertEqual(out['clips'][0]['viewer_knowledge'],[])  # later viewer knowledge must not leak backward
        self.assertEqual(out['clips'][0]['knowledge_gap_flags'],[])  # missing knowledge is unknown, not proof of unawareness
        self.assertTrue(out['narrative_audit']['release_ready'])

    def test_missing_payoff_prevents_release_ready(self):
        plan=self.plan();plan['segments']=plan['segments'][:-1];plan['continuity_links']=plan['continuity_links'][:-1]
        pf=self.root/'plan.json';pf.write_text(json.dumps(plan),encoding='utf-8')
        out=compile_story_arc(self.db,self.sources_path,pf,self.root/'project.json')
        self.assertFalse(out['narrative_audit']['release_ready'])
        self.assertIn('payoff',out['narrative_audit']['missing_roles'])
        with self.assertRaises(ValueError):compile_story_arc(self.db,self.sources_path,pf,self.root/'strict.json',require_release_ready=True)

    def test_chronology_inversion_rejected(self):
        plan=self.plan();plan['segments'][0],plan['segments'][1]=plan['segments'][1],plan['segments'][0]
        pf=self.root/'plan.json';pf.write_text(json.dumps(plan),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'角色顺序'):compile_story_arc(self.db,self.sources_path,pf,self.root/'bad.json')

    def test_non_linear_requires_reason(self):
        plan=self.plan();plan['sequence_strategy']='EDITORIAL_NONLINEAR';plan['segments']=list(reversed(plan['segments']))
        pf=self.root/'plan.json';pf.write_text(json.dumps(plan),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'reorder_reason'):compile_story_arc(self.db,self.sources_path,pf,self.root/'bad.json')
        plan['reorder_reason']='先展示最终揭露，再回溯背叛起因';pf.write_text(json.dumps(plan),encoding='utf-8')
        out=compile_story_arc(self.db,self.sources_path,pf,self.root/'nonlinear.json')
        self.assertEqual(out['clips'][0]['story_arc_role'],'payoff')

    def test_imagined_event_rejected(self):
        c=connect(self.db)
        try:c.execute("UPDATE events SET modality='imagined' WHERE episode_id='EP002' AND event_id='EVT_PEAK'");c.commit()
        finally:c.close()
        pf=self.root/'plan.json';pf.write_text(json.dumps(self.plan(),encoding='utf-8'),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'不允许将 imagined'):compile_story_arc(self.db,self.sources_path,pf,self.root/'bad.json')

    def test_story_arc_project_renders_multisource_mp4(self):
        import shutil, subprocess
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg/FFprobe not installed')
        for i,ep in enumerate(('EP001','EP002','EP003')):
            video=Path(self.sources[ep])
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',f'testsrc=size=64x64:rate=5:duration=20','-f','lavfi','-i',f'sine=frequency={440+i*80}:duration=20','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(video)],check=True)
        pf=self.root/'plan.json';pf.write_text(json.dumps(self.plan(),encoding='utf-8'),encoding='utf-8')
        project_path=self.root/'story-project.json'
        compile_story_arc(self.db,self.sources_path,pf,project_path)
        output=self.root/'story-arc.mp4'
        subprocess.run([sys.executable,str(ROOT/'scripts'/'render_series_project.py'),str(project_path),str(output),'--width','160','--height','90','--fps','5','--preset','ultrafast'],check=True,capture_output=True,text=True)
        self.assertTrue(output.is_file())
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type,codec_name','-of','json',str(output)],capture_output=True,text=True,check=True)
        streams=json.loads(probe.stdout)['streams']
        self.assertTrue(any(x['codec_type']=='video' and x['codec_name']=='h264' for x in streams))
        self.assertTrue(any(x['codec_type']=='audio' and x['codec_name']=='aac' for x in streams))

    def test_make_prompt_creates_context(self):
        out=make_prompt(self.db,'SER01','character',self.gid,'张三背叛复仇线',self.root/'prompt.md')
        self.assertGreater(out['candidate_events'],0)
        self.assertIn('不得以后续信息倒灌',(self.root/'prompt.md').read_text(encoding='utf-8'))

    def test_unreviewed_local_causal_edge_blocks_release_ready(self):
        c=connect(self.db)
        try:c.execute("UPDATE episode_causal_edges SET review_status='UNREVIEWED' WHERE episode_id='EP002' AND cause_id='CAU2'");c.commit()
        finally:c.close()
        pf=self.root/'plan.json';pf.write_text(json.dumps(self.plan(),encoding='utf-8'),encoding='utf-8')
        out=compile_story_arc(self.db,self.sources_path,pf,self.root/'draft.json')
        self.assertFalse(out['narrative_audit']['release_ready'])
        self.assertTrue(out['narrative_audit']['continuity_review_warnings'])

    def test_segment_evidence_must_match_selected_event(self):
        plan=self.plan();plan['segments'][0]['evidence_refs']=['EP003:EVD3A']
        pf=self.root/'plan.json';pf.write_text(json.dumps(plan),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'必须来自该段落的集数'):
            compile_story_arc(self.db,self.sources_path,pf,self.root/'bad.json')

    def test_continuity_link_must_connect_selected_events(self):
        plan=self.plan();plan['continuity_links'][0]['from_event']='EP001:EVT_SECRET'
        pf=self.root/'plan.json';pf.write_text(json.dumps(plan),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'必须连接计划中已选事件'):
            compile_story_arc(self.db,self.sources_path,pf,self.root/'bad.json')

    def test_knowledge_coverage_can_be_required(self):
        c=connect(self.db)
        try:c.execute("DELETE FROM character_knowledge WHERE subject_type='VIEWER'");c.commit()
        finally:c.close()
        plan=self.plan();plan['require_knowledge_coverage']=True
        pf=self.root/'plan.json';pf.write_text(json.dumps(plan),encoding='utf-8')
        out=compile_story_arc(self.db,self.sources_path,pf,self.root/'draft.json')
        self.assertFalse(out['narrative_audit']['release_ready'])
        with self.assertRaises(ValueError):compile_story_arc(self.db,self.sources_path,pf,self.root/'strict.json',require_release_ready=True)

    def test_cross_episode_edge_starts_unreviewed_and_audits_review(self):
        edge=add_cross_causal_link(self.db,'SER01','EP001:EVT_SECRET','EP003:EVT_PAYOFF','REVEALS','线索最终揭晓',['EP001:EVD1B','EP003:EVD3A'])
        c=connect(self.db)
        try:r=c.execute('SELECT review_status FROM cross_episode_causal_edges WHERE edge_id=?',(edge,)).fetchone()[0]
        finally:c.close()
        self.assertEqual(r,'UNREVIEWED')
        review_cross_causal_link(self.db,'SER01',edge,'DISPUTED','证据不足','tester')
        c=connect(self.db)
        try:
            r=c.execute('SELECT review_status FROM cross_episode_causal_edges WHERE edge_id=?',(edge,)).fetchone()[0]
            audit=c.execute('SELECT COUNT(*) FROM audit_log WHERE object_ref=?',(edge,)).fetchone()[0]
        finally:c.close()
        self.assertEqual(r,'DISPUTED');self.assertEqual(audit,2)


    def test_spoiler_guard_rejects_future_episode_events(self):
        plan=self.plan();plan['spoiler_guard']={'enabled':True,'max_episode_number':2}
        pf=self.root/'plan.json';pf.write_text(json.dumps(plan),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'剧透保护阻止使用截止集数之后的事件'):
            compile_story_arc(self.db,self.sources_path,pf,self.root/'blocked.json')

    def test_spoiler_guard_requires_visibility_for_cross_episode_edges(self):
        plan=self.plan();plan['spoiler_guard']={'enabled':True,'max_episode_number':3}
        declare_visible(self.db,'SER01','character',self.gid,'EP003',['EP003:EVD3A'],'观众在第3集前已确认张三身份','tester')
        declare_visible(self.db,'SER01','character',self.gid_b,'EP003',['EP003:EVD3A'],'观众在第3集前已确认李四身份','tester')
        pf=self.root/'plan.json';pf.write_text(json.dumps(plan),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'尚未声明在第 3 集前已可知'):
            compile_story_arc(self.db,self.sources_path,pf,self.root/'blocked.json')
        declare_visible(self.db,'SER01','causal_edge',self.cross1,'EP003',['EP001:EVD1A','EP002:EVD2A'],'该因果联系在第3集前已通过前两集证据揭示','tester')
        declare_visible(self.db,'SER01','causal_edge',self.cross2,'EP003',['EP002:EVD2B','EP003:EVD3A'],'该因果联系在第3集已揭示','tester')
        out=compile_story_arc(self.db,self.sources_path,pf,self.root/'visible.json')
        self.assertEqual(out['spoiler_guard']['max_episode_number'],3)

    def test_spoiler_prompt_requires_character_visibility_and_hides_future(self):
        with self.assertRaisesRegex(ValueError,'尚无证据声明'):
            make_prompt(self.db,'SER01','character',self.gid,'张三的故事',self.root/'blocked.md',2)
        declare_visible(self.db,'SER01','character',self.gid,'EP002',['EP002:EVD2A'],'观众在第2集已确认该身份','tester')
        result=make_prompt(self.db,'SER01','character',self.gid,'张三的故事',self.root/'safe.md',2)
        txt=(self.root/'safe.md').read_text(encoding='utf-8')
        self.assertEqual(result['spoiler_guard']['max_episode_number'],2)
        self.assertIn('EP001',txt);self.assertIn('EP002',txt);self.assertNotIn('EP003',txt)
        self.assertNotIn(self.gid_b,txt)  # unrelated un-declared global identity is not leaked

if __name__=='__main__':unittest.main()
