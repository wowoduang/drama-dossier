#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transcribe.py — 全片转录，产出可逐条引用的时间码

为什么要自己转录，而不是听原片猜台词：
影视剧的台词是剧情的唯一硬证据。听错一个字，人物关系就全歪
（"是，郭将军"听成"是，驸马爷"，人物关系立刻变成姻亲）。

输出两个文件，放在视频同目录：
  <stem>.transcript.json   [{start, end, text}]  给机器读
  <stem>.transcript.srt    给人和播放器看

用法：
  python transcribe.py video.mp4
  python transcribe.py video.mp4 --model medium --lang zh
  python transcribe.py video.mp4 --out-dir ./dossier

模型路径按 E 盘优先自动探测，找不到就让 faster-whisper 自己下载。
"""
import argparse
import json
import os
import sys
import time

MODEL_CANDIDATES = [
    "E:/aivideo-workspace/models/faster-whisper-large-v3",
    "D:/AIVideoGPT-main/AIVideoGPT-workspace/models/faster-whisper-large-v3",
    "E:/AI/Models/faster-whisper-large-v3",
]


def pick_model(name):
    """显式给了路径就用路径；给的是 small/medium 这种名字就按名字找本地目录。"""
    if os.path.sep in name or "/" in name:
        return name
    for p in MODEL_CANDIDATES:
        if os.path.isdir(p) and name in os.path.basename(p):
            print(f"  用本地模型 {p}")
            return p
    for p in MODEL_CANDIDATES:
        if os.path.isdir(p) and name == "large-v3":
            print(f"  用本地模型 {p}")
            return p
    print(f"  本地没找到 {name}，交给 faster-whisper 处理（可能要下载）")
    return name


def fmt_ts(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def main():
    ap = argparse.ArgumentParser(description="影视剧全片转录")
    ap.add_argument("video")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    a = ap.parse_args()

    if not os.path.exists(a.video):
        sys.exit(f"找不到视频: {a.video}")

    from faster_whisper import WhisperModel

    model_path = pick_model(a.model)
    device = "cuda" if a.device == "auto" else a.device
    compute = "float16" if device == "cuda" else "int8"
    if a.device == "auto":
        try:
            import torch  # noqa: F401
            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute = "float16" if device == "cuda" else "int8"
        except Exception:
            device, compute = "cpu", "int8"

    print(f"加载模型 {model_path}  device={device} compute={compute}")
    t0 = time.time()
    model = WhisperModel(model_path, device=device, compute_type=compute)

    print("转录中（长片请耐心，CPU 上 45 分钟片子可能要 20-40 分钟）...")
    segments, info = model.transcribe(
        a.video,
        language=a.lang,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        condition_on_previous_text=False,
    )

    rows = []
    for seg in segments:
        txt = seg.text.strip()
        if not txt:
            continue
        rows.append({"start": round(seg.start, 2),
                     "end": round(seg.end, 2),
                     "text": txt})
        if len(rows) % 50 == 0:
            el = time.time() - t0
            print(f"  {len(rows)} 段  已到 {seg.end/60:.1f} 分钟  用时 {el/60:.1f} 分")

    od = a.out_dir or os.path.dirname(os.path.abspath(a.video))
    os.makedirs(od, exist_ok=True)
    stem = os.path.splitext(os.path.basename(a.video))[0]

    jp = os.path.join(od, f"{stem}.transcript.json")
    json.dump(rows, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    sp = os.path.join(od, f"{stem}.transcript.srt")
    with open(sp, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            f.write(f"{i}\n{fmt_ts(r['start'])} --> {fmt_ts(r['end'])}\n{r['text']}\n\n")

    dur = rows[-1]["end"] if rows else 0
    print(f"\n完成：{len(rows)} 段，覆盖到 {dur/60:.1f} 分钟，用时 {(time.time()-t0)/60:.1f} 分")
    print(f"  {jp}")
    print(f"  {sp}")
    print("\n下一步：通读转录稿，先列出人物称谓，再建事件时间轴。"
          "任何要写进底稿的台词，都要能在这里查到原文和秒数。")


if __name__ == "__main__":
    main()
