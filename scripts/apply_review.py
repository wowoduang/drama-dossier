#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
apply_review.py — 把人工审核结果回填进剧情底稿

规则（对应 SKILL.md S7）：
  pass     不动
  noevi    （缺证据）把该条结论替换成人工改写的版本
  wrong    同上，并在修正记录里标红
  doubt    不动正文，全部沉到「疑点清单」

只改被点名的条目，不重写全篇。

用法:
  python apply_review.py review_result.json --md 人物关系档案.md
  python apply_review.py review_result.json --md 人物关系档案.md --dry-run
  python apply_review.py review_result.json            # 只出修正记录
"""
import json
import argparse
import datetime


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("review_result")
    ap.add_argument("--md", default=None, help="要回填的底稿；不给则只生成修正记录")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    data = load(a.review_result)
    items = data.get("items", [])
    stats = data.get("meta", {}).get("stats", {})

    by = {"pass": [], "noevi": [], "wrong": [], "doubt": [], "unreviewed": []}
    for x in items:
        by[x.get("verdict", "unreviewed")].append(x)

    print("审核结果：", "  ".join(f"{k} {len(v)}" for k, v in by.items() if v))

    # ---------- 回填 md ----------
    changed = 0
    missed = []
    if a.md:
        text = open(a.md, encoding="utf-8").read()
        for x in by["noevi"] + by["wrong"]:
            new = (x.get("edited") or "").strip()
            old = (x.get("claim") or "").strip()
            if not new:
                missed.append((x["id"], old, "没有给改写"))
                continue
            if not old:
                missed.append((x["id"], "", "原结论为空，无法定位"))
                continue
            if old in text:
                text = text.replace(old, new, 1)
                changed += 1
            else:
                missed.append((x["id"], old, "在 md 里找不到原结论原文"))
        if not a.dry_run and changed:
            open(a.md, "w", encoding="utf-8").write(text)
        print(f"回填 {changed} 处" + ("（dry-run，未写盘）" if a.dry_run else ""))

    if missed:
        print(f"\n未能回填 {len(missed)} 处（需人工处理）：")
        for i, o, why in missed:
            print(f"  {i}  {why}  {o[:40]}")

    # ---------- 修正记录 ----------
    lines = [
        "# 人物·关系·事实档案 · 人工审核修正记录",
        "",
        f"审核时间：{datetime.datetime.now():%Y-%m-%d %H:%M}",
        f"底稿：{data.get('meta', {}).get('source', '')}",
        "",
        "| 通过 | 缺证据 | 错误 | 存疑 | 未审 |",
        "|---|---|---|---|---|",
        f"| {len(by['pass'])} | {len(by['noevi'])} | {len(by['wrong'])} | "
        f"{len(by['doubt'])} | {len(by['unreviewed'])} |",
        "",
    ]

    if by["wrong"]:
        lines += ["## 一、事实错误（已修正）", ""]
        for x in by["wrong"]:
            lines += [
                f"### {x['id']} · {x.get('section','')} · `{x.get('timecode','')}`",
                "",
                f"- 原结论：{x.get('claim','')}",
                f"- 改为：**{x.get('edited','（未填）')}**",
                f"- 理由：{x.get('reason','（未填）')}",
                "",
            ]

    if by["noevi"]:
        lines += ["## 二、缺证据（已补正）", ""]
        for x in by["noevi"]:
            lines += [
                f"### {x['id']} · {x.get('section','')} · `{x.get('timecode','')}`",
                "",
                f"- 原结论：{x.get('claim','')}",
                f"- 改为：**{x.get('edited','（未填）')}**",
                f"- 缺什么证据：{x.get('reason','（未填）')}",
                "",
            ]

    if by["doubt"]:
        lines += ["## 三、存疑待查（未定论，交付时必须列出）", ""]
        for x in by["doubt"]:
            lines += [
                f"- `{x.get('timecode','')}` **{x.get('claim','')}**",
                f"  - 缺什么：{x.get('reason','（未填）')}",
            ]
        lines.append("")

    if by["unreviewed"]:
        lines += ["## 四、未审核", "", "以下条目本轮没有过审，不得当作已确认结论使用：", ""]
        for x in by["unreviewed"]:
            lines.append(f"- `{x.get('timecode','')}` {x.get('claim','')}")
        lines.append("")

    out = a.review_result.replace(".json", "") + "_修正记录.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"修正记录 -> {out}")

    if by["unreviewed"]:
        print(f"\n注意：还有 {len(by['unreviewed'])} 条未审，未达成交付条件。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
