#!/usr/bin/env python3
"""Evidence-linked shot/reverse-shot, reaction and action continuity candidates.

Requires shot observations produced by ChatGPT or a human after viewing frames
and short continuous source excerpts. Time adjacency alone is never proof.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
from typing import Any

VERSION="4.1.0"
def read_json(p):
    with open(p,encoding="utf-8") as f:return json.load(f)
def write_json(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+".tmp")
    t.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");t.replace(p)
def norm(s):
    return re.sub(r"[\W_]+","",str(s or "").casefold(),flags=re.UNICODE)
def overlap(a0,a1,b0,b1):
    return max(0.0,min(a1,b1)-max(a0,b0))
def collect_cues(multimodal):
    cues=multimodal.get("cues",multimodal.get("media_cues",[]))
    shots=[]
    for i,c in enumerate(cues):
        typ=str(c.get("type",c.get("cue_type",""))).lower()
        if "shot" not in typ and "scene" not in typ and "cut" not in typ:continue
        t=c.get("time_sec",c.get("start_sec"))
        if isinstance(t,(int,float)):shots.append(float(t))
    return sorted(set(shots))
def candidates(dossier,multimodal,observations,max_gap=4.0):
    shots=observations.get("shots",[])
    if not isinstance(shots,list):raise ValueError("shot-observations.shots 必须是数组")
    normalized=[]
    for i,s in enumerate(shots):
        start=s.get("start_sec");end=s.get("end_sec")
        if not isinstance(start,(int,float)) or not isinstance(end,(int,float)) or start<0 or end<=start:
            raise ValueError(f"镜头观察 {i} 时间范围无效")
        normalized.append({**s,"shot_id":s.get("shot_id") or f"SHOT_{i+1:04d}","start_sec":float(start),"end_sec":float(end)})
    normalized.sort(key=lambda x:(x["start_sec"],x["end_sec"]))
    events={e.get("event_id"):e for e in dossier.get("events",[])}
    evidence={e.get("evidence_id"):e for e in dossier.get("evidence",[])}
    cuts=collect_cues(multimodal)
    out=[]
    def emit(kind,items,reason,flags,refs):
        out.append({"candidate_id":f"SC_{len(out)+1:04d}","candidate_type":kind,
                    "shot_ids":[x["shot_id"] for x in items],
                    "start_sec":min(x["start_sec"] for x in items),"end_sec":max(x["end_sec"] for x in items),
                    "character_ids":list(dict.fromkeys(cid for x in items for cid in (x.get("visible_character_ids") or []) if cid)),
                    "event_ids":list(dict.fromkeys(eid for x in items for eid in (x.get("event_ids") or []) if eid in events)),
                    "evidence_ids":list(dict.fromkeys(ref for x in items for ref in (x.get("evidence_ids") or []) if ref in evidence)),
                    "reason":reason,"risk_flags":flags,"review_status":"UNREVIEWED"})
    for a,b in zip(normalized,normalized[1:]):
        gap=b["start_sec"]-a["end_sec"]
        if gap>max_gap:continue
        chars_a=set(a.get("visible_character_ids") or [])
        chars_b=set(b.get("visible_character_ids") or [])
        speaker_a=a.get("speaker_character_id");speaker_b=b.get("speaker_character_id")
        gaze_a=a.get("gaze_direction");gaze_b=b.get("gaze_direction")
        pos_a=a.get("screen_position");pos_b=b.get("screen_position")
        flags=[]
        shared_events=set(a.get("event_ids") or []) & set(b.get("event_ids") or [])
        dialogue_related=bool((speaker_a and speaker_a in chars_a) or (speaker_b and speaker_b in chars_b) or shared_events)
        opposite_gaze=(gaze_a in {"left","screen_left","off_right"} and gaze_b in {"right","screen_right","off_left"}) or (gaze_b in {"left","screen_left","off_right"} and gaze_a in {"right","screen_right","off_left"})
        if dialogue_related and chars_a and chars_b and chars_a.isdisjoint(chars_b) and opposite_gaze:
            if a.get("review_status")=="PASS" and b.get("review_status")=="PASS":
                emit("SHOT_REVERSE_SHOT",[a,b],"相邻镜头存在不同可见人物、对话关联和相反视线方向",flags,[])
            else:
                emit("SHOT_REVERSE_SHOT",[a,b],"存在正反打线索，但镜头观察未全部审核",["SHOT_OBSERVATIONS_NOT_REVIEWED"],[])
        # Reaction: second shot is an observed non-speaking listener/reaction to first shot's speaker.
        if speaker_a and speaker_a in chars_a and speaker_a not in chars_b and chars_b and b.get("action_phase") in {"reaction","listening","response"}:
            if b.get("reaction_target_character_id") in {None,speaker_a}:
                flags=[]
                if b.get("review_status")!="PASS":flags.append("REACTION_OBSERVATION_NOT_REVIEWED")
                if not b.get("reaction_target_character_id"):flags.append("REACTION_TARGET_UNCONFIRMED")
                emit("REACTION_SHOT",[a,b],"后一镜头观察到听者/反应阶段，可能回应前一镜头说话者",flags,[])
        phase_a=a.get("action_phase");phase_b=b.get("action_phase")
        same_actor=bool(a.get("actor_character_id") and a.get("actor_character_id")==b.get("actor_character_id"))
        action_sim=bool(norm(a.get("action_description")) and norm(a.get("action_description"))==norm(b.get("action_description")))
        valid_phases={"start","middle","end","continuation","result"}
        if same_actor and action_sim and phase_a in valid_phases and phase_b in valid_phases and phase_a!=phase_b:
            flags=[]
            if a.get("continuity_status")!="continuous" or b.get("continuity_status")!="continuous":flags.append("ACTION_CONTINUITY_NOT_CONFIRMED")
            if abs(gap)>1.0:flags.append("TEMPORAL_GAP_REVIEW")
            if pos_a and pos_b and pos_a!=pos_b:flags.append("SCREEN_POSITION_CHANGED")
            emit("ACTION_CONTINUITY",[a,b],"同一人物和动作描述存在不同动作阶段，可能形成连续动作剪辑",flags,[])
        if speaker_a and speaker_b and speaker_a!=speaker_b and dialogue_related:
            flags=[]
            if not a.get("dialogue_evidence_ids") or not b.get("dialogue_evidence_ids"):flags.append("DIALOGUE_EVIDENCE_MISSING")
            emit("DIALOGUE_TURN",[a,b],"相邻镜头可能构成不同说话人的对话轮次",flags,[])
    # Add shot boundary context as technical evidence, not as a semantic assertion.
    for item in out:
        item["nearby_shot_cut_times"]=[t for t in cuts if item["start_sec"]-0.5<=t<=item["end_sec"]+0.5]
        item["limitations"]=["镜头相邻不证明正反打","人物 ID/视线/动作阶段必须来自已审核观察","需回看连续原片确认轴线、视线、动作起止与反应对象"]
    return {"version":VERSION,"episode_id":dossier.get("episode",{}).get("episode_id"),"candidate_count":len(out),"candidates":out,
            "source_observation_count":len(normalized),"all_candidates_unreviewed":True}
def qc(payload):
    issues=[]
    seen=set()
    for c in payload.get("candidates",[]):
        cid=c.get("candidate_id")
        if cid in seen:issues.append({"severity":"ERROR","code":"DUPLICATE_CANDIDATE_ID","candidate_id":cid})
        seen.add(cid)
        if c.get("review_status")!="UNREVIEWED":issues.append({"severity":"WARN","code":"CANDIDATE_PREMATURELY_REVIEWED","candidate_id":cid})
        if not c.get("shot_ids") or len(c["shot_ids"])<2:issues.append({"severity":"ERROR","code":"INSUFFICIENT_SHOTS","candidate_id":cid})
        if c.get("candidate_type")=="REACTION_SHOT" and not c.get("character_ids"):issues.append({"severity":"WARN","code":"REACTION_WITHOUT_CHARACTER","candidate_id":cid})
        if not c.get("evidence_ids") and not c.get("event_ids"):issues.append({"severity":"WARN","code":"NO_EVENT_OR_EVIDENCE_LINK","candidate_id":cid})
    return {"candidate_count":len(payload.get("candidates",[])),"issues":issues,"release_ready":not any(x["severity"]=="ERROR" for x in issues)}
def main():
    p=argparse.ArgumentParser(description="正反打、反应镜头、对话轮次和动作连续候选")
    sub=p.add_subparsers(dest="cmd",required=True)
    a=sub.add_parser("candidates");a.add_argument("dossier");a.add_argument("multimodal");a.add_argument("observations");a.add_argument("output");a.add_argument("--max-gap",type=float,default=4.0)
    b=sub.add_parser("qc");b.add_argument("candidates");b.add_argument("output")
    x=p.parse_args()
    try:
        if x.cmd=="candidates":result=candidates(read_json(x.dossier),read_json(x.multimodal),read_json(x.observations),x.max_gap)
        else:result=qc(read_json(x.candidates))
        write_json(x.output,result);print(json.dumps({"output":x.output,"candidate_count":result.get("candidate_count"),"issues":len(result.get("issues",[]))},ensure_ascii=False))
    except Exception as e:print(f"错误: {e}",file=sys.stderr);raise SystemExit(2)
if __name__=="__main__":main()
