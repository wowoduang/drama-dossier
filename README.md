# Drama Dossier v4.3.0

证据驱动的影视剧情理解 + 多模态精彩片段发现 + 剪映式人工精修 Skill。
v3.8 在 v3.7 跨集故事弧之上，增加截止集数强制的剧透安全检索、人工声明跨集事实可见时间、相似镜头候选检测和人工去重审核。单集 dossier schema 与 Series Memory Engine 继续沿用各自版本化数据结构。

## 架构

1. **ChatGPT Skill**：理解人物、事件、关系、因果、剧情线；查看关键帧与转录片段；给出可核证的语义观察和事件候选。
2. **Python 采集/分析引擎**：FFmpeg 镜头变化、RMS 音频峰值/低能量窗口、字幕关键词、均匀抽帧、联系表、弧线候选、档案校验、剪辑项目和成片渲染。
3. **本地 Web 工作台**：`review.html` 负责证据/事实审核与 AI 视觉审核导入；`workbench.html` 负责剧情弧线、高光、时间轴、片段精修和序列预览；`series.html` 负责全剧人物、剧情线、伏笔、认知状态、跨集因果和审计记录的只读浏览；`story-arc-workbench.html` 负责故事弧计划逐段审核；`series-workbench.html` 负责多集素材预览、片段精修、重排和导出；`series-search.html` 负责截止集数检索；`dedupe-review.html` 负责相似素材人工去重。
4. **统一数据**：单集 dossier JSON 是剧情事实源；edit-project JSON 是成片选择；SQLite series DB 是可重建的跨集查询投影。线索、AI 候选和剪辑选择不得自动升级为已确认事实。

## 安装要求

- Python 3.10+
- FFmpeg 与 FFprobe 在 PATH 中
- 可选：Pillow（生成 JPG 联系表；没有时仍会生成 HTML 故事板）
- 可选：faster-whisper（全片 ASR；按需安装模型）

## 快速流程

### 1. 初始化档案

```bash
python scripts/dossier_v3.py init work/episode.json --episode-id E01 --title "第 1 集" --duration 2700 --source-version cut-A
```

### 2. 获取字幕/台词

优先使用原始字幕轨。如果没有字幕，可选用 ASR：

```bash
python scripts/transcribe.py episode.mp4 --model large-v3 --lang zh --out-dir work
```

ASR 输出 `.transcript.json` 和 `.transcript.srt`。ASR 文字不是说话人身份事实；重要台词必须回听原音。

### 3. 多模态采集

```bash
python scripts/multimodal_scan.py episode.mp4 work/episode.multimodal.json \
  --subtitle work/episode.transcript.json --sample-interval 8 --max-frames 100
```

输出：

- `episode.multimodal.json`：镜头切换、音量峰值、持续低能量窗口、完整转录时间段、字幕检索线索、时间码和关键帧清单。
- `episode.multimodal_frames/`：按信号和固定间隔抽取的关键帧。
- `storyboard_XX.jpg`：联系表，可上传给 ChatGPT 做视觉观察。
- `transcript_segment` cue：保留每一条字幕/ASR 时间段，供对齐与回看；不作为高光锚点。
- `index.html`：本地逐帧故事板。
- `episode.multimodal_ai_review_prompt.md`：指导 ChatGPT 进行视觉/事件审核的提示文件。

采集脚本不调用远程视觉模型，也不做人脸识别、说话人确认、动作语义识别、动机分析或场景语义裁决；视觉语义审核由 ChatGPT 在你提供联系表/关键帧、转录和对应原片片段后完成。ASR 不包含可靠的角色说话人归属。音量峰值不等于情绪高潮；静音不等于心理沉默；镜头切换不等于场景切换。

### 4. ChatGPT 视觉/事件语义审核

把以下材料提供给 ChatGPT：

- `storyboard_XX.jpg` 联系表（或逐张关键帧）；
- 对应原片的短片段，特别是动作连续性和反应镜头；
- `.srt` 或 `.transcript.json`；
- `episode.multimodal_ai_review_prompt.md`。

要求 ChatGPT 只写可观察画面事实，区分人物外观描述与人物身份；未知人物槽位填 `null`。事件候选使用 `I2_PLAUSIBLE` 或更谨慎等级，不能仅凭关键帧宣称动作连续、说话人身份、动机或因果。

将输出保存为 `work/ai_visual_review.json`，格式参考提示文件。

### 5. 导入 AI 视觉审核结果

```bash
python scripts/import_ai_review.py work/episode.json work/ai_visual_review.json
```

导入内容：

- `observations` → `VIS` 类型证据；
- `event_candidates` → `UNREVIEWED` 事件候选；
- `scene_candidates` → `uncertain` 场景候选。

导入器会检查人物 ID、时间、模态、极性、结论等级和证据引用。不会把 AI 结果自动标记为 `PASS`。

### 6. 生成剧情弧线候选

```bash
python scripts/arc_candidates.py work/episode.json work/episode.multimodal.json \
  --max-arcs 100 --pre-roll 20 --max-payoff-gap 45
```

引擎将多模态 cue 与已有事件、剧情线、因果边对齐，提出：

- `setup`：铺垫候选；
- `peak`：峰值候选；
- `reaction`：即时反应候选；
- `payoff`：同一剧情线/直接因果链的后续结果候选。

某段缺少语义支持时会进入 `missing_roles`，不会为了凑齐四段而编造。所有弧线默认 `UNREVIEWED`，需要回看连续原片。

### 7. 人工审核与剪辑

启动本地工作台：

```bash
python scripts/serve_review.py --port 8765
```

- `http://127.0.0.1:8765/web/review.html`：导入多模态线索、导入 AI 视觉审核 JSON、确认人物/事件/证据/剧情弧线。
- `http://127.0.0.1:8765/web/workbench.html`：查看音画线索、事件和剧情弧线时间轴；点击 setup/peak/reaction/payoff 时间码回看；将弧线转成高光后精修、重排和连续预览。

服务器只监听 `127.0.0.1`。视频由本机浏览器读取，不上传给本地页面服务器。

### 8. 生成剪辑方案与渲染

```bash
python scripts/highlight_engine.py generate work/episode.json --preset 60s
python scripts/highlight_engine.py select work/episode.json work/edit-project.json --preset 60s --video episode.mp4
python scripts/render_project.py work/edit-project.json work/highlight.mp4 --video episode.mp4
```

预设：`30s / 60s / 3m / 5m`。当前渲染使用硬切 H.264/AAC；高级转场、花字、BGM、自动字幕样式属于后续美化层。


## 全剧跨集记忆（v3.5）

### 1. 批量预扫描本地原片

```bash
python scripts/batch_media_scan.py ./series_videos ./series_work/scans \
  --pattern "**/*.mp4" --subtitle-dir ./series_subtitles \
  --sample-interval 8 --max-frames 100
```

逐集生成 `multimodal.json`、关键帧、联系表和 `ai_review_prompt.md`。任务按源文件 SHA-256 断点续跑；视频改变时需显式 `--replace`。本地扫描只提取可测线索，不自动理解人物身份、动作或剧情。

### 2. 逐集完成语义审核

ChatGPT 按需查看每集联系表、字幕和相关连续短片段，生成/修订该集 dossier。将通过结构校验的档案保存到例如 `series_work/dossiers/EP001/episode.json`。不需要把整季原片一次性上传。

### 3. 初始化全剧记忆库并批量导入

```bash
python scripts/series_memory.py init ./series_work/series.sqlite \
  --series-id SERIES01 --title "剧名"
python scripts/series_memory.py batch-import ./series_work/series.sqlite \
  ./series_work/dossiers --series-id SERIES01 --pattern "**/episode.json"
```

导入逐集事务化；相同 SHA-256 跳过；已有集数内容变化时要求 `--replace`。人物、剧情线与伏笔先保持分集实体，不因同名/同描述自动合并。

### 4. 人工确认跨集实体

```bash
python scripts/series_memory.py candidates ./series_work/series.sqlite --series-id SERIES01
python scripts/series_memory.py link-character ./series_work/series.sqlite \
  "EP001:CHR001" "EP008:CHR004" --series-id SERIES01 \
  --reason "回看原片并核实身份揭示" --reviewer "reviewer"
```

剧情线可使用 `link-storyline`。每次关联都要求理由并写入 `audit_log`。精确姓名/别名匹配只产生待审核候选，不自动合并。

### 5. 导出和查看全剧记忆

```bash
python scripts/series_memory.py export-memory ./series_work/series.sqlite \
  ./series_work/series-memory.json --series-id SERIES01
```

使用本地服务打开 `web/series.html` 并载入 JSON，可查看分集覆盖、人物跨集事件时间线、剧情线分布和待确认候选。此页面只读；实际合并仍由 CLI 执行，以保证审计。

详见 `references/series-memory-workflow.md`。


## 跨集智能剪辑（v3.6）

### 1. 建立原片路径映射

复制 `examples/sources.example.json` 为 `work/sources.json`，将每个 `episode_id` 对应到本地视频路径。视频无需上传到 ChatGPT。

### 2. 生成不同类型的跨集专辑

```bash
# 单个人物
python scripts/series_compiler.py work/series.sqlite work/sources.json work/person-edit.json --series-id SERIES01 --character GCHR_... --preset 5m

# 两个人物之间的关系（两个已人工关联的全剧人物 ID）
python scripts/series_compiler.py work/series.sqlite work/sources.json work/relationship-edit.json --series-id SERIES01 --pair GCHR_A,GCHR_B --preset 3m

# 一条剧情线或伏笔
python scripts/series_compiler.py work/series.sqlite work/sources.json work/storyline-edit.json --series-id SERIES01 --storyline GSTL_... --preset 5m
python scripts/series_compiler.py work/series.sqlite work/sources.json work/thread-edit.json --series-id SERIES01 --thread GTHR_... --preset 3m
```

`--query "背叛"` 只做词面检索，不等于语义理解。主题专辑应先由 ChatGPT 从全剧记忆中识别、核实相关事件/剧情线，再编译。

默认只纳入 `PASS` 的实际事件。`--include-unreviewed` 只适用于待审核草稿。默认目标时长会按可解释评分选择完整片段并保留原集数/时间顺序；不会切短单个事件凑时长。`--all-events` 可保留全部匹配事件。

### 3. 跨集工作台精修

启动 `python scripts/serve_review.py --port 8765`，打开 `http://127.0.0.1:8765/web/series-workbench.html`，载入项目 JSON 和对应原片文件。可逐段预览、修改入点/出点、启用/禁用、上移/下移、移除片段并导出新的项目 JSON。浏览器只在本地预览视频，不上传素材。

### 4. 多源 MP4 渲染

```bash
python scripts/render_series_project.py work/series-edit-project.json output/final.mp4
```

渲染器把不同集数的视频作为独立输入，统一画幅/帧率/像素格式/音频采样率；无音轨片段会补静音。输出 H.264 + AAC。项目保留每段素材的集数、原片路径、时间码、事件和证据 ID。

详见 `references/cross-episode-editing-workflow.md`。

## 验证与测试

```bash
python scripts/dossier_v3.py validate work/episode.json
python scripts/dossier_v3.py queue work/episode.json
python -m unittest discover -s tests -v
python scripts/series_memory.py --help
python scripts/batch_media_scan.py --help
python scripts/series_compiler.py --help
python scripts/story_arc_compiler.py --help
python scripts/render_series_project.py --help
```

`validate` 检查结构和引用，不判断剧情真伪。`validate --strict` 还会检查未完成审核对象。

## 重要边界

- 机器线索 ≠ 语义结论；AI 候选 ≠ 已核实事实。
- 不把人脸相似度或单帧外观直接当人物身份。
- 不把字幕顺序当说话人识别。
- 不把音量、镜头切换数量当情绪/剧情重要性。
- 不把时间先后当因果；payoff 只有同剧情线或明确因果关联时才提出。
- 不将回忆、想象、计划、威胁剪成“现实已发生”。
- 剪辑层不得反向修改剧情事实层。
- Series DB 是派生投影，不能替代每集原始 dossier。
- 人物/剧情线跨集链接必须人工确认；名字相同不等于同一人。
- 本地批量预扫描不等于自动剧情理解；语义事件仍需 ChatGPT/人工审阅。

## v3.7 跨集故事弧编译

按人物/关系/剧情线生成 ChatGPT 规划上下文：

```bash
python scripts/story_arc_compiler.py make-prompt work/series.sqlite work/arc-prompt.md --series-id SERIES01 --character GCHR_001 --goal "从信任到背叛再到复仇"
```

将上下文交给 ChatGPT，按 `references/story-arc-plan.schema.json` 生成叙事计划。先在 `web/story-arc-workbench.html` 逐段核对、填写理由并确认，再编译：

```bash
python scripts/story_arc_compiler.py compile work/series.sqlite work/sources.json work/story-arc-plan.reviewed.json work/story-arc-edit.json
python scripts/story_arc_compiler.py compile work/series.sqlite work/sources.json work/story-arc-plan.reviewed.json work/story-arc-edit.json --require-release-ready
```

编译器检查事件/证据引用、原片时间顺序、连续性连接、跨集因果审核、人物/观众认知状态。它不自动补齐缺失剧情，不将前后顺序当作因果，不为满足目标时长删除必要叙事角色。详见 `references/story-arc-compilation-workflow.md`。


## v3.8 剧透安全语义检索与重复素材审核

### 1. 截止集数检索（必须指定 as-of）

```bash
python scripts/series_search.py search work/series.sqlite "张三发现李四背叛后如何复仇" --series-id SERIES01 --as-of EP015 --limit 30 --output work/search-results.json
```

检索严格限制在 EP015 及以前的分集记录。默认只搜索 PASS 对象；`--include-unreviewed` 仅用于草稿。检索实现是 BM25 + 中文字符 n-gram + 可选 ChatGPT 扩展词，不是向量嵌入检索。

生成受限扩展提示：

```bash
python scripts/series_search.py make-expansion-prompt work/series.sqlite "背叛" work/expansion-prompt.md --series-id SERIES01 --as-of EP015
```

让 ChatGPT 只基于提示中的截止集上下文生成 JSON 扩展词，再用 `--expansion-json work/query-expansion.json` 检索。扩展词只是召回提示，不是剧情事实。

启动检索界面/API：

```bash
python scripts/series_search_server.py work/series.sqlite --series-id SERIES01 --port 8766
```

打开 `http://127.0.0.1:8766/`。必须填写“只看到第几集”；不允许无截止集检索。若指定人物视角，还必须先用 `series_visibility.py` 提供该人物在截止集之前已被观众识别的直接证据。

### 2. 声明跨集实体何时对观众可见

```bash
python scripts/series_visibility.py work/series.sqlite --series-id SERIES01 --entity-type character --entity-id GCHR_001 --known-by-episode EP015 --evidence-ref EP012:EVD034 --reason "第12集画面和台词明确揭示身份" --reviewer "审核者"
```

支持 `character / storyline / thread / causal_edge`。证据必须与目标实体直接关联，且不能晚于声明集。声明写入可见性表和审计表。没有声明的跨集关系在剧透保护模式下视为未知。

### 3. 相似素材检测与人工审核

```bash
python scripts/dedupe_clips.py work/series-edit-project.json work/dedupe-report.json --threshold 0.95
```

检测同源时间重叠、多帧灰度 dHash 相似和文本/台词相似。结果只是一组待审核候选；不会自动删除片段。用本地静态服务器打开 `web/dedupe-review.html`，载入项目和报告，人工选择保留/禁用，再导出项目副本。

详细规则见 `references/spoiler-safe-search-and-dedupe.md`。


## v3.8 版本边界与验证

- 语义检索采用 BM25 + 中文 n-gram + 可选 ChatGPT 查询扩展；当前不使用向量 embedding。
- 剧透隔离是数据库查询级的集数过滤，不依赖提示词；跨集身份/关系必须有带证据的可见性声明。
- 重复镜头检测只输出候选，不自动删除；人工审核结果只写回剪辑项目副本。
- 检查全部测试：`python -m unittest discover -s tests -v`。


## v3.9 智能剪辑边界与成片 QC

v3.9 新增 `scripts/edit_quality.py`。它根据已有字幕/ASR时间段、镜头切换和静音 cue，为已选中的片段提供保守的入点/出点建议，并对剪辑项目执行连续性与可追溯性基础检查。

生成边界建议：

```bash
python scripts/edit_quality.py boundaries work/edit-project.json work/cue-bundle.json work/edit-project.boundaries.json
```

cue bundle 示例：

```json
{
  "EP001": {
    "cues": [
      {"type":"shot_change","time_sec":12.4},
      {"type":"subtitle","start_sec":13.0,"end_sec":14.2},
      {"type":"silence","time_sec":15.0}
    ]
  }
}
```

成片 QC：

```bash
python scripts/edit_quality.py qc work/edit-project.json work/edit-project.qc.json
```

重要限制：边界算法只提供候选，不能自动理解台词语义、人物表演、动作连续性或情绪。它不会自动应用建议，也不会自动删除素材。所有边界建议必须人工确认后再用于最终渲染。


## v4.0 AI 导演编排与成片二次审片

生成导演节奏建议：

```bash
python scripts/director_engine.py work/edit-project.json work/director-plan.json --profile balanced
```

支持 `fast / balanced / slow` 三种节奏预设，也可以通过 `--observations` 导入按 `clip_id` 关联的结构化镜头/表演观察。输出包括叙事角色、目标停留时长、节奏、转场建议、观察状态与风险标记。它不会自动修改片段时长，也不会仅凭画面像素推断人物微表情、情绪或动机。

渲染后复核：

```bash
python scripts/post_render_review.py output/final.mp4 output/final.post-review.json
```

该脚本使用 FFprobe 检查视频/音频流和规格，并使用 FFmpeg 检查黑帧/黑场及持续静音。黑场、静音都可能是有意设计，报告仅提供候选，不自动剪掉片段。技术 QC 不能替代人工剧情和审美审片。


## v4.1 镜头反应匹配与动作连续性辅助

v4.1 新增 `scripts/shot_continuity.py`，从已有镜头边界、人物出场、台词时间段、人物互动事件和经审核的镜头观察中，生成正反打/反应镜头/动作连续候选。它不做人脸识别，也不把“时间上紧邻”当成同一人物或同一动作的证明。

生成候选：

```bash
python scripts/shot_continuity.py candidates work/episode.json work/episode.multimodal.json work/shot-observations.json work/shot-continuity.json
```

运行连续性风险检查：

```bash
python scripts/shot_continuity.py qc work/shot-continuity.json work/shot-continuity.qc.json
```

候选保留 `UNREVIEWED`。需要回看连续原片，核实视线方向、人物身份、对话轮次、动作起止和空间位置，再人工标记通过。正反打识别属于基于证据的候选匹配，不是自动语义真值。


## v4.1 镜头反应匹配与动作连续性

```bash
python scripts/shot_continuity.py candidates work/episode.json work/episode.multimodal.json work/shot-observations.json work/shot-continuity.json
python scripts/shot_continuity.py qc work/shot-continuity.json work/shot-continuity.qc.json
python scripts/serve_review.py --port 8765
```

打开 `http://127.0.0.1:8765/web/shot-continuity-review.html` 导入候选文件。支持正反打、反应镜头、对话轮次和动作连续性候选的人工审核。候选只依赖已提供的结构化镜头观察，不做人脸识别；所有候选默认待审核。



## v4.2.0 审核驱动镜头编排与多轮返工

延续既有架构，补齐连续性审核者、理由、时间和内容快照；导演引擎只消费审计完整的 PASS 候选，并按同集、同事件/证据、既有原片窗口提供可追溯替代镜头。

新增 A/B 版本、差异报告、版本比较页面、逐片段合并、反馈定位返工，以及新版本批准和渲染门槛。`review_cut.py` 联动真实渲染、技术复核、成片关键帧和问题源 clip 映射。所有新剪辑选择默认待审，不覆盖旧项目。

完整命令、旧版兼容方式和能力边界见 [edit-version-workflow.md](references/edit-version-workflow.md)。本版未增加自动视觉语义判断，字幕和突兀切换仍需人工审片；A/B 是候选辅助编排，不保证自动生成更精彩的成片。


## v4.3.0 人物状态与主角成长素材

新增带证据的 `character_states` / `character_state_changes`，记录目标、认知、处境、关系、外显反应和行动结果。审核页 `web/character-states.html` 支持回看原片、逐条审核和导出完整历史。解释性心理判断被保留，但不进入自动素材选择。

`character_state.py timeline` 按截止集数和集内秒数提供主角轨迹；`compile` 按完整状态变化组提取素材，保留变化两端，生成需要人工批准的新版本。沿用现有 dossier、SQLite episode_payloads、导演、版本与渲染流程，无需重做数据库。

用法、字段、示例和能力边界见 [character-state-workflow.md](references/character-state-workflow.md)。主角身份、状态转变和成片审美仍需人工核实；不宣称自动读心或自动理解完整成长线。


## v4.3.1 logic hardening (candidate)

State-change context events are now required to occur within the inclusive interval from the source state timestamp to the destination state timestamp. This prevents an unrelated earlier/later event from being attached to a transition solely because it involves the same character. Existing v4.3 dossiers remain schema-compatible; records that fail the new semantic check must be corrected and re-reviewed.


## v4.3.1 state integrity additions

- State-change context events must fall within the inclusive interval between the source and destination state timestamps.
- Character trajectories expose both `local_target_character_id` and a `global_target_character_id` only when exactly one reviewed mapping exists; otherwise the target identity remains unresolved/ambiguous.
- `state_graph_issues()` reports overlapping conflicting validity intervals and transition forks/merges as warnings; these are diagnostics, not automatic psychological-state inference.
- `scripts/character_arc_integrity.py` checks user-defined required milestones against state/change/event coverage in a compiled project. It does not decide what constitutes character growth.
