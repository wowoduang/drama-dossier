# v4.3 人物状态、状态变化与主角向素材

## 能解决什么

按主角的目标、认知、处境、关系、外显反应和行动结果建立带证据的时点记录，人工确认状态变化，再按变化前后两端的事件提取素材。主角可以是用户指定的任意人物，不自动凭出镜次数判断主角。

沿用既有事实/检索/剪辑分层。单集 dossier 增加可选 `character_states`、`character_state_changes`，schema_version 仍兼容 3.5；旧档案缺字段按空数组处理，不补造状态。SQLite 原有 episode_payloads 已保存完整 dossier，无需新增表或手工迁移。更新 dossier 后重新 import，数据库仍是可重建投影。

## 字段和证据边界

| 字段 | 含义与约束 |
|---|---|
| state_id / change_id | 单集稳定 ID；与其他对象 ID 不得冲突 |
| character_id | 分集人物 ID，跨集关联沿用现有人工身份映射 |
| dimension | goal / knowledge / situation / relationship / emotion / action_result |
| aspect | 同一维度中的主题，例如 primary、求生目标、对同伴的信任 |
| value / description | 简短状态值及具体描述 |
| basis | OBSERVATION / EXPLICIT_STATEMENT / INTERPRETATION |
| certainty | 观察/明确陈述使用 F1_DIRECT 或 F2_CORROBORATED；解释使用 I1/I2/I3/X 等级 |
| quote | EXPLICIT_STATEMENT 必须引用 evidence.raw 中的原句 |
| target_character_id | relationship 必填，不能是自己 |
| playback_time | 记录的状态时点，不等于实际世界的故事年代 |
| audience_available_sec | 观众最早可从证据得知的时点，不能早于状态、事件及引用证据 |
| valid_until_sec | 可选的本集失效时点；不自动把旧状态跨集延续 |
| event_ids / evidence_ids | 有效本集引用；事件必须记录该人物的参与，不能只有别人提到主角 |
| knowledge_ids | 引用既有 knowledge_states，knowledge 维度必填；主体必须一致，不得引用未来认知 |
| editorial_priority | 可选的 0–100 人工优先级，默认 50，不是机器评估的精彩程度 |

“握拳、停顿、说出‘我要查清楚’”可记为观察或明确陈述；“绝望、决心复仇”需要相应依据，解释性判断必须保留 INTERPRETATION。本版保留审核过的解释记录，但不将其用于自动素材筛选。语言校验不能证明说话人或动机正确，仍须回看连续原片。

变化记录引用同人物、维度、aspect、关系对象的 from_state_id/to_state_id；后者时点必须更晚且值不同。变化时间等于后状态时间。event_ids 表示关联上下文，link_type 只允许 CONTEXT，**不把先后出现当作事件导致心理变化的证明**。跨集轨迹按各集记录排列；本版不自动建立跨集状态因果边。

JSON 结构见 `references/character-state.schema.json`。跨引用、时序、证据归属与审核有效性以 Python 校验为准。

## 1. 导入候选

```bash
python scripts/character_state.py import work/episode.json \
  work/state-candidates.json work/episode.states.json
python scripts/dossier_v3.py validate work/episode.states.json
```

候选必须有匹配的 episode_id 及 character_states/character_state_changes 数组。导入始终设为 UNREVIEWED，不接受 AI 自报 PASS，不覆盖旧文件，错误输入不会修改原档案。文件中的 ID 必须引用当前真实档案。

`examples/character-state-base.example.json` 和 `character-state-candidates.example.json` 是虚构教学数据，所有对象均待审，不代表对真实剧集的分析。

给 ChatGPT/人工整理候选时要求：只写实际观察或有原句支持的状态；使用原档案人物/事件/证据 ID；明确可见时点；不要从单帧自动判断心理，不因后期剧情已知就补写早期认知；无法证明状态转变时只记录单独状态或明确缺证。

## 2. 人工审核

启动现有本地服务器，打开 `web/character-states.html`，载入完整单集 dossier 和对应本地原片。页面展示状态/变化、引用事件和证据、可见时点及审核历史。填写审核者、理由，PASS 需要确认已核对连续原片。导出的是 dossier 副本；导出后运行 Python 校验，再导入全剧记忆。

CLI 等价操作（先审核证据、人物、关联事件和认知，再审核状态，最后审核状态变化）：

```bash
python scripts/character_state.py review work/episode.states.json S1 \
  work/episode.states.r1.json --status PASS --reviewer editor \
  --reason "已核实连续片段、说话人、台词和观众可见时点" --source-checked
```

也可用 `dossier_v3.py review ... --source-checked`，新状态对象同样要求审核者。支持 PASS、INCORRECT、NEED_EVIDENCE、DISPUTED。审核记录追加 old_value/new_value、理由、审核者、时间和依赖快照。仅写 review_status=PASS 不足以被使用；状态、人物、事件、认知或证据变化后需重新审核。

```bash
python scripts/series_memory.py import series.sqlite \
  work/episode.states.reviewed.json --series-id SERIES01 --replace
```

`import` 是现有命令；初次导入无需 --replace。替换分集后，引用该集的旧 series_visibility 声明会重开为 NEED_EVIDENCE 并写入审计，需要重新声明，避免旧身份揭示继续被当作有效资料。

## 3. 查主角轨迹与截止时点状态

先沿用 `series_memory.py link-character` 审核跨集身份，并用 `series_visibility.py` 声明该身份何时已对观众可知。状态查询要求有效的身份可见性声明，按各集本地名字展示，不输出后期全局别名。

```bash
python scripts/character_state.py timeline series.sqlite work/hero-timeline.json \
  --series-id SERIES01 --character GCHR_xxx --as-of 15
python scripts/character_state.py timeline series.sqlite work/hero-ep15-600s.json \
  --series-id SERIES01 --character GCHR_xxx --as-of EP015 --at-sec 600 --dimension goal
```

查询只纳入截止集及截止秒数前已对观众可见、审核有效且有直接证据的记录，支持六种 dimension 筛选。身份声明的引用证据同样不能晚于集内截止秒数。

输出 states、changes、latest_in_cutoff_episode、conflicts。latest 只表示截止集中最新可用记录，不能断言整个人物当前的所有心理；没有本集状态不从上一集自动延续。新的记录已失效时，不让更早记录自动“复活”。同一时点/维度/主题的冲突记录保留提示并阻止自动选用。

全剧 `export-memory` 也会保留带 episode_id 的状态与变化，但它仍是全剧管理视图，不是截止时点安全视图。对观众提供资料应使用上述 timeline。

## 4. 按状态变化编译素材

```bash
python scripts/character_state.py compile series.sqlite work/hero-state-cut.json \
  --series-id SERIES01 --character GCHR_xxx --as-of 15 \
  --dimension goal --sources sources.json --target-sec 180
```

编译优先处理显式审核的变化组，并把变化前后两端及关联上下文事件一起纳入。窗口来自这些记录和事件的 PASS 证据，不直接沿用可能覆盖未来内容的高光标题/时间窗。按照集数和原片时间排序，保留 state_id/change_id/event_id/evidence_id 追踪关系。片段同时保留状态证据的完整时间范围；导演替代不得裁掉这些证据，手工精修若裁掉会触发 QC 阻塞。若同组超过目标时长，跳过并解释原因，不单独留下反击结果。无完整可用组时明确报错，不能编造铺垫。

没有合格变化时，可提取有事件锚点的独立状态片段。缺少证据、原片、事件参与记录或存在状态冲突时，不声称已构成完整人物弧线。优先级来自人工记录；素材组合不保证最佳叙事或审美。

新项目带 v4.2 版本审核门槛，默认为 UNREVIEWED。导演建议保留状态 ID；已有故事弧 make-prompt 在人物/双人物选择下加入截止集内的可用状态上下文，并提示不得倒灌较晚状态或把 CONTEXT 当作因果。

## 5. 精修、批准与渲染复核

```bash
python scripts/director_engine.py work/hero-state-cut.json work/hero-director.json
python scripts/edit_versions.py approve work/hero-director.json work/hero-approved.json \
  --reviewer editor --reason "核实变化两端、对白、连接与新入出点"
python scripts/review_cut.py work/hero-approved.json output/hero-cut-001
```

可继续使用 v4.2 的 A/B、版本比较、合并和反馈返工。人物状态审核通过不等于剪辑选择通过，更不等于最终成片审美通过。状态关联标识只是追踪信息；在剪辑工作台手工改动事件、来源或时间后，应重新核对对应状态。

## 能力边界

本版新增的是证据化状态记录、审核、时点检索和保守素材编译，没有新增人脸识别、情绪识别、自动读心或全自动人物成长分析。知识仍由原 knowledge_states 管理，状态层引用其 ID，不覆写原认知事实。完整视频理解、人物动机、状态转变语义和成片审美依然需要 ChatGPT 结合连续素材及人工审核。
