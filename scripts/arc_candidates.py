#!/usr/bin/env python3
"""Build reviewable setup/peak/reaction/payoff candidates.

The engine aligns media cues with already-known events and storylines. It does
not perform semantic video understanding: arc roles remain candidates until a
person/ChatGPT checks the continuous source footage.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dossier_v3 import load, migrate_to_v34, save_atomic, validate  # noqa: E402

DIM_KEYS = ("importance", "emotion", "conflict", "twist", "suspense", "visual", "standalone")
WEIGHTS = {"importance": .23, "emotion": .16, "conflict": .16, "twist": .14, "suspense": .12, "visual": .09, "standalone": .10}
KEYWORDS = {
    "emotion": r"哭|泪|拥抱|诀别|告白|崩溃|愤怒|悲|爱你|对不起|love|cry|farewell",
    "conflict": r"打|杀|斗|争|威胁|命令|拒绝|逃|追|枪|背叛|fight|kill|threat|betray",
    "twist": r"原来|真相|揭露|发现|身份|其实|反转|竟然|truth|reveal|identity",
    "suspense": r"秘密|失踪|跟踪|怀疑|线索|谜|危险|证据|谁|为什么|secret|missing|clue|danger",
}
PEAK_CUE_TYPES = {"shot_change", "audio_peak", "subtitle_keyword", "subtitle_emphasis", "dialogue_burst", "motion_candidate", "ocr_text"}
NON_ACTUAL = {"planned", "commanded", "desired", "threatened", "hypothetical", "recalled", "imagined"}


def uid() -> str:
    return "ARC_" + uuid.uuid4().hex[:12]


def clamp(x: float) -> float:
    return max(0.0, min(100.0, float(x)))


def score_text(text: str, key: str) -> float:
    return 76.0 if re.search(KEYWORDS[key], text, re.I) else 38.0


def _event_storylines(data: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in data.get("storylines", []):
        for eid in line.get("event_ids", []) or []:
            out.setdefault(eid, []).append(line.get("storyline_id"))
    return out


def _event_time(event: dict[str, Any]) -> float:
    return float(event.get("playback_time") or 0)


def _cluster_cues(cues: list[dict[str, Any]], gap: float = 5.0) -> list[list[dict[str, Any]]]:
    rows = sorted(cues, key=lambda x: float(x.get("start_sec", 0)))
    groups: list[list[dict[str, Any]]] = []
    for cue in rows:
        if not groups or float(cue["start_sec"]) - max(float(x["end_sec"]) for x in groups[-1]) > gap:
            groups.append([cue])
        else:
            groups[-1].append(cue)
    return groups


def _event_dims(events: list[dict[str, Any]], cues: list[dict[str, Any]], causal_degree: int) -> dict[str, float]:
    """Fallback scores are deliberately conservative; loudness/cut count is not emotion."""
    texts = " ".join(str(e.get(k) or "") for e in events for k in ("action", "quote"))
    supplied = [e.get("highlight_dimensions") for e in events if isinstance(e.get("highlight_dimensions"), dict)]
    def supplied_or(key: str, fallback: float) -> float:
        vals = [float(d[key]) for d in supplied if isinstance(d.get(key), (int, float))]
        return max(vals) if vals else fallback
    dims = {
        "importance": supplied_or("importance", clamp(40 + min(causal_degree * 5, 20))),
        "emotion": supplied_or("emotion", score_text(texts, "emotion")),
        "conflict": supplied_or("conflict", score_text(texts, "conflict")),
        "twist": supplied_or("twist", score_text(texts, "twist")),
        "suspense": supplied_or("suspense", score_text(texts, "suspense")),
        # Without semantic visual inspection, keep visual score at a neutral-low baseline.
        "visual": supplied_or("visual", 35.0),
        "standalone": supplied_or("standalone", 45.0 if events else 20.0),
    }
    # Subtitle cues may make the text searchable, but do not independently increase story value.
    if not events:
        dims["importance"] = min(dims["importance"], 25.0)
        dims["standalone"] = min(dims["standalone"], 25.0)
    return {k: round(clamp(v), 2) for k, v in dims.items()}


def _weighted(dims: dict[str, float]) -> float:
    return round(sum(dims[k] * WEIGHTS[k] for k in DIM_KEYS), 2)


def _segment(role: str, start: float, end: float, events: list[dict[str, Any]], cues: list[dict[str, Any]], note: str) -> dict[str, Any] | None:
    if end - start < .25:
        return None
    return {
        "role": role,
        "start_sec": round(start, 3),
        "end_sec": round(end, 3),
        "description": note,
        "event_ids": [e["event_id"] for e in events if e.get("event_id")],
        "cue_ids": [c["cue_id"] for c in cues if c.get("cue_id")],
        "semantic_status": "UNCLASSIFIED_CANDIDATE",
    }


def _causal_neighbors(data: dict[str, Any]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for edge in data.get("causal_edges", []):
        if edge.get("review_status") == "INCORRECT":
            continue
        a, b = edge.get("from_event"), edge.get("to_event")
        if a and b:
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
    return graph


def _related_events(anchor_event: dict[str, Any] | None, valid_events: list[dict[str, Any]],
                    storylines: dict[str, list[str]], causal_graph: dict[str, set[str]],
                    anchor: float, pre_roll: float, payoff_gap: float) -> list[dict[str, Any]]:
    if anchor_event is None:
        return [e for e in valid_events if anchor - pre_roll <= _event_time(e) <= anchor + payoff_gap]
    anchor_id = anchor_event.get("event_id")
    line_ids = set(storylines.get(anchor_id, []))
    linked_ids = {anchor_id} | causal_graph.get(anchor_id, set())
    for event in valid_events:
        if line_ids and line_ids.intersection(storylines.get(event.get("event_id"), [])):
            linked_ids.add(event.get("event_id"))
    rows = [e for e in valid_events
            if e.get("event_id") in linked_ids
            and anchor - pre_roll <= _event_time(e) <= anchor + payoff_gap]
    return sorted(rows, key=_event_time)


def _make_arc(data: dict[str, Any], anchor: float, start: float, end: float,
              events: list[dict[str, Any]], cues: list[dict[str, Any]],
              storylines: dict[str, list[str]], causal_degree: int,
              arc_source: str, anchor_event_id: str | None = None,
              max_payoff_gap: float = 45.0) -> dict[str, Any]:
    duration = float(data.get("episode", {}).get("duration_sec") or 0)
    start = max(0.0, start)
    if duration > 0:
        end = min(duration, end)
    end = max(start + .5, end)
    peak_start = max(start, anchor - 1.25)
    peak_end = min(end, anchor + 1.25)

    ordered = sorted(events, key=_event_time)
    setup_events = [e for e in ordered if _event_time(e) < anchor - 1.25]
    reaction_events = [e for e in ordered if anchor + 1.25 < _event_time(e) <= anchor + 8.0]
    anchor_line_ids = set(storylines.get(anchor_event_id or "", []))
    direct_related = set()
    for edge in data.get("causal_edges", []):
        if edge.get("review_status") == "INCORRECT":
            continue
        if edge.get("from_event") == anchor_event_id:
            direct_related.add(edge.get("to_event"))
        if edge.get("to_event") == anchor_event_id:
            direct_related.add(edge.get("from_event"))
    payoff_events = [e for e in ordered
                     if anchor + 8.0 < _event_time(e) <= anchor + max_payoff_gap
                     and ((anchor_line_ids and anchor_line_ids.intersection(storylines.get(e.get("event_id"), [])))
                          or e.get("event_id") in direct_related)]

    # Expand only when a later, related payoff candidate exists; never swallow a whole episode.
    if payoff_events:
        end = max(end, max(_event_time(e) for e in payoff_events) + 2.5)
        if duration > 0:
            end = min(duration, end)
    start = min(start, max(0.0, min([_event_time(e) for e in setup_events] or [start]) - 1.5))

    event_ids = list(dict.fromkeys(e["event_id"] for e in ordered if e.get("event_id")))
    storyline_ids = list(dict.fromkeys(x for eid in event_ids for x in storylines.get(eid, [])))
    evidence_ids = list(dict.fromkeys(x for e in ordered for x in (e.get("evidence_ids") or [])))
    cue_ids = list(dict.fromkeys(c["cue_id"] for c in cues if c.get("cue_id")))
    dims = _event_dims(ordered, cues, causal_degree)

    all_pass = bool(ordered) and all(e.get("review_status") == "PASS" for e in ordered)
    if storyline_ids and all_pass and anchor_event_id:
        context = "low"
    elif storyline_ids and evidence_ids:
        context = "medium"
    else:
        context = "high"

    segments: list[dict[str, Any]] = []
    pre_cues = [c for c in cues if float(c["start_sec"]) < peak_start and float(c["end_sec"]) >= start]
    peak_cues = [c for c in cues if float(c["start_sec"]) <= peak_end and float(c["end_sec"]) >= peak_start]
    post_cues = [c for c in cues if float(c["end_sec"]) > peak_end and float(c["start_sec"]) <= end]

    setup_start = min([start] + [max(0.0, _event_time(e) - 1.5) for e in setup_events])
    setup = _segment("setup", setup_start, peak_start, setup_events, pre_cues,
                     "峰值前的铺垫候选；需确认是否与峰值属于同一人物目标/冲突。")
    if setup and (setup_events or pre_cues):
        segments.append(setup)

    peak_events = [e for e in ordered if abs(_event_time(e) - anchor) <= 2.0]
    peak = _segment("peak", peak_start, peak_end, peak_events, peak_cues,
                    "时间锚点附近的事件/音画线索；需确认真正的剧情峰值。")
    if peak and (peak_events or peak_cues):
        segments.append(peak)

    payoff_start = max(peak_end, min([_event_time(e) - 1.5 for e in payoff_events] or [end]))
    reaction_end = min(end, anchor + 8.0, payoff_start)
    reaction = _segment("reaction", peak_end, reaction_end, reaction_events,
                        [c for c in post_cues if float(c["start_sec"]) < reaction_end],
                        "峰值后的即时反应候选；需核实镜头是否确为同一事件的反应。")
    if reaction and (reaction_events or any(float(c["start_sec"]) < reaction_end for c in post_cues)):
        segments.append(reaction)

    if payoff_events:
        payoff_start = max(peak_end, min(_event_time(e) - 1.5 for e in payoff_events))
        payoff_end = min(end, max(_event_time(e) for e in payoff_events) + 2.5)
        payoff_cues = [c for c in cues if float(c["start_sec"]) <= payoff_end and float(c["end_sec"]) >= payoff_start]
        payoff = _segment("payoff", payoff_start, payoff_end, payoff_events, payoff_cues,
                          "同一剧情线/直接因果关联的后续结果候选；必须核实其确为回收或后果。")
        if payoff:
            segments.append(payoff)

    present_roles = {s["role"] for s in segments}
    missing_roles = [r for r in ("setup", "peak", "reaction", "payoff") if r not in present_roles]
    risks: list[str] = []
    if not ordered:
        risks.append("no_structured_event_yet")
    if any(e.get("modality") in NON_ACTUAL for e in ordered):
        risks.append("non_actual_or_non_present_modality")
    if context == "high":
        risks.append("context_may_be_required")
    if not payoff_events:
        risks.append("payoff_not_identified")
    if not setup_events:
        risks.append("setup_not_semantically_linked")
    if not reaction_events:
        risks.append("reaction_not_semantically_linked")
    if not any(c.get("cue_type") in {"shot_change", "subtitle_keyword", "subtitle_emphasis", "dialogue_burst"} for c in cues):
        risks.append("limited_visual_or_dialogue_cues")

    anchor_event = next((e for e in ordered if e.get("event_id") == anchor_event_id), None)
    title = (anchor_event or next((e for e in ordered if e.get("action")), {})).get("highlight_title") or (anchor_event or next((e for e in ordered if e.get("action")), {})).get("action")
    if not title:
        title = f"多模态片段候选 @ {anchor:.1f}s"
    return {
        "arc_id": uid(), "title": str(title)[:120], "arc_type": "unclassified",
        "narrative_shape": "setup_peak_reaction_payoff",
        "start_sec": round(start, 3), "end_sec": round(end, 3), "anchor_sec": round(anchor, 3),
        "anchor_event_id": anchor_event_id,
        "segments": segments, "event_ids": event_ids, "storyline_ids": storyline_ids,
        "evidence_ids": evidence_ids, "cue_ids": cue_ids,
        "dimensions": dims, "total_score": _weighted(dims),
        "score_source": "hybrid_multimodal_heuristic", "candidate_source": arc_source,
        "semantic_status": "NEEDS_CHATGPT_REVIEW", "context_required": context,
        "missing_roles": missing_roles, "risk_flags": risks,
        "reason": "由时间对齐的镜头变化、音频能量、字幕线索与可用事件提出；机器线索不构成剧情语义证明。",
        "semantic_questions": [
            "画面中实际发生了什么？谁在场、谁行动？",
            "锚点是剧情事件还是音乐/音效/镜头切换造成的强信号？",
            "setup 是否确实建立了峰值所需的目标、关系或风险？",
            "reaction 是否是同一事件的即时反应？",
            "payoff 是否由同一剧情线或可核实的因果边支持？",
            "是否遗漏了否定、条件、回忆、想象或说话人不确定性？",
            "单独剪出是否会改变台词或人物立场含义？",
        ],
        "review_status": "UNREVIEWED",
    }


def _anchor_rank(event: dict[str, Any]) -> tuple[int, int, int, int, int]:
    certainty = {"F1_DIRECT": 5, "F2_CORROBORATED": 4, "I1_STRONG": 3, "I2_PLAUSIBLE": 2, "I3_SPECULATIVE": 1}.get(event.get("certainty"), 0)
    return (
        1 if event.get("review_status") == "PASS" else 0,
        len(event.get("evidence_ids", []) or []),
        1 if isinstance(event.get("highlight_dimensions"), dict) and bool(event.get("highlight_dimensions")) else 0,
        1 if event.get("actor") else 0,
        certainty,
    )


def _same_anchor_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ta, tb = _event_time(a), _event_time(b)
    aa = re.sub(r"[^\w]", "", str(a.get("action") or "")).lower()
    ab = re.sub(r"[^\w]", "", str(b.get("action") or "")).lower()
    actor_a, actor_b = a.get("actor"), b.get("actor")
    return abs(ta - tb) <= 1.5 and bool(aa) and aa == ab and (actor_a is None or actor_b is None or actor_a == actor_b)


def generate_arcs(data: dict[str, Any], scan: dict[str, Any], max_arcs: int = 100,
                  min_signal: float = .42, pre_roll: float = 20.0, max_payoff_gap: float = 45.0) -> int:
    cues = [copy.deepcopy(c) for c in scan.get("cues", []) if isinstance(c, dict) and c.get("cue_id")]
    existing_cue_map = {
        (c.get("cue_type"), round(float(c.get("start_sec", 0)), 2), round(float(c.get("end_sec", 0)), 2)): c.get("cue_id")
        for c in data.get("media_cues", [])
    }
    normalized_cues: list[dict[str, Any]] = []
    for cue in cues:
        key = (cue.get("cue_type"), round(float(cue.get("start_sec", 0)), 2), round(float(cue.get("end_sec", 0)), 2))
        if key in existing_cue_map:
            cue["cue_id"] = existing_cue_map[key]
        else:
            data.setdefault("media_cues", []).append(copy.deepcopy(cue))
            existing_cue_map[key] = cue["cue_id"]
        normalized_cues.append(cue)
    cues = normalized_cues

    duration = float(data.get("episode", {}).get("duration_sec") or scan.get("media", {}).get("duration_sec") or 0)
    if not data.get("episode", {}).get("duration_sec") and duration:
        data["episode"]["duration_sec"] = duration
    storylines = _event_storylines(data)
    causal_graph = _causal_neighbors(data)
    degree: dict[str, int] = {}
    for event_id, neighbors in causal_graph.items():
        degree[event_id] = len(neighbors)

    valid_events = [e for e in data.get("events", [])
                    if e.get("review_status") != "INCORRECT" and e.get("modality") not in {"imagined", "hypothetical"}]
    peak_cues = [c for c in cues if c.get("cue_type") in PEAK_CUE_TYPES]
    anchors: list[tuple[float, str, dict[str, Any] | None, list[dict[str, Any]]]] = []

    for event in valid_events:
        t = _event_time(event)
        nearby = [c for c in cues if float(c["start_sec"]) <= t + 8 and float(c["end_sec"]) >= t - 8]
        has_manual_score = isinstance(event.get("highlight_dimensions"), dict) and bool(event.get("highlight_dimensions"))
        has_storyline = bool(storylines.get(event.get("event_id")))
        if nearby or has_manual_score or has_storyline or event.get("review_status") == "PASS":
            duplicate_index = next((i for i, row in enumerate(anchors)
                                    if row[1] == "dossier_event" and row[2] is not None
                                    and _same_anchor_event(row[2], event)), None)
            candidate = (t, "dossier_event", event, nearby)
            if duplicate_index is None:
                anchors.append(candidate)
            elif _anchor_rank(event) > _anchor_rank(anchors[duplicate_index][2]):
                old_cues = anchors[duplicate_index][3]
                anchors[duplicate_index] = (t, "dossier_event", event,
                                            list({c.get("cue_id"): c for c in old_cues + nearby}.values()))

    event_times = [_event_time(e) for e in valid_events]
    for group in _cluster_cues(peak_cues):
        if not group:
            continue
        signal = max(float(c.get("score") or 0) for c in group)
        if signal < min_signal and len(group) < 2:
            continue
        center = sum((float(c["start_sec"]) + float(c["end_sec"])) / 2 for c in group) / len(group)
        if any(abs(center - t) <= 7 for t in event_times):
            continue
        anchors.append((center, "media_signal_cluster", None, group))

    anchors.sort(key=lambda x: (-(max([float(c.get("score") or 0) for c in x[3]] or [0]) + (0.15 if x[1] == "dossier_event" else 0)), x[0]))
    existing = {
        (a.get("anchor_event_id"), tuple(sorted(a.get("event_ids", []))), round(float(a.get("anchor_sec", 0)), 1))
        for a in data.get("story_arcs", [])
    }
    made = 0
    for anchor, source, anchor_event, group in anchors:
        if made >= max_arcs:
            break
        related = _related_events(anchor_event, valid_events, storylines, causal_graph, anchor, pre_roll, max_payoff_gap)
        earliest = min([_event_time(e) for e in related] or [anchor - 7.0])
        latest = max([_event_time(e) for e in related] or [anchor + 10.0])
        start = max(0.0, min(anchor - 7.0, earliest - 1.5))
        end = max(anchor + 8.0, min(anchor + max_payoff_gap + 2.5, latest + 2.5))
        if duration > 0:
            end = min(duration, end)
        local_cues = [c for c in cues if float(c["start_sec"]) <= end and float(c["end_sec"]) >= start]
        if not local_cues and not related:
            continue
        anchor_id = anchor_event.get("event_id") if anchor_event else None
        key = (anchor_id, tuple(sorted(e.get("event_id") for e in related if e.get("event_id"))), round(anchor, 1))
        if key in existing:
            continue
        arc = _make_arc(data, anchor, start, end, related, local_cues, storylines,
                        sum(degree.get(e.get("event_id"), 0) for e in related), source,
                        anchor_id, max_payoff_gap)
        data.setdefault("story_arcs", []).append(arc)
        existing.add(key)
        made += 1
    return made


def main() -> None:
    p = argparse.ArgumentParser(description="从剧情档案与音画字幕线索生成 setup/peak/reaction/payoff 候选")
    p.add_argument("dossier")
    p.add_argument("multimodal_scan")
    p.add_argument("--output", help="输出档案；默认覆盖输入档案")
    p.add_argument("--max-arcs", type=int, default=100)
    p.add_argument("--min-signal", type=float, default=.42)
    p.add_argument("--pre-roll", type=float, default=20.0, help="关联事件向前搜索的最大秒数")
    p.add_argument("--max-payoff-gap", type=float, default=45.0, help="同一剧情线/因果链向后搜索结果的最大秒数")
    a = p.parse_args()
    if a.max_arcs < 1 or not 0 <= a.min_signal <= 1 or a.pre_roll < 0 or a.max_payoff_gap <= 0:
        raise SystemExit("max-arcs/min-signal/pre-roll/max-payoff-gap 参数无效")
    data = migrate_to_v34(load(a.dossier))
    scan = json.loads(Path(a.multimodal_scan).read_text(encoding="utf-8"))
    count = generate_arcs(data, scan, a.max_arcs, a.min_signal, a.pre_roll, a.max_payoff_gap)
    errors = validate(data)
    if errors:
        raise SystemExit("生成后档案校验失败：\n" + "\n".join(errors))
    out = a.output or a.dossier
    save_atomic(data, out)
    print(f"已导入 {len(scan.get('cues', []))} 条多模态线索，新增 {count} 条剧情弧线候选：{out}")
    print("所有弧线均为待审核候选，不代表模型已理解剧情。")


if __name__ == "__main__":
    main()
