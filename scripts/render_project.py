#!/usr/bin/env python3
"""Render a Drama Dossier edit project with FFmpeg."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("项目根节点必须为对象")
    return data


def has_audio(video: str | Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("未找到 ffprobe")
    cp = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
        text=True,
        capture_output=True,
        check=True,
    )
    return bool(cp.stdout.strip())


def enabled_clips(project: dict[str, Any]) -> list[dict[str, Any]]:
    from edit_versions import require_approved
    require_approved(project)
    out = []
    for clip in project.get("clips", []):
        if not isinstance(clip, dict) or clip.get("enabled", True) is False:
            continue
        start, end = clip.get("start_sec"), clip.get("end_sec")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            raise ValueError(f"无效片段时间: {clip.get('clip_id', '?')}")
        out.append(clip)
    if not out:
        raise ValueError("项目没有可渲染片段")
    return out


def build_filter(clips: list[dict[str, Any]], audio: bool) -> str:
    parts: list[str] = []
    concat_inputs: list[str] = []
    for i, clip in enumerate(clips):
        start = float(clip["start_sec"])
        end = float(clip["end_sec"])
        parts.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}]")
        concat_inputs.append(f"[v{i}]")
        if audio:
            parts.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]")
            concat_inputs.append(f"[a{i}]")
    if audio:
        parts.append("".join(concat_inputs) + f"concat=n={len(clips)}:v=1:a=1[outv][outa]")
    else:
        parts.append("".join(concat_inputs) + f"concat=n={len(clips)}:v=1:a=0[outv]")
    return ";\n".join(parts)


def build_command(project: dict[str, Any], video: str | Path, output: str | Path, crf: int = 20, preset: str = "medium") -> tuple[list[str], str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg")
    clips = enabled_clips(project)
    if project.get('edit_version'):
        sources = {str(Path(c.get('source_video') or project.get('source_video') or '').resolve()) for c in clips}
        if sources != {str(Path(video).resolve())}:
            raise ValueError('审核版本的原片与 --video 不匹配；多源项目请用跨集渲染器')
    audio = has_audio(video)
    filter_text = build_filter(clips, audio)
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(video), "-/filter_complex", "FILTER_FILE", "-map", "[outv]"]
    if audio:
        cmd += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-movflags", "+faststart", str(output)]
    return cmd, filter_text


def main() -> None:
    p = argparse.ArgumentParser(description="根据精彩剪辑项目 JSON 调用 FFmpeg 渲染成片")
    p.add_argument("project")
    p.add_argument("output")
    p.add_argument("--video", help="原片路径；未提供时读取项目 source_video")
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--preset", default="medium")
    p.add_argument("--dry-run", action="store_true", help="只打印 FFmpeg 命令和滤镜，不渲染")
    a = p.parse_args()

    project = load_json(a.project)
    video = a.video or project.get("source_video")
    if not video:
        raise SystemExit("缺少原片路径：使用 --video 或在项目中设置 source_video")
    video = Path(video)
    if not video.exists():
        raise SystemExit(f"原片不存在: {video}")
    if not (0 <= a.crf <= 51):
        raise SystemExit("CRF 必须在 0-51")

    cmd, filter_text = build_command(project, video, a.output, a.crf, a.preset)
    if a.dry_run:
        print("# filter_complex_script")
        print(filter_text)
        print("# command")
        print(" ".join(cmd))
        return

    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".ffscript", encoding="utf-8", delete=False) as f:
        f.write(filter_text)
        filter_path = f.name
    try:
        real_cmd = [filter_path if x == "FILTER_FILE" else x for x in cmd]
        subprocess.run(real_cmd, check=True)
    finally:
        Path(filter_path).unlink(missing_ok=True)
    print(f"渲染完成: {out}")


if __name__ == "__main__":
    main()

