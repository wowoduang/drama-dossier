#!/usr/bin/env python3
"""Validate user-defined character-arc milestones against a compiled edit project."""
import argparse, json, sys
from pathlib import Path

def read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def validate_arc(project, plan):
    ctx = project.get("character_state_context") or {}
    states = {(x.get("episode_id"), x.get("state_id")): x for x in ctx.get("states", [])}
    changes = {(x.get("episode_id"), x.get("change_id")): x for x in ctx.get("changes", [])}
    clips = project.get("clips", [])
    used_states = {(c.get("episode_id"), sid) for c in clips for sid in c.get("character_state_ids", [])}
    used_changes = {(c.get("episode_id"), cid) for c in clips for cid in c.get("character_state_change_ids", [])}
    results = []
    for milestone in plan.get("milestones", []):
        required_states = [(milestone.get("episode_id"), x) for x in milestone.get("required_state_ids", [])]
        required_changes = [(milestone.get("episode_id"), x) for x in milestone.get("required_change_ids", [])]
        missing_states = [x for x in required_states if x not in states or x not in used_states]
        missing_changes = [x for x in required_changes if x not in changes or x not in used_changes]
        missing_events = []
        for key in required_states:
            state = states.get(key)
            if state and not any(eid in {e for c in clips if c.get("episode_id") == key[0] for e in c.get("event_ids", [])} for eid in state.get("event_ids", [])):
                missing_events.append({"state_id": key[1], "reason": "required state event not represented by an enabled clip"})
        results.append({
            "milestone_id": milestone.get("milestone_id"),
            "label": milestone.get("label", ""),
            "status": "PASS" if not (missing_states or missing_changes or missing_events) else "MISSING",
            "missing_state_ids": [x[1] for x in missing_states],
            "missing_change_ids": [x[1] for x in missing_changes],
            "missing_event_coverage": missing_events
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
            "完整性仅针对用户明确列出的里程碑，不自动判断人物是否真正成长",
            "通过表示状态/变化/事件进入工程，不代表叙事质量或因果成立",
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
