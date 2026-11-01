#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_typos.py — 修正 OCR 的形近字错读

OCR 最常见的不是"读不出"，而是"读成另一个字"。实测遇到过的：

  接旨了 → 接冒了     （旨/冒）
  秃头   → 秀头       （秃/秀）
  郭将军 → 禀将军     （郭/禀）
  敕封天下 → 教封天下 （敕/教）
  咱朱重八 → 诸崇巴   （整句串味，Whisper 的典型错法）

这类错必须人工判断，机器分不出来。所以把确认过的替换规则存成文件，
下次同一部片、同一批演员、同一套字幕能直接复用。

规则文件格式（每行一条，# 开头是注释）：
  接冒了=接旨了
  秀头=秃头
  禀将军=郭将军

用法：
  python fix_typos.py 片子.subtitle.json --rule 秀头=秃头 --rule 接冒了=接旨了
  python fix_typos.py 片子.subtitle.json --rules rules.txt
  python fix_typos.py 片子.subtitle.json --rules rules.txt --dry-run

--dry-run 只报告会改什么，不写文件。改之前建议先跑一次看看。
"""
import argparse
import json
import os
import re
import sys


def fmt_ts(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def load_rules(a):
    rules = []
    if a.rules and os.path.exists(a.rules):
        for line in open(a.rules, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                bad, good = line.split("=", 1)
                rules.append((bad.strip(), good.strip()))
    for r in a.rule or []:
        if "=" in r:
            bad, good = r.split("=", 1)
            rules.append((bad.strip(), good.strip()))
    return rules


def main():
    ap = argparse.ArgumentParser(description="修正 OCR 形近字错读")
    ap.add_argument("json_path")
    ap.add_argument("--rule", action="append", help="错=对，可重复")
    ap.add_argument("--rules", help="规则文件路径")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.json_path):
        sys.exit(f"找不到 {a.json_path}")
    rules = load_rules(a)
    if not rules:
        sys.exit("没给任何规则。用 --rule 错=对 或 --rules 文件")

    rows = json.load(open(a.json_path, encoding="utf-8"))
    changed = 0
    for r in rows:
        old = r["text"]
        new = old
        for bad, good in rules:
            new = new.replace(bad, good)
        if new != old:
            r["text"] = new
            changed += 1
            print(f"  {int(r['start']//60)}:{r['start']%60:05.2f}  {old}  →  {new}")

    print(f"\n{changed} 条被修正（共 {len(rows)} 条）")
    if a.dry_run:
        print("dry-run，没有写文件")
        return
    if changed == 0:
        return

    json.dump(rows, open(a.json_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    srt = a.json_path.replace(".subtitle.json", ".subtitle.srt")
    if srt == a.json_path:
        srt = os.path.splitext(a.json_path)[0] + ".srt"
    with open(srt, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            f.write(f"{i}\n{fmt_ts(r['start'])} --> {fmt_ts(r['end'])}\n{r['text']}\n\n")
    print(f"已写入 {a.json_path}")
    print(f"       {srt}")


if __name__ == "__main__":
    main()
