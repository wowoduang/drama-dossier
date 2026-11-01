#!/usr/bin/env python3
"""Import ChatGPT/human visual review JSON as unreviewed evidence/event candidates."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dossier_v3 import (  # noqa: E402
    VALID_CERTAINTY, VALID_MODALITY, VALID_POLARITY, empty_dossier,
    load, migrate_to_v34, save_atomic, uid, validate,
)


def _time(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{label} 必须是非负数字")
    return round(float(value), 3)


def import_review(data: dict[str, Any], review: dict[str, Any]) -> dict[str, int]:
    """Import transactionally: invalid input leaves the caller's dossier untouched."""
    target = data
    data = copy.deepcopy(data)
    if not isinstance(review, dict):
        raise ValueError("审核结果根节点必须为 JSON 对象")
    observations = review.get("observations", [])
    candidates = review.get("event_candidates", [])
    if not isinstance(observations, list) or not isinstance(candidates, list):
        raise ValueError("observations 和 event_candidates 必须是数组")

    observation_evidence: dict[int, str] = {}
    known_evidence = {x.get("evidence_id") for x in data.get("evidence", [])}
    for index, obs in enumerate(observations):
        if not isinstance(obs, dict):
            raise ValueError(f"observations[{index}] 必须是对象")
        start = _time(obs.get("start_sec"), f"observations[{index}].start_sec")
        end = _time(obs.get("end_sec", start), f"observations[{index}].end_sec")
        if end < start:
            raise ValueError(f"observations[{index}] end_sec 早于 start_sec")
        description = str(obs.get("visual_description") or obs.get("observation") or "").strip()
        if not description:
            raise ValueError(f"observations[{index}] 缺少 visual_description")
        confidence = str(obs.get("confidence") or "low").lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        duration = float(data.get("episode", {}).get("duration_sec") or 0)
        if duration > 0 and end > duration + 0.05:
            raise ValueError(f"observations[{index}] 超出本集时长 {duration}")
        frame_refs = obs.get("frame_refs") or []
        if not isinstance(frame_refs, list):
            raise ValueError(f"observations[{index}].frame_refs 必须为数组")
        eid = uid("EVD")
        observation_evidence[index] = eid
        data.setdefault("evidence", []).append({
            "evidence_id": eid,
            "type": "VIS",
            "source": "ChatGPT/human visual review",
            "start_sec": start,
            "end_sec": end,
            "raw": "",
            "observation": description,
            "quality": confidence,
            "locator": ", ".join(str(x) for x in frame_refs),
            "review_status": "UNREVIEWED",
            "visual_observation_id": obs.get("observation_id") or f"OBS_{index + 1:04d}",
            "continuity_notes": str(obs.get("continuity_notes") or ""),
            "alternatives": obs.get("alternatives") if isinstance(obs.get("alternatives"), list) else [],
        })

    imported_events = 0
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"event_candidates[{index}] 必须是对象")
        action = str(candidate.get("action") or "").strip()
        if not action:
            raise ValueError(f"event_candidates[{index}] 缺少 action")
        t = _time(candidate.get("playback_time"), f"event_candidates[{index}].playback_time")
        modality = candidate.get("modality", "actual")
        polarity = candidate.get("polarity", "positive")
        certainty = candidate.get("certainty", "I2_PLAUSIBLE")
        if modality not in VALID_MODALITY:
            raise ValueError(f"event_candidates[{index}].modality 无效: {modality}")
        if polarity not in VALID_POLARITY:
            raise ValueError(f"event_candidates[{index}].polarity 无效: {polarity}")
        if certainty not in VALID_CERTAINTY:
            raise ValueError(f"event_candidates[{index}].certainty 无效: {certainty}")
        scene_id = candidate.get("scene_id")
        scene_ids = {x.get("scene_id") for x in data.get("scenes", [])}
        if scene_id not in scene_ids:
            scene_id = None
        char_ids = {x.get("character_id") for x in data.get("characters", [])}
        slots = {}
        for slot in ("actor", "patient", "recipient", "target"):
            value = candidate.get(slot)
            if value is not None and value not in char_ids:
                raise ValueError(f"event_candidates[{index}].{slot} 引用了不存在的人物 ID: {value}")
            slots[slot] = value

        raw_evidence_ids = candidate.get("evidence_ids") or []
        if not isinstance(raw_evidence_ids, list):
            raise ValueError(f"event_candidates[{index}].evidence_ids 必须为数组")
        evidence_ids = list(raw_evidence_ids)
        duration = float(data.get("episode", {}).get("duration_sec") or 0)
        if duration > 0 and t > duration + 0.05:
            raise ValueError(f"event_candidates[{index}] 超出本集时长 {duration}")
        obs_index = candidate.get("observation_index")
        if obs_index is not None:
            if not isinstance(obs_index, int) or obs_index not in observation_evidence:
                raise ValueError(f"event_candidates[{index}].observation_index 无效: {obs_index}")
            evidence_ids.append(observation_evidence[obs_index])
        evidence_ids = list(dict.fromkeys(evidence_ids))
        if not evidence_ids or any(eid not in known_evidence and eid not in observation_evidence.values() for eid in evidence_ids):
            raise ValueError(f"event_candidates[{index}] 必须引用有效 evidence_ids 或 observation_index")

        data.setdefault("events", []).append({
            "event_id": uid("EVT"),
            "scene_id": scene_id,
            "playback_time": t,
            "story_time": str(candidate.get("story_time") or ""),
            **slots,
            "action": action,
            "modality": modality,
            "polarity": polarity,
            "quote": str(candidate.get("quote") or ""),
            "evidence_ids": evidence_ids,
            "certainty": certainty,
            "review_status": "UNREVIEWED",
            "candidate_source": "ai_visual_review",
            "semantic_note": str(candidate.get("semantic_note") or ""),
        })
        imported_events += 1

    scene_candidates = review.get("scene_candidates", [])
    imported_scenes = 0
    if scene_candidates:
        from dossier_v3 import import_scene_candidates
        imported_scenes = import_scene_candidates(data, {"method": "ai_visual_review_candidate", "segments": scene_candidates})

    target.clear()
    target.update(data)
    return {"observations": len(observation_evidence), "events": imported_events, "scenes": imported_scenes}


def main() -> None:
    p = argparse.ArgumentParser(description="导入 ChatGPT/人工视觉语义审核结果，作为待审核证据与事件候选")
    p.add_argument("dossier")
    p.add_argument("review_json")
    p.add_argument("--output", help="输出档案；默认覆盖 dossier")
    a = p.parse_args()
    data = migrate_to_v34(load(a.dossier))
    review = json.loads(Path(a.review_json).read_text(encoding="utf-8"))
    counts = import_review(data, review)
    errors = validate(data)
    if errors:
        raise SystemExit("导入后档案校验失败：\n" + "\n".join(errors))
    out = a.output or a.dossier
    save_atomic(data, out)
    print(f"已导入视觉观察 {counts['observations']} 条、事件候选 {counts['events']} 条、场景候选 {counts['scenes']} 个：{out}")
    print("所有导入对象均为 UNREVIEWED；AI 输出不是已确认剧情事实。")


if __name__ == "__main__":
    main()
