#!/usr/bin/env python3
"""Extract time-aligned audiovisual candidates and review-ready keyframes.

This tool extracts measurable signals only. It does not identify people, actions,
emotions, motives, semantic scenes, or plot twists. Semantic interpretation is
performed later by ChatGPT/human review against the original clips.
"""
from __future__ import annotations

import argparse
import array
import html
import json
import math
import re
import shutil
import statistics
import subprocess
import uuid
from pathlib import Path
from typing import Any

KEYWORDS = {
    "reveal": ["真相", "原来", "身份", "其实", "秘密", "揭穿", "发现", "竟然", "没想到", "truth", "reveal", "secret"],
    "conflict": ["杀", "打", "滚", "闭嘴", "威胁", "命令", "背叛", "不许", "休想", "反对", "fight", "kill", "threat", "betray"],
    "emotion": ["对不起", "我爱你", "别走", "再见", "恨你", "哭", "救命", "求你", "我错了", "love", "sorry", "goodbye"],
    "suspense": ["谁", "为什么", "到底", "失踪", "跟踪", "小心", "危险", "线索", "证据", "where", "why", "missing", "danger"],
}


def tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"缺少 {name}；请安装 FFmpeg 并确保其位于 PATH")
    return found


def run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr.decode("utf-8", "replace")[-3000:])
    return p


def probe_media(video: Path) -> dict[str, Any]:
    raw = run([tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)])
    info = json.loads(raw.stdout.decode("utf-8"))
    streams = info.get("streams", [])
    v = next((x for x in streams if x.get("codec_type") == "video"), {})
    a = next((x for x in streams if x.get("codec_type") == "audio"), {})
    duration = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    fps_text = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"
    try:
        n, d = fps_text.split("/")
        fps = float(n) / max(1.0, float(d))
    except Exception:
        fps = 0.0
    return {
        "filename": video.name,
        "duration_sec": round(duration, 3),
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": round(fps, 3),
        "video_codec": v.get("codec_name"),
        "audio_present": bool(a),
        "audio_codec": a.get("codec_name"),
        "audio_channels": a.get("channels"),
        "audio_sample_rate": a.get("sample_rate"),
    }


def parse_timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        h, m, sec = 0, int(parts[0]), float(parts[1])
    elif len(parts) == 3:
        h, m, sec = int(parts[0]), int(parts[1]), float(parts[2])
    else:
        raise ValueError(f"无法解析时间码: {value}")
    return h * 3600 + m * 60 + sec


def load_subtitles(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("segments", payload.get("cues", []))
        out = []
        for row in payload if isinstance(payload, list) else []:
            try:
                out.append({"start_sec": float(row.get("start", row.get("start_sec"))),
                            "end_sec": float(row.get("end", row.get("end_sec"))),
                            "text": str(row.get("text", "")).strip()})
            except (TypeError, ValueError):
                continue
        return [x for x in out if x["start_sec"] >= 0 and x["end_sec"] >= x["start_sec"] and x["text"]]

    rows: list[dict[str, Any]] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    for block in blocks:
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        stamp_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if stamp_index is None:
            continue
        pair = lines[stamp_index].split("-->", 1)
        try:
            start = parse_timestamp(pair[0])
            end = parse_timestamp(pair[1].split()[0])
        except Exception:
            continue
        body = " ".join(lines[stamp_index + 1:]).strip()
        body = re.sub(r"<[^>]+>", "", body)
        body = re.sub(r"\{\\an\d+\}", "", body).strip()
        if body and start >= 0 and end >= start:
            rows.append({"start_sec": start, "end_sec": end, "text": body})
    return rows


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def subtitle_cues(rows: list[dict[str, Any]], source: str = "subtitle") -> list[dict[str, Any]]:
    cues = []
    for row in rows:
        text = row["text"]
        start, end = round(row["start_sec"], 3), round(row["end_sec"], 3)
        # Preserve every timed transcript line as a low-confidence alignment cue.
        # It is excluded from peak-anchor generation so ordinary dialogue does not
        # create hundreds of false highlight candidates.
        cues.append({"cue_id": uid("CUE"), "cue_type": "transcript_segment",
                     "start_sec": start, "end_sec": end, "score": 0.12,
                     "source": source, "confidence": "low",
                     "details": {"text": text, "warning": "转录/字幕时间段；不自动确认说话人或台词真实性"}})
        tags = [tag for tag, words in KEYWORDS.items() if any(w.lower() in text.lower() for w in words)]
        punctuation = sum(text.count(x) for x in "!?！？…")
        if tags or punctuation or len(text) >= 24:
            typ = "subtitle_keyword" if tags else "subtitle_emphasis" if punctuation else "dialogue_burst"
            score = min(1.0, 0.38 + 0.13 * len(tags) + 0.08 * min(punctuation, 3) + 0.0015 * min(len(text), 80))
            cues.append({"cue_id": uid("CUE"), "cue_type": typ,
                         "start_sec": start, "end_sec": end,
                         "score": round(score, 3), "source": source, "confidence": "low",
                         "details": {"text": text, "matched_tags": tags, "punctuation_count": punctuation,
                                     "warning": "关键词/标点只用于检索，不代表情绪或剧情意义"}})
    return cues


def shot_cues(video: Path, threshold: float) -> list[dict[str, Any]]:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scene_candidates import detect_cuts
    cuts = detect_cuts(str(video), threshold)
    return [{"cue_id": uid("CUE"), "cue_type": "shot_change",
             "start_sec": round(x["time_sec"], 3), "end_sec": round(x["time_sec"], 3),
             "score": round(min(1.0, float(x["score"])), 3), "source": "ffmpeg_scene_score",
             "confidence": "low", "details": {"scene_score": x["score"],
             "warning": "镜头切换候选，不等于语义场景边界"}} for x in cuts]


def audio_cues(video: Path, window: float, max_cues: int = 120) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return low-confidence RMS peaks and silence windows; never label emotions."""
    info = probe_media(video)
    if not info["audio_present"]:
        return [], {"available": False, "reason": "no_audio_stream"}
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-i", str(video),
           "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"]
    try:
        raw = run(cmd, timeout=1800).stdout
    except Exception as exc:
        return [], {"available": False, "reason": str(exc)}
    samples = array.array("h")
    samples.frombytes(raw[:len(raw) - (len(raw) % 2)])
    if not samples:
        return [], {"available": False, "reason": "empty_audio_decode"}
    rate = 8000
    size = max(1, int(rate * window))
    rms_rows: list[tuple[float, float]] = []
    for i in range(0, len(samples), size):
        chunk = samples[i:i + size]
        if not chunk:
            continue
        rms = math.sqrt(sum((int(x) / 32768.0) ** 2 for x in chunk) / len(chunk))
        rms_rows.append((i / rate, rms))
    values = [x[1] for x in rms_rows]
    p50 = statistics.median(values)
    sorted_values = sorted(values)
    p85 = sorted_values[min(len(sorted_values) - 1, int((len(sorted_values) - 1) * .85))]
    peak_threshold = max(.035, p85 * 1.25, p50 * 2.0)
    silence_threshold = max(.004, min(.012, p50 * .22))

    peaks: list[tuple[float, float]] = []
    for i, (t, value) in enumerate(rms_rows):
        left = rms_rows[i - 1][1] if i else -1
        right = rms_rows[i + 1][1] if i + 1 < len(rms_rows) else -1
        if value >= peak_threshold and value >= left and value >= right:
            peaks.append((t, value))
    chosen: list[tuple[float, float]] = []
    for t, value in sorted(peaks, key=lambda x: x[1], reverse=True):
        if all(abs(t - old_t) >= 1.5 for old_t, _ in chosen):
            chosen.append((t, value))
        if len(chosen) >= max_cues:
            break
    peak_max = max(values) or 1.0
    cues = [{"cue_id": uid("CUE"), "cue_type": "audio_peak",
             "start_sec": round(t, 3), "end_sec": round(min(info["duration_sec"], t + window), 3),
             "score": round(min(1.0, value / peak_max), 3), "source": "ffmpeg_pcm_rms",
             "confidence": "low", "details": {"rms": round(value, 6), "threshold": round(peak_threshold, 6),
             "window_sec": window, "warning": "音量峰值可能来自音乐/音效，不等于情绪或剧情高潮"}}
            for t, value in chosen]

    # Group consecutive low-energy windows; only report sustained silence candidates.
    silence_runs: list[list[tuple[float, float]]] = []
    for row in rms_rows:
        if row[1] <= silence_threshold:
            if not silence_runs or row[0] - silence_runs[-1][-1][0] > window * 1.6:
                silence_runs.append([row])
            else:
                silence_runs[-1].append(row)
        else:
            if silence_runs and silence_runs[-1] and row[0] - silence_runs[-1][-1][0] <= window * 1.6:
                continue
    silence_count = 0
    for run_rows in silence_runs:
        start = run_rows[0][0]
        end = run_rows[-1][0] + window
        if end - start >= max(1.5, window * 3):
            cues.append({"cue_id": uid("CUE"), "cue_type": "audio_silence",
                         "start_sec": round(start, 3), "end_sec": round(min(info["duration_sec"], end), 3),
                         "score": round(min(1.0, (end - start) / 8.0), 3), "source": "ffmpeg_pcm_rms",
                         "confidence": "low", "details": {"rms_threshold": round(silence_threshold, 6),
                         "warning": "低能量区间可能是静音、弱对白或低音量环境声；不能推断沉默的心理含义"}})
            silence_count += 1

    return cues, {"available": True, "sample_rate": rate, "window_sec": window,
                  "rms_median": round(p50, 6), "rms_p85": round(p85, 6),
                  "peak_threshold": round(peak_threshold, 6), "silence_threshold": round(silence_threshold, 6),
                  "peak_count": len(chosen), "silence_window_count": silence_count}


def sample_times(duration: float, interval: float) -> list[float]:
    if duration <= 0 or interval <= 0:
        return []
    times = []
    t = 0.0
    while t < duration:
        times.append(round(t, 3))
        t += interval
    if not times or duration - times[-1] > interval * .35:
        times.append(round(max(0.0, duration - .05), 3))
    return list(dict.fromkeys(times))


def extract_frames(video: Path, cues: list[dict[str, Any]], frames_dir: Path, max_frames: int,
                   sample_interval: float = 10.0) -> list[dict[str, Any]]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    cue_candidates: list[tuple[float, float, str, list[str]]] = []
    for cue in cues:
        if cue.get("cue_type") == "audio_silence":
            continue
        t = (float(cue["start_sec"]) + float(cue["end_sec"])) / 2
        cue_candidates.append((float(cue.get("score") or 0) + .15, t, "cue", [cue.get("cue_id", "")]))
    uniform_all = sample_times(probe_media(video)["duration_sec"], sample_interval)
    # Reserve up to half the frame budget for whole-episode coverage. If a long
    # episode has thousands of interval samples, downsample evenly across it.
    uniform_slots = min(len(uniform_all), max(1, max_frames // 2))
    if uniform_slots and len(uniform_all) > uniform_slots:
        indices = [round(i * (len(uniform_all) - 1) / max(1, uniform_slots - 1)) for i in range(uniform_slots)]
        uniform_times = [uniform_all[i] for i in dict.fromkeys(indices)]
    else:
        uniform_times = uniform_all
    selected: list[tuple[float, str, list[str]]] = []
    for t in uniform_times:
        if all(abs(t - old[0]) >= 1.25 for old in selected):
            selected.append((t, "uniform_sample", []))
    cue_candidates.sort(key=lambda x: (-x[0], x[1]))
    for _, t, typ, cue_ids in cue_candidates:
        if len(selected) >= max_frames:
            break
        if all(abs(t - old[0]) >= 1.25 for old in selected):
            selected.append((t, typ, cue_ids))
    # Fill any remaining slots with unused uniform/cue candidates.
    if len(selected) < max_frames:
        for t in uniform_all:
            if all(abs(t - old[0]) >= 1.25 for old in selected):
                selected.append((t, "uniform_sample", []))
            if len(selected) >= max_frames:
                break
    selected.sort(key=lambda x: x[0])
    manifest = []
    for t, typ, cue_ids in selected:
        t = max(0.0, t)
        name = f"frame_{t:09.3f}_{typ}.jpg"
        path = frames_dir / name
        run([tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-v", "error", "-ss", f"{t:.3f}",
             "-i", str(video), "-frames:v", "1", "-vf", "scale=min(960\\,iw):-2,format=yuvj420p", "-threads", "1", "-q:v", "3", str(path)])
        manifest.append({"frame_id": uid("FRM"), "time_sec": round(t, 3), "path": str(path),
                         "frame_type": typ, "cue_ids": cue_ids})
        for cue in cues:
            if cue.get("cue_id") in cue_ids:
                cue["frame_path"] = str(path)
    return manifest


def write_contact_sheets(frames: list[dict[str, Any]], output_dir: Path, columns: int = 4, rows: int = 3) -> list[str]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return []
    per_sheet = columns * rows
    paths: list[str] = []
    thumb_w, thumb_h, label_h = 320, 180, 30
    font = ImageFont.load_default()
    for sheet_index in range(0, len(frames), per_sheet):
        group = frames[sheet_index:sheet_index + per_sheet]
        sheet = Image.new("RGB", (columns * thumb_w, rows * (thumb_h + label_h)), (18, 22, 30))
        draw = ImageDraw.Draw(sheet)
        for i, item in enumerate(group):
            try:
                image = Image.open(item["path"]).convert("RGB")
                image.thumbnail((thumb_w, thumb_h))
                x = (i % columns) * thumb_w
                y = (i // columns) * (thumb_h + label_h)
                sheet.paste(image, (x + (thumb_w - image.width) // 2, y + (thumb_h - image.height) // 2))
                label = f"{item['time_sec']:.2f}s | {item['frame_type']}"
                draw.text((x + 6, y + thumb_h + 6), label, fill=(235, 239, 245), font=font)
            except Exception:
                continue
        path = output_dir / f"storyboard_{len(paths) + 1:02d}.jpg"
        sheet.save(path, quality=88, optimize=True)
        paths.append(str(path))
    return paths


def write_storyboard(path: Path, manifest: list[dict[str, Any]]) -> None:
    cards = []
    for row in manifest:
        name = html.escape(Path(row["path"]).name)
        cards.append(f'<article><img src="{name}" loading="lazy"><b>{row["time_sec"]:.2f}s · {html.escape(row["frame_type"])}</b><small>{html.escape(row["frame_id"])} · cues {html.escape(",".join(row.get("cue_ids", [])))}</small></article>')
    page = """<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>多模态证据故事板</title><style>body{font:14px system-ui;background:#101319;color:#e8eaf0;margin:20px}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}article{background:#1b202a;border:1px solid #303746;border-radius:10px;padding:8px}img{width:100%;aspect-ratio:16/9;object-fit:contain;background:#050608}b,small{display:block;margin-top:6px}small{color:#9aa4b7}</style><h1>多模态证据故事板</h1><p>帧图仅用于辅助观察；单帧不能证明动作连续、人物身份或动机。请回看相邻视频片段。</p><main>""" + "".join(cards) + "</main></html>"
    path.write_text(page, encoding="utf-8")


def write_ai_review_prompt(path: Path, scan: dict[str, Any]) -> None:
    frames = scan.get("frame_manifest", [])
    cues = scan.get("cues", [])
    cue_summary: dict[str, int] = {}
    for cue in cues:
        cue_summary[cue.get("cue_type", "unknown")] = cue_summary.get(cue.get("cue_type", "unknown"), 0) + 1
    lines = [
        "# 多模态剧情语义审核任务",
        "",
        f"- 原片：`{scan.get('source_video')}`",
        f"- 时长：{scan.get('media', {}).get('duration_sec', 0)} 秒",
        f"- 关键帧：{len(frames)} 张；线索：{len(cues)} 条",
        f"- 字幕/转录源：`{scan.get('subtitle_source') or '未提供'}`（请同时提供该 SRT/JSON 文件，以便核对完整上下文）",
        f"- 线索类型统计：`{json.dumps(cue_summary, ensure_ascii=False)}`",
        "",
        "## 给 ChatGPT / 人工审核者的指令",
        "1. 先查看附带的 storyboard_contact_sheet 或 storyboard_XX.jpg，再按 frame_manifest 的时间码回看原视频片段。",
        "2. 只描述可观察内容：画面中有哪些人/物、可见动作、镜头关系、屏幕文字；不要仅凭单帧确认身份、动机、亲缘或因果。",
        "3. 将画面观察与字幕/ASR 对齐。字幕只证明文本轨道出现该文字，不自动证明说话人。",
        "4. 音量峰值和镜头切换只是检索线索，不等于情绪、冲突或剧情高潮。",
        "5. 输出事件候选时必须区分 actor/patient/recipient/target；未知人物槽位填 null，不要编造角色 ID。",
        "6. 将不确定内容写入 alternatives / uncertainty，不要强行归类。",
        "7. 关键事件必须回看连续片段；单帧只作为定位线索。",
        "",
        "## 建议输出 JSON 格式",
        "```json",
        json.dumps({
            "review_version": "1.0",
            "observations": [{
                "start_sec": 12.4, "end_sec": 16.8,
                "frame_refs": ["frame_00012.400_uniform_sample.jpg"],
                "visual_description": "只写画面可观察事实",
                "visible_person_descriptions": ["画面左侧穿深色衣服的人，身份未确认"],
                "actions": ["将物件放在桌面上"],
                "continuity_notes": "是否与前后镜头连续，待核对",
                "confidence": "medium",
                "alternatives": ["也可能是递交而非放置"]
            }],
            "event_candidates": [{
                "playback_time": 14.1, "action": "将物件放在桌面上",
                "actor": None, "patient": None, "recipient": None, "target": None,
                "modality": "actual", "polarity": "positive",
                "certainty": "I2_PLAUSIBLE", "quote": "",
                "observation_index": 0, "evidence_ids": []
            }]
        }, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 帧清单",
    ]
    for frame in frames:
        lines.append(f"- {frame['time_sec']:.3f}s · `{Path(frame['path']).name}` · {frame['frame_type']} · cues: {', '.join(frame.get('cue_ids', []))}")
    lines += ["", "## 线索注意事项"]
    # Preserve transcript context across the full episode instead of truncating
    # to the first N chronological subtitle rows. Include top signal cues plus
    # the nearest transcript rows around each sampled frame.
    selected: dict[str, dict[str, Any]] = {}
    for cue in sorted((c for c in cues if c.get("cue_type") != "transcript_segment"),
                      key=lambda x: float(x.get("score") or 0), reverse=True)[:100]:
        selected[cue["cue_id"]] = cue
    transcript = [c for c in cues if c.get("cue_type") == "transcript_segment"]
    for frame in frames:
        t = float(frame["time_sec"])
        nearby = sorted(transcript, key=lambda c: abs((float(c["start_sec"]) + float(c["end_sec"])) / 2 - t))[:2]
        for cue in nearby:
            if abs((float(cue["start_sec"]) + float(cue["end_sec"])) / 2 - t) <= 15:
                selected[cue["cue_id"]] = cue
    for cue in sorted(selected.values(), key=lambda x: float(x.get("start_sec", 0))):
        details = cue.get("details", {})
        text = details.get("text", "") if isinstance(details, dict) else ""
        lines.append(f"- {cue['start_sec']:.3f}-{cue['end_sec']:.3f}s `{cue['cue_type']}` score={cue.get('score')} {text[:100]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_scan(video: Path, subtitle: Path | None, threshold: float, audio_window: float,
               frames_dir: Path, max_frames: int, sample_interval: float = 10.0) -> dict[str, Any]:
    media = probe_media(video)
    cues = shot_cues(video, threshold)
    audio, audio_meta = audio_cues(video, audio_window)
    cues.extend(audio)
    subtitle_rows = load_subtitles(subtitle) if subtitle else []
    sub = subtitle_cues(subtitle_rows, "asr_or_subtitle")
    cues.extend(sub)
    cues.sort(key=lambda x: (float(x["start_sec"]), x["cue_type"]))
    frames = extract_frames(video, cues, frames_dir, max_frames, sample_interval) if max_frames else []
    return {
        "scan_version": "1.1.0", "source_video": str(video.resolve()), "media": media,
        "subtitle_source": str(subtitle.resolve()) if subtitle else None,
        "subtitle_cue_count": len(subtitle_rows), "audio_analysis": audio_meta,
        "cue_count": len(cues), "cues": cues, "frame_manifest": frames,
        "limitations": [
            "本工具只抽取镜头变化、PCM 音量峰值/低能量区间、字幕语言线索和抽样帧，不识别人脸/说话人/动作/动机。",
            "音量峰值不等于情绪高潮；音频低能量不等于人物沉默或心理状态；字幕关键词不等于剧情反转。",
            "shot cut 不等于语义场景；帧图必须结合连续片段和声音核对。",
            "关键动作/说话人/身份/因果结论必须回看原片连续片段并由人工核证。",
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser(description="抽取音画字幕线索、均匀关键帧和 AI 语义审核材料（不做语义裁决）")
    p.add_argument("video")
    p.add_argument("output")
    p.add_argument("--subtitle", help="SRT/VTT 或 faster-whisper JSON")
    p.add_argument("--threshold", type=float, default=.30)
    p.add_argument("--audio-window", type=float, default=.5)
    p.add_argument("--frames-dir", help="帧图目录；默认输出文件名_frames")
    p.add_argument("--max-frames", type=int, default=80)
    p.add_argument("--sample-interval", type=float, default=10.0, help="均匀关键帧间隔秒数")
    args = p.parse_args()
    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        raise SystemExit(f"找不到视频: {video}")
    if not 0 < args.threshold < 1 or args.audio_window <= 0 or not 1 <= args.max_frames <= 500 or args.sample_interval <= 0:
        raise SystemExit("threshold/audio-window/max-frames/sample-interval 参数无效")
    subtitle = Path(args.subtitle).expanduser().resolve() if args.subtitle else None
    if subtitle and not subtitle.is_file():
        raise SystemExit(f"找不到字幕: {subtitle}")
    out = Path(args.output).expanduser().resolve()
    frames_dir = Path(args.frames_dir).expanduser().resolve() if args.frames_dir else out.with_suffix("").with_name(out.stem + "_frames")
    payload = build_scan(video, subtitle, args.threshold, args.audio_window, frames_dir, args.max_frames, args.sample_interval)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if payload["frame_manifest"]:
        write_storyboard(frames_dir / "index.html", payload["frame_manifest"])
        contact_sheets = write_contact_sheets(payload["frame_manifest"], frames_dir)
        payload["contact_sheets"] = contact_sheets
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prompt_path = out.with_name(out.stem + "_ai_review_prompt.md")
    write_ai_review_prompt(prompt_path, payload)
    print(f"扫描完成：{payload['cue_count']} 条音画/字幕线索，{len(payload['frame_manifest'])} 张关键帧")
    print(f"多模态线索：{out}")
    if payload.get("contact_sheets"):
        print("故事板联系表：")
        for item in payload["contact_sheets"]:
            print(f"  {item}")
    print(f"AI 语义审核提示：{prompt_path}")
    print("注意：这是机器线索，不是剧情事实；需检查关键帧并回看原片。")


if __name__ == "__main__":
    main()
