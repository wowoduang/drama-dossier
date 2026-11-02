#!/usr/bin/env python3
"""Validate user-defined character-arc milestones against enabled clips only."""
import argparse, json, sys
from pathlib import Path

PLAN_FIELDS = {"arc_id", "title", "description", "milestones"}
MILESTONE_FIELDS = {"milestone_id", "episode_id", "label", "required_state_ids", "required_change_ids", "required_event_ids"}

def read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def _id_list(value, label):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{label} 必须是非空字符串 ID 数组")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} 存在重复 ID")
    return value

def validate_arc(project, plan):
    if not isinstance(project, dict) or not isinstance(plan, dict):
        raise ValueError("project 和 arc plan 必须是 JSON 对象")
    unknown_plan = set(plan) - PLAN_FIELDS
    if unknown_plan:
        raise ValueError("arc plan 存在未知字段: " + ", ".join(sorted(unknown_plan)))
    milestones = plan.get("milestones")
    if not isinstance(milestones, list) or not milestones:
        raise ValueError("milestones 必须是非空数组")
    ctx = project.get("character_state_context") or {}
    if not isinstance(ctx, dict):
        raise ValueError("character_state_context 必须是对象")
    state_rows = ctx.get("states", [])
    change_rows = ctx.get("changes", [])
    clips = project.get("clips", [])
    if not isinstance(state_rows, list) or not isinstance(change_rows, list) or not isinstance(clips, list):
        raise ValueError("states、changes 和 clips 必须是数组")
    states = {}
    for row in state_rows:
        if not isinstance(row, dict) or not row.get("episode_id") or not row.get("state_id"):
            raise ValueError("character_state_context.states 存在无效记录")
        key = (row["episode_id"], row["state_id"])
        if key in states:
            raise ValueError(f"状态上下文 ID 重复: {key}")
        states[key] = row
    changes = {}
    for row in change_rows:
        if not isinstance(row, dict) or not row.get("episode_id") or not row.get("change_id"):
            raise ValueError("character_state_context.changes 存在无效记录")
        key = (row["episode_id"], row["change_id"])
        if key in changes:
            raise ValueError(f"变化上下文 ID 重复: {key}")
        changes[key] = row
    enabled_clips = []
    for clip in clips:
        if not isinstance(clip, dict):
            raise ValueError("clips 中每项必须是对象")
        if clip.get("enabled", True) is not False:
            enabled_clips.append(clip)
    used_states = {(c.get("episode_id"), sid) for c in enabled_clips for sid in _id_list(c.get("character_state_ids", []), "clip.character_state_ids")}
    used_changes = {(c.get("episode_id"), cid) for c in enabled_clips for cid in _id_list(c.get("character_state_change_ids", []), "clip.character_state_change_ids")}
    used_events_by_episode = {}
    for clip in enabled_clips:
        ep = clip.get("episode_id")
        used_events_by_episode.setdefault(ep, set()).update(_id_list(clip.get("event_ids", []), "clip.event_ids"))
    results = []
    seen_milestones = set()
    for milestone in milestones:
        if not isinstance(milestone, dict):
            raise ValueError("milestones 每项必须是对象")
        unknown = set(milestone) - MILESTONE_FIELDS
        if unknown:
            raise ValueError("milestone 存在未知字段: " + ", ".join(sorted(unknown)))
        mid = milestone.get("milestone_id")
        episode_id = milestone.get("episode_id")
        if not isinstance(mid, str) or not mid.strip() or not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError("每个 milestone 必须提供 milestone_id 和 episode_id")
        if mid in seen_milestones:
            raise ValueError(f"milestone_id 重复: {mid}")
        seen_milestones.add(mid)
        required_states = [(episode_id, x) for x in _id_list(milestone.get("required_state_ids", []), "required_state_ids")]
        required_changes = [(episode_id, x) for x in _id_list(milestone.get("required_change_ids", []), "required_change_ids")]
        required_events = _id_list(milestone.get("required_event_ids", []), "required_event_ids")
        missing_states = [x for x in required_states if x not in states or x not in used_states]
        missing_changes = [x for x in required_changes if x not in changes or x not in used_changes]
        missing_events = [eid for eid in required_events if eid not in used_events_by_episode.get(episode_id, set())]
        for key in required_states:
            state = states.get(key)
            if state:
                represented = used_events_by_episode.get(key[0], set())
                if not any(eid in represented for eid in state.get("event_ids", [])):
                    missing_events.append(key[1])
        results.append({
            "milestone_id": mid,
            "label": milestone.get("label", ""),
            "status": "PASS" if not (missing_states or missing_changes or missing_events) else "MISSING",
            "missing_state_ids": [x[1] for x in missing_states],
            "missing_change_ids": [x[1] for x in missing_changes],
            "missing_event_ids": list(dict.fromkeys(missing_events))
        })
    blockers = [x for x in results if x["status"] != "PASS"]
    return {
        "version": "4.3.1",
        "arc_id": plan.get("arc_id"),
        "milestone_count": len(results),
        "passed_milestones": len(results) - len(blockers),
        "missing_milestones": blockers,
        "complete": bool(results) and not blockers,
        "limitations": [
            "仅统计 enabled 不为 false 的剪辑片段",
            "完整性仅针对用户明确列出的里程碑，不自动判断人物是否真正成长",
            "通过表示状态/变化/事件进入启用片段，不代表叙事质量或因果成立",
            "必须人工核对原片、人物认知和转折语境"
        ]
    }

def main():
    p = argparse.ArgumentParser(description="Check character arc milestone coverage")
    p.add_argument("project")
    p.add_argument("arc_plan")
    p.add_argument("output")
    a = p.parse_args()
    try:
        result = validate_arc(read(a.project), read(a.arc_plan))
        out = Path(a.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(out)
        print(json.dumps({"output": a.output, "complete": result["complete"], "missing": len(result["missing_milestones"])}, ensure_ascii=False))
    except Exception as e:
        print(f"character arc validation failed: {e}", file=sys.stderr)
        raise SystemExit(2)

if __name__ == "__main__":
    main()
