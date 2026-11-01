#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
relation_scan.py — 从字幕稿机械抽出「人物 / 事实 / 关系」的全部候选，供人工定稿。

不做主题解读，不做金句锚点。只干三件事：
  1. 人物：谁被怎么叫、叫了多少次、什么时候开始/停止被这么叫
  2. 关系：呼语出处（谁喊谁什么）、称谓两两同场共现、关系事件候选（谁对谁做了什么）
  3. 事实：按场景切好的完整台词流，每场能确认在场的称谓/人名

输出:
  relation_scan.json   结构化数据，给审核台 / 后续脚本用
  人物关系扫描.md      可直接当档案骨架

用法:
  python relation_scan.py 片子.subtitle.json
  python relation_scan.py 片子.subtitle.json --gap 8 -o ./out
  python relation_scan.py 片子.subtitle.json --names 朱重八,郭子兴,马秀英
  python relation_scan.py 片子.subtitle.json --extra-titles 大帅,先锋官
  python relation_scan.py 片子.subtitle.json --keep-credits   # 不剔片头演职员表
"""
import json
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict, OrderedDict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------- 称谓词表
# 只收「台词里真的会用来叫人」的词，不收抽象名词
TITLES = OrderedDict([
    ("亲缘", ["爹娘", "爹", "娘", "父", "母", "大哥", "二哥", "哥", "姐", "妹", "弟",
              "儿子", "女儿", "娘子", "媳妇儿", "媳妇", "叔", "伯", "婶", "爷爷", "奶奶",
              "干爹", "干娘", "义父", "义母", "岳父", "岳母", "女婿", "驸马"]),
    ("官职", ["将军", "元帅", "丞相", "大人", "陛下", "皇上", "圣上", "御史", "指挥使",
              "先锋", "总兵", "知县", "知府", "都督", "主公", "公子", "少爷",
              "老爷", "太守", "县令", "尚书", "侍郎", "公公", "千岁", "王爷", "大帅"]),
    ("军阶", ["头儿", "百户", "千户", "把总", "哨长", "什长", "弟兄们", "兄弟们", "士兵"]),
    ("社交", ["大哥", "兄弟", "朋友", "掌柜", "姑娘", "小姐", "夫人", "太太", "大叔",
              "大爷", "大伯", "老人家", "老丈", "先生", "师父", "师叔", "师兄", "师弟"]),
    ("贬称", ["小子", "秃头", "秃驴", "和尚", "泼皮", "探子", "元军探子", "死囚",
              "囚犯", "贱民", "叫花子", "贼", "反贼", "鞑子"]),
])

# 关系事件：动词 → 语义类。硬字幕没有说话人，所以只记「动词 + 对象」的候选
VERBS = OrderedDict([
    ("杀", ["处死", "斩首", "杀掉", "杀了", "杀", "砍了", "砍", "宰了", "宰", "毙了"]),
    ("救", ["救命", "救", "放了", "放走", "饶了", "饶", "护着", "挡下"]),
    ("令", ["下令", "吩咐", "命令", "派", "让", "叫", "交给你", "交给"]),
    ("赏封", ["赏", "赐", "封", "升", "举荐", "提拔"]),
    ("罚迫", ["抽", "罚", "绑了", "绑", "押", "关起来", "赶出去", "赶", "骂", "训",
              "打", "打死", "劈了", "劈"]),
    ("收留", ["收留", "收下", "留下", "跟着我", "跟着你", "当我", "做我的", "安置"]),
    ("投靠", ["投奔", "投靠", "归顺"]),
    ("给送", ["递给", "给了", "送", "分给", "分"]),
    ("结盟", ["结拜", "拜为", "认作", "认我", "认你", "认"]),
])

# 动词表里被误伤的常见词，命中则整条丢弃
VERB_FALSE_POS = [
    "打仗", "打架", "打开", "打发", "打量", "大不了", "叫花子", "叫你", "叫我",
    "叫什么", "让我说", "送你", "送来", "送死", "分不清", "分开", "认字", "认识",
    "认识你", "认不得", "关门", "关心", "关系", "杀价", "杀出", "吩咐过",
]

# 片头片尾 / 版权信息，不是台词
CREDITS = ("出品人", "制片人", "执行制片", "导演", "编剧", "主演", "监制", "摄影",
           "美术", "录音", "剪辑", "化妆", "服装", "道具", "统筹", "场记", "灯光",
           "许可证", "发行许可证", "广播电视", "字幕组", "翻译", "校对", "压制",
           "片头曲", "片尾曲", "主题曲", "作词", "作曲", "演唱")

SURNAMES = set(
    "赵钱孙李周吴郑王冯陈蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹苏潘葛范彭"
    "鲁韦马苗方俞任袁柳鲍史唐薛雷贺倪汤滕殷罗毕郝安常于傅齐康伍余元顾孟黄穆"
    "萧尹姚汪毛狄米贝明戴谈宋庞熊纪舒屈项祝董梁杜阮蓝闵季麻贾路江童颜郭梅盛"
    "林钟徐邱骆高夏蔡田樊胡凌霍虞万卢莫房")

# 称谓前面不该被吃进来的字（代词、量词、指示词）
BAD_PREFIX = set("你我他她它这那哪们众诸位个两几谁的了着过在是有和跟给对向把被"
                 "不就都也还很太更最一二三四五六七八九十百千万")
# 排行：八弟、三哥，虽然含数字但确实是称呼的一部分
RANK = set("大二三四五六七八九十小")
# 礼貌词 / 动词，紧贴称呼但不是名字的一部分
NOT_NAME = ("谢谢", "感谢", "你好", "请问", "拜见", "参见", "见过", "回禀", "禀报",
            "报告", "不知道", "好个", "快叫", "去叫", "就叫", "敢叫")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fmt(t):
    m, s = divmod(float(t), 60)
    return f"{int(m):02d}:{s:05.2f}"


def clean(t):
    return re.sub(r"\s+", "", t)


def clauses(text):
    """把一句字幕切成小句（用于判断呼语在句首还是句末）"""
    out, buf = [], ""
    for ch in clean(text):
        if ch in "，。、？！：；…—～·,.?!:;":
            if buf:
                out.append(buf)
                buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def strip_credits(subs):
    """剔掉片头片尾演职员表 / 许可证号这类非台词行"""
    kept, drop = [], []
    for r in subs:
        t = clean(r["text"])
        if any(k in t for k in CREDITS) or len(re.findall(r"[\u4e00-\u9fa5]", t)) == 0:
            drop.append({"t": round(r["start"], 2), "text": r["text"].strip()})
        else:
            kept.append(r)
    return kept, drop


def build_title_index(extra=()):
    idx = defaultdict(list)
    for cat, words in TITLES.items():
        for w in words:
            if cat not in idx[w]:
                idx[w].append(cat)
    for w in extra:
        w = w.strip()
        if w and w not in idx:
            idx[w] = ["自定义"]
    return idx


def find_titles(text, idx):
    """长词优先匹配，已占用的字符不再匹配，避免「大哥」被「哥」重复计"""
    text = clean(text)
    hits, taken = [], [False] * len(text)
    for w in sorted(idx, key=len, reverse=True):
        start = 0
        while True:
            p = text.find(w, start)
            if p < 0:
                break
            if not any(taken[p:p + len(w)]):
                for i in range(p, p + len(w)):
                    taken[i] = True
                hits.append((p, w))
            start = p + 1
    hits.sort()
    return hits


def expand_voc(text, pos, word, names):
    """把「叔」还原成「郭大叔」、「哥」还原成「重八哥」、「弟」还原成「八弟」"""
    pre = text[:pos]
    out = word
    if any(pre.endswith(b) for b in NOT_NAME):
        return out
    for n in (2, 1):
        if len(pre) < n:
            continue
        frag = pre[-n:]
        is_name = frag in names or any(nm.startswith(frag) or nm.endswith(frag)
                                       for nm in names)
        if is_name:                       # 已知人名的片段，直接并入
            return frag + out
        if any(ch in BAD_PREFIX for ch in frag) and frag not in RANK:
            continue
        if frag[0] in SURNAMES or frag in RANK:
            return frag + out
    return out


def scan_titles(subs, idx):
    stat = OrderedDict()
    for w in sorted(idx, key=lambda x: -len(x)):
        rows = [r for r in subs if w in clean(r["text"])]
        if not rows:
            continue
        stat[w] = {
            "word": w,
            "cats": idx[w],
            "count": len(rows),
            "first": round(rows[0]["start"], 2),
            "last": round(rows[-1]["start"], 2),
            "span_min": round((rows[-1]["start"] - rows[0]["start"]) / 60, 1),
            "times": [round(r["start"], 2) for r in rows],
            "lines": [{"t": round(r["start"], 2), "text": r["text"].strip()} for r in rows],
        }
    return stat


def scan_vocatives(subs, idx, names):
    """呼语：整句或整小句就是一个称呼，是「谁喊谁」最硬的证据位置"""
    out = []
    for r in subs:
        cs = clauses(r["text"])
        for i, c in enumerate(cs):
            if len(c) > 8:
                continue
            hits = find_titles(c, idx)
            if not hits:
                continue
            rest = c
            full = []
            for p, w in hits:
                v = expand_voc(c, p, w, names)
                full.append(v)
                rest = rest.replace(v, "", 1)
            if len(rest) > 3:
                continue
            conf = "高" if len(rest) <= 1 else ("中" if len(rest) <= 2 else "低")
            out.append({
                "t": round(r["start"], 2),
                "text": r["text"].strip(),
                "vocative": full,
                "base": [w for _, w in hits],
                "pos": "句首" if i == 0 else ("句末" if i == len(cs) - 1 else "句中"),
                "rest": rest,
                "conf": conf,
            })
    return out


def split_scenes(subs, gap):
    scenes, cur = [], []
    for r in subs:
        if cur and r["start"] - cur[-1]["end"] > gap:
            scenes.append(cur)
            cur = []
        cur.append(r)
    if cur:
        scenes.append(cur)
    return scenes


def strong_entities(scenes, names, min_len=2):
    """共现矩阵只用强实体：已知人名 + 长度≥2 的称谓。
    单字称谓（哥/弟/兄/娘）几乎每场都出现，纳入统计只会淹没信号。"""
    ents = set(names)
    for sc in scenes:
        for w in sc["titles"]:
            if len(w) >= min_len:
                ents.add(w)
    return ents


def scan_scenes(subs, idx, names, gap):
    scenes = split_scenes(subs, gap)
    out = []
    for i, sc in enumerate(scenes, 1):
        blob = " ".join(clean(r["text"]) for r in sc)
        titles = [w for w in sorted(idx, key=len, reverse=True) if w in blob]
        nm = [n for n in names if n in blob]
        out.append({
            "no": i,
            "start": round(sc[0]["start"], 2),
            "end": round(sc[-1]["end"], 2),
            "lines": len(sc),
            "titles": titles,
            "names": nm,
            "head": sc[0]["text"].strip(),
            "tail": sc[-1]["text"].strip(),
            "texts": [{"t": round(r["start"], 2), "text": r["text"].strip()} for r in sc],
        })
    return out


def cooccur(scenes, ents):
    pair = defaultdict(int)
    for sc in scenes:
        e = sorted((set(sc["titles"]) | set(sc["names"])) & ents)
        for a in range(len(e)):
            for b in range(a + 1, len(e)):
                pair[(e[a], e[b])] += 1
    rows = [{"a": k[0], "b": k[1], "n": v} for k, v in pair.items() if v >= 2]
    rows.sort(key=lambda x: -x["n"])
    return rows


def _event_of(text, idx, names, allwords):
    """在一段文本里找「动词 + 对象」"""
    if any(fp in text for fp in VERB_FALSE_POS):
        return None
    who = [w for w in sorted(allwords, key=len, reverse=True) if w in text]
    if not who:
        return None
    for cat, vs in VERBS.items():
        v = next((x for x in sorted(vs, key=len, reverse=True) if x in text), None)
        if not v:
            continue
        # 自报姓名 / 问名字：「我叫朱重八」「叫什么马姑娘」不是关系事件
        if v in ("叫", "让", "认") and re.search(r"(我|俺|咱)(就)?(叫|让|认)", text):
            return None
        if "什么" in text and v in ("叫", "让"):
            return None
        return cat, v, who
    return None


def scan_events(subs, idx, names, win=2, maxgap=3.0, maxlen=30):
    """单句抽 + 相邻窗口抽。字幕被拆行时，动词和对象常落在相邻两条里。"""
    allwords = list(idx) + list(names)
    out, seen = [], set()
    for i, r in enumerate(subs):
        # 单句
        for c in clauses(r["text"]):
            ev = _event_of(c, idx, names, allwords)
            if ev:
                key = (round(r["start"]), ev[1], tuple(ev[2]))
                if key not in seen:
                    seen.add(key)
                    out.append({"t": round(r["start"], 2), "cat": ev[0], "verb": ev[1],
                                "who": ev[2], "text": r["text"].strip(), "win": 1})
        # 相邻窗口
        buf, tend = clean(r["text"]), r["end"]
        for j in range(1, win):
            if i + j >= len(subs):
                break
            nxt = subs[i + j]
            if nxt["start"] - tend > maxgap:
                break
            buf += clean(nxt["text"])
            tend = nxt["end"]
            if len(buf) > maxlen:
                break
            ev = _event_of(buf, idx, names, allwords)
            if ev:
                key = (round(r["start"]), ev[1], tuple(ev[2]))
                if key not in seen:
                    seen.add(key)
                    out.append({"t": round(r["start"], 2), "cat": ev[0], "verb": ev[1],
                                "who": ev[2], "text": buf, "win": j + 1})
    out.sort(key=lambda x: x["t"])
    # 合并：同一动作在 2 秒内被单句和跨条窗口各命中一次，who 取并集
    merged = []
    for e in out:
        if merged:
            p = merged[-1]
            if (p["cat"] == e["cat"] and p["verb"] == e["verb"]
                    and e["t"] - p["t"] <= 3.0):
                for w in e["who"]:
                    if w not in p["who"]:
                        p["who"].append(w)
                if len(e["text"]) < len(p["text"]):
                    p["text"] = e["text"]
                p["win"] = min(p["win"], e["win"])
                continue
        merged.append(dict(e))
    return merged


def merge_streaks(times, gap=30.0):
    if not times:
        return []
    segs, s, p = [], times[0], times[0]
    for t in times[1:]:
        if t - p > gap:
            segs.append((s, p))
            s = t
        p = t
    segs.append((s, p))
    return segs


def render_md(name, subs, titles, voc, scenes, pairs, events, names, dropped, ents):
    L = []
    A = L.append
    A(f"# 《{name}》人物 · 关系 · 事实扫描")
    A("")
    A("> 本文件是**候选**，不是结论。每条都带时间码，可直接抽帧复核。")
    A("> 人物归并、说话人归属、亲缘关系三项机器做不了，必须人工确认。")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| 字幕条数（已剔片头片尾） | {len(subs)} |")
    A(f"| 覆盖区间 | {fmt(subs[0]['start'])} – {fmt(subs[-1]['end'])} |")
    A(f"| 场景数 | {len(scenes)} |")
    A(f"| 命中称谓 | {len(titles)} 种 |")
    A(f"| 呼语证据 | {len(voc)} 处 |")
    A(f"| 关系事件候选 | {len(events)} 条 |")
    A(f"| 剔除的非台词行 | {len(dropped)} 条 |")
    A("")

    A("## 1 称谓清单")
    A("")
    A("| 称谓 | 类别 | 次数 | 首次 | 末次 | 跨度 | 出场段数 |")
    A("|---|---|---|---|---|---|---|")
    for w, d in sorted(titles.items(), key=lambda x: -x[1]["count"]):
        A(f"| {w} | {'/'.join(d['cats'])} | {d['count']} | `{fmt(d['first'])}` | "
          f"`{fmt(d['last'])}` | {d['span_min']}分 | {len(merge_streaks(d['times']))} |")
    A("")
    A("> 查看某个称谓的全部出处：`python query.py 字幕.json 重八`。")
    A("> **同一人物被叫多个称呼时不要急着合并**——同一场里两个称呼交替出现可能是两个人；"
      "一个称呼在某秒后彻底消失，通常是关系变了。")
    A("")

    A("## 2 呼语证据（谁喊谁什么）")
    A("")
    A("| 时间码 | 呼语 | 位置 | 可信 | 原句 |")
    A("|---|---|---|---|---|")
    for v in voc:
        A(f"| `{fmt(v['t'])}` | **{'、'.join(v['vocative'])}** | {v['pos']} | {v['conf']} | "
          f"{v['text']} |")
    A("")
    A("> 呼语只证明「有人在喊这个身份」，**不证明喊的人是谁**。说话人归属必须抽帧看镜头/口型，"
      "或靠上下文一问一答确认。可信「低」的多半不是呼语，是普通句子。")
    A("")

    A("## 3 场景清单（事实层）")
    A("")
    A("| 场 | 起止 | 句数 | 本场出现的称谓/人名 | 首句 | 尾句 |")
    A("|---|---|---|---|---|---|")
    for sc in scenes:
        who = "、".join(sc["names"] + sc["titles"]) or "—"
        A(f"| {sc['no']:02d} | `{fmt(sc['start'])}–{fmt(sc['end'])}` | {sc['lines']} | {who} | "
          f"{sc['head']} | {sc['tail']} |")
    A("")

    A("## 4 共现矩阵（同场次数 ≥2，仅强实体）")
    A("")
    A("> 只统计已知人名和长度≥2 的称谓。单字称谓（哥/弟/兄/娘）几乎每场都有，纳入会淹没信号。")
    A("")
    A("| A | B | 同场次数 |")
    A("|---|---|---|")
    for p in pairs[:40]:
        A(f"| {p['a']} | {p['b']} | {p['n']} |")
    A("")
    A("> 共现只说明「这两个称呼在同一场戏里都被提到」，可能是对话双方，也可能只是被同时议论。"
      "**不能直接当关系。**")
    A("")

    A("## 5 关系事件候选")
    A("")
    A("| 时间码 | 类别 | 动词 | 涉及 | 原句 | 跨条 |")
    A("|---|---|---|---|---|---|")
    for e in events:
        A(f"| `{fmt(e['t'])}` | {e['cat']} | {e['verb']} | {'、'.join(e['who'])} | "
          f"{e['text']} | {e['win']} |")
    A("")
    A("> 「涉及」里的称呼只说明这句话谈到了谁，**说话人和受事方都还要人工定**。跨条=1 是单句命中，"
      "2–3 是相邻字幕拼起来才命中（字幕断行）。")
    A("")

    A("## 6 必须人工确认的三件事")
    A("")
    A("| # | 待确认 | 为什么机器做不了 | 怎么确认 |")
    A("|---|---|---|---|")
    A("| 1 | 称谓归并（哪些称呼指同一个人） | 同人异称与撞称字面无法区分 | 抽帧比对造型 + 看两人是否同场出现 |")
    A("| 2 | 说话人归属（这句话谁说的） | 硬字幕不带说话人 | 抽该秒画面看口型/镜头，或找一问一答 |")
    A("| 3 | 亲缘/从属关系（父子、上下级） | 台词常省略，称谓只能猜 | 找明确台词（「我爹」「末将」「属下」）+ 官方简介 |")
    A("")

    if dropped:
        A("## 附：被剔除的非台词行")
        A("")
        A("> 确认没有误杀台词。有误杀就加 `--keep-credits` 重跑。")
        A("")
        A("| 时间码 | 原文 |")
        A("|---|---|")
        for d in dropped:
            A(f"| `{fmt(d['t'])}` | {d['text']} |")
        A("")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("subtitle_json")
    ap.add_argument("-o", "--out", default=None, help="输出目录，默认与字幕同目录")
    ap.add_argument("--gap", type=float, default=8.0, help="场景切分空档(秒)")
    ap.add_argument("--names", default="", help="已知人名，逗号分隔，参与共现与呼语扩展")
    ap.add_argument("--extra-titles", default="", help="补充称谓词，逗号分隔")
    ap.add_argument("--title", default="", help="片名，默认取字幕文件名")
    ap.add_argument("--keep-credits", action="store_true", help="不剔除片头演职员表")
    a = ap.parse_args()

    raw = load(a.subtitle_json)
    if not raw:
        print("字幕为空")
        return 1

    subs, dropped = (raw, []) if a.keep_credits else strip_credits(raw)
    if not subs:
        print("剔除片头片尾后没有剩下台词，检查 CREDITS 关键词或加 --keep-credits")
        return 1

    names = [x.strip() for x in a.names.split(",") if x.strip()]
    extra = [x.strip() for x in a.extra_titles.split(",") if x.strip()]
    idx = build_title_index(extra)

    titles = scan_titles(subs, idx)
    voc = scan_vocatives(subs, idx, names)
    scenes = scan_scenes(subs, idx, names, a.gap)
    ents = strong_entities(scenes, names)
    pairs = cooccur(scenes, ents)
    events = scan_events(subs, idx, names)

    name = a.title or Path(a.subtitle_json).stem.replace(".subtitle", "")
    data = {
        "meta": {
            "title": name,
            "source": a.subtitle_json,
            "lines": len(subs),
            "dropped": len(dropped),
            "span": [round(subs[0]["start"], 2), round(subs[-1]["end"], 2)],
            "gap": a.gap,
            "names": names,
            "strong_entities": sorted(ents),
        },
        "titles": titles,
        "vocatives": voc,
        "scenes": scenes,
        "pairs": pairs,
        "events": events,
        "dropped": dropped,
    }

    outdir = Path(a.out) if a.out else Path(a.subtitle_json).parent
    outdir.mkdir(parents=True, exist_ok=True)
    jp = outdir / "relation_scan.json"
    mp = outdir / "人物关系扫描.md"
    jp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_md(name, subs, titles, voc, scenes, pairs, events, names, dropped, ents),
                  encoding="utf-8")

    print(f"字幕 {len(raw)} 条，剔除片头片尾 {len(dropped)} 条，剩 {len(subs)} 条")
    print(f"{fmt(subs[0]['start'])}–{fmt(subs[-1]['end'])}　称谓 {len(titles)} 种 / "
          f"呼语 {len(voc)} 处 / 场景 {len(scenes)} 场 / 事件 {len(events)} 条")
    print(f"-> {jp}")
    print(f"-> {mp}")
    print("\n下一步：人物归并与说话人归属必须人工确认，然后跑 make_review.py 出审核台。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
