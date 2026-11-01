#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""subtitle_ocr.py — 从画面硬字幕提取全片台词（准确度远高于语音转录）

为什么需要它：
古装剧、方言、儿童演员、多人抢话，Whisper 会错到没法用。
实测原台词「朕本布衣，上承天命，下顺民心」被转成「诸崇巴，上崇天道，下顺人心」，
「重八哥」变「妈妈哥」。这种稿子当剧情证据就是灾难。
但这类片子通常把官方字幕烧在画面上，那些字是准确的。

两种识别模式：

  rec（默认，快 70 倍）
    字幕位置固定，不需要"检测"这一步。
    先用白色像素掩码找到最底部那一行字，裁紧，再只跑识别。
    实测 20–30 ms/帧，可以逐帧全识别，不用跳帧。
    要求：字幕是白字（或接近白）。

  det（保底，慢）
    走完整的文本检测 + 识别，约 2200 ms/帧。
    用在字幕是黄字、双行、或 rec 模式读不准的片子上。

输出：
  <stem>.subtitle.json   [{start, end, text, conf}]
  <stem>.subtitle.srt

用法：
  python subtitle_ocr.py video.mp4
  python subtitle_ocr.py video.mp4 --mode det          # 读不准时换这个
  python subtitle_ocr.py video.mp4 --start 200 --end 330
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np


def fmt_ts(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def similar(a, b):
    """字符级重合比例。够用，不引额外依赖。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(len(sa), len(sb))


def find_text_row(mask, min_h, max_h):
    """在掩码里找最底部那一行字。

    字幕有两个特征：贴着画面下方、是连续的一行。
    找到高度合适、位置最靠下的连续白像素带，就是字幕行。
    """
    rs = mask.sum(axis=1)
    thr = max(3, mask.shape[1] * 0.004)
    on = rs > thr
    segs, s = [], None
    for i, v in enumerate(on):
        if v and s is None:
            s = i
        elif not v and s is not None:
            segs.append((s, i))
            s = None
    if s is not None:
        segs.append((s, len(on)))
    if not segs:
        return None
    cand = [x for x in segs if min_h <= x[1] - x[0] <= max_h]
    if not cand:
        cand = segs
    return max(cand, key=lambda x: x[1])


def tight_crop(band, white_thresh, min_h, max_h):
    """裁出字幕的紧凑矩形。裁不准就别硬认，返回 None。"""
    g = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    m = (g > white_thresh).astype(np.uint8)
    ratio = float(m.mean())
    # 白像素太少 = 没字幕；太多 = 一片亮画面，不是字
    if ratio < 0.003 or ratio > 0.45:
        return None
    row = find_text_row(m, min_h, max_h)
    if not row:
        return None
    y0, y1 = row
    sub = m[y0:y1]
    cols = np.where(sub.sum(axis=0) > 0)[0]
    if len(cols) == 0:
        return None
    x0, x1 = int(cols.min()), int(cols.max()) + 1
    # 太窄当杂物（噪点、竖排水印残片）
    if (x1 - x0) < band.shape[1] * 0.05:
        return None
    pad_x, pad_y = 6, 4
    crop = band[max(0, y0 - pad_y):min(band.shape[0], y1 + pad_y),
                max(0, x0 - pad_x):min(band.shape[1], x1 + pad_x)]
    if crop.size == 0:
        return None
    # 字太小就放大，识别更准
    if crop.shape[0] < 48:
        s = 48.0 / crop.shape[0]
        crop = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    return crop


def main():
    ap = argparse.ArgumentParser(description="硬字幕 OCR 提取")
    ap.add_argument("video")
    ap.add_argument("--mode", default="rec", choices=["rec", "det"],
                    help="rec=快70倍(默认,限白字)  det=慢但通用")
    ap.add_argument("--fps", type=float, default=2.0, help="每秒取帧数")
    ap.add_argument("--sub-ratio", type=float, default=0.20, help="底部字幕区高度占比")
    ap.add_argument("--side-margin", type=float, default=0.07,
                    help="左右各裁掉的比例，躲开台标和竖排水印")
    ap.add_argument("--white-thresh", type=int, default=200, help="白字灰度阈值")
    ap.add_argument("--min-row-h", type=int, default=12, help="字幕行最小像素高")
    ap.add_argument("--max-row-h", type=int, default=110, help="字幕行最大像素高")
    ap.add_argument("--min-conf", type=float, default=0.55)
    ap.add_argument("--min-len", type=int, default=2)
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    if not os.path.exists(a.video):
        sys.exit(f"找不到视频: {a.video}")

    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        sys.exit("打不开视频")
    fps_src = cap.get(cv2.CAP_PROP_FPS) or 25.0
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dur = nframes / fps_src if fps_src else 0

    t_begin = a.start or 0.0
    t_finish = a.end if a.end is not None else dur
    step = 1.0 / a.fps

    sub_h = int(h * a.sub_ratio)
    sub_y = h - sub_h
    x_lo = int(w * a.side_margin)
    x_hi = int(w * (1 - a.side_margin))
    band_w = x_hi - x_lo

    print(f"视频 {w}x{h}  时长 {dur:.1f}s  mode={a.mode}")
    print(f"字幕区 x={x_lo}..{x_hi} y={sub_y}..{h}  取样 {a.fps}/s")
    print(f"处理区间 {t_begin:.1f}s - {t_finish:.1f}s")

    subs = []
    cur = None
    seen = 0
    calls = 0
    t0 = time.time()

    def close_cur():
        nonlocal cur
        if cur:
            subs.append(cur)
            cur = None

    def take(t, text, conf):
        nonlocal cur
        if not text:
            return
        if cur and similar(cur["text"], text) >= 0.70:
            cur["end"] = t + step
            # 同一条字幕读多次，留最完整的那个版本
            if len(text) > len(cur["text"]) and conf >= cur["conf"] * 0.7:
                cur["text"], cur["conf"] = text, round(conf, 3)
            elif conf > cur["conf"] and len(text) >= len(cur["text"]):
                cur["text"], cur["conf"] = text, round(conf, 3)
        else:
            close_cur()
            cur = {"start": t, "end": t + step, "text": text, "conf": round(conf, 3)}

    t = t_begin
    while t < t_finish:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            break
        seen += 1
        band = frame[sub_y:h, x_lo:x_hi]

        if a.mode == "rec":
            crop = tight_crop(band, a.white_thresh, a.min_row_h, a.max_row_h)
            if crop is None:
                close_cur()
            else:
                r, _ = engine(crop, use_det=False, use_cls=False, use_rec=True)
                calls += 1
                # 置信度必须过滤：画面里的牌匾、幌子也会被认出来，那些置信度通常 <0.4
                if r and len(r[0][0]) >= a.min_len and float(r[0][1]) >= a.min_conf:
                    take(t, r[0][0].strip(), float(r[0][1]))
                else:
                    close_cur()
        else:
            big = cv2.resize(band, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            r, _ = engine(big)
            calls += 1
            if r:
                items = [(min(p[0] for p in box), min(p[1] for p in box), txt, cf)
                         for box, txt, cf in r]
                items.sort(key=lambda x: (round(x[1] / 24), x[0]))
                text = "".join(x[2] for x in items).strip()
                conf = sum(x[3] for x in items) / len(items) if items else 0
                if len(text) >= a.min_len and conf >= a.min_conf:
                    take(t, text, conf)
                else:
                    close_cur()

        if seen % 300 == 0:
            el = time.time() - t0
            print(f"  {t/60:.1f}/{t_finish/60:.1f} 分钟  识别 {calls} 次  "
                  f"条目 {len(subs)}  用时 {el/60:.1f} 分")

        t += step

    close_cur()
    cap.release()

    # 后处理：合并被淡入淡出拆碎、或相邻重复的条目
    cleaned = []
    for r in subs:
        if len(r["text"]) <= 2 and r["conf"] < 0.9:
            continue
        if cleaned and similar(cleaned[-1]["text"], r["text"]) >= 0.70:
            prev = cleaned[-1]
            prev["end"] = r["end"]
            if len(r["text"]) > len(prev["text"]):
                prev["text"], prev["conf"] = r["text"], r["conf"]
        else:
            cleaned.append(r)
    subs = cleaned

    od = a.out_dir or os.path.dirname(os.path.abspath(a.video))
    os.makedirs(od, exist_ok=True)
    stem = os.path.splitext(os.path.basename(a.video))[0]

    jp = os.path.join(od, f"{stem}.subtitle.json")
    json.dump(subs, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    sp = os.path.join(od, f"{stem}.subtitle.srt")
    with open(sp, "w", encoding="utf-8") as f:
        for i, r in enumerate(subs, 1):
            f.write(f"{i}\n{fmt_ts(r['start'])} --> {fmt_ts(r['end'])}\n{r['text']}\n\n")

    cover = sum(r["end"] - r["start"] for r in subs)
    print(f"\n完成：{len(subs)} 条  覆盖 {cover:.0f}s = {cover/max(dur,1)*100:.1f}%")
    print(f"  识别 {calls} 次 / 看帧 {seen} 张  总用时 {(time.time()-t0)/60:.1f} 分")
    print(f"  {jp}")
    print(f"  {sp}")
    print("\n注意：OCR 也会错认形近字（旨→冒、敕→教）。"
          "凡要写进底稿的台词，用 probe.py --sub 抽原帧再确认一次。")


if __name__ == "__main__":
    main()
