# AI 剧情精彩剪辑工作流

## 1. 原则

精彩剪辑建立在已结构化剧情上，不直接从“声音大、镜头快”推导剧情价值。先理解事件，再评估是否值得剪；评分只是排序依据，不是真相或审美事实。

## 2. 七维评分

每个重要事件可写 `highlight_dimensions`，每项 0–100：

- `importance`：是否改变目标、关系、局面或后续剧情。
- `emotion`：情绪强度与感染力。
- `conflict`：对抗、阻碍、选择压力。
- `twist`：身份/真相/立场变化或反预期程度。
- `suspense`：是否制造或推进未解问题。
- `visual`：画面动作、表演、构图或视觉信息承载力。
- `standalone`：脱离大量前文后是否仍可理解。

不要仅因音乐、音量或快速剪辑把 `importance` 评高。身份揭示、死亡、生还、背叛、关系反转等高风险事件必须先核证。

## 3. 上下文需求

`context_required`：

- `low`：单独看基本成立，适合 30/60 秒高能剪辑。
- `medium`：需要前后 1–2 个事件铺垫。
- `high`：脱离长上下文容易误解，短视频中应补桥接片段或避免单独使用。

## 4. 候选生成

先运行多模态采集和剧情弧线候选生成，查看 `story_arcs` 中的 `setup / peak / reaction / payoff`。只有经过连续原片核证的弧线才应转成正式高光；缺失段落不要硬补，无法脱离上下文的片段保留 `context_required=high`。弧线来源通过 `source_arc_id / story_arc_ids` 追踪到剪辑项目。

优先让模型根据已审核事件写入七维评分、`highlight_title`、`highlight_reason` 和 `context_required`。然后运行：

```bash
python scripts/highlight_engine.py generate work/episode.json --preset 60s
```

脚本只负责把事件转换成可审核候选，并根据场景边界给出保守前后缓冲。若事件没有 AI 评分，脚本使用保守启发式并标记 `score_source=deterministic_fallback`。

## 5. 人工精修

启动：

```bash
python scripts/serve_review.py
```

打开 `web/workbench.html`：

1. 载入剧情档案与原片。
2. 点击高光候选回看。
3. 调整起止点，不要切断关键动作、句子或反应镜头。
4. 检查入选理由与七维评分。
5. 对会改变剧情理解的候选先去 `review.html` 核证。
6. 选择 30 秒、60 秒、3 分钟或 5 分钟预设生成序列。
7. 连续预览；必要时人工重排、删除或补桥接片段。
8. 导出 dossier JSON 与 edit-project JSON。

## 6. 成片原则

- 默认保持故事时间顺序；只有明确需要钩子式开场时才打乱。
- 不把计划、想象、回忆剪成“现实已发生”。
- 不删除会反转一句台词含义的否定、条件或回答。
- 不利用断章取义制造与正片相反的角色立场。
- 对高上下文片段，优先补 setup / reaction / payoff，而不是单纯延长同一镜头。
- 30/60 秒更偏高能密度；3/5 分钟更偏剧情完整性和独立观看价值。

## 7. 渲染

从档案生成剪辑项目：

```bash
python scripts/highlight_engine.py select work/episode.json work/edit-project.json --preset 60s --video /path/to/episode.mp4
```

渲染：

```bash
python scripts/render_project.py work/edit-project.json work/highlight.mp4 --video /path/to/episode.mp4
```

渲染器当前使用硬切和 H.264/AAC，目的是保证可复现与时间边界准确；字幕样式、转场、BGM、画幅重构属于后续美化层，不应反向影响剧情事实层。
