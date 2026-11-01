#!/usr/bin/env python3
"""Cross-episode story compiler for Drama Dossier v3.6.

The compiler selects traceable event/highlight windows from a Series Memory
SQLite database and creates a multi-source edit project. It does not infer
missing events or silently turn unreviewed hypotheses into established facts.
"""
from __future__ import annotations
import argparse, json, re, sqlite3, sys, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from series_memory import connect, _json

VERSION="3.6.0"

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def uid(prefix): return f"{prefix}_{uuid.uuid4().hex[:10]}"
def norm(s): return re.sub(r"[\W_]+","",str(s or "").casefold(),flags=re.UNICODE)
def read_json(path):
    with open(path,encoding="utf-8") as f:return json.load(f)
def write_json(path,obj):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); tmp=p.with_suffix(p.suffix+".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); tmp.replace(p)

def load_sources(path: str|Path)->dict[str,str]:
    raw=read_json(path)
    if isinstance(raw,dict) and "episodes" in raw: raw=raw["episodes"]
    if not isinstance(raw,dict): raise ValueError("sources.json 必须是 episode_id -> 视频路径的 JSON 对象")
    out={}
    for ep,src in raw.items():
        if isinstance(src,dict): src=src.get("video") or src.get("source_video")
        if not src: continue
        p=Path(str(src)).expanduser().resolve()
        if not p.is_file(): raise FileNotFoundError(f"{ep} 对应视频不存在: {p}")
        out[str(ep)]=str(p)
    return out

def get_episode_payload(c,series_id,episode_id):
    r=c.execute("SELECT payload_json FROM episode_payloads WHERE series_id=? AND episode_id=?",(series_id,episode_id)).fetchone()
    return json.loads(r[0]) if r else {}

def event_ids_for_selector(c,series_id,selector_type,selector_id,query=""):
    if selector_type=="character":
        row=c.execute("SELECT 1 FROM global_characters WHERE series_id=? AND global_character_id=?",(series_id,selector_id)).fetchone()
        if not row: raise ValueError(f"找不到全剧人物 ID: {selector_id}")
        rows=c.execute("SELECT episode_id,event_id FROM events WHERE series_id=? AND (actor_global_id=? OR patient_global_id=? OR recipient_global_id=? OR target_global_id=?) ORDER BY episode_number,playback_time",(series_id,selector_id,selector_id,selector_id,selector_id)).fetchall()
        return {(r[0],r[1]) for r in rows}
    if selector_type=="pair":
        ids=[x.strip() for x in selector_id.split(',') if x.strip()]
        if len(ids)!=2 or ids[0]==ids[1]:raise ValueError('--pair 需要两个不同的全剧人物 ID，用逗号分隔')
        for gid in ids:
            if not c.execute("SELECT 1 FROM global_characters WHERE series_id=? AND global_character_id=?",(series_id,gid)).fetchone():raise ValueError(f'找不到全剧人物 ID: {gid}')
        a,b=ids
        sql=("SELECT episode_id,event_id FROM events WHERE series_id=? AND ("
             "(actor_global_id=? AND (patient_global_id=? OR recipient_global_id=? OR target_global_id=?)) OR "
             "(actor_global_id=? AND (patient_global_id=? OR recipient_global_id=? OR target_global_id=?)) OR "
             "(patient_global_id=? AND recipient_global_id=?) OR (patient_global_id=? AND target_global_id=?) OR "
             "(recipient_global_id=? AND target_global_id=?)) ORDER BY episode_number,playback_time")
        rows=c.execute(sql,(series_id,a,b,b,b,b,a,a,a,a,b,a,b,a,b)).fetchall()
        out={(r[0],r[1]) for r in rows}
        # Include explicit relation-transition events even if the event actor
        # slot does not name both participants in the same event record.
        rels=c.execute("SELECT episode_id,valid_from_event,valid_to_event FROM relations WHERE series_id=? AND review_status='PASS' AND ((source_global_id=? AND target_global_id=?) OR (source_global_id=? AND target_global_id=?))",(series_id,a,b,b,a)).fetchall()
        for r in rels:
            if r[1]:out.add((r[0],r[1]))
            if r[2]:out.add((r[0],r[2]))
        return out
    if selector_type=="storyline":
        row=c.execute("SELECT 1 FROM global_storylines WHERE series_id=? AND global_storyline_id=?",(series_id,selector_id)).fetchone()
        if not row: raise ValueError(f"找不到全剧剧情线 ID: {selector_id}")
        out=set()
        for r in c.execute("SELECT episode_id,event_ids_json FROM storyline_mentions WHERE series_id=? AND global_storyline_id=? AND review_status='PASS'",(series_id,selector_id)):
            for eid in json.loads(r[1] or "[]"):out.add((r[0],eid))
        return out
    if selector_type=="thread":
        row=c.execute("SELECT 1 FROM global_threads WHERE series_id=? AND global_thread_id=?",(series_id,selector_id)).fetchone()
        if not row: raise ValueError(f"找不到全剧伏笔 ID: {selector_id}")
        out=set()
        for r in c.execute("SELECT episode_id,payload_json FROM thread_mentions WHERE series_id=? AND global_thread_id=? AND review_status='PASS'",(series_id,selector_id)):
            th=json.loads(r[1] or "{}")
            if th.get("first_event"):out.add((r[0],th["first_event"]))
            # A thread may cite evidence instead of a direct event. Link every event that cites it.
            evidence=set(th.get("evidence_ids",[]) or [])|set(th.get("resolution_evidence_ids",[]) or [])
            if evidence:
                for ev in c.execute("SELECT event_id,evidence_ids_json FROM events WHERE series_id=? AND episode_id=?",(series_id,r[0])):
                    if evidence.intersection(json.loads(ev[1] or "[]")):out.add((r[0],ev[0]))
        return out
    if selector_type=="query":
        q=f"%{query}%"
        rows=c.execute("SELECT episode_id,event_id FROM events WHERE series_id=? AND (action LIKE ? OR quote LIKE ? OR payload_json LIKE ?) ORDER BY episode_number,playback_time",(series_id,q,q,q)).fetchall()
        return {(r[0],r[1]) for r in rows}
    raise ValueError("selector_type 必须为 character/storyline/thread/query")

def build_event_clip(c,series_id,episode_id,event_id,source_video,before,after,reviewed_only):
    row=c.execute("SELECT * FROM events WHERE series_id=? AND episode_id=? AND event_id=?",(series_id,episode_id,event_id)).fetchone()
    if not row:return None
    ev=dict(row); payload=json.loads(ev["payload_json"])
    if ev["modality"] in {"imagined","hypothetical"} or ev["review_status"]=="INCORRECT":return None
    if reviewed_only and ev["review_status"]!="PASS":return None
    episode_payload=get_episode_payload(c,series_id,episode_id)
    matching=[]
    for h in episode_payload.get("highlights",[]) or []:
        if event_id in (h.get("event_ids") or []) and h.get("review_status")!="INCORRECT":
            if reviewed_only and h.get("review_status")!="PASS": continue
            matching.append(h)
    # Prefer a reviewed highlight interval. If absent, derive a conservative window
    # from evidence timestamps, then from the event timestamp.
    chosen=max(matching,key=lambda h:float(h.get("total_score",0)),default=None)
    evidence_by_id={e.get("evidence_id"):e for e in episode_payload.get("evidence",[]) or []}
    evidence=[evidence_by_id[x] for x in json.loads(ev["evidence_ids_json"] or "[]") if x in evidence_by_id]
    if reviewed_only:evidence=[e for e in evidence if e.get("review_status")=="PASS"]
    evidence=[e for e in evidence if isinstance(e.get("start_sec"),(int,float)) and isinstance(e.get("end_sec"),(int,float)) and e["end_sec"]>e["start_sec"]]
    if chosen:
        start,end=float(chosen["start_sec"]),float(chosen["end_sec"])
        score=float(chosen.get("total_score",0)); title=str(chosen.get("title") or ev["action"])
        highlight_id=chosen.get("highlight_id")
        source_kind="reviewed_highlight" if chosen.get("review_status")=="PASS" else "unreviewed_highlight"
    elif evidence:
        start=min(float(x["start_sec"]) for x in evidence)-before
        end=max(float(x["end_sec"]) for x in evidence)+after
        score=0; title=ev["action"] or event_id; highlight_id=None; source_kind="evidence_window"
    else:
        t=float(ev["playback_time"]); start=t-before; end=t+after
        score=0; title=ev["action"] or event_id; highlight_id=None; source_kind="event_window"
    duration=float(c.execute("SELECT duration_sec FROM episodes WHERE series_id=? AND episode_id=?",(series_id,episode_id)).fetchone()[0] or 0)
    start=max(0.0,start); end=min(duration,end) if duration>0 else end
    if end<=start:return None
    scene=c.execute("SELECT payload_json FROM episode_payloads WHERE series_id=? AND episode_id=?",(series_id,episode_id)).fetchone()
    scene_id=payload.get("scene_id")
    if scene_id and scene:
        sc=next((x for x in json.loads(scene[0]).get("scenes",[]) if x.get("scene_id")==scene_id),None)
        if sc:
            if isinstance(sc.get("playback_start"),(int,float)):start=max(start,float(sc["playback_start"]))
            if isinstance(sc.get("playback_end"),(int,float)):end=min(end,float(sc["playback_end"]))
    if end<=start:return None
    arc_refs=[]; arc_roles=[]
    for arc in episode_payload.get("story_arcs",[]) or []:
        if event_id not in (arc.get("event_ids") or []):continue
        roles=sorted({seg.get("role") for seg in (arc.get("segments") or []) if event_id in (seg.get("event_ids") or []) and seg.get("role")})
        arc_refs.append({"arc_id":arc.get("arc_id"),"roles":roles,"semantic_status":arc.get("semantic_status"),"review_status":arc.get("review_status","UNREVIEWED")})
        arc_roles.extend(roles)
    return {
        "clip_id":uid("CLP"),"episode_id":episode_id,"source_video":source_video,"source_basename":Path(source_video).name,
        "start_sec":round(start,3),"end_sec":round(end,3),"title":title,
        "reason":f"跨集故事候选：{ev['action']}；来源={source_kind}；事件审核={ev['review_status']}",
        "event_id":event_id,"highlight_id":highlight_id,"event_ids":[event_id],
        "storyline_ids":[],"source_arc_ids":[x["arc_id"] for x in arc_refs if x.get("arc_id")],"arc_roles":list(dict.fromkeys(arc_roles)),"arc_refs":arc_refs,"score":round(score,2),
        "event_review_status":ev["review_status"],"context_required":payload.get("context_required","medium"),
        "evidence_ids":json.loads(ev["evidence_ids_json"] or "[]"),"enabled":True,
        "source_kind":source_kind,"modality":ev["modality"],"certainty":ev["certainty"]
    }

def attach_global_refs(c,series_id,selector_type,selector_id,clips):
    if selector_type=="pair":
        ids=[x.strip() for x in selector_id.split(',') if x.strip()]
        names=[]
        for gid in ids:
            r=c.execute("SELECT canonical_name FROM global_characters WHERE series_id=? AND global_character_id=?",(series_id,gid)).fetchone(); names.append(r[0] if r else gid)
        for cl in clips:cl["character_names"]=names
        return
    if selector_type=="storyline":
        for cl in clips:
            row=c.execute("SELECT local_storyline_id FROM storyline_mentions WHERE series_id=? AND episode_id=? AND global_storyline_id=?",(series_id,cl["episode_id"],selector_id)).fetchone()
            if row:cl["storyline_ids"]=[row[0]]
    elif selector_type=="character":
        row=c.execute("SELECT canonical_name FROM global_characters WHERE series_id=? AND global_character_id=?",(series_id,selector_id)).fetchone()
        for cl in clips:cl["character_name"]=row[0] if row else ""
    elif selector_type=="thread":
        row=c.execute("SELECT description FROM global_threads WHERE series_id=? AND global_thread_id=?",(series_id,selector_id)).fetchone()
        for cl in clips:cl["thread_description"]=row[0] if row else ""

def merge_same_source(clips,gap=0.5):
    # Merge only overlapping windows from the same episode/source; never merge
    # across episodes, because that would erase provenance and edit boundaries.
    out=[]
    for cl in sorted(clips,key=lambda x:(x["episode_id"],x["start_sec"])):
        if out and out[-1]["episode_id"]==cl["episode_id"] and out[-1]["source_video"]==cl["source_video"] and cl["start_sec"]<=out[-1]["end_sec"]+gap:
            prev=out[-1]; prev["end_sec"]=max(prev["end_sec"],cl["end_sec"])
            prev["event_ids"]=list(dict.fromkeys(prev["event_ids"]+cl["event_ids"]))
            prev["source_arc_ids"]=list(dict.fromkeys(prev.get("source_arc_ids",[])+cl.get("source_arc_ids",[])))
            prev["arc_roles"]=list(dict.fromkeys(prev.get("arc_roles",[])+cl.get("arc_roles",[])))
            prev["arc_refs"]=(prev.get("arc_refs",[])+cl.get("arc_refs",[]))
            prev["evidence_ids"]=list(dict.fromkeys(prev["evidence_ids"]+cl["evidence_ids"]))
            prev["title"] += " / "+cl["title"] if cl["title"] not in prev["title"] else ""
            prev["reason"] += "；"+cl["reason"]
            prev["score"]=max(prev["score"],cl["score"])
        else:out.append(cl.copy())
    return out

def compile_project(db_path,series_id,selector_type,selector_id,sources_path,output,preset="5m",target_sec=None,before=4.0,after=7.0,reviewed_only=True,all_events=False):
    sources=load_sources(sources_path); c=connect(db_path)
    try:
        selected=event_ids_for_selector(c,series_id,selector_type,selector_id,selector_id if selector_type=="query" else "")
        if not selected:raise ValueError("没有找到匹配事件；请检查 selector ID 或先导入分集档案")
        clips=[]; missing=[]; excluded=[]
        # Episode ordering is canonical; playback time is preserved within each episode.
        event_rows=[]
        for ep,eid in selected:
            row=c.execute("SELECT episode_number,playback_time FROM events WHERE series_id=? AND episode_id=? AND event_id=?",(series_id,ep,eid)).fetchone()
            if row:event_rows.append((row[0],row[1],ep,eid))
        for _,__,ep,eid in sorted(event_rows):
            source=sources.get(ep)
            if not source:missing.append({"episode_id":ep,"event_id":eid,"reason":"sources.json 缺少该集视频路径"}); continue
            clip=build_event_clip(c,series_id,ep,eid,source,before,after,reviewed_only)
            if clip:clips.append(clip)
            else:excluded.append({"episode_id":ep,"event_id":eid,"reason":"事件未通过审核、属于假设/想象，或没有有效时间窗"})
        if not clips:raise ValueError("没有可剪辑片段。默认只纳入 PASS 事件；如需候选素材请使用 --include-unreviewed。")
        clips=merge_same_source(clips)
        candidate_count=len(clips)
        target=float(target_sec if target_sec is not None else {"30s":30,"60s":60,"3m":180,"5m":300}.get(preset,300))
        total_before=sum(x['end_sec']-x['start_sec'] for x in clips)
        if not all_events and total_before>target:
            ranked=[]
            for cl in clips:
                ep_payload=get_episode_payload(c,series_id,cl['episode_id'])
                event_set=set(cl['event_ids'])
                evs=[x for x in ep_payload.get('events',[]) if x.get('event_id') in event_set]
                weights={'importance':.30,'emotion':.14,'conflict':.12,'twist':.12,'suspense':.10,'visual':.08,'standalone':.14}
                # For merged adjacent event windows, use the strongest reviewed
                # dimension per axis rather than accidentally scoring only the first event.
                dims={k:max([float((ev.get('highlight_dimensions') or {}).get(k,50) or 0) for ev in evs] or [50]) for k in weights}
                score=sum(max(0,min(100,dims[k]))*w for k,w in weights.items())
                for arc in ep_payload.get('story_arcs',[]) or []:
                    overlap=event_set.intersection(arc.get('event_ids',[]) or [])
                    if overlap:
                        confirmed=arc.get('semantic_status')=='HUMAN_CONFIRMED' or arc.get('review_status')=='PASS'
                        score+=8 if confirmed else 2
                        roles={seg.get('role') for seg in arc.get('segments',[]) if overlap.intersection(seg.get('event_ids',[]) or [])}
                        score+=max([{'peak':8,'payoff':10,'setup':5,'reaction':4}.get(r,0) for r in roles] or [0]) if confirmed else 0
                if any(ev.get('review_status')=='PASS' for ev in evs):score+=5
                duration=cl['end_sec']-cl['start_sec']
                ranked.append((score/max(duration,.5),score,cl))
            picked=[]; used=0.0
            for density,score,cl in sorted(ranked,key=lambda x:(x[0],x[1]),reverse=True):
                d=cl['end_sec']-cl['start_sec']
                if used+d<=target:picked.append(cl);used+=d
            if not picked:raise ValueError(f'目标时长 {target:.1f}s 小于所有完整片段；请增大目标时长或使用 --all-events')
            def ep_order(x):
                m=re.search(r'(\d+)$',x['episode_id']);return int(m.group(1)) if m else 0
            clips=sorted(picked,key=lambda x:(ep_order(x),x['start_sec']))
        attach_global_refs(c,series_id,selector_type,selector_id,clips)
        s=c.execute("SELECT title FROM series WHERE series_id=?",(series_id,)).fetchone()
        ep_map={r["episode_id"]:dict(r) for r in c.execute("SELECT episode_id,title,episode_number FROM episodes WHERE series_id=?",(series_id,))}
        for i,cl in enumerate(clips,1):
            cl["clip_id"]=f"CLP_{i:03d}"; cl["episode_title"]=ep_map.get(cl["episode_id"],{}).get("title","")
        total=sum(x["end_sec"]-x["start_sec"] for x in clips)
        project={
            "project_version":"2.0","compiler_version":VERSION,"project_type":"cross_episode_compilation",
            "series_id":series_id,"series_title":s[0] if s else "","selector":{"type":selector_type,"id":selector_id},
            "title":f"{s[0] if s else series_id} · {selector_type} · {selector_id}","preset":preset,
            "target_duration_sec":target,
            "estimated_duration_sec":round(total,3),"sequence_strategy":"chronological",
            "created_at":now(),"sources":sources,"clips":clips,
            "compile_report":{"matched_events":len(selected),"candidate_clips":candidate_count,"clips_created":len(clips),"duration_before_selection_sec":round(total_before,3),"duration_after_selection_sec":round(total,3),"target_respected":total<=target,"all_events":all_events,"missing_sources":missing,"excluded_events":excluded,
                              "reviewed_only":reviewed_only,"warnings":["跨集剪辑按集数及原片时间排序。","默认只纳入 review_status=PASS 的事件。","目标时长筛选可能省略连接性事件；请检查 setup/peak/reaction/payoff 是否完整。","请人工确认剪辑上下文、对白连续性和版权/发布权限。"]}
        }
        write_json(output,project)
        return project
    finally:c.close()

def main():
    p=argparse.ArgumentParser(description="跨集人物/剧情线/伏笔智能剪辑项目生成器")
    p.add_argument("db");p.add_argument("sources");p.add_argument("output")
    g=p.add_mutually_exclusive_group(required=True);g.add_argument("--character");g.add_argument("--pair",help="两个全剧人物 ID，逗号分隔");g.add_argument("--storyline");g.add_argument("--thread");g.add_argument("--query")
    p.add_argument("--series-id",required=True);p.add_argument("--preset",choices=["30s","60s","3m","5m"],default="5m");p.add_argument("--target-sec",type=float)
    p.add_argument("--before",type=float,default=4);p.add_argument("--after",type=float,default=7);p.add_argument("--include-unreviewed",action="store_true");p.add_argument("--all-events",action="store_true",help="不按目标时长筛选，保留全部匹配的合格事件")
    a=p.parse_args(); st="character" if a.character else "pair" if a.pair else "storyline" if a.storyline else "thread" if a.thread else "query"; sid=a.character or a.pair or a.storyline or a.thread or a.query
    if a.before<0 or a.after<0 or (a.target_sec is not None and a.target_sec<=0):raise SystemExit("时间参数无效")
    try:
        proj=compile_project(a.db,a.series_id,st,sid,a.sources,a.output,a.preset,a.target_sec,a.before,a.after,not a.include_unreviewed,a.all_events)
        print(json.dumps({"output":a.output,"clips":len(proj['clips']),"duration_sec":proj['estimated_duration_sec'],"compile_report":proj['compile_report']},ensure_ascii=False,indent=2))
    except Exception as e:print(f"错误: {e}",file=sys.stderr);raise SystemExit(2)
if __name__=="__main__":main()
