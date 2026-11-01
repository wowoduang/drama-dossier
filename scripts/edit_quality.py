#!/usr/bin/env python3
"""Evidence-aware cut-boundary suggestions and continuity QC for edit projects.

This tool proposes safe trims from subtitle/shot/audio cues. It never claims
semantic understanding and never changes the source dossier or silently edits
clips. Suggestions require human acceptance.
"""
from __future__ import annotations
import argparse, json, math, re, sys
from pathlib import Path
from typing import Any

def read_json(p):
    with open(p, encoding="utf-8") as f: return json.load(f)

def write_json(p, obj):
    p=Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp=p.with_suffix(p.suffix+".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    tmp.replace(p)

def _cue_time(c, *keys):
    for k in keys:
        v=c.get(k)
        if isinstance(v,(int,float)): return float(v)
    return None

def _near(value, points, radius):
    return min((p for p in points if abs(p-value)<=radius), key=lambda p:abs(p-value), default=None)

def suggest_boundaries(project:dict[str,Any], cue_payloads:dict[str,dict[str,Any]], pre_roll=1.2, post_roll=1.8, snap_window=1.0):
    """Add cut suggestions per clip. cue_payloads keyed by episode_id."""
    out=json.loads(json.dumps(project))
    suggestions=[]
    for clip in out.get("clips",[]):
        ep=str(clip.get("episode_id",""))
        cues=cue_payloads.get(ep,{})
        start=float(clip["start_sec"]); end=float(clip["end_sec"])
        shot=[]; subtitle_starts=[]; subtitle_ends=[]; silence=[]
        for cue in cues.get("cues", cues.get("media_cues", [])):
            typ=str(cue.get("type",cue.get("cue_type",""))).lower()
            t=_cue_time(cue,"time_sec","start_sec","playback_time")
            e=_cue_time(cue,"end_sec")
            if t is None: continue
            if "shot" in typ or "scene" in typ or "cut" in typ: shot.append(t)
            if "subtitle" in typ or "transcript" in typ or "asr" in typ:
                subtitle_starts.append(t)
                if e is not None: subtitle_ends.append(e)
            if "silence" in typ or "low_energy" in typ: silence.append(t)
        event_t=float(clip.get("event_time_sec", clip.get("anchor_time_sec", (start+end)/2)))
        desired_in=max(0.0, min(start, event_t-pre_roll))
        desired_out=max(end, event_t+post_roll)
        # Snap in-point backward to nearby shot boundary; avoid snapping into dialogue.
        in_shot=_near(desired_in,shot,snap_window)
        out_shot=_near(desired_out,shot,snap_window)
        in_sub=_near(desired_in,subtitle_starts,snap_window)
        out_sub=_near(desired_out,subtitle_ends,snap_window)
        proposed_in=in_shot if in_shot is not None and in_shot<=desired_in else desired_in
        proposed_out=out_shot if out_shot is not None and out_shot>=desired_out else desired_out
        if in_sub is not None and in_sub<proposed_in: proposed_in=in_sub
        if out_sub is not None and out_sub>proposed_out: proposed_out=out_sub
        # Never expand outside the original clip unless caller explicitly opts in later.
        proposed_in=max(start,proposed_in); proposed_out=min(end,proposed_out)
        flags=[]
        if not shot: flags.append("NO_SHOT_BOUNDARY_CUES")
        if not subtitle_starts: flags.append("NO_SUBTITLE_CUES")
        if not any(proposed_in <= x <= event_t for x in subtitle_starts): flags.append("CHECK_DIALOGUE_LEAD_IN")
        if not any(event_t <= x <= proposed_out for x in subtitle_ends): flags.append("CHECK_DIALOGUE_TAIL")
        if proposed_out-proposed_in < 0.8: flags.append("VERY_SHORT_CLIP")
        suggestion={
            "clip_id":clip.get("clip_id"),"episode_id":ep,
            "original_start_sec":start,"original_end_sec":end,
            "suggested_start_sec":round(proposed_in,3),"suggested_end_sec":round(proposed_out,3),
            "confidence":"low" if flags else "medium",
            "signals":{"shot_boundaries":len(shot),"subtitle_segments":len(subtitle_starts),"silence_cues":len(silence)},
            "risk_flags":flags,"review_status":"UNREVIEWED"
        }
        clip["boundary_suggestion"]=suggestion
        suggestions.append(suggestion)
    out["boundary_suggestions"]=suggestions
    out["boundary_engine_version"]="3.9.0"
    return out

def continuity_qc(project:dict[str,Any], source_durations:dict[str,float]|None=None):
    issues=[]
    clips=[c for c in project.get("clips",[]) if c.get("enabled",True)]
    by_source={}
    for i,c in enumerate(clips):
        src=str(c.get("source_video",""))
        start=c.get("start_sec"); end=c.get("end_sec")
        if not isinstance(start,(int,float)) or not isinstance(end,(int,float)) or start<0 or end<=start:
            issues.append({"severity":"ERROR","code":"INVALID_CLIP_RANGE","clip_id":c.get("clip_id"),"message":"片段入点/出点无效"})
            continue
        if source_durations and src in source_durations and end>source_durations[src]+0.05:
            issues.append({"severity":"ERROR","code":"SOURCE_DURATION_EXCEEDED","clip_id":c.get("clip_id"),"message":f"出点 {end:.3f}s 超过源视频时长 {source_durations[src]:.3f}s"})
        if end-start<0.5:
            issues.append({"severity":"WARN","code":"VERY_SHORT_CLIP","clip_id":c.get("clip_id"),"message":"片段短于 0.5 秒，检查是否误切"})
        by_source.setdefault(src,[]).append((start,end,c))
        if c.get("boundary_suggestion") and c["boundary_suggestion"].get("review_status")!="PASS":
            issues.append({"severity":"WARN","code":"BOUNDARY_NOT_REVIEWED","clip_id":c.get("clip_id"),"message":"智能边界建议尚未人工确认"})
        if c.get("evidence_ids") is None and not c.get("event_id") and not c.get("highlight_id"):
            issues.append({"severity":"WARN","code":"NO_TRACEABILITY","clip_id":c.get("clip_id"),"message":"片段缺少事件/高光/证据来源"})
    for src,items in by_source.items():
        items.sort(key=lambda x:x[0])
        for a,b in zip(items,items[1:]):
            if b[0]<a[1]-0.05:
                issues.append({"severity":"WARN","code":"SOURCE_OVERLAP","clip_id":b[2].get("clip_id"),"message":f"与同源片段 {a[2].get('clip_id')} 时间重叠"})
    for a,b in zip(clips,clips[1:]):
        if a.get("episode_id")!=b.get("episode_id") and a.get("transition_type") not in {"HARD_CUT","DISSOLVE","BRIDGE"}:
            issues.append({"severity":"INFO","code":"CROSS_EPISODE_TRANSITION","clip_id":b.get("clip_id"),"message":"跨集切换；检查时间/地点/人物连续性与转场意图"})
    return {"qc_version":"3.9.0","issue_count":len(issues),"release_blockers":sum(x["severity"]=="ERROR" for x in issues),"issues":issues,"release_ready":not any(x["severity"]=="ERROR" for x in issues)}

def main():
    p=argparse.ArgumentParser(description="剧情剪辑边界建议与连续性 QC")
    sub=p.add_subparsers(dest="cmd",required=True)
    a=sub.add_parser("boundaries"); a.add_argument("project"); a.add_argument("cue_bundle"); a.add_argument("output"); a.add_argument("--pre-roll",type=float,default=1.2); a.add_argument("--post-roll",type=float,default=1.8); a.add_argument("--snap-window",type=float,default=1.0)
    b=sub.add_parser("qc"); b.add_argument("project"); b.add_argument("output")
    x=p.parse_args()
    if x.cmd=="boundaries":
        project=read_json(x.project); cues=read_json(x.cue_bundle)
        result=suggest_boundaries(project,cues,x.pre_roll,x.post_roll,x.snap_window)
    else: result=continuity_qc(read_json(x.project))
    write_json(x.output,result); print(json.dumps({"output":x.output,"issue_count":result.get("issue_count",len(result.get("boundary_suggestions",[]))),"release_ready":result.get("release_ready")},ensure_ascii=False))
if __name__=="__main__": main()
