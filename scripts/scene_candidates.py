#!/usr/bin/env python3
"""Generate visual shot-cut candidates with FFmpeg scene detection.

Output is intentionally *not* treated as semantic scenes. Each segment remains an
uncertain candidate for human review.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

PTS_RE = re.compile(r"pts_time:([0-9.]+)")
SCORE_RE = re.compile(r"lavfi\.scene_score=([0-9.]+)")


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"缺少外部工具: {name}。请先安装 FFmpeg 并确保 {name} 在 PATH 中。")
    return path


def probe_duration(video: str) -> float:
    ffprobe = require_tool("ffprobe")
    cmd = [
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video,
    ]
    out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
    return float(out)


def detect_cuts(video: str, threshold: float) -> list[dict[str, float]]:
    ffmpeg = require_tool("ffmpeg")
    # metadata=print writes frame metadata, including pts_time and lavfi.scene_score,
    # to stderr without producing video files.
    vf = f"select='gt(scene,{threshold})',metadata=print"
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", video, "-vf", vf, "-an", "-f", "null", "-"]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise SystemExit("FFmpeg 场景检测失败:\n" + proc.stderr[-4000:])

    cuts: list[dict[str, float]] = []
    pending_time: float | None = None
    for line in proc.stderr.splitlines():
        m = PTS_RE.search(line)
        if m and "frame:" in line:
            pending_time = float(m.group(1))
            continue
        m = SCORE_RE.search(line)
        if m and pending_time is not None:
            cuts.append({"time_sec": pending_time, "score": float(m.group(1))})
            pending_time = None
    # De-duplicate any repeated metadata rows from unusual filters/codecs.
    out: list[dict[str, float]] = []
    seen: set[float] = set()
    for item in cuts:
        key = round(item["time_sec"], 3)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def cuts_to_segments(cuts: list[dict[str, float]], duration: float) -> list[dict[str, Any]]:
    boundaries = [0.0] + [c["time_sec"] for c in cuts if 0 < c["time_sec"] < duration] + [duration]
    boundaries = sorted(set(round(x, 6) for x in boundaries))
    score_by_time = {round(c["time_sec"], 6): c["score"] for c in cuts}
    segments: list[dict[str, Any]] = []
    for i in range(len(boundaries) - 1):
        start, end = boundaries[i], boundaries[i + 1]
        if end <= start:
            continue
        segments.append({
            "start_sec": start,
            "end_sec": end,
            "score": score_by_time.get(round(start, 6)) if start > 0 else None,
            "method": "ffmpeg_scene_change",
            "boundary_confidence": "uncertain",
        })
    return segments


def build_payload(video: str, threshold: float) -> dict[str, Any]:
    duration = probe_duration(video)
    cuts = detect_cuts(video, threshold)
    return {
        "schema_version": "1.0",
        "source": str(Path(video).name),
        "duration_sec": duration,
        "method": "ffmpeg_scene_change",
        "threshold": threshold,
        "warning": "shot-cut candidates only; not semantic scene boundaries",
        "cuts": cuts,
        "segments": cuts_to_segments(cuts, duration),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="FFmpeg 镜头切换候选生成器（不是语义场景识别器）")
    p.add_argument("video")
    p.add_argument("output")
    p.add_argument("--threshold", type=float, default=0.30, help="FFmpeg scene threshold, usually 0.20-0.50")
    args = p.parse_args()
    if not 0 < args.threshold < 1:
        raise SystemExit("--threshold 必须在 0 与 1 之间")
    payload = build_payload(args.video, args.threshold)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {len(payload['cuts'])} 个镜头切换点、{len(payload['segments'])} 个候选片段: {args.output}")


if __name__ == "__main__":
    main()
