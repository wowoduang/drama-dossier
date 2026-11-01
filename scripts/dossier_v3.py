#!/usr/bin/env python3
"""Drama Dossier v3.4 structured dossier engine (standard library only)."""
from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "3.5.0"
COMPATIBLE_SCHEMA_VERSIONS = {"3.0.0", "3.1.0", "3.2.0", "3.3.0", "3.4.0", SCHEMA_VERSION}
TOP_LEVEL = [
    "episode", "evidence", "characters", "appearances", "scenes", "events",
    "relations", "causal_edges", "threads", "storylines", "highlights", "media_cues", "story_arcs", "knowledge_states", "character_states", "character_state_changes", "review_history",
]
ID_FIELDS = {
    "evidence": "evidence_id",
    "characters": "character_id",
    "appearances": "appearance_id",
    "scenes": "scene_id",
    "events": "event_id",
    "relations": "relation_id",
    "causal_edges": "cause_id",
    "threads": "thread_id",
    "storylines": "storyline_id",
    "highlights": "highlight_id",
    "media_cues": "cue_id",
    "story_arcs": "arc_id",
    "knowledge_states": "knowledge_id",
    "character_states": "state_id",
    "character_state_changes": "change_id",
    "review_history": "review_id",
}
REVIEWABLE = tuple(k for k in ID_FIELDS if k not in {"review_history", "media_cues"})
VALID_REVIEW = {"UNREVIEWED", "PASS", "NEED_EVIDENCE", "INCORRECT", "DISPUTED"}
VALID_MODALITY = {"actual", "planned", "commanded", "desired", "threatened", "hypothetical", "recalled", "imagined"}
VALID_CERTAINTY = {"F1_DIRECT", "F2_CORROBORATED", "I1_STRONG", "I2_PLAUSIBLE", "I3_SPECULATIVE", "X_INTERPRETATION"}
VALID_EVIDENCE_TYPE = {"SUB", "VIS", "AUD", "ASR", "OCR", "OFF", "INF"}
VALID_QUALITY = {"high", "medium", "low"}
VALID_IDENTITY = {"confirmed", "probable", "unknown"}
VALID_SPEAKER = {"in_frame", "off_screen", "voiceover", "unknown"}
VALID_BOUNDARY = {"certain", "probable", "uncertain"}
VALID_POLARITY = {"positive", "negative"}
VALID_RELATION_STATUS = {"confirmed", "inferred", "disputed"}
VALID_CAUSE = {"CAUSES", "ENABLES", "BLOCKS", "MOTIVATES", "REVEALS", "FOLLOWS", "POSSIBLE_CAUSE"}
VALID_THREAD_STATUS = {"OPEN", "HINTED", "PAID_OFF", "MISDIRECT", "ABANDONED"}
VALID_STORYLINE_STATUS = {"ACTIVE", "DORMANT", "RESOLVED"}
VALID_CONTEXT_REQUIRED = {"low", "medium", "high"}
HIGHLIGHT_DIMENSIONS = {"importance", "emotion", "conflict", "twist", "suspense", "visual", "standalone"}
VALID_CUE_TYPES = {"shot_change", "audio_peak", "audio_silence", "subtitle_emphasis", "subtitle_keyword", "dialogue_burst", "motion_candidate", "ocr_text", "speech_activity", "transcript_segment", "frame_sample", "unknown"}
VALID_ARC_ROLES = {"setup", "peak", "reaction", "payoff", "bridge"}
VALID_ARC_SEMANTIC_STATUS = {"NEEDS_CHATGPT_REVIEW", "HUMAN_CONFIRMED", "REJECTED"}
VALID_KNOWLEDGE_SUBJECT = {"CHARACTER", "VIEWER"}
VALID_KNOWLEDGE_STATE = {"KNOWS", "SUSPECTS", "BELIEVES", "FALSE_BELIEF", "UNAWARE", "FORGOT", "UNCERTAIN"}


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_dossier(episode_id: str = "E001", title: str = "", duration_sec: float = 0, source_version: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "episode": {
            "episode_id": episode_id,
            "title": title,
            "source_version": source_version,
            "duration_sec": duration_sec,
        },
        "evidence": [],
        "characters": [],
        "appearances": [],
        "scenes": [],
        "events": [],
        "relations": [],
        "causal_edges": [],
        "threads": [],
        "storylines": [],
        "highlights": [],
        "media_cues": [],
        "story_arcs": [],
        "knowledge_states": [],
        "character_states": [],
        "character_state_changes": [],
        "review_history": [],
    }


def load(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("档案根节点必须是 JSON 对象")
    return data


def save_atomic(data: dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def index_by_id(data: dict[str, Any], collection: str) -> dict[str, dict[str, Any]]:
    field = ID_FIELDS[collection]
    return {x.get(field): x for x in data.get(collection, []) if isinstance(x, dict) and x.get(field)}


def _append_enum_error(errors: list[str], value: Any, valid: set[str], label: str) -> None:
    if value not in valid:
        errors.append(f"{label} 无效: {value!r}")


def _refs(errors: list[str], obj: dict[str, Any], field: str, valid: set[str], label: str) -> None:
    vals = obj.get(field, [])
    if vals is None:
        return
    if not isinstance(vals, list):
        errors.append(f"{label}.{field} 必须为数组")
        return
    for value in vals:
        if value not in valid:
            errors.append(f"{label}.{field} 引用了不存在的 ID: {value}")


def _valid_time_pair(start: Any, end: Any) -> bool:
    return isinstance(start, (int, float)) and isinstance(end, (int, float)) and start >= 0 and end >= start


def validate(data: dict[str, Any]) -> list[str]:
    """Validate structure/references. This intentionally does not judge story truth."""
    errors: list[str] = []
    data.setdefault("knowledge_states", [])
    data.setdefault("character_states", [])
    data.setdefault("character_state_changes", [])
    if data.get("schema_version") not in COMPATIBLE_SCHEMA_VERSIONS:
        errors.append(f"不支持的 schema_version: {data.get('schema_version')!r}")
    for key in TOP_LEVEL:
        expected = dict if key == "episode" else list
        if key == "knowledge_states" and key not in data:
            continue
        if key not in data or not isinstance(data[key], expected):
            errors.append(f"顶层字段 {key} 缺失或类型错误")
    if errors:
        return errors

    ids: dict[str, set[str]] = {}
    object_ids: set[str] = set()
    for collection, field in ID_FIELDS.items():
        seen: set[str] = set()
        ids[collection] = set()
        for i, obj in enumerate(data[collection]):
            if not isinstance(obj, dict):
                errors.append(f"{collection}[{i}] 必须是对象")
                continue
            value = obj.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{collection}[{i}] 缺少 {field}")
                continue
            if value in seen:
                errors.append(f"{collection} 存在重复 ID: {value}")
            if collection != "review_history" and value in object_ids:
                errors.append(f"跨集合稳定 ID 冲突: {value}")
            seen.add(value)
            ids[collection].add(value)
            if collection != "review_history":
                object_ids.add(value)

    evidence_ids = ids["evidence"]
    character_ids = ids["characters"]
    scene_ids = ids["scenes"]
    event_ids = ids["events"]

    for collection in REVIEWABLE:
        for i, obj in enumerate(data[collection]):
            if not isinstance(obj, dict):
                continue
            _append_enum_error(errors, obj.get("review_status", "UNREVIEWED"), VALID_REVIEW, f"{collection}[{i}].review_status")

    for i, ev in enumerate(data["evidence"]):
        if not isinstance(ev, dict):
            continue
        if not _valid_time_pair(ev.get("start_sec"), ev.get("end_sec")):
            errors.append(f"evidence[{i}] 时间范围无效")
        _append_enum_error(errors, ev.get("type"), VALID_EVIDENCE_TYPE, f"evidence[{i}].type")
        _append_enum_error(errors, ev.get("quality"), VALID_QUALITY, f"evidence[{i}].quality")

    for i, char in enumerate(data["characters"]):
        if not isinstance(char, dict):
            continue
        _append_enum_error(errors, char.get("identity_status", "unknown"), VALID_IDENTITY, f"characters[{i}].identity_status")
        _refs(errors, char, "evidence_ids", evidence_ids, f"characters[{i}]")

    for i, app in enumerate(data["appearances"]):
        if not isinstance(app, dict):
            continue
        if app.get("character_id") not in character_ids:
            errors.append(f"appearances[{i}] character_id 不存在")
        if app.get("scene_id") not in scene_ids:
            errors.append(f"appearances[{i}] scene_id 不存在")
        if not _valid_time_pair(app.get("start_sec"), app.get("end_sec")):
            errors.append(f"appearances[{i}] 时间范围无效")
        _append_enum_error(errors, app.get("speaker_status", "unknown"), VALID_SPEAKER, f"appearances[{i}].speaker_status")
        _refs(errors, app, "evidence_ids", evidence_ids, f"appearances[{i}]")

    for i, scene in enumerate(data["scenes"]):
        if not isinstance(scene, dict):
            continue
        if not _valid_time_pair(scene.get("playback_start"), scene.get("playback_end")):
            errors.append(f"scenes[{i}] 时间范围无效")
        _append_enum_error(errors, scene.get("boundary_confidence", "uncertain"), VALID_BOUNDARY, f"scenes[{i}].boundary_confidence")
        _refs(errors, scene, "evidence_ids", evidence_ids, f"scenes[{i}]")

    for i, event in enumerate(data["events"]):
        if not isinstance(event, dict):
            continue
        if event.get("scene_id") is not None and event.get("scene_id") not in scene_ids:
            errors.append(f"events[{i}] scene_id 不存在")
        t = event.get("playback_time")
        if not isinstance(t, (int, float)) or t < 0:
            errors.append(f"events[{i}] playback_time 无效")
        for slot in ("actor", "patient", "recipient", "target"):
            cid = event.get(slot)
            if cid is not None and cid not in character_ids:
                errors.append(f"events[{i}].{slot} 人物不存在: {cid}")
        _append_enum_error(errors, event.get("modality"), VALID_MODALITY, f"events[{i}].modality")
        _append_enum_error(errors, event.get("polarity"), VALID_POLARITY, f"events[{i}].polarity")
        _append_enum_error(errors, event.get("certainty"), VALID_CERTAINTY, f"events[{i}].certainty")
        _refs(errors, event, "evidence_ids", evidence_ids, f"events[{i}]")

    for i, rel in enumerate(data["relations"]):
        if not isinstance(rel, dict):
            continue
        for slot in ("source_character", "target_character"):
            if rel.get(slot) not in character_ids:
                errors.append(f"relations[{i}].{slot} 人物不存在")
        for field in ("valid_from_event", "valid_to_event"):
            value = rel.get(field)
            if value is not None and value not in event_ids:
                errors.append(f"relations[{i}].{field} 事件不存在: {value}")
        _append_enum_error(errors, rel.get("status"), VALID_RELATION_STATUS, f"relations[{i}].status")
        _refs(errors, rel, "evidence_ids", evidence_ids, f"relations[{i}]")

    for i, edge in enumerate(data["causal_edges"]):
        if not isinstance(edge, dict):
            continue
        if edge.get("from_event") not in event_ids or edge.get("to_event") not in event_ids:
            errors.append(f"causal_edges[{i}] 引用了不存在的事件")
        if edge.get("from_event") == edge.get("to_event"):
            errors.append(f"causal_edges[{i}] 不允许事件自因果")
        _append_enum_error(errors, edge.get("type"), VALID_CAUSE, f"causal_edges[{i}].type")
        _refs(errors, edge, "evidence_ids", evidence_ids, f"causal_edges[{i}]")
        _refs(errors, edge, "counter_evidence_ids", evidence_ids, f"causal_edges[{i}]")

    for i, thread in enumerate(data["threads"]):
        if not isinstance(thread, dict):
            continue
        if thread.get("first_event") not in event_ids:
            errors.append(f"threads[{i}] first_event 不存在")
        _append_enum_error(errors, thread.get("status"), VALID_THREAD_STATUS, f"threads[{i}].status")
        _refs(errors, thread, "evidence_ids", evidence_ids, f"threads[{i}]")
        _refs(errors, thread, "resolution_evidence_ids", evidence_ids, f"threads[{i}]")

    storyline_ids = ids["storylines"]
    story_arc_ids = ids["story_arcs"]
    for i, line in enumerate(data["storylines"]):
        if not isinstance(line, dict):
            continue
        _append_enum_error(errors, line.get("status", "ACTIVE"), VALID_STORYLINE_STATUS, f"storylines[{i}].status")
        _refs(errors, line, "character_ids", character_ids, f"storylines[{i}]")
        _refs(errors, line, "event_ids", event_ids, f"storylines[{i}]")
        _refs(errors, line, "evidence_ids", evidence_ids, f"storylines[{i}]")

    for i, clip in enumerate(data["highlights"]):
        if not isinstance(clip, dict):
            continue
        if not _valid_time_pair(clip.get("start_sec"), clip.get("end_sec")):
            errors.append(f"highlights[{i}] 时间范围无效")
        _refs(errors, clip, "event_ids", event_ids, f"highlights[{i}]")
        _refs(errors, clip, "storyline_ids", storyline_ids, f"highlights[{i}]")
        _refs(errors, clip, "story_arc_ids", story_arc_ids, f"highlights[{i}]")
        if clip.get("source_arc_id") is not None and clip.get("source_arc_id") not in story_arc_ids:
            errors.append(f"highlights[{i}].source_arc_id 不存在: {clip.get('source_arc_id')}")
        _refs(errors, clip, "evidence_ids", evidence_ids, f"highlights[{i}]")
        _append_enum_error(errors, clip.get("context_required", "medium"), VALID_CONTEXT_REQUIRED, f"highlights[{i}].context_required")
        dims = clip.get("dimensions", {})
        if not isinstance(dims, dict):
            errors.append(f"highlights[{i}].dimensions 必须为对象")
        else:
            for key, value in dims.items():
                if key not in HIGHLIGHT_DIMENSIONS:
                    errors.append(f"highlights[{i}].dimensions 未知维度: {key}")
                if not isinstance(value, (int, float)) or not 0 <= float(value) <= 100:
                    errors.append(f"highlights[{i}].dimensions.{key} 必须在 0-100")
        score = clip.get("total_score")
        if score is not None and (not isinstance(score, (int, float)) or not 0 <= float(score) <= 100):
            errors.append(f"highlights[{i}].total_score 必须在 0-100")

    cue_ids = ids["media_cues"]
    for i, cue in enumerate(data["media_cues"]):
        if not isinstance(cue, dict):
            continue
        if not _valid_time_pair(cue.get("start_sec"), cue.get("end_sec")):
            errors.append(f"media_cues[{i}] 时间范围无效")
        if cue.get("cue_type") not in VALID_CUE_TYPES:
            errors.append(f"media_cues[{i}].cue_type 无效: {cue.get('cue_type')!r}")
        score = cue.get("score")
        if score is not None and (not isinstance(score, (int, float)) or not 0 <= float(score) <= 1):
            errors.append(f"media_cues[{i}].score 必须在 0-1")

    for i, arc in enumerate(data["story_arcs"]):
        if not isinstance(arc, dict):
            continue
        if not _valid_time_pair(arc.get("start_sec"), arc.get("end_sec")):
            errors.append(f"story_arcs[{i}] 时间范围无效")
        _refs(errors, arc, "event_ids", event_ids, f"story_arcs[{i}]")
        _refs(errors, arc, "storyline_ids", storyline_ids, f"story_arcs[{i}]")
        _refs(errors, arc, "evidence_ids", evidence_ids, f"story_arcs[{i}]")
        _refs(errors, arc, "cue_ids", cue_ids, f"story_arcs[{i}]")
        if arc.get("anchor_event_id") is not None and arc.get("anchor_event_id") not in event_ids:
            errors.append(f"story_arcs[{i}].anchor_event_id 不存在: {arc.get('anchor_event_id')}")
        if arc.get("review_status", "UNREVIEWED") not in VALID_REVIEW:
            errors.append(f"story_arcs[{i}].review_status 无效: {arc.get('review_status')!r}")
        if arc.get("semantic_status") is not None and arc.get("semantic_status") not in VALID_ARC_SEMANTIC_STATUS:
            errors.append(f"story_arcs[{i}].semantic_status 无效: {arc.get('semantic_status')!r}")
        anchor = arc.get("anchor_sec")
        arc_start, arc_end = arc.get("start_sec"), arc.get("end_sec")
        if anchor is not None:
            if not all(isinstance(v, (int, float)) for v in (anchor, arc_start, arc_end)):
                errors.append(f"story_arcs[{i}].anchor_sec/start_sec/end_sec 必须是数字")
            elif anchor < arc_start - 0.001 or anchor > arc_end + 0.001:
                errors.append(f"story_arcs[{i}].anchor_sec 必须位于弧线时间范围内")
        if not isinstance(arc.get("segments", []), list):
            errors.append(f"story_arcs[{i}].segments 必须为数组")
        for role_key in ("missing_roles", "risk_flags", "semantic_questions"):
            if role_key in arc and not isinstance(arc[role_key], list):
                errors.append(f"story_arcs[{i}].{role_key} 必须为数组")
        for j, segment in enumerate(arc.get("segments", []) or []):
            if not isinstance(segment, dict):
                errors.append(f"story_arcs[{i}].segments[{j}] 必须为对象")
                continue
            if not _valid_time_pair(segment.get("start_sec"), segment.get("end_sec")):
                errors.append(f"story_arcs[{i}].segments[{j}] 时间范围无效")
            if segment.get("role") not in VALID_ARC_ROLES:
                errors.append(f"story_arcs[{i}].segments[{j}].role 无效: {segment.get('role')!r}")
            if all(isinstance(v, (int, float)) for v in (segment.get("start_sec"), segment.get("end_sec"), arc_start, arc_end)):
                if segment["start_sec"] < arc_start - 0.001 or segment["end_sec"] > arc_end + 0.001:
                    errors.append(f"story_arcs[{i}].segments[{j}] 超出弧线整体时间范围")
            _refs(errors, segment, "event_ids", event_ids, f"story_arcs[{i}].segments[{j}]")
            _refs(errors, segment, "cue_ids", cue_ids, f"story_arcs[{i}].segments[{j}]")
        dims = arc.get("dimensions", {})
        if not isinstance(dims, dict):
            errors.append(f"story_arcs[{i}].dimensions 必须为对象")
        else:
            for key, value in dims.items():
                if key not in HIGHLIGHT_DIMENSIONS:
                    errors.append(f"story_arcs[{i}].dimensions 未知维度: {key}")
                if not isinstance(value, (int, float)) or not 0 <= float(value) <= 100:
                    errors.append(f"story_arcs[{i}].dimensions.{key} 必须在 0-100")
        _append_enum_error(errors, arc.get("context_required", "medium"), VALID_CONTEXT_REQUIRED, f"story_arcs[{i}].context_required")
        score = arc.get("total_score")
        if score is not None and (not isinstance(score, (int, float)) or not 0 <= float(score) <= 100):
            errors.append(f"story_arcs[{i}].total_score 必须在 0-100")

    for i, state in enumerate(data.get("knowledge_states", [])):
        if not isinstance(state, dict):
            errors.append(f"knowledge_states[{i}] 必须为对象")
            continue
        _append_enum_error(errors, state.get("subject_type"), VALID_KNOWLEDGE_SUBJECT, f"knowledge_states[{i}].subject_type")
        _append_enum_error(errors, state.get("state"), VALID_KNOWLEDGE_STATE, f"knowledge_states[{i}].state")
        if not isinstance(state.get("proposition"), str) or not state.get("proposition", "").strip():
            errors.append(f"knowledge_states[{i}].proposition 不能为空")
        if not isinstance(state.get("playback_time"), (int, float)) or state.get("playback_time", -1) < 0:
            errors.append(f"knowledge_states[{i}].playback_time 无效")
        if state.get("subject_type") == "CHARACTER" and state.get("character_id") not in character_ids:
            errors.append(f"knowledge_states[{i}].character_id 人物不存在")
        if state.get("subject_type") == "VIEWER" and state.get("character_id") is not None:
            errors.append(f"knowledge_states[{i}] VIEWER 的 character_id 必须为空")
        if state.get("event_id") is not None and state.get("event_id") not in event_ids:
            errors.append(f"knowledge_states[{i}].event_id 事件不存在")
        _refs(errors, state, "evidence_ids", evidence_ids, f"knowledge_states[{i}]")
        _append_enum_error(errors, state.get("review_status", "UNREVIEWED"), VALID_REVIEW, f"knowledge_states[{i}].review_status")

    seen_review_ids: set[str] = set()
    for i, rev in enumerate(data["review_history"]):
        if not isinstance(rev, dict):
            continue
        rid = rev.get("review_id")
        if rid in seen_review_ids:
            errors.append(f"review_history 存在重复 ID: {rid}")
        if rid:
            seen_review_ids.add(rid)
        if rev.get("object_id") not in object_ids:
            errors.append(f"review_history[{i}] object_id 不存在: {rev.get('object_id')}")
        _append_enum_error(errors, rev.get("status"), VALID_REVIEW - {"UNREVIEWED"}, f"review_history[{i}].status")
        _refs(errors, rev, "evidence_ids", evidence_ids, f"review_history[{i}]")
        version = rev.get("version")
        if not isinstance(version, int) or version < 1:
            errors.append(f"review_history[{i}] version 无效")

    from character_state_model import validation_errors
    errors.extend(validation_errors(data))
    return errors


def _locate_object(data: dict[str, Any], object_id: str) -> tuple[str, dict[str, Any]]:
    for collection in REVIEWABLE:
        field = ID_FIELDS[collection]
        for obj in data.get(collection, []):
            if isinstance(obj, dict) and obj.get(field) == object_id:
                return collection, obj
    raise KeyError(f"找不到审核对象: {object_id}")


def add_review(
    data: dict[str, Any],
    object_id: str,
    status: str,
    reason: str,
    evidence_ids: Iterable[str] | None = None,
    new_value: dict[str, Any] | None = None,
    reviewer: str = "",
    source_checked: bool = False,
) -> dict[str, Any]:
    if status not in VALID_REVIEW - {"UNREVIEWED"}:
        raise ValueError(f"无效审核状态: {status}")
    if not reason.strip():
        raise ValueError("审核理由不能为空")
    collection, obj = _locate_object(data, object_id)
    if collection in {"character_states", "character_state_changes"} and not reviewer.strip():
        raise ValueError("人物状态审核必须填写审核者")
    if collection in {"character_states", "character_state_changes"} and status == "PASS" and not source_checked:
        raise ValueError("人物状态 PASS 必须确认已核对连续原片")
    id_field = ID_FIELDS[collection]
    old_value = copy.deepcopy(obj)
    if new_value is not None:
        if not isinstance(new_value, dict):
            raise ValueError("new_value 必须是对象")
        if id_field in new_value and new_value[id_field] != object_id:
            raise ValueError("审核修订不得更改对象稳定 ID")
        obj.update(copy.deepcopy(new_value))
    obj["review_status"] = status
    if collection == "story_arcs":
        if status == "PASS":
            obj["semantic_status"] = "HUMAN_CONFIRMED"
        elif status == "INCORRECT":
            obj["semantic_status"] = "REJECTED"
    ev_ids = list(evidence_ids or obj.get("evidence_ids") or [])
    previous = [r for r in data["review_history"] if r.get("object_id") == object_id]
    record = {
        "review_id": uid("REV"),
        "object_id": object_id,
        "collection": collection,
        "status": status,
        "old_value": old_value,
        "new_value": copy.deepcopy(obj),
        "reason": reason,
        "evidence_ids": ev_ids,
        "reviewer": reviewer,
        "timestamp": now(),
        "version": len(previous) + 1,
    }
    if collection in {"character_states", "character_state_changes"}:
        from character_state_model import context_snapshot
        record["context_snapshot"] = context_snapshot(data, obj, collection)
        record["source_checked"] = bool(source_checked)
    data["review_history"].append(record)
    return record


def unresolved(data: dict[str, Any]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for collection in REVIEWABLE:
        field = ID_FIELDS[collection]
        for obj in data.get(collection, []):
            status = obj.get("review_status", "UNREVIEWED")
            if status in {"UNREVIEWED", "NEED_EVIDENCE", "DISPUTED"}:
                out.append((collection, obj.get(field, "?"), status))
    return out


def migrate_to_v34(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate v3.x dossiers to v3.4 without inventing semantic conclusions."""
    migrated = copy.deepcopy(data)
    for key in TOP_LEVEL:
        if key == "episode":
            migrated.setdefault(key, {})
        else:
            migrated.setdefault(key, [])
    for collection in REVIEWABLE:
        for obj in migrated.get(collection, []):
            if isinstance(obj, dict):
                obj.setdefault("review_status", "UNREVIEWED")
    for thread in migrated.get("threads", []):
        if isinstance(thread, dict):
            thread.setdefault("evidence_ids", [])
            thread.setdefault("resolution_evidence_ids", [])
    for line in migrated.get("storylines", []):
        if isinstance(line, dict):
            line.setdefault("character_ids", [])
            line.setdefault("event_ids", [])
            line.setdefault("evidence_ids", [])
            line.setdefault("status", "ACTIVE")
    for clip in migrated.get("highlights", []):
        if isinstance(clip, dict):
            clip.setdefault("event_ids", [])
            clip.setdefault("storyline_ids", [])
            clip.setdefault("evidence_ids", [])
            clip.setdefault("dimensions", {})
            clip.setdefault("context_required", "medium")
    for cue in migrated.get("media_cues", []):
        if isinstance(cue, dict):
            cue.setdefault("details", {})
            cue.setdefault("source", "unknown")
    migrated.setdefault("knowledge_states", [])
    for state in migrated.get("knowledge_states", []):
        if isinstance(state, dict):
            state.setdefault("review_status", "UNREVIEWED")
            state.setdefault("evidence_ids", [])
    for arc in migrated.get("story_arcs", []):
        if isinstance(arc, dict):
            arc.setdefault("segments", [])
            arc.setdefault("event_ids", [])
            arc.setdefault("storyline_ids", [])
            arc.setdefault("evidence_ids", [])
            arc.setdefault("cue_ids", [])
            arc.setdefault("dimensions", {})
            arc.setdefault("context_required", "high")
            arc.setdefault("review_status", "UNREVIEWED")
    migrated["schema_version"] = SCHEMA_VERSION
    return migrated


# Backward-compatible helper names for older callers.
migrate_to_v35 = migrate_to_v34
migrate_to_v33 = migrate_to_v34
migrate_to_v32 = migrate_to_v34


def import_scene_candidates(data: dict[str, Any], candidates: dict[str, Any]) -> int:
    segments = candidates.get("segments") or candidates.get("scenes") or []
    if not isinstance(segments, list):
        raise ValueError("候选文件 segments/scenes 必须为数组")
    existing = {
        (round(float(s.get("playback_start", -1)), 3), round(float(s.get("playback_end", -1)), 3))
        for s in data.get("scenes", []) if isinstance(s, dict)
    }
    count = 0
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start = seg.get("start_sec", seg.get("playback_start"))
        end = seg.get("end_sec", seg.get("playback_end"))
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or start < 0 or end < start:
            continue
        key = (round(float(start), 3), round(float(end), 3))
        if key in existing:
            continue
        data["scenes"].append({
            "scene_id": uid("SCN"),
            "playback_start": float(start),
            "playback_end": float(end),
            "story_time": "",
            "location": "待确认",
            "boundary_confidence": "uncertain",
            "evidence_ids": [],
            "review_status": "UNREVIEWED",
            "candidate_method": seg.get("method") or candidates.get("method") or "imported_scene_candidate",
            "candidate_score": seg.get("score"),
        })
        existing.add(key)
        count += 1
    return count


def markdown_report(data: dict[str, Any]) -> str:
    episode = data.get("episode", {})
    lines = [
        f"# {episode.get('title') or episode.get('episode_id', '剧情档案')}",
        f"\n> Schema: {data.get('schema_version')} | 事实源：结构化 JSON\n",
        "## 人物",
        "| ID | 名称 | 别名 | 审核 |",
        "|---|---|---|---|",
    ]
    for c in data.get("characters", []):
        lines.append(f"| {c.get('character_id','')} | {c.get('canonical_name','')} | {', '.join(c.get('aliases',[]))} | {c.get('review_status','UNREVIEWED')} |")
    lines += [
        "\n## 事件",
        "| ID | 时间 | 施事 | 动作 | 受事 | 模态 | 等级 | 审核 |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    chars = index_by_id(data, "characters")
    for e in data.get("events", []):
        name = lambda k: chars.get(e.get(k), {}).get("canonical_name", e.get(k) or "—")
        lines.append(
            f"| {e.get('event_id','')} | {e.get('playback_time','')} | {name('actor')} | {e.get('action','')} | "
            f"{name('patient')} | {e.get('modality','')} | {e.get('certainty','')} | {e.get('review_status','UNREVIEWED')} |"
        )
    lines += ["\n## 因果边"]
    for edge in data.get("causal_edges", []):
        lines.append(
            f"- {edge.get('from_event')} → {edge.get('to_event')}：{edge.get('type')}"
            f"（{edge.get('review_status','UNREVIEWED')}）{edge.get('reasoning','')}"
        )
    lines += ["\n## 多模态剧情弧线候选"]
    if data.get("story_arcs"):
        for arc in sorted(data.get("story_arcs", []), key=lambda x: x.get("start_sec", 0)):
            roles = " → ".join(seg.get("role", "?") for seg in arc.get("segments", [])) or "尚无段落"
            missing = ", ".join(arc.get("missing_roles", [])) or "无"
            lines.append(
                f"- {arc.get('arc_id')} {arc.get('start_sec', 0):.1f}-{arc.get('end_sec', 0):.1f}s "
                f"{arc.get('title', '')} · {roles} · 缺项：{missing} · "
                f"{arc.get('review_status', 'UNREVIEWED')}"
            )
    else:
        lines.append("- 无")
    lines += ["\n## 精彩片段"]
    if data.get("highlights"):
        for clip in sorted(data.get("highlights", []), key=lambda x: x.get("start_sec", 0)):
            lines.append(
                f"- {clip.get('highlight_id')} {clip.get('start_sec', 0):.1f}-{clip.get('end_sec', 0):.1f}s "
                f"{clip.get('title','')} · {clip.get('total_score','—')} · {clip.get('review_status','UNREVIEWED')}"
            )
    else:
        lines.append("- 无")
    lines += ["\n## 未解决审核项"]
    todo = unresolved(data)
    lines += [f"- {collection}/{oid}: {status}" for collection, oid, status in todo] or ["- 无"]
    return "\n".join(lines) + "\n"


def cmd_init(a: argparse.Namespace) -> None:
    data = empty_dossier(a.episode_id, a.title, a.duration, a.source_version)
    save_atomic(data, a.output)
    print(f"已创建: {a.output}")


def cmd_validate(a: argparse.Namespace) -> None:
    data = load(a.input)
    errors = validate(data)
    if errors:
        print("校验失败:")
        for error in errors:
            print("- " + error)
        raise SystemExit(1)
    print(
        f"校验通过：{len(data['evidence'])} 条证据，{len(data['characters'])} 个人物，"
        f"{len(data['scenes'])} 个场景，{len(data['events'])} 个事件"
    )
    if a.strict:
        todo = unresolved(data)
        if todo:
            print(f"严格模式失败：还有 {len(todo)} 个未完成审核对象")
            for collection, oid, status in todo[:25]:
                print(f"- {collection}/{oid}: {status}")
            if len(todo) > 25:
                print(f"- ... 其余 {len(todo)-25} 项")
            raise SystemExit(2)


def cmd_review(a: argparse.Namespace) -> None:
    data = load(a.input)
    new_value = json.loads(a.new_value) if a.new_value else None
    add_review(data, a.object_id, a.status, a.reason, a.evidence_id, new_value, a.reviewer, a.source_checked)
    errors = validate(data)
    if errors:
        raise SystemExit("审核后档案校验失败:\n" + "\n".join(errors))
    save_atomic(data, a.output or a.input)
    print(f"审核已写入: {a.output or a.input}")


def cmd_report(a: argparse.Namespace) -> None:
    data = load(a.input)
    errors = validate(data)
    if errors:
        raise SystemExit("档案校验失败:\n" + "\n".join(errors))
    Path(a.output).write_text(markdown_report(data), encoding="utf-8")
    print(f"报告已生成: {a.output}")


def cmd_migrate(a: argparse.Namespace) -> None:
    data = migrate_to_v34(load(a.input))
    errors = validate(data)
    if errors:
        raise SystemExit("迁移后仍有结构问题:\n" + "\n".join(errors))
    save_atomic(data, a.output)
    print(f"已迁移到 {SCHEMA_VERSION}: {a.output}")


def cmd_import_scenes(a: argparse.Namespace) -> None:
    data = migrate_to_v34(load(a.input))
    candidates = load(a.candidates)
    count = import_scene_candidates(data, candidates)
    errors = validate(data)
    if errors:
        raise SystemExit("导入场景候选后校验失败:\n" + "\n".join(errors))
    save_atomic(data, a.output or a.input)
    print(f"已导入 {count} 个场景候选: {a.output or a.input}")


def cmd_queue(a: argparse.Namespace) -> None:
    data = load(a.input)
    todo = unresolved(data)
    if a.json:
        print(json.dumps([{"collection": c, "object_id": oid, "status": s} for c, oid, s in todo], ensure_ascii=False, indent=2))
    else:
        if not todo:
            print("没有未完成审核对象")
            return
        for collection, oid, status in todo:
            print(f"{collection}\t{oid}\t{status}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="剧情理解档案 v3.4 - 证据优先的剧情理解与精彩剪辑档案引擎")
    sub = p.add_subparsers(dest="cmd", required=True)

    x = sub.add_parser("init", help="创建空白单集档案")
    x.add_argument("output")
    x.add_argument("--episode-id", default="E001")
    x.add_argument("--title", default="")
    x.add_argument("--duration", type=float, default=0)
    x.add_argument("--source-version", default="")
    x.set_defaults(func=cmd_init)

    x = sub.add_parser("validate", help="校验结构与引用完整性")
    x.add_argument("input")
    x.add_argument("--strict", action="store_true")
    x.set_defaults(func=cmd_validate)

    x = sub.add_parser("review", help="按稳定 ID 写入审核记录")
    x.add_argument("input")
    x.add_argument("object_id")
    x.add_argument("status", choices=sorted(VALID_REVIEW - {"UNREVIEWED"}))
    x.add_argument("--reason", required=True)
    x.add_argument("--evidence-id", action="append", default=[])
    x.add_argument("--new-value", help="JSON 对象字符串")
    x.add_argument("--reviewer", default="")
    x.add_argument("--source-checked", action="store_true", help="人物状态 PASS 必须确认已核对连续原片")
    x.add_argument("--output")
    x.set_defaults(func=cmd_review)

    x = sub.add_parser("report", help="从 JSON 生成 Markdown 视图")
    x.add_argument("input")
    x.add_argument("output")
    x.set_defaults(func=cmd_report)

    x = sub.add_parser("migrate", help="把 v3.x 档案迁移到 v3.4 结构")
    x.add_argument("input")
    x.add_argument("output")
    x.set_defaults(func=cmd_migrate)

    x = sub.add_parser("import-scenes", help="导入镜头/场景边界候选为待审核场景")
    x.add_argument("input")
    x.add_argument("candidates")
    x.add_argument("--output")
    x.set_defaults(func=cmd_import_scenes)

    x = sub.add_parser("queue", help="列出未完成审核对象")
    x.add_argument("input")
    x.add_argument("--json", action="store_true")
    x.set_defaults(func=cmd_queue)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)

