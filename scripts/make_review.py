#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make_review.py — 把人物·关系·事实档案拆成可人工逐条审核的 review.json

把 markdown 里每个表格块解析成条目，每条带：
  section（所在小节） / timecode（自动抽取） / fields（原始全部列）
  claim（核心断言列，优先"事件""关系""身份"） / quote（台词列）
  status（unreviewed） / verdict（空） / edited（空） / reason（空）

用法:
  python make_review.py 剧情底稿.md --out review.json
  python make_review.py 剧情底稿.md --out review.json --only 台词锚点 人物关系表
"""
import re
import json
import argparse
from pathlib import Path

TC = re.compile(r"(\d{1,2}):(\d{2})(?:[–\-~](\d{1,2}):(\d{2}))?")

# 这一列是"待审的核心断言"，决定审核台上那句大字
CLAIM_KEYS = ("事件", "关系", "身份", "结论", "判断", "作用", "用途", "说明")
QUOTE_KEYS = ("原台词", "台词", "原文", "原话")

# 这些小节是元信息，不逐条审（要审就加 --keep-meta）
SKIP_SECTIONS = ("片源", "证据来源", "统计")


def find_timecode(cells):
    for c in cells:
        m = TC.search(c)
        if m:
            return m.group(0)
    return ""


def parse(text):
    lines = text.split("\n")
    items, section, i, seq = [], "", 0, 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if m:
            section = m.group(1).strip()
            i += 1
            continue

        is_table = (
            line.strip().startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|[\s:\-|]+\|$", lines[i + 1].strip())
        )
        if not is_table:
            i += 1
            continue

        header = [c.strip() for c in line.strip().strip("|").split("|")]
        i += 2
        while i < len(lines) and lines[i].strip().startswith("|"):
            cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            i += 1
            if not any(cells):
                continue
            fields = {}
            for k, v in zip(header, cells):
                fields[k] = v
            claim = ""
            for k in CLAIM_KEYS:
                for h in header:
                    if k in h:
                        claim = fields.get(h, "")
                        break
                if claim:
                    break
            quote = ""
            for k in QUOTE_KEYS:
                for h in header:
                    if k in h:
                        quote = fields.get(h, "")
                        break
                if quote:
                    break
            if not claim:
                claim = cells[-1] if cells else ""
            seq += 1
            items.append({
                "id": f"I{seq:03d}",
                "section": section,
                "timecode": find_timecode(cells),
                "claim": claim,
                "quote": quote,
                "fields": fields,
                "status": "unreviewed",
                "verdict": "",
                "edited": "",
                "reason": "",
            })
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("markdown")
    ap.add_argument("--out", default="review.json")
    ap.add_argument("--html", default=None,
                    help="同时生成自包含审核台（双击即用，无需导入）")
    ap.add_argument("--only", nargs="*", default=None,
                    help="只导出这些小节（按小节名包含匹配）")
    ap.add_argument("--keep-meta", action="store_true",
                    help="保留无小节标题的表格（片源参数等元信息，通常不该审）")
    ap.add_argument("--title", default="")
    a = ap.parse_args()

    text = open(a.markdown, encoding="utf-8").read()
    items = parse(text)

    if not a.keep_meta:
        # 无小节的表格（片头参数）和"片源与证据"这类元信息表不该逐条审
        items = [x for x in items
                 if x["section"] and not any(k in x["section"] for k in SKIP_SECTIONS)]

    if a.only:
        items = [x for x in items if any(k in x["section"] for k in a.only)]

    # 过滤后重编号，否则审核台里 ID 会跳号
    for i, x in enumerate(items, 1):
        x["id"] = f"I{i:03d}"

    data = {
        "meta": {
            "title": a.title or a.markdown.split("/")[-1].replace(".md", ""),
            "source": a.markdown,
            "total": len(items),
        },
        "items": items,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"导出 {len(items)} 条 -> {a.out}")
    sec = {}
    for x in items:
        sec[x["section"]] = sec.get(x["section"], 0) + 1
    for k, v in sec.items():
        print(f"  {v:3d}  {k}")

    if a.html:
        tpl = Path(__file__).resolve().parent / "review.html"
        html = tpl.read_text(encoding="utf-8")
        # 必须注入在 <body> 开头：主逻辑在末尾执行，晚注入会读不到
        inject = ("<script>window.__DATA__="
                  + json.dumps(data, ensure_ascii=False)
                  + ";</script>")
        html = html.replace("<body>", "<body>\n" + inject, 1)
        Path(a.html).write_text(html, encoding="utf-8")
        print(f"审核台 -> {a.html}（双击打开，数据已内置）")


if __name__ == "__main__":
    main()
