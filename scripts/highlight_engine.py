#!/usr/bin/env python3
"""Generate explainable highlight candidates and edit projects from a Drama Dossier v3.4 file."""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dossier_v3 import load, migrate_to_v33, save_atomic, validate

DIM_KEYS = ("importance", "emotion", "conflict", "twist", "suspense", "visual", "standalone")
PRESETS: dict[str, dict[str, Any]] = {
    "30s": {
        "target": 30.0,
        "weights": {"importance": .20, "emotion": .18, "conflict": .20, "twist": .16, "suspense": .12, "visual": .09, "standalone": .05},
        "strategy": "energy",
    },
    "60s": {
        "target": 60.0,
        "weights": {"importance": .23, "emotion": .17, "conflict": .17, "twist": .15, "suspense": .12, "visual": .08, "standalone": .08},
        "strategy": "energy",
    },
    "3m": {
        "target": 180.0,
        "weights": {"importance": .28, "emotion": .15, "conflict": .12, "twist": .12, "suspense": .10, "visual": .08, "standalone": .15},
        "strategy": "story",
    },
    "5m": {
        "target": 300.0,
        "weights": {"importance": .30, "emotion": .14, "conflict": .10, "twist": .10, "suspense": .08, "visual": .08, "standalone": .20},
        "strategy": "story",
    },
}

KEYWORDS = {
    "emotion": [r"哭", r"泪", r"拥抱", r"诀别", r"告白", r"崩溃", r"愤怒", r"悲", r"love", r"cry", r"farewell"],
    "conflict": [r"打", r"杀", r"斗", r"争", r"威胁", r"命令", r"拒绝", r"逃", r"追", r"枪", r"fight", r"kill", r"threat", r"refuse"],
    "twist": [r"原来", r"真相", r"揭露", r"发现", r"身份", r"其实", r"反转", r"reveal", r"truth", r"identity"],
    "suspense": [r"秘密", r"失踪", r"跟踪", r"怀疑", r"线索", r"谜", r"危险", r"secret", r"missing", r"clue", r"danger"],
}


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clamp(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


def _scene_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {x.get("scene_id"): x for x in data.get("scenes", []) if isinstance(x, dict) and x.get("scene_id")}


def _event_storylines(data: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in data.get("storylines", []):
        lid = line.get("storyline_id")
        if not lid:
            continue
        for event_id in line.get("event_ids", []) or []:
            out.setdefault(event_id, []).append(lid)
    return out


def _causal_degree(data: dict[str, Any]) -> dict[str, int]:
    degree: dict[str, int] = {}
    for edge in data.get("causal_edges", []):
        if edge.get("review_status") == "INCORRECT":
            continue
        for key in ("from_event", "to_event"):
            eid = edge.get(key)
            if eid:
                degree[eid] = degree.get(eid, 0) + (2 if edge.get("type") in {"CAUSES", "REVEALS", "MOTIVATES"} else 1)
    return degree


def _matched(text: str, group: str) -> bool:
    return any(re.search(p, text, re.I) for p in KEYWORDS[group])


def event_dimensions(event: dict[str, Any], causal_degree: int = 0) -> tuple[dict[str, float], str]:
    """Use AI-provided dimensions when present; otherwise deterministic conservative heuristics."""
    supplied = event.get("highlight_dimensions")
    if isinstance(supplied, dict) and supplied:
        dims = {k: clamp(supplied.get(k, 50)) for k in DIM_KEYS}
        return dims, "event.highlight_dimensions"

    text = " ".join(str(event.get(k) or "") for k in ("action", "quote", "story_time"))
    importance = 50 + min(causal_degree * 8, 30)
    if event.get("certainty") in {"F1_DIRECT", "F2_CORROBORATED"}:
        importance += 5
    if event.get("review_status") == "PASS":
        importance += 5
    dims = {
        "importance": clamp(importance),
        "emotion": 72 if _matched(text, "emotion") else 45,
        "conflict": 78 if _matched(text, "conflict") else 42,
        "twist": 82 if _matched(text, "twist") else 38,
        "suspense": 76 if _matched(text, "suspense") else 40,
        "visual": clamp(event.get("visual_score", 50)),
        "standalone": clamp(event.get("standalone_score", 55)),
    }
    return dims, "deterministic_fallback"


def weighted_score(dimensions: dict[str, float], preset: str = "60s") -> float:
    weights = PRESETS[preset]["weights"]
    return round(sum(clamp(dimensions.get(k, 50)) * weights[k] for k in DIM_KEYS), 2)


def _bounds_for_event(event: dict[str, Any], scene: dict[str, Any] | None, before: float, after: float) -> tuple[float, float]:
    t = float(event.get("playback_time", 0) or 0)
    start, end = max(0.0, t - before), t + after
    if scene:
        ss, se = scene.get("playback_start"), scene.get("playback_end")
        if isinstance(ss, (int, float)):
            start = max(float(ss), start)
        if isinstance(se, (int, float)):
            end = min(float(se), end)
    if end <= start:
        end = start + 1.0
    return round(start, 3), round(end, 3)


def generate_highlights(data: dict[str, Any], preset: str = "60s", before: float = 3.0, after: float = 6.0, replace: bool = False) -> int:
    if preset not in PRESETS:
        raise ValueError(f"未知预设: {preset}")
    scenes = _scene_index(data)
    storylines = _event_storylines(data)
    degree = _causal_degree(data)
    existing_event_sets = {
        tuple(sorted(h.get("event_ids", [])))
        for h in data.get("highlights", [])
        if isinstance(h, dict) and h.get("event_ids")
    }
    if replace:
        data["highlights"] = []
        existing_event_sets.clear()

    created = 0
    for event in sorted(data.get("events", []), key=lambda e: float(e.get("playback_time", 0) or 0)):
        if event.get("review_status") == "INCORRECT" or event.get("modality") in {"imagined", "hypothetical"}:
            continue
        eid = event.get("event_id")
        if not eid or (eid,) in existing_event_sets:
            continue
        scene = scenes.get(event.get("scene_id"))
        start, end = _bounds_for_event(event, scene, before, after)
        dims, source = event_dimensions(event, degree.get(eid, 0))
        title = str(event.get("highlight_title") or event.get("action") or eid).strip()
        reason = str(event.get("highlight_reason") or f"由事件 {eid} 生成的精彩片段候选；需结合原片确认剪辑边界。")
        evidence_ids = list(dict.fromkeys(event.get("evidence_ids", []) or []))
        data["highlights"].append({
            "highlight_id": uid("HL"),
            "start_sec": start,
            "end_sec": end,
            "title": title[:120],
            "reason": reason,
            "event_ids": [eid],
            "storyline_ids": storylines.get(eid, []),
            "evidence_ids": evidence_ids,
            "dimensions": dims,
            "total_score": weighted_score(dims, preset),
            "score_profile": preset,
            "score_source": source,
            "context_required": str(event.get("context_required") or "medium"),
            "review_status": "UNREVIEWED",
        })
        created += 1
    return created


def merge_overlapping(highlights: list[dict[str, Any]], gap_sec: float = 0.75) -> list[dict[str, Any]]:
    clips = sorted((copy.deepcopy(x) for x in highlights), key=lambda x: (float(x.get("start_sec", 0)), float(x.get("end_sec", 0))))
    if not clips:
        return []
    merged = [clips[0]]
    for clip in clips[1:]:
        prev = merged[-1]
        if float(clip["start_sec"]) <= float(prev["end_sec"]) + gap_sec:
            prev["end_sec"] = max(float(prev["end_sec"]), float(clip["end_sec"]))
            for key in ("event_ids", "storyline_ids", "evidence_ids", "story_arc_ids", "source_arc_ids"):
                prev[key] = list(dict.fromkeys((prev.get(key, []) or []) + (clip.get(key, []) or [])))
            if clip.get("source_arc_id"):
                prev["source_arc_ids"] = list(dict.fromkeys((prev.get("source_arc_ids", []) or []) + [clip["source_arc_id"]]))
            if prev.get("source_arc_id"):
                prev["source_arc_ids"] = list(dict.fromkeys((prev.get("source_arc_ids", []) or []) + [prev["source_arc_id"]]))
            pd = prev.get("dimensions", {})
            cd = clip.get("dimensions", {})
            prev["dimensions"] = {k: max(float(pd.get(k, 0)), float(cd.get(k, 0))) for k in DIM_KEYS}
            prev["total_score"] = max(float(prev.get("total_score", 0)), float(clip.get("total_score", 0)))
            prev["title"] = prev.get("title") or clip.get("title")
            prev["reason"] = "；".join(x for x in [prev.get("reason", ""), clip.get("reason", "")] if x)
        else:
            merged.append(clip)
    return merged


def _utility(clip: dict[str, Any], preset: str) -> float:
    dims = clip.get("dimensions", {}) or {}
    base = weighted_score(dims, preset)
    duration = max(.5, float(clip.get("end_sec", 0)) - float(clip.get("start_sec", 0)))
    context = clip.get("context_required", "medium")
    context_factor = {"low": 1.08, "medium": 1.0, "high": .88}.get(context, 1.0)
    review_factor = {"PASS": 1.10, "UNREVIEWED": 1.0, "NEED_EVIDENCE": .90, "DISPUTED": .82, "INCORRECT": 0.0}.get(clip.get("review_status", "UNREVIEWED"), 1.0)
    density = 0.75 + 0.25 / math.sqrt(duration)
    return base * context_factor * review_factor * density


def select_project(data: dict[str, Any], preset: str = "60s", video: str = "", reviewed_only: bool = False) -> dict[str, Any]:
    if preset not in PRESETS:
        raise ValueError(f"未知预设: {preset}")
    target = float(PRESETS[preset]["target"])
    candidates = []
    for h in data.get("highlights", []):
        if h.get("review_status") == "INCORRECT":
            continue
        if reviewed_only and h.get("review_status") != "PASS":
            continue
        start, end = h.get("start_sec"), h.get("end_sec")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            continue
        candidates.append(copy.deepcopy(h))
    candidates = merge_overlapping(candidates)
    ranked = sorted(candidates, key=lambda c: (_utility(c, preset), float(c.get("total_score", 0))), reverse=True)

    chosen: list[dict[str, Any]] = []
    used = 0.0
    soft_limit = target * 1.08
    for clip in ranked:
        duration = float(clip["end_sec"]) - float(clip["start_sec"])
        if used + duration <= soft_limit or not chosen:
            chosen.append(clip)
            used += duration
        if used >= target * .92:
            break

    # A story cut defaults to chronology. Users can reorder later in the workbench.
    chosen.sort(key=lambda c: float(c.get("start_sec", 0)))
    clips = []
    for i, clip in enumerate(chosen, 1):
        clips.append({
            "clip_id": f"CLP_{i:03d}",
            "highlight_id": clip.get("highlight_id"),
            "start_sec": round(float(clip["start_sec"]), 3),
            "end_sec": round(float(clip["end_sec"]), 3),
            "title": clip.get("title", ""),
            "reason": clip.get("reason", ""),
            "event_ids": clip.get("event_ids", []),
            "storyline_ids": clip.get("storyline_ids", []),
            "story_arc_ids": list(dict.fromkeys((clip.get("story_arc_ids", []) or []) + ([clip["source_arc_id"]] if clip.get("source_arc_id") else []) + (clip.get("source_arc_ids", []) or []))),
            "score": round(_utility(clip, preset), 2),
            "enabled": True,
        })
    total = round(sum(c["end_sec"] - c["start_sec"] for c in clips), 3)
    ep = data.get("episode", {})
    return {
        "project_version": "1.0",
        "dossier_schema_version": data.get("schema_version"),
        "episode_id": ep.get("episode_id", ""),
        "title": ep.get("title", ""),
        "source_video": video,
        "preset": preset,
        "target_duration_sec": target,
        "estimated_duration_sec": total,
        "sequence_strategy": "chronological",
        "created_at": now(),
        "clips": clips,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="从 v3.4 剧情档案生成精彩片段候选和智能剪辑方案")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="从事件生成可审核的精彩片段候选")
    g.add_argument("dossier")
    g.add_argument("--output")
    g.add_argument("--preset", choices=PRESETS, default="60s")
    g.add_argument("--before", type=float, default=3.0)
    g.add_argument("--after", type=float, default=6.0)
    g.add_argument("--replace", action="store_true")

    s = sub.add_parser("select", help="从精彩片段候选生成剪辑项目 JSON")
    s.add_argument("dossier")
    s.add_argument("output")
    s.add_argument("--preset", choices=PRESETS, default="60s")
    s.add_argument("--video", default="")
    s.add_argument("--reviewed-only", action="store_true")

    a = p.parse_args()
    data = migrate_to_v33(load(a.dossier))
    errors = validate(data)
    if errors:
        raise SystemExit("档案校验失败:\n" + "\n".join(errors))
    if a.cmd == "generate":
        n = generate_highlights(data, a.preset, a.before, a.after, a.replace)
        errors = validate(data)
        if errors:
            raise SystemExit("生成候选后校验失败:\n" + "\n".join(errors))
        out = a.output or a.dossier
        save_atomic(data, out)
        print(f"已生成 {n} 个精彩片段候选: {out}")
    else:
        project = select_project(data, a.preset, a.video, a.reviewed_only)
        Path(a.output).write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已生成剪辑方案: {a.output} ({len(project['clips'])} 段, 约 {project['estimated_duration_sec']:.1f}s)")


if __name__ == "__main__":
    main()
