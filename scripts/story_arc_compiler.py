#!/usr/bin/env python3
"""Evidence- and knowledge-aware cross-episode story-arc compiler v3.7."""
from __future__ import annotations
import argparse, json, sys, uuid
from datetime import datetime, timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from series_memory import connect
from series_compiler import load_sources, build_event_clip

VERSION='3.8.0'
ROLES={'setup','inciting_incident','escalation','peak','reaction','payoff','resolution','bridge'}
ROLE_ORDER={'setup':0,'inciting_incident':1,'escalation':2,'bridge':2.5,'peak':3,'reaction':4,'payoff':5,'resolution':6}
LINK_BASES={'CROSS_EPISODE_CAUSAL','LOCAL_CAUSAL','SAME_STORYLINE','SAME_THREAD','SHARED_CHARACTER','HUMAN_CONTEXT'}

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def uid(prefix): return f'{prefix}_{uuid.uuid4().hex[:10]}'
def read_json(path):
    with open(path,encoding='utf-8') as f: return json.load(f)
def write_json(path,obj):
    out=Path(path); out.parent.mkdir(parents=True,exist_ok=True); tmp=out.with_suffix(out.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); tmp.replace(out)
def split_ref(ref):
    if not isinstance(ref,str) or ':' not in ref: raise ValueError(f'引用格式必须为 EPISODE_ID:OBJECT_ID: {ref!r}')
    a,b=ref.split(':',1)
    if not a or not b: raise ValueError(f'引用格式无效: {ref}')
    return a,b
def event_ref(ep,eid): return f'{ep}:{eid}'
def _event(c,sid,ep,eid):
    r=c.execute('SELECT * FROM events WHERE series_id=? AND episode_id=? AND event_id=?',(sid,ep,eid)).fetchone()
    if not r: raise ValueError(f'事件不存在: {ep}:{eid}')
    x=dict(r); x['evidence_ids']=json.loads(x.pop('evidence_ids_json') or '[]'); x['payload']=json.loads(x.pop('payload_json') or '{}')
    return x
def _order(c,sid,ep,eid):
    e=_event(c,sid,ep,eid)
    n=c.execute('SELECT episode_number FROM episodes WHERE series_id=? AND episode_id=?',(sid,ep)).fetchone()
    if not n: raise ValueError(f'集数不存在: {ep}')
    return int(n[0]),float(e['playback_time']),ep,eid
def _evidence_exists(c,sid,ref):
    ep,evid=split_ref(ref); r=c.execute('SELECT payload_json FROM episode_payloads WHERE series_id=? AND episode_id=?',(sid,ep)).fetchone()
    return bool(r and any(x.get('evidence_id')==evid for x in json.loads(r[0]).get('evidence',[])))
def _event_has_storyline(c,sid,ep,eid,gid):
    for r in c.execute("SELECT event_ids_json FROM storyline_mentions WHERE series_id=? AND episode_id=? AND global_storyline_id=? AND review_status='PASS'",(sid,ep,gid)):
        if eid in json.loads(r[0] or '[]'): return True
    return False
def _event_has_thread(c,sid,ep,eid,gid):
    ev=_event(c,sid,ep,eid); refs=set(ev['evidence_ids'])
    for r in c.execute("SELECT first_event,evidence_ids_json,payload_json FROM thread_mentions WHERE series_id=? AND episode_id=? AND global_thread_id=? AND review_status='PASS'",(sid,ep,gid)):
        if r[0]==eid: return True
        payload=json.loads(r[2] or '{}'); thread_refs=set(json.loads(r[1] or '[]'))|set(payload.get('resolution_evidence_ids',[]) or [])
        if refs & thread_refs: return True
    return False
def _character_episode_count(c,sid,gid,cutoff):
    return int(c.execute("SELECT COUNT(DISTINCT m.episode_id) FROM character_mentions m JOIN episodes e ON e.series_id=m.series_id AND e.episode_id=m.episode_id WHERE m.series_id=? AND m.global_character_id=? AND e.episode_number<=?",(sid,gid,cutoff)).fetchone()[0])

def _visible_by(c,sid,entity_type,entity_id,cutoff):
    if not entity_id:return False
    try:
        row=c.execute("SELECT effective_episode_number,review_status FROM series_visibility WHERE series_id=? AND entity_type=? AND entity_id=?",(sid,entity_type,entity_id)).fetchone()
    except Exception:
        return False
    return bool(row and row['review_status']=='PASS' and int(row['effective_episode_number'])<=int(cutoff))

def _validate_link(c,sid,link,errors,warnings,spoiler_cutoff=None):
    try: fe,fi=split_ref(link.get('from_event')); te,ti=split_ref(link.get('to_event'))
    except ValueError as exc: errors.append(str(exc)); return
    basis=link.get('basis')
    if basis not in LINK_BASES: errors.append(f'continuity link basis 无效: {basis}'); return
    try: a=_event(c,sid,fe,fi); b=_event(c,sid,te,ti)
    except ValueError as exc: errors.append(str(exc)); return
    if spoiler_cutoff is not None and basis!='LOCAL_CAUSAL':
        visibility_map={'CROSS_EPISODE_CAUSAL':('causal_edge',link.get('edge_id')),'SAME_STORYLINE':('storyline',link.get('storyline_id')),'SAME_THREAD':('thread',link.get('thread_id')),'SHARED_CHARACTER':('character',link.get('character_id')),'HUMAN_CONTEXT':('context',link.get('visibility_ref'))}
        vtype,vid=visibility_map.get(basis,('',None))
        if not _visible_by(c,sid,vtype,vid,spoiler_cutoff):
            errors.append(f'剧透保护阻止使用尚未声明在第 {spoiler_cutoff} 集前已可知的跨集关联: {basis} {vid or "(缺少 visibility_ref)"}')
    if basis=='CROSS_EPISODE_CAUSAL':
        edge=c.execute('SELECT * FROM cross_episode_causal_edges WHERE series_id=? AND edge_id=?',(sid,link.get('edge_id'))).fetchone()
        if not edge: errors.append(f'跨集因果边不存在: {link.get("edge_id")}')
        elif (edge['from_episode_id'],edge['from_event_id'],edge['to_episode_id'],edge['to_event_id'])!=(fe,fi,te,ti): errors.append(f'因果边 {link.get("edge_id")} 与事件不匹配')
        elif edge['review_status']!='PASS': warnings.append(f'跨集因果边 {edge["edge_id"]} 尚未人工确认（{edge["review_status"]}）')
    elif basis=='LOCAL_CAUSAL':
        if fe!=te: errors.append('LOCAL_CAUSAL 的两个事件必须在同一集')
        else:
            edge=c.execute('SELECT * FROM episode_causal_edges WHERE series_id=? AND episode_id=? AND cause_id=?',(sid,fe,link.get('edge_id'))).fetchone()
            if not edge: errors.append(f'单集因果边不存在: {link.get("edge_id")}')
            elif (edge['from_event'],edge['to_event'])!=(fi,ti): errors.append('单集因果边与声明的事件不匹配')
            elif edge['review_status']!='PASS': warnings.append(f'单集因果边 {edge["cause_id"]} 尚未人工确认')
    elif basis=='SAME_STORYLINE':
        gid=link.get('storyline_id')
        if not gid or not _event_has_storyline(c,sid,fe,fi,gid) or not _event_has_storyline(c,sid,te,ti,gid): errors.append(f'两个事件没有共同的已审核剧情线成员关系: {gid}')
    elif basis=='SAME_THREAD':
        gid=link.get('thread_id')
        if not gid or not _event_has_thread(c,sid,fe,fi,gid) or not _event_has_thread(c,sid,te,ti,gid): errors.append(f'两个事件没有共同的已审核伏笔/线索关系: {gid}')
    elif basis=='SHARED_CHARACTER':
        cols=('actor_global_id','patient_global_id','recipient_global_id','target_global_id')
        shared={a[k] for k in cols if a[k]} & {b[k] for k in cols if b[k]}
        if not shared or (link.get('character_id') and link['character_id'] not in shared): errors.append('两个事件没有声明的共同人物参与证据')
    elif basis=='HUMAN_CONTEXT':
        refs=link.get('evidence_refs') or []
        if not link.get('reason') or not refs: errors.append('HUMAN_CONTEXT 必须填写 reason 和 evidence_refs')
        for ref in refs:
            if not _evidence_exists(c,sid,ref): errors.append(f'人工上下文证据不存在: {ref}')
        if link.get('review_status')!='PASS': warnings.append('存在依赖人工上下文解释、但尚未标记 PASS 的叙事连接')

def latest_knowledge(c,sid,ep,eid,viewpoint):
    target=_order(c,sid,ep,eid)
    if viewpoint.get('type')=='VIEWER':
        rows=c.execute("SELECT * FROM character_knowledge WHERE series_id=? AND subject_type='VIEWER' AND review_status='PASS' ORDER BY episode_number,playback_time",(sid,)).fetchall()
    else:
        gid=viewpoint.get('character_id')
        rows=c.execute("SELECT * FROM character_knowledge WHERE series_id=? AND subject_type='CHARACTER' AND subject_global_id=? AND review_status='PASS' ORDER BY episode_number,playback_time",(sid,gid)).fetchall()
    latest={}
    for r in rows:
        key=(int(r['episode_number']),float(r['playback_time']),r['episode_id'])
        if key<=target: latest[r['proposition']]=dict(r)
    return sorted([{'proposition':k,'state':r['state'],'episode_id':r['episode_id'],'playback_time':r['playback_time'],'event_id':r['event_id'],'evidence_ids':json.loads(r['evidence_ids_json'] or '[]')} for k,r in latest.items()],key=lambda x:(x['episode_id'],x['playback_time'],x['proposition']))

def validate_plan(c,sid,plan,reviewed_only=True):
    errors=[]; warnings=[]
    spoiler_guard=plan.get('spoiler_guard') or {}
    cutoff=None
    if spoiler_guard.get('enabled',False):
        cutoff=spoiler_guard.get('max_episode_number')
        if not isinstance(cutoff,int) or cutoff<1: errors.append('spoiler_guard.enabled 时必须提供正整数 max_episode_number')
        else: warnings.append(f'剧透保护已启用：仅允许第 {cutoff} 集及以前的事件')
    elif spoiler_guard and spoiler_guard.get('enabled') is False:
        warnings.append('计划明确关闭剧透保护；可能包含全剧后续信息')
    if plan.get('schema_version')!='1.0.0': errors.append('story arc plan schema_version 必须为 1.0.0')
    if not str(plan.get('plan_id') or '').strip(): errors.append('plan.plan_id 不能为空')
    if plan.get('series_id')!=sid: errors.append('plan.series_id 与目标数据库不一致')
    if not str(plan.get('goal') or '').strip(): errors.append('plan.goal 不能为空')
    strategy=plan.get('sequence_strategy','CHRONOLOGICAL')
    if strategy not in {'CHRONOLOGICAL','EDITORIAL_NONLINEAR'}: errors.append('sequence_strategy 必须为 CHRONOLOGICAL 或 EDITORIAL_NONLINEAR')
    if strategy=='EDITORIAL_NONLINEAR' and not str(plan.get('reorder_reason') or '').strip(): errors.append('非线性叙事必须填写 reorder_reason')
    if plan.get('review_status','UNREVIEWED') not in {'UNREVIEWED','PASS','NEED_EVIDENCE','INCORRECT','DISPUTED'}: errors.append('plan.review_status 无效')
    if plan.get('target_duration_sec') is not None and (not isinstance(plan.get('target_duration_sec'),(int,float)) or plan.get('target_duration_sec')<=0): errors.append('target_duration_sec 必须是正数')
    if not isinstance(plan.get('risk_flags',[]),list): errors.append('risk_flags 必须是数组')
    viewpoint=plan.get('viewpoint') or {'type':'VIEWER'}
    if not isinstance(viewpoint,dict):
        errors.append('viewpoint 必须为对象'); viewpoint={'type':'VIEWER'}
    if viewpoint.get('type') not in {'VIEWER','CHARACTER'}: errors.append('viewpoint.type 必须是 VIEWER 或 CHARACTER')
    if viewpoint.get('type')=='CHARACTER' and not c.execute('SELECT 1 FROM global_characters WHERE series_id=? AND global_character_id=?',(sid,viewpoint.get('character_id'))).fetchone(): errors.append('viewpoint.character_id 不存在')
    if cutoff is not None and viewpoint.get('type')=='CHARACTER' and _character_episode_count(c,sid,viewpoint.get('character_id'),cutoff)>1 and not _visible_by(c,sid,'character',viewpoint.get('character_id'),cutoff):
        errors.append(f'剧透保护：视角人物 {viewpoint.get("character_id")} 跨集身份尚未声明在第 {cutoff} 集前已可知')
    segments=plan.get('segments') or []
    if not isinstance(segments,list) or not segments: return errors+['plan.segments 必须包含至少一个叙事段落'],warnings,[]
    seen=set(); ordered=[]; present_roles=set(); segment_ids=set()
    for i,seg in enumerate(segments):
        if not isinstance(seg,dict):
            errors.append(f'segments[{i}] 必须为对象'); continue
        segment_id=seg.get('segment_id')
        if not segment_id or segment_id in segment_ids: errors.append(f'segments[{i}].segment_id 缺失或重复')
        segment_ids.add(segment_id)
        if not str(seg.get('rationale') or '').strip(): errors.append(f'segments[{i}].rationale 不能为空')
        if seg.get('review_status','UNREVIEWED') not in {'UNREVIEWED','PASS','NEED_EVIDENCE','INCORRECT','DISPUTED'}: errors.append(f'segments[{i}].review_status 无效')
        role=seg.get('role')
        if role not in ROLES: errors.append(f'segments[{i}].role 无效: {role}')
        else: present_roles.add(role)
        ep=seg.get('episode_id'); ids=seg.get('event_ids') or []
        if not ep or not isinstance(ids,list) or not ids: errors.append(f'segments[{i}] 必须有 episode_id 和非空 event_ids'); continue
        for eid in ids:
            ref=(ep,eid)
            if ref in seen: errors.append(f'事件重复出现在多个片段: {ep}:{eid}'); continue
            seen.add(ref)
            try: ev=_event(c,sid,ep,eid)
            except ValueError as exc: errors.append(str(exc)); continue
            if cutoff is not None and int(ev['episode_number'])>cutoff:
                errors.append(f'剧透保护阻止使用截止集数之后的事件: {ep}:{eid}（第 {ev["episode_number"]} 集 > 第 {cutoff} 集）')
            if cutoff is not None:
                for gid in {ev[k] for k in ('actor_global_id','patient_global_id','recipient_global_id','target_global_id') if ev[k]}:
                    if _character_episode_count(c,sid,gid,cutoff)>1 and not _visible_by(c,sid,'character',gid,cutoff):
                        errors.append(f'剧透保护：事件 {ep}:{eid} 涉及尚未声明在第 {cutoff} 集前已可知的跨集人物身份 {gid}')
            if ev['modality'] in {'imagined','hypothetical'}: errors.append(f'不允许将 {ev["modality"]} 作为现实发生的故事节点: {ep}:{eid}')
            if ev['certainty'] in {'I3_SPECULATIVE','X_INTERPRETATION'}: errors.append(f'推测/主观解读不能作为已核实剧情事件: {ep}:{eid}（{ev["certainty"]}）')
            if ev['certainty']=='I2_PLAUSIBLE': warnings.append(f'{ep}:{eid} 属于合理推断，不是直接事实；避免用旁白把它说成确定事实')
            if ev['review_status']=='INCORRECT': errors.append(f'事件已被否决: {ep}:{eid}')
            if reviewed_only and ev['review_status']!='PASS': errors.append(f'事件未审核通过: {ep}:{eid}（{ev["review_status"]}）')
            if role in {'peak','payoff','inciting_incident'} and not ev['evidence_ids']:
                warnings.append(f'关键叙事角色 {role} 缺少直接证据引用: {ep}:{eid}')
            if ev['modality'] in {'planned','commanded','desired','threatened'}: warnings.append(f'{ep}:{eid} 是计划/命令/愿望/威胁表达，不代表行动已执行')
            if ev['modality']=='recalled': warnings.append(f'{ep}:{eid} 属于回忆内容，成片需明确其回忆属性')
            ordered.append((_order(c,sid,ep,eid),role,ep,eid,seg))
        segment_event_evidence=set()
        for eid in ids:
            try: segment_event_evidence.update(_event(c,sid,ep,eid)['evidence_ids'])
            except ValueError: pass
        for ref in seg.get('evidence_refs',[]) or []:
            if not _evidence_exists(c,sid,ref): errors.append(f'片段证据引用不存在: {ref}')
            else:
                ref_ep,ref_id=split_ref(ref)
                if ref_ep!=ep: errors.append(f'段落证据必须来自该段落的集数: {ref}')
                elif ref_id not in segment_event_evidence: errors.append(f'段落证据没有关联到该段落所选事件: {ref}')
    required=plan.get('required_roles') or ['setup','peak','reaction','payoff']
    if not isinstance(required,list): errors.append('required_roles 必须是数组'); required=['setup','peak','reaction','payoff']
    invalid_required=[x for x in required if x not in ROLES]
    if invalid_required: errors.append('required_roles 含无效角色: '+', '.join(invalid_required))
    missing=[r for r in required if r not in present_roles]
    if missing: warnings.append('缺少叙事角色: '+', '.join(missing)+'；不得虚构补齐')
    if strategy=='CHRONOLOGICAL':
        ranks=[ROLE_ORDER[x.get('role')] for x in segments if x.get('role') in ROLES]
        if ranks!=sorted(ranks): errors.append('CHRONOLOGICAL 角色顺序不符合 setup→peak→reaction→payoff；需要重排请声明 EDITORIAL_NONLINEAR')
        times=[x[0] for x in ordered]
        if times!=sorted(times): errors.append('CHRONOLOGICAL 计划中的原片事件顺序发生倒置')
    else: warnings.append('这是非线性叙事顺序；必须保留叙事说明并检查信息揭示顺序')
    links=plan.get('continuity_links') or []
    if not isinstance(links,list): errors.append('continuity_links 必须是数组'); links=[]
    valid_links=[]
    selected_refs={event_ref(ep,eid) for ep,eid in seen}
    for link in links:
        if not isinstance(link,dict):
            errors.append('continuity_links 项必须为对象'); continue
        valid_links.append(link)
        if link.get('from_event') not in selected_refs or link.get('to_event') not in selected_refs:
            errors.append(f"连续性连接必须连接计划中已选事件: {link.get('from_event')} → {link.get('to_event')}")
        _validate_link(c,sid,link,errors,warnings,cutoff)
    links=valid_links
    linked={(x.get('from_event'),x.get('to_event')) for x in links}
    flat=[(seg.get('episode_id'),eid) for seg in segments if isinstance(seg,dict) for eid in (seg.get('event_ids') or [])]
    for a,b in zip(flat,flat[1:]):
        if (event_ref(*a),event_ref(*b)) not in linked: warnings.append(f'叙事连接缺少明确依据: {a[0]}:{a[1]} → {b[0]}:{b[1]}')
    return errors,list(dict.fromkeys(warnings)),ordered
def compile_story_arc(db_path,sources_path,plan_path,output,reviewed_only=True,require_release_ready=False,before=3.0,after=5.0):
    plan=read_json(plan_path); sid=plan.get('series_id')
    if not sid: raise ValueError('plan.series_id 不能为空')
    sources=load_sources(sources_path); c=connect(db_path)
    try:
        errors,warnings,ordered=validate_plan(c,sid,plan,reviewed_only)
        if errors: raise ValueError('叙事计划校验失败:\n- '+'\n- '.join(errors))
        viewpoint=plan.get('viewpoint') or {'type':'VIEWER'}
        clips=[]; missing_sources=[]
        for order,(_,role,ep,eid,seg) in enumerate(ordered,1):
            source=sources.get(ep)
            if not source:
                missing_sources.append({'episode_id':ep,'event_id':eid,'reason':'sources.json 缺少原片路径'}); continue
            clip=build_event_clip(c,sid,ep,eid,source,before,after,reviewed_only)
            if not clip:
                warnings.append(f'未能生成有效片段: {ep}:{eid}'); continue
            clip['narrative_order']=order
            clip['story_arc_role']=role
            clip['story_arc_segment_id']=seg.get('segment_id') or f'SEG{order:03d}'
            clip['segment_rationale']=seg.get('rationale','')
            clip['connection_note']=seg.get('connection_note','')
            clip['perspective_knowledge']=latest_knowledge(c,sid,ep,eid,viewpoint)
            clip['viewer_knowledge']=latest_knowledge(c,sid,ep,eid,{'type':'VIEWER'})
            if viewpoint.get('type')=='CHARACTER' and not clip['perspective_knowledge']:
                warnings.append(f'缺少该人物在 {ep}:{eid} 时点的已审核认知记录')
            if not clip['viewer_knowledge']: warnings.append(f'缺少观众在 {ep}:{eid} 时点的已审核认知记录')
            char_map={x['proposition']:x for x in clip['perspective_knowledge']}
            viewer_map={x['proposition']:x for x in clip['viewer_knowledge']}
            clip['knowledge_gap_flags']=[]
            for proposition in sorted(set(char_map)|set(viewer_map)):
                cs=char_map.get(proposition,{}).get('state','UNKNOWN')
                vs=viewer_map.get(proposition,{}).get('state','UNKNOWN')
                if viewpoint.get('type')=='CHARACTER' and vs in {'KNOWS','SUSPECTS'} and cs in {'UNAWARE','FALSE_BELIEF'}:
                    clip['knowledge_gap_flags'].append({'proposition':proposition,'type':'VIEWER_AHEAD_OF_CHARACTER','character_state':cs,'viewer_state':vs})
                elif viewpoint.get('type')=='CHARACTER' and cs in {'KNOWS','SUSPECTS'} and vs in {'UNAWARE','FALSE_BELIEF'}:
                    clip['knowledge_gap_flags'].append({'proposition':proposition,'type':'CHARACTER_AHEAD_OF_VIEWER','character_state':cs,'viewer_state':vs})
            clip['evidence_refs']=[f'{ep}:{x}' for x in clip.get('evidence_ids',[])]
            clips.append(clip)
        if not clips: raise ValueError('没有可渲染片段；检查来源路径、事件审核状态和时间窗')
        if missing_sources: warnings.append(f'有 {len(missing_sources)} 个事件缺少原片路径')
        required=plan.get('required_roles') or ['setup','peak','reaction','payoff']
        present={x.get('role') for x in plan.get('segments',[])}
        missing_roles=[x for x in required if x not in present]
        unreviewed_segments=[x.get('segment_id') for x in plan.get('segments',[]) if x.get('review_status','UNREVIEWED')!='PASS']
        unreviewed_edges=[]
        for link in plan.get('continuity_links',[]):
            if link.get('basis')=='CROSS_EPISODE_CAUSAL' and link.get('edge_id'):
                r=c.execute('SELECT review_status FROM cross_episode_causal_edges WHERE series_id=? AND edge_id=?',(sid,link['edge_id'])).fetchone()
                if not r or r[0]!='PASS': unreviewed_edges.append(link['edge_id'])
        plan_review=plan.get('review_status','UNREVIEWED')
        continuity_review_warnings=[w for w in warnings if '尚未人工确认' in w or '尚未标记 PASS' in w]
        plausible_inference_warnings=[w for w in warnings if '合理推断' in w]
        critical_evidence_missing=any('关键叙事角色' in w and '缺少直接证据' in w for w in warnings)
        knowledge_missing=any('缺少该人物' in w or '缺少观众' in w for w in warnings)
        knowledge_required=bool(plan.get('require_knowledge_coverage',False))
        risk_flags=plan.get('risk_flags') or []
        release_ready=(not missing_roles and not missing_sources and not unreviewed_segments and not unreviewed_edges and not continuity_review_warnings and not plausible_inference_warnings and not critical_evidence_missing and not risk_flags and (not knowledge_required or not knowledge_missing) and plan_review=='PASS' and all(x.get('event_review_status')=='PASS' for x in clips) and not any('缺少明确依据' in w for w in warnings))
        target=float(plan.get('target_duration_sec') or 300)
        duration=sum(x['end_sec']-x['start_sec'] for x in clips)
        if duration>target: warnings.append(f'当前完整叙事片段共 {duration:.1f}s，超过目标 {target:.1f}s；不自动删除关键角色段，请在工作台精修')
        if missing_roles: warnings.append('计划缺少必需叙事角色: '+', '.join(missing_roles))
        if risk_flags: warnings.append('计划仍有未解除风险标记: '+', '.join(str(x) for x in risk_flags))
        if knowledge_required and knowledge_missing: warnings.append('计划要求完整人物/观众认知覆盖，但部分片段缺少已审核认知记录')
        if require_release_ready and not release_ready: raise ValueError('计划尚未达到可发布条件；检查缺失角色、人工审核、证据连接和原片路径')
        series=c.execute('SELECT title FROM series WHERE series_id=?',(sid,)).fetchone()
        project={'project_version':'3.8.0','compiler_version':VERSION,'project_type':'cross_episode_story_arc','project_subtype':'narrative_arc','series_id':sid,'series_title':series[0] if series else '',
          'title':plan.get('title') or plan.get('goal'),'goal':plan.get('goal'),'viewpoint':viewpoint,'spoiler_guard':plan.get('spoiler_guard') or {'enabled':False},'sequence_strategy':plan.get('sequence_strategy','CHRONOLOGICAL'),'reorder_reason':plan.get('reorder_reason',''),
          'target_duration_sec':target,'estimated_duration_sec':round(duration,3),'created_at':now(),'sources':sources,'story_arc_plan':plan,'clips':clips,
          'narrative_audit':{'required_roles':required,'present_roles':sorted(present),'missing_roles':missing_roles,'continuity_links':plan.get('continuity_links',[]),'warnings':list(dict.fromkeys(warnings)),
            'missing_sources':missing_sources,'unreviewed_segments':unreviewed_segments,'unreviewed_cross_episode_edges':unreviewed_edges,'continuity_review_warnings':continuity_review_warnings,'plausible_inference_warnings':plausible_inference_warnings,'critical_evidence_missing':critical_evidence_missing,'knowledge_coverage_required':knowledge_required,'knowledge_coverage_missing':knowledge_missing,'spoiler_guard':plan.get('spoiler_guard') or {'enabled':False},'risk_flags':risk_flags,'release_ready':release_ready,'reviewed_only':reviewed_only}}
        write_json(output,project); return project
    finally: c.close()

def make_prompt(db_path,sid,selector_type,selector_id,goal,output,max_episode_number=None):
    from series_compiler import event_ids_for_selector
    c=connect(db_path)
    try:
        selected=event_ids_for_selector(c,sid,selector_type,selector_id)
        # Expand a person/pair selector to the approved storyline context they participate in.
        target_gids=[]
        if selector_type=='character': target_gids=[selector_id]
        elif selector_type=='pair': target_gids=[x.strip() for x in selector_id.split(',') if x.strip()]
        if max_episode_number is not None:
            if selector_type in {'character','pair'}:
                for gid in target_gids:
                    if not _visible_by(c,sid,'character',gid,max_episode_number):
                        raise ValueError(f'剧透保护：人物 {gid} 尚无证据声明在第 {max_episode_number} 集前已被观众识别；请先运行 series_visibility.py declare')
            elif selector_type=='storyline' and not _visible_by(c,sid,'storyline',selector_id,max_episode_number):
                raise ValueError(f'剧透保护：剧情线 {selector_id} 尚未声明在截止集数前已可知')
            elif selector_type=='thread' and not _visible_by(c,sid,'thread',selector_id,max_episode_number):
                raise ValueError(f'剧透保护：伏笔/线索 {selector_id} 尚未声明在截止集数前已可知')
        storyline_ids={selector_id} if selector_type=='storyline' else set()
        if target_gids:
            for row in c.execute("SELECT episode_id,global_storyline_id,event_ids_json,character_ids_json FROM storyline_mentions WHERE series_id=? AND review_status='PASS'",(sid,)):
                if max_episode_number is not None and not _visible_by(c,sid,'storyline',row['global_storyline_id'],max_episode_number): continue
                if max_episode_number is not None and (c.execute('SELECT episode_number FROM episodes WHERE series_id=? AND episode_id=?',(sid,row['episode_id'])).fetchone() or [10**9])[0] > max_episode_number: continue
                local_ids=set()
                for gid in target_gids:
                    mention=c.execute('SELECT local_character_id FROM character_mentions WHERE series_id=? AND episode_id=? AND global_character_id=?',(sid,row['episode_id'],gid)).fetchone()
                    if mention: local_ids.add(mention[0])
                chars=set(json.loads(row['character_ids_json'] or '[]'))
                if len(local_ids)==len(target_gids) and local_ids.issubset(chars):
                    storyline_ids.add(row['global_storyline_id'])
                    for event_id in json.loads(row['event_ids_json'] or '[]'): selected.add((row['episode_id'],event_id))
        # Add one-hop causal neighbours for context, but never convert an edge into a confirmed fact.
        selected_before=set(selected)
        for edge in c.execute('SELECT * FROM cross_episode_causal_edges WHERE series_id=?',(sid,)):
            if max_episode_number is not None and not _visible_by(c,sid,'causal_edge',edge['edge_id'],max_episode_number): continue
            left=(edge['from_episode_id'],edge['from_event_id']); right=(edge['to_episode_id'],edge['to_event_id'])
            if left in selected_before: selected.add(right)
            if right in selected_before: selected.add(left)
        for ep in {x[0] for x in selected_before}:
            for edge in c.execute('SELECT * FROM episode_causal_edges WHERE series_id=? AND episode_id=?',(sid,ep)):
                left=(ep,edge['from_event']); right=(ep,edge['to_event'])
                if left in selected_before: selected.add(right)
                if right in selected_before: selected.add(left)
        if max_episode_number is not None:
            selected={(ep,eid) for ep,eid in selected if (c.execute('SELECT episode_number FROM episodes WHERE series_id=? AND episode_id=?',(sid,ep)).fetchone() or [10**9])[0] <= max_episode_number}
        events=[]
        for ep,eid in sorted(selected,key=lambda x:_order(c,sid,*x)):
            ev=_event(c,sid,ep,eid)
            if max_episode_number is not None:
                local_payload=ev['payload']
                local_names={r['local_character_id']:r['canonical_name'] for r in c.execute('SELECT local_character_id,canonical_name FROM character_mentions WHERE series_id=? AND episode_id=?',(sid,ep))}
                events.append({'episode_id':ep,'event_id':eid,'episode_number':ev['episode_number'],'playback_time':ev['playback_time'],
                    'actor_local_id':local_payload.get('actor'),'actor_name':local_names.get(local_payload.get('actor')),
                    'patient_local_id':local_payload.get('patient'),'patient_name':local_names.get(local_payload.get('patient')),
                    'recipient_local_id':local_payload.get('recipient'),'recipient_name':local_names.get(local_payload.get('recipient')),
                    'target_local_id':local_payload.get('target'),'target_name':local_names.get(local_payload.get('target')),
                    'action':ev['action'],'quote':ev['quote'],'modality':ev['modality'],'certainty':ev['certainty'],'review_status':ev['review_status'],'evidence_ids':ev['evidence_ids'],'payload':local_payload})
            else:
                events.append({'episode_id':ep,'event_id':eid,'episode_number':ev['episode_number'],'playback_time':ev['playback_time'],'actor_global_id':ev['actor_global_id'],'patient_global_id':ev['patient_global_id'],'recipient_global_id':ev['recipient_global_id'],'target_global_id':ev['target_global_id'],'action':ev['action'],'quote':ev['quote'],'modality':ev['modality'],'certainty':ev['certainty'],'review_status':ev['review_status'],'evidence_ids':ev['evidence_ids'],'payload':ev['payload']})
        selected_refs={event_ref(ep,eid) for ep,eid in selected}
        involved_global_ids={x for ep,eid in selected for x in (_event(c,sid,ep,eid)[k] for k in ('actor_global_id','patient_global_id','recipient_global_id','target_global_id')) if x}
        causal=[]
        for r in c.execute('SELECT * FROM cross_episode_causal_edges WHERE series_id=? ORDER BY created_at',(sid,)):
            if max_episode_number is not None and not _visible_by(c,sid,'causal_edge',r['edge_id'],max_episode_number): continue
            if event_ref(r['from_episode_id'],r['from_event_id']) not in selected_refs or event_ref(r['to_episode_id'],r['to_event_id']) not in selected_refs: continue
            x=dict(r);x['evidence_refs']=json.loads(x.pop('evidence_refs_json'));x['counter_evidence_refs']=json.loads(x.pop('counter_evidence_refs_json'));causal.append(x)
        local_causal=[]
        for r in c.execute('SELECT * FROM episode_causal_edges WHERE series_id=? ORDER BY episode_id,cause_id',(sid,)):
            if event_ref(r['episode_id'],r['from_event']) in selected_refs and event_ref(r['episode_id'],r['to_event']) in selected_refs:
                x=dict(r);x['evidence_ids']=json.loads(x.pop('evidence_ids_json'));x['counter_evidence_ids']=json.loads(x.pop('counter_evidence_ids_json'));x['payload']=json.loads(x.pop('payload_json'));local_causal.append(x)
        knowledge=[]
        for r in c.execute('SELECT * FROM character_knowledge WHERE series_id=? ORDER BY episode_number,playback_time',(sid,)):
            if (max_episode_number is None or int(r['episode_number'])<=max_episode_number) and (r['subject_type']=='VIEWER' or r['subject_global_id'] in target_gids or (max_episode_number is None and r['subject_global_id'] in involved_global_ids)):
                x=dict(r);x['evidence_ids']=json.loads(x.pop('evidence_ids_json'));x['payload']=json.loads(x.pop('payload_json'))
                if max_episode_number is not None:
                    x['subject_local_id']=x['payload'].get('character_id');x.pop('subject_global_id',None)
                knowledge.append(x)
        storyline_context=[]
        for gid in storyline_ids:
            if max_episode_number is not None and not _visible_by(c,sid,'storyline',gid,max_episode_number): continue
            row=c.execute('SELECT * FROM global_storylines WHERE series_id=? AND global_storyline_id=?',(sid,gid)).fetchone()
            if row:
                mentions=c.execute('SELECT episode_id,local_storyline_id,title,summary,status,review_status,event_ids_json FROM storyline_mentions WHERE series_id=? AND global_storyline_id=? ORDER BY episode_id',(sid,gid)).fetchall()
                mentions=[m for m in mentions if max_episode_number is None or (c.execute('SELECT episode_number FROM episodes WHERE series_id=? AND episode_id=?',(sid,m['episode_id'])).fetchone() or [10**9])[0] <= max_episode_number]
                if mentions:
                    storyline_context.append({'global_storyline_id':gid,'title':mentions[0]['title'],'description':mentions[0]['summary'],'mentions':[{'episode_id':m['episode_id'],'local_storyline_id':m['local_storyline_id'],'summary':m['summary'],'status':m['status'],'review_status':m['review_status'],'event_ids':json.loads(m['event_ids_json'] or '[]')} for m in mentions]})
        selected_evidence={(ep,evid) for ep,eid in selected for evid in _event(c,sid,ep,eid)['evidence_ids']}
        thread_context=[]
        for th in c.execute('SELECT * FROM global_threads WHERE series_id=?',(sid,)):
            if max_episode_number is not None and not _visible_by(c,sid,'thread',th['global_thread_id'],max_episode_number): continue
            if selector_type=='thread' and th['global_thread_id']!=selector_id: continue
            mentions=c.execute('SELECT episode_id,local_thread_id,description,status,first_event,review_status,evidence_ids_json,payload_json FROM thread_mentions WHERE series_id=? AND global_thread_id=?',(sid,th['global_thread_id'])).fetchall()
            mentions=[m for m in mentions if max_episode_number is None or (c.execute('SELECT episode_number FROM episodes WHERE series_id=? AND episode_id=?',(sid,m['episode_id'])).fetchone() or [10**9])[0] <= max_episode_number]
            relevant=[]
            for m in mentions:
                payload=json.loads(m['payload_json'] or '{}')
                refs=set(json.loads(m['evidence_ids_json'] or '[]'))|set(payload.get('resolution_evidence_ids',[]) or [])
                evidence_match=any((m['episode_id'],ref) in selected_evidence for ref in refs)
                if (m['episode_id'],m['first_event']) in selected or evidence_match:
                    relevant.append({'episode_id':m['episode_id'],'local_thread_id':m['local_thread_id'],'description':m['description'],'status':m['status'],'first_event':m['first_event'],'review_status':m['review_status'],'payload':payload})
            if relevant: thread_context.append({'global_thread_id':th['global_thread_id'],'description':relevant[0]['description'],'mentions':relevant})
        arc_context=[]
        for row in c.execute('SELECT * FROM episode_story_arcs WHERE series_id=? ORDER BY episode_id,arc_id',(sid,)):
            if max_episode_number is not None and (c.execute('SELECT episode_number FROM episodes WHERE series_id=? AND episode_id=?',(sid,row['episode_id'])).fetchone() or [10**9])[0] > max_episode_number: continue
            ids=set(json.loads(row['event_ids_json'] or '[]'))
            if ids & {eid for ep,eid in selected if ep==row['episode_id']}:
                arc_context.append({'episode_id':row['episode_id'],'arc_id':row['arc_id'],'title':row['title'],'arc_type':row['arc_type'],'semantic_status':row['semantic_status'],'review_status':row['review_status'],'event_ids':sorted(ids),'segments':json.loads(row['segments_json'] or '[]')})
        relation_context=[]
        involved=involved_global_ids
        for row in c.execute('SELECT * FROM relations WHERE series_id=? ORDER BY episode_id,relation_id',(sid,)):
            if max_episode_number is not None and (c.execute('SELECT episode_number FROM episodes WHERE series_id=? AND episode_id=?',(sid,row['episode_id'])).fetchone() or [10**9])[0] > max_episode_number: continue
            if row['source_global_id'] in involved or row['target_global_id'] in involved:
                payload=json.loads(row['payload_json'] or '{}')
                if max_episode_number is not None:
                    relation_context.append({'episode_id':row['episode_id'],'relation_id':row['relation_id'],'source_character_id':payload.get('source_character'),'target_character_id':payload.get('target_character'),'type':row['relation_type'],'status':row['status'],'review_status':row['review_status'],'evidence_ids':json.loads(row['evidence_ids_json'] or '[]'),'payload':payload})
                else:
                    x=dict(row);x['evidence_ids']=json.loads(x.pop('evidence_ids_json'));x['payload']=payload;relation_context.append(x)
        state_context = []
        from character_state import trajectory
        state_cutoff = max_episode_number if max_episode_number is not None else c.execute('SELECT MAX(episode_number) FROM episodes WHERE series_id=?', (sid,)).fetchone()[0]
        if state_cutoff is not None:
            for gid in sorted(set(target_gids)):
                if _visible_by(c, sid, 'character', gid, state_cutoff):
                    timeline, _ = trajectory(c, sid, gid, str(state_cutoff))
                    if timeline['states']:
                        state_context.append(timeline)
        context={'series_id':sid,'goal':goal,'spoiler_guard':{'enabled':max_episode_number is not None,'max_episode_number':max_episode_number,'strict':True,'cross_episode_links_require_visibility_declaration':True},'selector':{'type':selector_type,'id':selector_id},'events':events,'storyline_context':storyline_context,'thread_context':thread_context,'episode_story_arcs':arc_context,'relation_context':relation_context,'character_state_context':state_context,'local_causal_edges':local_causal,'cross_episode_causal_edges':causal,'knowledge_states':knowledge}
    finally:c.close()
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    prompt=('''# 跨集故事弧规划任务

请根据以下全剧记忆生成符合 `references/story-arc-plan.schema.json` 的 JSON 叙事计划。

目标：'''+goal+'''

必须遵守：只引用存在的 episode_id/event_id；默认按原剧时间顺序；不得把计划、威胁、想象写成已执行事实；不得凭时间先后推断因果；每段说明选择理由；缺少 setup/peak/reaction/payoff/resolution 时写入 missing_roles，不得编造；人物认知必须按该时点 knowledge_states 判断，不得以后续信息倒灌；关键转折保留 evidence_refs。若 spoiler_guard.enabled=true，只能使用 max_episode_number 及以前的事件、分集本地别名、剧情线和认知资料；任何跨集人物身份、剧情线、伏笔或因果关联，只有存在 series_visibility 中 effective_episode_number 不晚于截止集数且 review_status=PASS 的声明才可使用；不得使用全剧合并身份推断截止集数之前尚未揭示的身份。只把已审核 PASS 的因果边写成已确认因果；相同剧情线、伏笔、共同人物仅能作为连续性依据，不自动证明因果。若缺乏可靠连接，明确写 risk_flags / missing_roles，不能用 episode 顺序补因果。对每个段落给出人物与观众认知差异。character_state_context 仅提供已审核、可见时点受限的状态记录和显式状态变化；按 episode_id/playback_time/audience_available_sec 对齐，不把较晚状态倒灌到早期事件，不把 CONTEXT 事件关联写成因果，不补写缺失心理。输出纯 JSON，不要 Markdown 围栏。

```json
'''+json.dumps(context,ensure_ascii=False,indent=2)+'''
```
''')
    out.write_text(prompt,encoding='utf-8')
    return {'output':str(out),'candidate_events':len(events),'cross_episode_causal_edges':len(causal),'knowledge_states':len(knowledge),'spoiler_guard':context['spoiler_guard']}

def main():
    p=argparse.ArgumentParser(description='跨集故事弧规划与证据/认知/剧透校验 v3.8')
    sub=p.add_subparsers(dest='cmd',required=True)
    x=sub.add_parser('compile',help='校验叙事计划并生成跨集剪辑项目')
    for arg in ('db','sources','plan','output'): x.add_argument(arg)
    x.add_argument('--include-unreviewed',action='store_true');x.add_argument('--require-release-ready',action='store_true');x.add_argument('--before',type=float,default=3);x.add_argument('--after',type=float,default=5)
    x=sub.add_parser('make-prompt',help='按人物/关系/剧情线/伏笔生成 ChatGPT 规划上下文');x.add_argument('db');x.add_argument('output');x.add_argument('--series-id',required=True)
    g=x.add_mutually_exclusive_group(required=True);g.add_argument('--character');g.add_argument('--pair');g.add_argument('--storyline');g.add_argument('--thread');x.add_argument('--goal',required=True);cut=x.add_mutually_exclusive_group(required=True);cut.add_argument('--as-of',help='截止集数，例如 15 或 EP015；严格隔离未来集');cut.add_argument('--full-series',action='store_true',help='明确允许使用全剧信息，可能包含剧透')
    a=p.parse_args()
    try:
        if a.cmd=='compile':
            if a.before<0 or a.after<0: raise ValueError('before/after 不能为负')
            pr=compile_story_arc(a.db,a.sources,a.plan,a.output,not a.include_unreviewed,a.require_release_ready,a.before,a.after)
            print(json.dumps({'output':a.output,'clips':len(pr['clips']),'duration_sec':pr['estimated_duration_sec'],'release_ready':pr['narrative_audit']['release_ready'],'warnings':pr['narrative_audit']['warnings']},ensure_ascii=False,indent=2))
        else:
            st='character' if a.character else 'pair' if a.pair else 'storyline' if a.storyline else 'thread';sid=a.character or a.pair or a.storyline or a.thread
            cutoff=None
            if a.as_of:
                from series_search import resolve_cutoff
                cc=connect(a.db)
                try:cutoff=resolve_cutoff(cc,a.series_id,a.as_of)[0]
                finally:cc.close()
            print(json.dumps(make_prompt(a.db,a.series_id,st,sid,a.goal,a.output,cutoff),ensure_ascii=False,indent=2))
    except Exception as exc: print(f'错误: {exc}',file=sys.stderr);raise SystemExit(2)
if __name__=='__main__': main()

