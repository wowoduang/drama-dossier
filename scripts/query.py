#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""query.py — 在字幕稿里查台词

写底稿时最高频的动作是"这句台词到底怎么说的、在第几秒"。
不要靠记忆，也不要肉眼翻 srt，直接查。

用法：
  python query.py 片子.subtitle.json "郭将军"          关键词检索
  python query.py 片子.subtitle.json --at 372          某时间点前后各 3 条
  python query.py 片子.subtitle.json --range 355 380   区间内全部台词
  python query.py 片子.subtitle.json --at 372 -n 6     前后各 6 条

支持直接给 .json 或 .srt（.srt 会自动解析）。
"""
import argparse
import json
import os
import re
import sys


def load(path):
    if path.endswith(".json"):
        return json.load(open(path, encoding="utf-8"))
    rows = []
    block = []
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip():
            if block:
                rows.append(block)
                block = []
            continue
        block.append(line)
    if block:
        rows.append(block)

    out = []
    for b in rows:
        if len(b) < 3:
            continue
        m = re.match(r"(\d+):(\d+):([\d,\.]+)\s*-->\s*(\d+):(\d+):([\d,\.]+)", b[1])
        if not m:
            continue
        st = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3).replace(",", "."))
        en = int(m.group(4)) * 3600 + int(m.group(5)) * 60 + float(m.group(6).replace(",", "."))
        out.append({"start": st, "end": en, "text": " ".join(b[2:])})
    return out


def ts(s):
    return f"{int(s//60)}:{s%60:05.2f}"


def show(r, mark=""):
    print(f"{mark}{ts(r['start']):>8} - {ts(r['end']):<8} {r['text']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("keyword", nargs="?", help="关键词")
    ap.add_argument("--at", type=float, help="时间点（秒），查前后")
    ap.add_argument("--range", type=float, nargs=2, metavar=("A", "B"))
    ap.add_argument("-n", type=int, default=3, help="--at 时前后各显示几条")
    a = ap.parse_args()

    if not os.path.exists(a.path):
        sys.exit(f"找不到 {a.path}")
    rows = load(a.path)
    if not rows:
        sys.exit("字幕稿是空的")
    print(f"# {len(rows)} 条，覆盖到 {ts(rows[-1]['end'])}\n")

    if a.keyword:
        hit = [r for r in rows if a.keyword in r["text"]]
        print(f"# 命中 {len(hit)} 条\n")
        for r in hit:
            show(r)
        if not hit:
            print("  没找到。可能是 OCR 错字，换个词或试试 --range 看原句。")

    elif a.at is not None:
        idx = min(range(len(rows)), key=lambda i: abs(rows[i]["start"] - a.at))
        lo, hi = max(0, idx - a.n), min(len(rows), idx + a.n + 1)
        print(f"# {ts(a.at)} 附近（标注 ▶ 的是最接近的一条）\n")
        for i in range(lo, hi):
            show(rows[i], "▶ " if i == idx else "  ")

    elif a.range:
        x, y = a.range
        hit = [r for r in rows if x <= r["start"] <= y]
        print(f"# {ts(x)} - {ts(y)} 共 {len(hit)} 条\n")
        for r in hit:
            show(r)

    else:
        for r in rows:
            show(r)


if __name__ == "__main__":
    main()
