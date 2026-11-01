# setup / peak / reaction / payoff 剧情弧线候选

## 1. 定义

- **setup**：建立人物目标、关系、风险、信息或行动条件的铺垫。
- **peak**：局部冲突/情绪/信息变化最集中的事件窗口。
- **reaction**：峰值之后的即时人物反应或局面变化。
- **payoff**：后续结果、伏笔回收或前述行动的可验证后果。

这些标签是候选叙事角色，不是由音量、镜头数量或时间顺序自动证明的事实。

## 2. 候选生成

```bash
python scripts/arc_candidates.py work/episode.json work/episode.multimodal.json \
  --max-arcs 100 --min-signal 0.42 --pre-roll 20 --max-payoff-gap 45
```

锚点来自：

- 已有事件与其邻近多模态线索；
- 一段时间内聚集的镜头变化、音量峰值和字幕线索。

若锚点对应事件，系统优先连接：

- 与锚点事件共享 `storyline_id` 的前后事件；
- 与锚点事件存在非 `INCORRECT` 直接因果边的事件。

`payoff` 只从同剧情线或直接因果关联的后续事件中提出。单纯时间上较晚的事件不能自动成为 payoff。

## 3. 结果字段

- `anchor_sec` / `anchor_event_id`：峰值候选锚点。
- `segments[]`：setup、peak、reaction、payoff 的候选时间窗及对应事件/cue ID。
- `missing_roles[]`：缺少语义支持的角色。
- `risk_flags[]`：无结构化事件、非现实模态、上下文依赖、缺少 payoff 等风险。
- `semantic_questions[]`：供 ChatGPT/审核者逐项回答的问题。
- `review_status`：初始为 `UNREVIEWED`。

## 4. 人工审核原则

1. 对每段回看前后至少数秒的连续原片。
2. 检查动作是否连续，切镜是否改变空间/时间/人物。
3. 对照字幕和音轨，确认台词、否定词、条件句和说话人不确定性。
4. 判断 reaction 是否真的回应 peak，而非同一时段的另一个故事线。
5. 判断 payoff 是否是结果/回收，或只是后来发生的无关事件。
6. 片段脱离上下文容易误导时设置 `context_required=high`，不要硬塞入 30 秒短片。

## 5. 不完整弧线也有价值

弧线不必强行凑齐四段。悬念可以没有 payoff；冷开场可以没有 setup；突发事件可以没有明显反应镜头。缺失角色应该留在 `missing_roles`，让剪辑者决定是否补桥接素材或放弃该候选。
