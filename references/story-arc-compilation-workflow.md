# v3.7 跨集故事弧编译流程

## 目标

将全剧素材检索升级为有叙事角色、有连续性依据、考虑人物认知状态的跨集剪辑。Python 不代替 ChatGPT 作语义判断；ChatGPT 根据全剧记忆提出计划，编译器验证引用、时间、审核状态和连接依据。

## 1. 为单集补充认知状态

在 dossier 的 `knowledge_states` 中记录某个时点某个角色或观众对某个命题的认知：

- `subject_type`: `CHARACTER` 或 `VIEWER`
- `character_id`: `CHARACTER` 必填；`VIEWER` 必须为 null
- `proposition`: 被知道/怀疑/误信的命题
- `state`: `KNOWS / SUSPECTS / BELIEVES / FALSE_BELIEF / UNAWARE / FORGOT / UNCERTAIN`
- `playback_time`、`event_id`、`evidence_ids`
- `review_status`: 只有 `PASS` 才参与剪辑时点认知回溯

认知状态必须按播放时点记录。后续揭晓不能倒灌成角色在前几集早已知道。

## 2. 导入与核验跨集因果

分集 `causal_edges` 只能引用同集事件。跨集因果另用全剧数据库记录，初始状态为 `UNREVIEWED`：

```bash
python scripts/series_memory.py add-causal-link work/series.sqlite EP001:EVT001 EP008:EVT021 --series-id SERIES01 --type CAUSES --reasoning "前一事件为后一事件提供直接动机" --evidence-ref EP001:EVD001 --evidence-ref EP008:EVD021
```

人工回看片段后再审核：

```bash
python scripts/series_memory.py review-causal-link work/series.sqlite XCAU_xxxxx --series-id SERIES01 --status PASS --reason "核对两集原片及证据" --reviewer "审核者"
```

`CAUSES` 不应轻率使用。若只知道前后发生，可用 `FOLLOWS`；若证据不足，保留 `POSSIBLE_CAUSE` 或不建立因果边。

## 3. 让 ChatGPT 制作叙事计划

按人物/关系/剧情线/伏笔生成规划上下文：

```bash
python scripts/story_arc_compiler.py make-prompt work/series.sqlite work/arc-prompt.md --series-id SERIES01 --character GCHR_001 --goal "从信任、发现背叛、决定复仇到复仇结果" --full-series
```

把 `arc-prompt.md` 提供给 ChatGPT，要求它只引用真实的 `episode_id/event_id`，输出符合 `references/story-arc-plan.schema.json` 的 JSON。也可参考 `examples/story-arc-plan.example.json`。

角色：

- `setup`: 建立人物目标、关系、风险或前提
- `inciting_incident`: 触发主线的事件
- `escalation`: 冲突升级
- `peak`: 核心冲突/揭示/高潮
- `reaction`: 人物即时反应或决策
- `payoff`: 前面铺垫/线索的兑现
- `resolution`: 结果/余波
- `bridge`: 必要衔接，不可冒充剧情事件

若某角色缺失，放进 `missing_roles`。禁止补写不存在的场面。

## 4. 编译为剪辑项目

```bash
python scripts/story_arc_compiler.py compile work/series.sqlite work/sources.json work/story-arc-plan.json work/story-arc-edit.json
```

严格发布检查：

```bash
python scripts/story_arc_compiler.py compile work/series.sqlite work/sources.json work/story-arc-plan.json work/story-arc-edit.json --require-release-ready
```

默认只接收 `PASS` 事件。计划必须声明时间顺序；若选择 `EDITORIAL_NONLINEAR`，必须写 `reorder_reason`。编译器不会为满足 5 分钟目标而自动丢掉必要叙事角色；超时只提示人工精修。

## 5. 认知与叙事风险

每段剪辑会附上该时点人物视角和观众视角下最新的已审核认知状态，用于检查角色是否已知秘密、观众是否先于角色知道、是否提前泄露后续信息、是否把计划/命令/威胁误剪成行动已执行。

缺少认知记录时显示“未知”，不能推断成“角色不知道”。

## 6. 发布门槛

`release_ready=true` 至少要求：所有必需叙事角色出现、原片源完整、所有事件通过审核、计划与段落通过审核、关键连续性连接有可验证依据、跨集因果边已人工确认。超时不自动删剧情；在跨集工作台精修后再渲染。
