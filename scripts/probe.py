#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe.py — 影视剧画面取证工具

目的只有一个：让「我看到的」不是靠猜。
- --frame  单帧：整帧 + 底部字幕条放大，用来读清画面上的字
- --grid   宫格：一次看 20-30 帧，用来快速过一遍一段剧情
- --sub    只拼字幕条：用来核对一串连续台词
- --audio  抽音频片段：用来复核听不清的台词

用法：
  python probe.py video.mp4 --frame 372
  python probe.py video.mp4 --grid 203 --length 124 --step 4
  python probe.py video.mp4 --sub 358 362.5 366 372
  python probe.py video.mp4 --audio 368 376

约定：所有输出都放在 <视频目录>/_probe/ 下，文件名带时间码，方便回头引用。
"""
import argparse
import os
import shutil
import subprocess
import sys

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def run(cmd):
    """跑命令，失败就抛，不吞错。"""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        msg = p.stderr.decode("utf-8", "ignore")[-800:]
        raise RuntimeError(f"ffmpeg 失败:\n{msg}")
    return p


def probe_size(video):
    p = run([FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", video])
    w, h = p.stdout.decode().strip().split(",")[:2]
    return int(w), int(h)


def probe_duration(video):
    p = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", video])
    return float(p.stdout.decode().strip())


def outdir_for(video):
    d = os.path.join(os.path.dirname(os.path.abspath(video)), "_probe")
    os.makedirs(d, exist_ok=True)
    return d


def grab_frame(video, t, dst, w, h, sub_ratio, scale):
    """单帧 + 字幕条放大，垂直拼接。

    vstack 要求两路宽度一致，所以都缩到同一个目标宽度再拼。
    """
    sub_h = int(h * sub_ratio)
    sub_h -= sub_h % 2
    sub_y = h - sub_h
    target_w = int(1100 * max(1.0, scale / 3.0))  # scale=3 时宽 1100
    target_w -= target_w % 2
    vf = (
        f"[0:v]scale={target_w}:-2[a];"
        f"[0:v]crop={w}:{sub_h}:0:{sub_y},scale={target_w}:-2[b];"
        f"[a][b]vstack=inputs=2"
    )
    run([FFMPEG, "-nostdin", "-y", "-ss", str(t), "-i", video,
         "-frames:v", "1", "-filter_complex", vf, "-q:v", "2", dst])


def grab_sub_only(video, t, dst, w, h, sub_ratio, scale):
    sub_h = int(h * sub_ratio)
    sub_h -= sub_h % 2
    sub_y = h - sub_h
    target_w = int(1100 * max(1.0, scale / 3.0))
    target_w -= target_w % 2
    vf = f"crop={w}:{sub_h}:0:{sub_y},scale={target_w}:-2"
    run([FFMPEG, "-nostdin", "-y", "-ss", str(t), "-i", video,
         "-frames:v", "1", "-vf", vf, "-q:v", "2", dst])


def grab_grid(video, start, length, step, dst, cols=4):
    # 先按 step 算出能铺满几格，行数自适应
    n = max(1, int(round(length / step)))
    rows = (n + cols - 1) // cols
    fps = 1.0 / step
    vf = f"fps={fps:.6f},scale=560:-2,tile={cols}x{rows}"
    run([FFMPEG, "-nostdin", "-y", "-ss", str(start), "-t", str(length),
         "-i", video, "-vf", vf, "-frames:v", "1", "-q:v", "2", dst])


def grab_audio(video, a, b, dst):
    run([FFMPEG, "-nostdin", "-y", "-ss", str(a), "-t", str(b - a),
         "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-q:a", "4", dst])


def main():
    ap = argparse.ArgumentParser(description="影视剧画面取证")
    ap.add_argument("video")
    ap.add_argument("--frame", type=float, help="抽单帧（秒）")
    ap.add_argument("--grid", type=float, help="宫格起点（秒）")
    ap.add_argument("--length", type=float, default=60, help="宫格覆盖时长（秒）")
    ap.add_argument("--step", type=float, default=3, help="宫格抽帧间隔（秒）")
    ap.add_argument("--cols", type=int, default=4, help="宫格列数")
    ap.add_argument("--sub", type=float, nargs="+", help="只抽字幕条的时间点列表")
    ap.add_argument("--audio", type=float, nargs=2, metavar=("START", "END"))
    ap.add_argument("--sub-ratio", type=float, default=0.22,
                    help="底部字幕区占画面高度的比例（默认 0.22）")
    ap.add_argument("--scale", type=float, default=3.0, help="字幕条放大倍数")
    a = ap.parse_args()

    if not os.path.exists(a.video):
        sys.exit(f"找不到视频: {a.video}")

    w, h = probe_size(a.video)
    od = outdir_for(a.video)
    stem = os.path.splitext(os.path.basename(a.video))[0]
    print(f"视频 {w}x{h}  时长 {probe_duration(a.video):.1f}s  输出 {od}")

    made = []

    if a.frame is not None:
        dst = os.path.join(od, f"{stem}_f{a.frame:07.2f}.jpg")
        grab_frame(a.video, a.frame, dst, w, h, a.sub_ratio, a.scale)
        made.append(dst)

    if a.grid is not None:
        dst = os.path.join(od, f"{stem}_grid{a.grid:.0f}_{a.length:.0f}s.jpg")
        grab_grid(a.video, a.grid, a.length, a.step, dst, a.cols)
        made.append(dst)

    if a.sub:
        # 多帧字幕条竖向拼接
        tmp = []
        for i, t in enumerate(a.sub):
            p = os.path.join(od, f"_tmp_{i}.jpg")
            grab_sub_only(a.video, t, p, w, h, a.sub_ratio, a.scale)
            tmp.append(p)
        dst = os.path.join(od, f"{stem}_sub_{a.sub[0]:.0f}s.jpg")
        if len(tmp) == 1:
            shutil.move(tmp[0], dst)
        else:
            inputs = []
            for p in tmp:
                inputs += ["-i", p]
            labels = "".join(f"[{i}]" for i in range(len(tmp)))
            fc = f"{labels}vstack=inputs={len(tmp)}"
            run([FFMPEG, "-nostdin", "-y"] + inputs +
                ["-filter_complex", fc, "-q:v", "2", dst])
            for p in tmp:
                os.remove(p)
        made.append(dst)

    if a.audio:
        dst = os.path.join(od, f"{stem}_a{a.audio[0]:.0f}_{a.audio[1]:.0f}.mp3")
        grab_audio(a.video, a.audio[0], a.audio[1], dst)
        made.append(dst)

    if not made:
        sys.exit("没给任何动作。用 --frame / --grid / --sub / --audio")

    for p in made:
        print(f"  [OK] {p}")
    print("\n下一步：用看图工具打开这些文件，读清画面上的字，再下结论。")


if __name__ == "__main__":
    main()
