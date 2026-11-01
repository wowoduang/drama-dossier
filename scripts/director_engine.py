#!/usr/bin/env python3
"""Explainable AI-director orchestration layer.

Consumes an existing edit project plus human/AI-reviewed shot observations.
It suggests pacing and transitions; it does not infer facial emotion from
pixels or silently rewrite clip durations.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any

VERSION="4.2.0"
from continuity_review import approved_candidates
ROLE_PROFILES={
    "setup":{"target_sec":4.5,"min_sec":2.5,"max_sec":8.0,"transition":"CUT","pace":"measured","reason":"给观众时间建立人物、地点或风险"},
    "inciting_incident":{"target_sec":3.5,"min_sec":2.0,"max_sec":6.0,"transition":"CUT","pace":"building","reason":"快速明确引发故事变化的事件"},
    "escalation":{"target_sec":2.8,"min_sec":1.5,"max_sec":5.0,"transition":"CUT","pace":"building","reason":"逐步压缩停留时间，增强推进感"},
    "peak":{"target_sec":3.2,"min_sec":1.5,"max_sec":7.0,"transition":"CUT","pace":"impact","reason":"保留动作/台词核心及必要反应，不因追求快节奏切掉结果"},
    "reaction":{"target_sec":2.8,"min_sec":1.5,"max_sec":5.5,"transition":"CUT","pace":"breathing","reason":"保留人物反应，让观众理解事件影响"},
    "payoff":{"target_sec":4.0,"min_sec":2.0,"max_sec":7.0,"transition":"CUT","pace":"reveal","reason":"给揭晓或回收信息留出理解时间"},
    "resolution":{"target_sec":5.0,"min_sec":3.0,"max_sec":9.0,"transition":"DISSOLVE","pace":"release","reason":"给结果与情绪余韵留出空间"},
    "bridge":{"target_sec":1.8,"min_sec":0.8,"max_sec":3.5,"transition":"CUT","pace":"transitional","reason":"只保留必要的时空/动作衔接信息"}
}
def read_json(p):
    with open(p,encoding="utf-8") as f:return json.load(f)
def write_json(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+".tmp")
    t.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");t.replace(p)
def plan_director(project:dict[str,Any], observations:dict[str,Any]|None=None, profile="balanced", continuity=None):
    out=json.loads(json.dumps(project))
    obs=observations or {}
    by_clip={x.get("clip_id"):x for x in obs.get("clip_observations",[]) if x.get("clip_id")}
    suggestions=[]
    for i,clip in enumerate(out.get("clips",[])):
        role=str(clip.get("narrative_role",clip.get("story_role","bridge"))).lower()
        role=role.replace(" ","_").replace("-","_")
        rp=ROLE_PROFILES.get(role,ROLE_PROFILES["bridge"])
        duration=float(clip["end_sec"])-float(clip["start_sec"])
        annotation=by_clip.get(clip.get("clip_id"),{})
        flags=[]
        if annotation.get("performance_observation_status") not in {"PASS","REVIEWED"}:
            flags.append("PERFORMANCE_OBSERVATION_NOT_REVIEWED")
        if annotation.get("action_continuity") not in {"continuous","confirmed"}:
            flags.append("ACTION_CONTINUITY_NEEDS_REVIEW")
        if annotation.get("dialogue_complete") is False:
            flags.append("DIALOGUE_MAY_BE_TRUNCATED")
        if annotation.get("reaction_complete") is False:
            flags.append("REACTION_MAY_BE_MISSING")
        target=rp["target_sec"]
        if profile=="fast":target*=0.78
        elif profile=="slow":target*=1.25
        target=max(rp["min_sec"],min(rp["max_sec"],target))
        suggestions.append({
            "clip_id":clip.get("clip_id"),"episode_id":clip.get("episode_id"),
            "narrative_role":role,"current_duration_sec":round(duration,3),
            "target_duration_sec":round(target,3),"min_duration_sec":rp["min_sec"],
            "max_duration_sec":rp["max_sec"],"pacing":rp["pace"],
            "transition_suggestion":rp["transition"],"editorial_reason":rp["reason"],
            "shot_observation":annotation.get("visual_observation"),
            "performance_observation_status":annotation.get("performance_observation_status","UNREVIEWED"),
            "risk_flags":flags,"review_status":"UNREVIEWED"
        })
    out["director_plan"]={"version":VERSION,"profile":profile,"suggestions":suggestions,"review_status":"UNREVIEWED",
                          "limitations":["不从像素自动推断微表情、情绪或动机","目标时长不是自动裁切指令","转场建议需人工检查时空连续性"]}
    out["director_plan"]["shot_alternatives"] = shot_alternatives(project, continuity) if continuity else []
    out["director_engine_version"]=VERSION
    return out
def shot_alternatives(project, continuity):
    """Only within an existing event/episode/source window; no invented B-roll."""
    if 'episodes' in continuity:
        payloads = continuity['episodes']
        if not isinstance(payloads, list) or any(not isinstance(p, dict) or not p.get('episode_id') for p in payloads):
            raise ValueError('continuity.episodes 必须为带 episode_id 的审核结果数组')
        ids = [p['episode_id'] for p in payloads]
        if len(set(ids)) != len(ids):
            raise ValueError('连续性 bundle 中 episode_id 重复')
        return [a for payload in payloads for a in shot_alternatives(project, payload)]
    if not continuity.get('episode_id'):
        raise ValueError('连续性数据缺少 episode_id')
    approved = approved_candidates(continuity)
    shots = {s['shot_id']: s for s in continuity.get('shots', [])}
    alternatives = []
    for clip in project.get('clips', []):
        if clip.get('enabled', True) is False or clip.get('episode_id') != continuity.get('episode_id'):
            continue
        events = set(clip.get('event_ids') or ([clip['event_id']] if clip.get('event_id') else []))
        evidence = set(clip.get('evidence_ids') or [])
        for c in approved:
            if not set(c['event_ids']).issubset(events) or not set(c['evidence_ids']).issubset(evidence):
                continue
            if c['candidate_type'] not in {'REACTION_SHOT','SHOT_REVERSE_SHOT','ACTION_CONTINUITY','DIALOGUE_TURN'}:
                continue
            # Reaction replacement is limited to a reaction slot; other pairs stay intact.
            if c['candidate_type'] == 'REACTION_SHOT':
                if clip.get('narrative_role', clip.get('story_role')) != 'reaction':
                    continue
                selected = [shots[c['shot_ids'][-1]]]
            else:
                selected = [shots[i] for i in c['shot_ids']]
            start, end = min(s['start_sec'] for s in selected), max(s['end_sec'] for s in selected)
            if not clip.get('source_video') or start < clip['start_sec'] or end > clip['end_sec']:
                continue
            if start == clip['start_sec'] and end == clip['end_sec']:
                continue
            alternatives.append({'alternative_id': f"{clip['clip_id']}:{c['candidate_id']}",
                'clip_id': clip['clip_id'], 'episode_id': clip['episode_id'],
                'candidate_id': c['candidate_id'], 'candidate_type': c['candidate_type'],
                'source_shot_ids': c['shot_ids'], 'selected_shot_ids': [s['shot_id'] for s in selected],
                'start_sec': start, 'end_sec': end, 'source_video': clip['source_video'],
                'event_ids': c['event_ids'], 'evidence_ids': c['evidence_ids'],
                'reason': c['reason'], 'resolved_risk_flags': c.get('risk_flags', []),
                'risk_flags': ['EDIT_SELECTION_REQUIRES_REVIEW', 'CHECK_DIALOGUE_AND_CONTEXT'],
                'continuity_review': c['review_history'][-1], 'review_status': 'UNREVIEWED'})
    return alternatives

def main():
    p=argparse.ArgumentParser(description="生成可解释的导演节奏与镜头编排建议")
    p.add_argument("project");p.add_argument("output");p.add_argument("--observations");p.add_argument("--profile",choices=["fast","balanced","slow"],default="balanced")
    p.add_argument("--continuity",help="已审核的 shot-continuity JSON")
    a=p.parse_args()
    try:
        out=plan_director(read_json(a.project),read_json(a.observations) if a.observations else None,a.profile,read_json(a.continuity) if a.continuity else None)
        write_json(a.output,out)
        print(json.dumps({"output":a.output,"clips":len(out.get("director_plan",{}).get("suggestions",[])),"status":"UNREVIEWED"},ensure_ascii=False))
    except Exception as e:raise SystemExit(f"导演编排失败: {e}")
if __name__=="__main__":main()

