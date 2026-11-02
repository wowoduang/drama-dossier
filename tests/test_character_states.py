import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from dossier_v3 import empty_dossier,validate,add_review,save_atomic,migrate_to_v35
from character_state_model import usable,context_snapshot
from character_state import import_candidates,reviewed_dossier,trajectory,compile_state_cut
from series_memory import connect,init_db,import_dossier,link_characters,build_memory
from series_visibility import declare_visible
from edit_versions import approve,require_approved
from director_engine import plan_director


def fixture(ep='EP001'):
    d=empty_dossier(ep,'测试',100,'original')
    d['characters']=[{'character_id':'A','canonical_name':'主角早期名字','aliases':[],'identity_status':'confirmed','evidence_ids':['V1'],'review_status':'PASS'},{'character_id':'B','canonical_name':'同伴','aliases':[],'identity_status':'confirmed','evidence_ids':['V1'],'review_status':'PASS'}]
    for i,t in [(1,10),(2,30),(3,70)]:
        d['evidence'].append({'evidence_id':f'V{i}','type':'SUB','start_sec':t,'end_sec':t+2,'raw':['我相信你','我要亲自查清楚','后期真实身份'][i-1],'quality':'high','review_status':'PASS'})
        d['events'].append({'event_id':f'E{i}','scene_id':None,'playback_time':t+1,'actor':'A','patient':'B','recipient':None,'target':None,'action':f'事件{i}','modality':'actual','polarity':'positive','certainty':'F1_DIRECT','evidence_ids':[f'V{i}'],'review_status':'PASS'})
    s1={'state_id':'S1','character_id':'A','dimension':'goal','aspect':'primary','value':'信任同伴并等待','description':'主角明确说我相信你','basis':'EXPLICIT_STATEMENT','quote':'我相信你','certainty':'F1_DIRECT','playback_time':11,'audience_available_sec':12,'event_ids':['E1'],'evidence_ids':['V1'],'review_status':'UNREVIEWED'}
    s2={**s1,'state_id':'S2','value':'主动调查','description':'主角明确决定亲自调查','quote':'我要亲自查清楚','playback_time':31,'audience_available_sec':32,'event_ids':['E2'],'evidence_ids':['V2']}
    ch={'change_id':'CH1','character_id':'A','from_state_id':'S1','to_state_id':'S2','playback_time':31,'audience_available_sec':32,'event_ids':['E2'],'evidence_ids':['V2'],'reason':'目标由等待转为调查，仅记录状态变化及事件上下文','link_type':'CONTEXT','editorial_priority':90,'review_status':'UNREVIEWED'}
    d['character_states']=[s1,s2];d['character_state_changes']=[ch]
    return d


def reviewed(d):
    result=copy.deepcopy(d)
    for col,field in [('character_states','state_id'),('character_state_changes','change_id')]:
        for row in result[col]: add_review(result,row[field],'PASS','核对连续片源、角色归属和明确台词',reviewer='editor',source_checked=True)
    return result


class StateModelTests(unittest.TestCase):
    def test_optional_extension_preserves_legacy_dossier(self):
        d=fixture();d.pop('character_states');d.pop('character_state_changes')
        self.assertEqual(validate(d),[])
        self.assertEqual(migrate_to_v35(d)['character_states'],[])

    def test_unknown_character_state_field_is_rejected(self):
        d=fixture()
        d['character_states'][0]['playback_tim'] = 11
        self.assertTrue(any('未知字段' in err and 'playback_tim' in err for err in validate(d)))

    def test_records_validate_and_have_review_context(self):
        d=reviewed(fixture());self.assertEqual(validate(d),[])
        self.assertTrue(all(usable(d,s) for s in d['character_states']))
        self.assertTrue(usable(d,d['character_state_changes'][0],'character_state_changes'))
        self.assertIn('context_snapshot',d['review_history'][0])

    def test_import_is_transactional_and_forces_unreviewed(self):
        original=fixture();payload={'episode_id':'EP001','character_states':[original['character_states'][0]],'character_state_changes':[]}
        d=fixture();d['character_states']=[];d['character_state_changes']=[];before=copy.deepcopy(d)
        payload['character_states'][0]['review_status']='PASS'
        result=import_candidates(d,payload)
        self.assertEqual(result['character_states'][0]['review_status'],'UNREVIEWED');self.assertEqual(d,before)
        payload['character_states'][0]['character_id']='MISSING'
        with self.assertRaises(ValueError):import_candidates(d,payload)
        self.assertEqual(d,before)
        payload['episode_id']='OTHER'
        with self.assertRaises(ValueError):import_candidates(d,payload)

    def test_downgrading_pass_invalidates_state_and_dependent_change(self):
        d=reviewed(fixture())
        self.assertTrue(usable(d,d['character_states'][0]))
        self.assertTrue(usable(d,d['character_state_changes'][0],'character_state_changes'))
        downgraded=reviewed_dossier(d,'S1','NEED_EVIDENCE','台词上下文仍需复核','editor',source_checked=True)
        state=next(x for x in downgraded['character_states'] if x['state_id']=='S1')
        change=downgraded['character_state_changes'][0]
        self.assertFalse(usable(downgraded,state))
        self.assertFalse(usable(downgraded,change,'character_state_changes'))
        self.assertEqual(downgraded['review_history'][-1]['status'],'NEED_EVIDENCE')

    def test_state_pass_requires_reviewer_and_source_check(self):
        d=fixture();before=copy.deepcopy(d)
        with self.assertRaises(ValueError):add_review(d,'S1','PASS','why',reviewer='',source_checked=True)
        with self.assertRaises(ValueError):add_review(d,'S1','PASS','why',reviewer='editor')
        self.assertEqual(d,before)
        self.assertFalse(usable(d,d['character_states'][0]))

    def test_state_or_dependency_change_invalidates_old_review(self):
        d=reviewed(fixture())
        for mutator in [lambda p:p['character_states'][0].update(value='changed'),lambda p:p['evidence'][0].update(raw='different statement'),lambda p:p['events'][0].update(actor='B'),lambda p:p['characters'][0].update(review_status='DISPUTED')]:
            q=copy.deepcopy(d);mutator(q)
            self.assertFalse(usable(q,q['character_states'][0]))
            self.assertFalse(usable(q,q['character_state_changes'][0],'character_state_changes'))

    def test_interpretation_preserved_but_not_auto_selected(self):
        d=fixture();d['character_states'][0].update(basis='INTERPRETATION',certainty='I1_STRONG',value='也许决定复仇')
        self.assertEqual(validate(d),[])
        d=reviewed(d);self.assertFalse(usable(d,d['character_states'][0]))
        d['character_states'][0]['certainty']='F1_DIRECT'
        self.assertTrue(any('心理解释' in err for err in validate(d)))

    def test_quote_must_be_in_cited_evidence(self):
        d=fixture();d['character_states'][0]['quote']='不存在的台词'
        self.assertTrue(any('原句' in err for err in validate(d)))

    def test_invalid_time_nonfinite_or_future_evidence_rejected(self):
        for value in [float('nan'),float('inf'),True,-1,5]:
            d=fixture();d['character_states'][0]['audience_available_sec']=value
            self.assertTrue(validate(d))
        d=fixture();d['character_states'][0]['audience_available_sec']=11.5
        self.assertTrue(any('证据晚于' in err for err in validate(d)))

    def test_relationship_target_and_knowledge_scope(self):
        d=fixture();s=d['character_states'][0];s.update(dimension='relationship')
        self.assertTrue(any('target_character_id' in err for err in validate(d)))
        s['target_character_id']='B';self.assertTrue(any('维度' in err for err in validate(d)))
        d['character_state_changes']=[];self.assertEqual(validate(d),[])
        s.update(dimension='knowledge',target_character_id=None,knowledge_ids=['K1'])
        d['knowledge_states']=[{'knowledge_id':'K1','subject_type':'CHARACTER','character_id':'A','proposition':'同伴意图','state':'BELIEVES','playback_time':11,'evidence_ids':['V1'],'review_status':'PASS'}]
        self.assertEqual(validate(d),[])
        d['knowledge_states'][0]['playback_time']=40
        self.assertTrue(any('未来认知' in err for err in validate(d)))

    def test_change_event_strictly_before_source_state_is_rejected(self):
        d=fixture()
        d['events'][0]['playback_time']=10.5
        d['character_state_changes'][0]['event_ids']=['E1']
        self.assertTrue(any('必须位于前后状态时点之间' in err for err in validate(d)))

    def test_change_event_at_source_state_endpoint_is_allowed(self):
        d=fixture()
        d['events'][0]['playback_time']=11
        d['character_state_changes'][0]['event_ids']=['E1']
        self.assertFalse(any('必须位于前后状态时点之间' in err for err in validate(d)))
        d=fixture()
        d['character_state_changes'][0]['event_ids']=['E3']
        self.assertTrue(any('必须位于前后状态时点之间' in err for err in validate(d)))

    def test_change_event_at_transition_endpoint_is_allowed(self):
        d=fixture()
        # E2 occurs at 31s, exactly the destination state time.
        self.assertEqual(validate(d),[])

    def test_change_not_cause_or_reverse_timeline(self):
        d=fixture();d['character_state_changes'][0]['link_type']='CAUSES'
        self.assertTrue(any('不凭时间先后' in err for err in validate(d)))
        d=fixture();d['character_state_changes'][0]['from_state_id']='S2'
        self.assertTrue(validate(d))

    def test_mention_without_participation_rejected(self):
        d=fixture();d['events'][0].update(actor='B',patient=None)
        self.assertTrue(any('未记录该人物参与' in err for err in validate(d)))

    @unittest.skipUnless(shutil.which('node'),'Node required')
    def test_browser_dossier_review_export_is_consumable(self):
        d=fixture()
        js="const a=require('./web/character-state-review.js');let d=JSON.parse(require('fs').readFileSync(0,'utf8'));for(const [c,f] of [['character_states','state_id'],['character_state_changes','change_id']])for(const o of d[c])d=a.review(d,c,o[f],'PASS','reviewed source','web-editor',true);process.stdout.write(JSON.stringify(d));"
        result=subprocess.run(['node','-e',js],input=json.dumps(d),cwd=ROOT,text=True,encoding='utf-8',errors='replace',capture_output=True,check=True)
        d=json.loads(result.stdout)
        self.assertEqual(validate(d),[])
        self.assertTrue(all(usable(d,s) for s in d['character_states']))
        self.assertTrue(usable(d,d['character_state_changes'][0],'character_state_changes'))


    def test_state_graph_fork_issue_exposes_state_ids_for_compiler(self):
        from character_state_model import state_graph_issues
        d=fixture()
        s3={**d['character_states'][1],'state_id':'S3','value':'离开','playback_time':41,'audience_available_sec':42,'quote':'我要亲自查清楚','event_ids':['E2'],'evidence_ids':['V2']}
        d['character_states'].append(s3)
        c2={**d['character_state_changes'][0],'change_id':'CH2','to_state_id':'S3','playback_time':41,'audience_available_sec':42,'event_ids':['E2']}
        d['character_state_changes'].append(c2)
        issues=state_graph_issues(d)
        fork=next(x for x in issues if x['code']=='STATE_TRANSITION_FORK')
        self.assertIn('state_ids',fork)
        self.assertEqual(fork['state_ids'],['S1'])



class StateSeriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'series.sqlite'
        init_db(self.db,'SER','state story')
        self.d=reviewed(fixture());self.path=self.root/'ep.json';save_atomic(self.d,self.path);import_dossier(self.db,'SER',self.path)
        c=connect(self.db)
        try:self.gid=c.execute("SELECT global_character_id FROM character_mentions WHERE series_id='SER' AND episode_id='EP001' AND local_character_id='A'").fetchone()[0]
        finally:c.close()
        declare_visible(self.db,'SER','character',self.gid,'EP001',['EP001:V1'],'early public identity','editor')
        video=self.root/'video.mp4';video.write_bytes(b'placeholder');self.sources=self.root/'sources.json';self.sources.write_text(json.dumps({'EP001':str(video)}),encoding='utf-8')
    def tearDown(self):self.tmp.cleanup()
    def timeline(self,as_of='1',at_sec=None):
        c=connect(self.db)
        try:return trajectory(c,'SER',self.gid,as_of,at_sec)[0]
        finally:c.close()
    def replace(self,d):
        save_atomic(d,self.path);import_dossier(self.db,'SER',self.path,replace=True)
        declare_visible(self.db,'SER','character',self.gid,'EP001',['EP001:V1'],'current identity evidence','editor')

    def test_trajectory_state_graph_diagnostics_are_scoped_to_requested_character(self):
        d=copy.deepcopy(self.d)
        b1={**d['character_states'][0],'state_id':'B1','character_id':'B','value':'保护同伴'}
        b2={**d['character_states'][1],'state_id':'B2','character_id':'B','value':'离开同伴','playback_time':31,'audience_available_sec':32}
        b3={**d['character_states'][1],'state_id':'B3','character_id':'B','value':'寻找同伴','playback_time':41,'audience_available_sec':42}
        d['character_states'].extend([b1,b2,b3])
        cb1={**d['character_state_changes'][0],'change_id':'BC1','character_id':'B','from_state_id':'B1','to_state_id':'B2'}
        cb2={**cb1,'change_id':'BC2','to_state_id':'B3','playback_time':41,'audience_available_sec':42}
        d['character_state_changes'].extend([cb1,cb2])
        d['review_history']=[]
        for collection,field in [('character_states','state_id'),('character_state_changes','change_id')]:
            for row in d[collection]:
                add_review(d,row[field],'PASS','审核状态与证据',reviewer='editor',source_checked=True)
        self.replace(d)
        result=self.timeline()
        self.assertFalse(any(x.get('code')=='STATE_TRANSITION_FORK' for x in result['conflicts']))

    def test_fork_diagnostic_does_not_crash_state_cut_compiler(self):
        d=copy.deepcopy(self.d)
        s3={**d['character_states'][1],'state_id':'S3','value':'离开同伴','playback_time':41,'audience_available_sec':42}
        d['character_states'].append(s3)
        add_review(d,'S3','PASS','核对状态证据',reviewer='editor',source_checked=True)
        c2={**d['character_state_changes'][0],'change_id':'CH2','to_state_id':'S3','playback_time':41,'audience_available_sec':42}
        d['character_state_changes'].append(c2)
        add_review(d,'CH2','PASS','核对变化上下文',reviewer='editor',source_checked=True)
        self.replace(d)
        result=self.timeline()
        fork=next(x for x in result['conflicts'] if x.get('code')=='STATE_TRANSITION_FORK')
        self.assertEqual(fork['state_ids'],['S1'])
        with self.assertRaisesRegex(ValueError,'没有完整'):
            compile_state_cut(self.db,'SER',self.gid,'1',self.sources)

    def test_state_graph_diagnostics_respect_intra_episode_cutoff(self):
        d=copy.deepcopy(self.d)
        s3={**d['character_states'][1],'state_id':'S3','value':'离开同伴','playback_time':41,'audience_available_sec':42}
        d['character_states'].append(s3)
        add_review(d,'S3','PASS','核对状态证据',reviewer='editor',source_checked=True)
        c2={**d['character_state_changes'][0],'change_id':'CH2','to_state_id':'S3','playback_time':41,'audience_available_sec':42}
        d['character_state_changes'].append(c2)
        add_review(d,'CH2','PASS','核对变化上下文',reviewer='editor',source_checked=True)
        self.replace(d)
        early=self.timeline(at_sec=20)
        self.assertFalse(any(x.get('code')=='STATE_TRANSITION_FORK' for x in early['conflicts']))
        late=self.timeline(at_sec=50)
        self.assertTrue(any(x.get('code')=='STATE_TRANSITION_FORK' for x in late['conflicts']))

    def test_compiler_rejects_malformed_state_conflict_without_keyerror(self):
        from unittest.mock import patch
        with patch('character_state.state_graph_issues', return_value=[{'code':'BROKEN_DIAGNOSTIC','severity':'WARN'}]):
            with self.assertRaisesRegex(ValueError,'缺少有效 state_ids'):
                compile_state_cut(self.db,'SER',self.gid,'1',self.sources)

    def test_unreviewed_target_mapping_stays_unresolved_not_ambiguous(self):
        d=copy.deepcopy(self.d)
        relationship={**d['character_states'][0],'state_id':'S3','dimension':'relationship','aspect':'trust','value':'信任同伴','description':'主角明确表达信任','target_character_id':'B','playback_time':15,'audience_available_sec':16,'event_ids':['E1'],'evidence_ids':['V1']}
        d['character_states'].append(relationship)
        d['review_history']=[]
        self.replace(reviewed(d))
        c=connect(self.db)
        try:
            c.execute("UPDATE character_mentions SET review_status='NEED_EVIDENCE' WHERE series_id='SER' AND episode_id='EP001' AND local_character_id='B'")
            c.commit()
        finally:c.close()
        result=self.timeline()
        target=next(x for x in result['states'] if x['state_id']=='S3')
        self.assertEqual(target['local_target_character_id'],'B')
        self.assertIsNone(target['global_target_character_id'])
        self.assertEqual(target['target_identity_status'],'UNRESOLVED')
        self.assertNotEqual(target['target_identity_status'],'AMBIGUOUS')

    def test_cutoff_time_excludes_future_state_and_change(self):
        result=self.timeline(at_sec=20)
        self.assertEqual([s['state_id'] for s in result['states']],['S1']);self.assertEqual(result['changes'],[])
        self.assertNotIn('主动调查',json.dumps(result,ensure_ascii=False))
        result=self.timeline(at_sec=33);self.assertEqual(len(result['changes']),1)

    def test_identity_visibility_has_intra_episode_time_boundary(self):
        with self.assertRaisesRegex(ValueError,'可见性证据'):self.timeline(at_sec=5)
        c=connect(self.db)
        try:c.execute("UPDATE series_visibility SET review_status='NEED_EVIDENCE'");c.commit()
        finally:c.close()
        with self.assertRaisesRegex(ValueError,'series_visibility'):self.timeline(at_sec=20)

    def test_no_states_is_not_invented(self):
        d=copy.deepcopy(self.d);d['character_states']=[];d['character_state_changes']=[];d['review_history']=[];self.replace(d)
        self.assertEqual(self.timeline()['states'],[])
        with self.assertRaisesRegex(ValueError,'没有完整'):compile_state_cut(self.db,'SER',self.gid,'1',self.sources)

    def test_projection_export_and_replace_do_not_leave_old_states(self):
        c=connect(self.db)
        try:self.assertEqual(len(build_memory(c,'SER')['character_states']),2)
        finally:c.close()
        d=copy.deepcopy(self.d);d['character_state_changes']=[];d['character_states']=d['character_states'][:1];d['review_history']=d['review_history'][:1];self.replace(d)
        self.assertEqual(len(self.timeline()['states']),1)

    def test_episode_replace_reopens_visibility(self):
        d=copy.deepcopy(self.d);d['episode']['title']='new source';save_atomic(d,self.path);import_dossier(self.db,'SER',self.path,replace=True)
        c=connect(self.db)
        try:
            self.assertEqual(c.execute('SELECT review_status FROM series_visibility').fetchone()[0],'NEED_EVIDENCE')
            self.assertGreater(c.execute("SELECT COUNT(*) FROM audit_log WHERE action='REOPEN_VISIBILITY_AFTER_EPISODE_REPLACE'").fetchone()[0],0)
        finally:c.close()

    def test_compile_keeps_both_sides_and_is_pending(self):
        p=compile_state_cut(self.db,'SER',self.gid,'1',self.sources,target_sec=20,at_sec=40)
        self.assertEqual({c['event_id'] for c in p['clips']},{'E1','E2'})
        self.assertTrue(all('CH1' in c['character_state_change_ids'] for c in p['clips']))
        self.assertEqual({s['state_id'] for s in p['character_state_context']['states']},{'S1','S2'})
        with self.assertRaises(ValueError):require_approved(p)
        q=approve(p,'editor','核对两个阶段及上下文');require_approved(q)
        self.assertTrue(plan_director(p)['director_plan']['suggestions'][0]['character_state_ids'])

    def test_trimming_state_proof_blocks_version_approval(self):
        from edit_quality import continuity_qc
        p=compile_state_cut(self.db,'SER',self.gid,'1',self.sources,target_sec=20)
        p['clips'][0]['end_sec']=11
        self.assertIn('STATE_EVIDENCE_TRUNCATED',[r['code'] for r in continuity_qc(p)['issues']])
        with self.assertRaisesRegex(ValueError,'QC'):approve(p,'editor','trim request')

    def test_director_cannot_propose_trimming_state_proof(self):
        from shot_continuity import candidates
        from continuity_review import review
        from director_engine import shot_alternatives
        p=compile_state_cut(self.db,'SER',self.gid,'1',self.sources,target_sec=20)
        shots=[{'shot_id':'X1','start_sec':9,'end_sec':10,'visible_character_ids':['A'],'speaker_character_id':'A','gaze_direction':'right','review_status':'PASS','event_ids':['E1'],'evidence_ids':['V1']},{'shot_id':'X2','start_sec':10,'end_sec':11,'visible_character_ids':['B'],'gaze_direction':'left','review_status':'PASS','event_ids':['E1'],'evidence_ids':['V1']}]
        continuity=candidates(self.d,{}, {'shots':shots})
        for row in list(continuity['candidates']):continuity=review(continuity,row['candidate_id'],'PASS','continuous source checked','editor',True)
        self.assertTrue(continuity['candidates'])
        self.assertEqual(shot_alternatives(p,continuity),[])

    def test_budget_does_not_silently_drop_before_state(self):
        with self.assertRaisesRegex(ValueError,'没有完整'):compile_state_cut(self.db,'SER',self.gid,'1',self.sources,target_sec=5)

    def test_clip_and_metadata_respect_intra_episode_cutoff(self):
        p=compile_state_cut(self.db,'SER',self.gid,'1',self.sources,target_sec=20,at_sec=20)
        self.assertEqual(len(p['clips']),1);self.assertLessEqual(p['clips'][0]['end_sec'],20)
        self.assertNotIn('主动调查',json.dumps(p,ensure_ascii=False));self.assertNotIn('后期真实身份',json.dumps(p,ensure_ascii=False))

    def test_no_resurrection_of_expired_latest_state(self):
        d=copy.deepcopy(self.d);d['character_states'][1]['valid_until_sec']=35;d['review_history']=[];self.replace(reviewed(d))
        self.assertEqual(self.timeline(at_sec=40)['latest_in_cutoff_episode'],[])

    def test_unreviewed_overlapping_draft_does_not_block_approved_change(self):
        d=fixture()
        d['character_states'][0]['valid_until_sec']=25
        d=reviewed(d)
        draft={**d['character_states'][0],'state_id':'S1X','value':'开始怀疑同伴','description':'未审核草稿状态','playback_time':20,'audience_available_sec':21,'valid_until_sec':24,'event_ids':['E1'],'review_status':'UNREVIEWED'}
        d['character_states'].append(draft)
        self.replace(d)
        result=compile_state_cut(self.db,'SER',self.gid,'1',self.sources,target_sec=100)
        self.assertTrue(result['clips'])
        conflicts=result['compile_report']['non_blocking_conflicts']
        self.assertTrue(any('S1X' in x['state_ids'] for x in conflicts))

    def test_conflicts_block_automatic_change_selection(self):
        d=copy.deepcopy(self.d);s={**d['character_states'][0],'state_id':'S1_ALT','value':'另一个冲突目标'};d['character_states'].append(s);d['review_history']=[];self.replace(reviewed(d))
        self.assertTrue(self.timeline()['conflicts'])
        with self.assertRaisesRegex(ValueError,'没有完整'):compile_state_cut(self.db,'SER',self.gid,'1',self.sources)

    def test_linked_later_identity_does_not_leak_future_names(self):
        d=reviewed(fixture('EP002'));d['characters'][0]['canonical_name']='未来揭晓身份';d['review_history']=[];d=reviewed(d)
        path=self.root/'later.json';save_atomic(d,path);import_dossier(self.db,'SER',path)
        link_characters(self.db,'SER','EP001:A','EP002:A','identity verified','editor')
        early=self.timeline(as_of='1');self.assertNotIn('未来揭晓身份',json.dumps(early,ensure_ascii=False))
        full=self.timeline(as_of='2');self.assertEqual(len(full['states']),4)
        self.assertEqual(len({(s['episode_id'],s['state_id']) for s in full['states']}),4)

    def test_story_prompt_contains_cutoff_safe_state_context(self):
        from story_arc_compiler import make_prompt
        output=self.root/'prompt.txt'
        make_prompt(self.db,'SER','character',self.gid,'整理主角目标变化',output,max_episode_number=1)
        text=output.read_text(encoding='utf-8')
        self.assertIn('character_state_context',text)
        self.assertIn('信任同伴并等待',text)
        self.assertIn('不把 CONTEXT 事件关联写成因果',text)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_real_state_cut_render_review(self):
        from review_cut import render_review
        video=self.root/'real.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=160x120:d=100:r=12','-an','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
        self.sources.write_text(json.dumps({'EP001':str(video)}),encoding='utf-8')
        project=compile_state_cut(self.db,'SER',self.gid,'1',self.sources,target_sec=20,at_sec=40)
        project=approve(project,'editor','核对状态两端和台词上下文')
        result=render_review(project,self.root/'rendered',width=160,height=120,fps=12)
        self.assertEqual(result['technical_check'],'PASS')
        self.assertEqual(len(result['timeline']),2)
        self.assertEqual(len(result['keyframes']),2)
        self.assertTrue(result['timeline_mapping_valid'])

    def test_cli_review_and_timeline_output(self):
        script=str(ROOT/'scripts/character_state.py');out=self.root/'timeline.json'
        subprocess.run([sys.executable,script,'timeline',str(self.db),str(out),'--series-id','SER','--character',self.gid,'--as-of','1','--at-sec','20'],check=True,capture_output=True)
        self.assertEqual(len(json.loads(out.read_text(encoding='utf-8'))['states']),1)
        result=subprocess.run([sys.executable,script,'timeline',str(self.db),str(out),'--series-id','SER','--character',self.gid,'--as-of','1'],capture_output=True)
        self.assertNotEqual(result.returncode,0)

if __name__=='__main__':unittest.main()
