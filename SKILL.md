---
name: drama-dossier
description: "Evidence-first film and TV story understanding plus explainable multimodal highlight editing with spoiler-safe as-of retrieval, cross-episode visibility controls, and duplicate-clip review. Use when ChatGPT must analyze episode videos, subtitles, transcripts, keyframes, or drama dossiers; understand characters, scenes, events, motives, relationships, causal chains, storylines, foreshadowing and full-series continuity; batch-scan seasons locally; build a resumable SQLite series memory; review cross-episode identity/storyline/foreshadowing candidates; generate setup/peak/reaction/payoff arcs; create 30s/60s/3m/5m story cuts; or operate local evidence-review and editing workbenches."
---

# 剧情理解与多模态精彩剪辑 v4.2

将任务分成**剧情事实层**与**剪辑选择层**。剧情事实层回答发生了什么、谁做了什么、证据是什么；剪辑层回答哪些片段值得组合。不得让剪辑偏好反向修改事实。

## 标准工作流

1. 固定片源版本、集数、时长、帧率和音轨/字幕轨。
2. 采集字幕/ASR、镜头变化、音频能量和均匀关键帧。
3. 查看联系表并对关键时间窗回看连续原片，形成可观察的视觉观察。
4. 将 AI 视觉审核结果导入为 `VIS` 证据和 `UNREVIEWED` 事件候选。
5. 结合已有事件、剧情线和因果边生成 setup/peak/reaction/payoff 弧线候选。
6. 人工核证人物身份、动作连续性、说话人、因果和片段语境。
7. 将通过审核的弧线转为高光，精修起止点，生成 30s/60s/3m/5m 序列。
8. 连续预览并渲染；JSON 始终是唯一剧情事实源，edit-project JSON 是派生剪辑选择。

## 核心规则

- 先记录可观察证据，再写解释；不确定性必须显式保留。
- `SUB` 不自动证明说话人；`VIS` 不自动证明人物身份、动作连续或动机；`AUD` 不自动证明情绪。
- 人物被提及不等于在场或参与事件。
- 事件区分 `actor / patient / recipient / target`。
- `planned / commanded / desired / threatened / hypothetical / recalled / imagined` 不得冒充当前已发生事实。
- 时间先后不等于因果。因果只使用 `CAUSES / ENABLES / BLOCKS / MOTIVATES / REVEALS / FOLLOWS / POSSIBLE_CAUSE`。
- 后续揭示不得覆盖角色当时的认知。
- 身份、死亡/生还、亲缘、背叛、关系反转、关键因果和伏笔回收优先人工核证。
- 音量峰值、低能量区间、镜头切换、字幕关键词只用于定位，不得直接提升情绪、重要性或反转分数。
- 所有 AI 视觉事件候选默认为 `UNREVIEWED`；不得导入即 `PASS`。
- `setup / peak / reaction / payoff` 是待验证的叙事角色，不是机器已理解的语义事实。

## 数据对象

单集档案维护：

`episode / evidence / characters / appearances / scenes / events / relations / causal_edges / threads / storylines / highlights / media_cues / story_arcs / knowledge_states / review_history`

查看 `record-schema.json`。

## 片源采集

优先原始字幕轨；没有字幕时可运行：

```bash
python scripts/transcribe.py episode.mp4 --model large-v3 --lang zh --out-dir work
```

再运行多模态扫描：

```bash
python scripts/multimodal_scan.py episode.mp4 work/episode.multimodal.json --subtitle work/episode.transcript.json --sample-interval 8 --max-frames 100
```

检查输出的 `*_ai_review_prompt.md`、`*_frames/index.html` 和 `storyboard_XX.jpg`。将联系表、转录文本和关键短视频片段提供给 ChatGPT。联系表用于检索；动作连续性必须回看原片。扫描脚本不调用远程视觉模型、不做人脸识别或说话人确认；ASR 不自动确认角色说话人，语义审核由 ChatGPT 在收到上述材料后完成。

## AI 视觉审核

要求 ChatGPT 输出 JSON，至少包含：

- `observations[]`：`start_sec / end_sec / frame_refs / visual_description / confidence / continuity_notes / alternatives`。
- `event_candidates[]`：`playback_time / action / actor / patient / recipient / target / modality / polarity / certainty / observation_index / evidence_ids`。
- 可选 `scene_candidates[]`：`start_sec / end_sec / score`。

未知角色槽位填 `null`，不能编造 `character_id`。若无法确认人物身份，使用可见外观描述。仅凭单帧不能断言动作先后、说话人、动机或因果。

导入：

```bash
python scripts/import_ai_review.py work/episode.json work/ai_visual_review.json
```

导入器创建 `VIS` 证据与 `UNREVIEWED` 事件候选；验证引用、时间、人物 ID、事件模态和结论等级。不要手动把候选批量改为 `PASS`。

## 剧情弧线候选

```bash
python scripts/arc_candidates.py work/episode.json work/episode.multimodal.json --max-arcs 100 --pre-roll 20 --max-payoff-gap 45
```

弧线引擎只使用可定位 cue、已有事件、剧情线成员和非错误因果边。`payoff` 只在后续事件与锚点事件共享剧情线或有直接因果边时提出；不满足条件时放入 `missing_roles` 和 `risk_flags`。镜头/声音 cue 不能代替语义事件。

每条弧线都应检查：

1. `setup` 是否确实建立了目标、关系或风险？
2. `peak` 是剧情事件还是音画强信号？
3. `reaction` 是否是同一事件的即时反应？
4. `payoff` 是否由同一剧情线或可核实因果支持？
5. 是否有否定、条件、回忆、想象、误导剪辑或信息差？

## 人工交互界面

```bash
python scripts/serve_review.py --port 8765
```

- `web/review.html`：导入 dossier、镜头候选、多模态扫描 JSON、AI 视觉审核 JSON；核证人物、证据、场景、事件、关系、因果、伏笔、剧情线和弧线。
- `web/workbench.html`：载入原片和 dossier；查看场景、事件、音画 cue、剧情弧线、高光和成片序列多轨时间轴；点击弧线的 setup/peak/reaction/payoff 段落定位回看；转为高光、精修、排序、预览和导出剪辑项目。

本地服务器只监听 `127.0.0.1`。页面修改只有导出 JSON 后才持久化。

## 高光与渲染

先生成高光：

```bash
python scripts/highlight_engine.py generate work/episode.json --preset 60s
```

再生成剪辑项目与成片：

```bash
python scripts/highlight_engine.py select work/episode.json work/edit-project.json --preset 60s --video episode.mp4
python scripts/render_project.py work/edit-project.json work/highlight.mp4 --video episode.mp4
```

七维评分：`importance / emotion / conflict / twist / suspense / visual / standalone`。每项 0–100。无 AI/人工评分时只能用保守回退值，标记来源；音量、镜头数和字幕关键词不得直接抬高情绪/剧情重要性。`context_required=high` 的片段不能脱离必要铺垫单独剪。

## 档案引擎与质量检查

`scripts/dossier_v3.py`：`init / migrate / import-scenes / validate / queue / review / report`。

新增脚本：

- `multimodal_scan.py`：音画/字幕线索、关键帧、联系表和 AI 审核提示。
- `import_ai_review.py`：将 AI 视觉审核结果导入为待审核证据/事件候选。
- `arc_candidates.py`：提出 setup/peak/reaction/payoff 候选。
- `series_compiler.py`：按人物、双人物关系、剧情线、伏笔或词面查询生成跨集剪辑项目。
- `render_series_project.py`：把多集原片作为多个输入，渲染为统一规格的 H.264/AAC MP4。
- `story_arc_compiler.py`：生成 ChatGPT 规划上下文，验证跨集故事弧的叙事角色、事件、证据、因果连接和人物认知，并编译多源剪辑项目。

交付前读取 `references/checklist.md` 并运行：

```bash
python -m unittest discover -s tests -v
python scripts/dossier_v3.py validate work/episode.json
```

结构校验通过不代表剧情语义已核实。严格模式 `validate --strict` 要求没有 `UNREVIEWED / NEED_EVIDENCE / DISPUTED` 对象。

## 全剧记忆与跨集理解 v3.5

单集档案使用 v3.5 dossier schema；v3.5 起新增独立的 SQLite 全剧记忆层，不把跨集映射写回单集事实档案。

### 批量预扫描整季原片（视频留在本地）

```bash
python scripts/batch_media_scan.py ./series_videos ./series_work/scans --pattern "**/*.mp4" --subtitle-dir ./series_subtitles --sample-interval 8 --max-frames 100
```

该步骤逐集生成多模态线索、关键帧、联系表和 AI 审核提示；按 SHA-256 断点续跑。它不自动理解剧情。ChatGPT 只需按需查看联系表、字幕和相关短片段，不必一次上传整季原片。

### 导入逐集审核档案，构建全剧记忆

```bash
python scripts/series_memory.py init ./series_work/series.sqlite --series-id SERIES01 --title "剧名"
python scripts/series_memory.py batch-import ./series_work/series.sqlite ./series_work/dossiers --series-id SERIES01 --pattern "**/episode.json"
python scripts/series_memory.py status ./series_work/series.sqlite --series-id SERIES01
python scripts/series_memory.py candidates ./series_work/series.sqlite --series-id SERIES01
python scripts/series_memory.py export-memory ./series_work/series.sqlite ./series_work/series-memory.json --series-id SERIES01
```

- 每集 dossier 必须先通过 v3.5 结构校验；旧版 v3.0–v3.4 档案可迁移，缺少认知状态时保持空数组，不推断、不补造。导入以 episode 为事务单位。
- 相同 SHA-256 自动跳过；同一集内容改变时必须显式使用 `--replace`。
- 人物姓名/别名相同只生成 `NEEDS_REVIEW` 候选，不自动合并。
- 人工确认身份、剧情线或伏笔时，必须通过 `link-character` / `link-storyline` / `link-thread` CLI 并填写理由；动作写入审计日志。
- 错误候选使用 `reject-candidate` 驳回并记录理由。
- 全剧记忆导出包含分集档案来源、人物时间线、剧情线分布、关系历史、伏笔条目、单集/跨集因果边、故事弧和人物/观众认知状态。
- `web/series.html` 可载入 `series-memory.json`，查看全剧人物时间线、剧情线、伏笔、认知状态、跨集因果边和待确认候选。界面为只读视图，合并操作在 CLI 完成以保留审计。

完整流程、命令和边界见 [references/series-memory-workflow.md](references/series-memory-workflow.md)。


## 跨集智能剪辑 v3.6

全剧记忆建立后，可按人物、双人物关系、剧情线、伏笔或词面查询生成多集素材序列。跨集剪辑不会修改单集 dossier 或 Series DB 事实。

先创建 `work/sources.json`，把每个 `episode_id` 映射到本地原片路径，格式参考 `examples/sources.example.json`。路径必须指向实际存在的视频文件。

人物专辑：

```bash
python scripts/series_compiler.py work/series.sqlite work/sources.json work/person-edit.json --series-id SERIES01 --character GCHR_... --preset 5m
```

双人物关系专辑：

```bash
python scripts/series_compiler.py work/series.sqlite work/sources.json work/relationship-edit.json --series-id SERIES01 --pair GCHR_A,GCHR_B --preset 3m
```

剧情线、伏笔专辑分别使用 `--storyline GSTL_...` 和 `--thread GTHR_...`。`--query` 仅执行 SQL 词面检索；主题专辑应先从全剧记忆中找出并核实相关剧情线，再按剧情线 ID 编译。

默认只纳入 `PASS` 的实际事件，优先使用已审核高光时间窗；没有高光时才回退到证据/事件时间窗。`--include-unreviewed` 可将未审核候选纳入草稿，但不可直接视为事实或发布版本。默认按 30s/60s/3m/5m 目标时长挑选完整片段，不会截短单个事件凑时长；使用 `--all-events` 保留全部合格匹配事件。目标时长筛选后仍按原集数和原片时间顺序排列。

跨集剪辑工作台：

```bash
python scripts/serve_review.py --port 8765
```

打开 `http://127.0.0.1:8765/web/series-workbench.html`，载入 edit-project JSON，并多选载入对应原片。页面只在浏览器本地预览；可以调整入点/出点、标题、理由、启用状态和片段顺序，然后导出新的项目 JSON。

多源渲染：

```bash
python scripts/render_series_project.py work/series-edit-project.json output/final.mp4
```

该渲染器支持多个不同集数作为独立视频输入，统一画幅、帧率、像素格式、音频采样率；无音轨片段会补静音。默认输出 H.264 + AAC。项目保留 `episode_id / source_video / start_sec / end_sec / event_ids / highlight_id / evidence_ids / event_review_status` 来源追溯。

限制：双人物关系剪辑依赖档案中已核实的共同参与事件或显式关系起止事件；词面查询不是语义检索；自动时长筛选是候选排序，不保证故事弧完整。非线性重排、对白连续性、色彩/响度匹配、转场、BGM、字幕烧录和发布版权检查仍需人工处理。

## 参考资料

- `references/multimodal-review-workflow.md`：多模态采集、视觉审核和事件导入。
- `references/ai-visual-review-schema.json`：视觉审核 JSON 结构。
- `examples/ai-visual-review.example.json`：视觉审核输出示例。
- `references/story-arc-workflow.md`：剧情弧线候选与人工核证。
- `references/highlight-editing-workflow.md`：高光评分与剪辑。
- `references/edit-project-format.md`：剪辑项目格式。
- `references/human-review-workflow.md`：人工审核规则。
- `references/checklist.md`：交付质检。
- `references/dossier-template.md`：人工阅读版档案。
- `references/upgrade-roadmap.md`：版本路线。
- `references/series-memory-workflow.md`：整季批量预扫描、断点续跑、跨集身份核验与全剧记忆。
- `references/series-memory-schema.json`：全剧 SQLite 投影结构。
- `references/story-arc-plan.schema.json`：跨集叙事计划结构。
- `references/story-arc-compilation-workflow.md`：跨集故事弧规划、认知审核、编译与发布门槛。
- `examples/story-arc-plan.example.json`：跨集故事弧计划格式示例。
- `references/cross-episode-editing-workflow.md`：跨集人物/关系/剧情线/伏笔专辑生成、人工精修与多源渲染。
- `references/spoiler-safe-search-and-dedupe.md`：截止集数检索、跨集可见性声明、重复镜头检测和人工去重边界。

## 跨集故事弧与人物认知剪辑 v3.7

v3.7 把跨集专辑升级为“叙事弧编译”：ChatGPT 从全剧记忆提出 setup / inciting incident / escalation / peak / reaction / payoff / resolution 计划；Python 验证每个事件、证据、原片时间、人物身份、连续性依据和认知状态，再生成多源剪辑项目。

### 1. 为跨集因果建立审核记录

```bash
python scripts/series_memory.py add-causal-link work/series.sqlite EP001:EVT001 EP008:EVT021 --series-id SERIES01 --type CAUSES --reasoning "前一事件为后一事件提供直接动机" --evidence-ref EP001:EVD001 --evidence-ref EP008:EVD021
python scripts/series_memory.py review-causal-link work/series.sqlite XCAU_xxxxx --series-id SERIES01 --status PASS --reason "回看两集原片并核对证据" --reviewer "审核者"
```

跨集因果边默认 `UNREVIEWED`。替换任一端点集的 dossier 后，已通过的跨集因果边自动重开为 `NEED_EVIDENCE`，避免沿用过期审核。

### 2. 建立认知状态

在单集 dossier 的 `knowledge_states` 中记录角色或观众在某个播放时点知道、怀疑、误信或尚未知道的命题。没有记录代表“未知”，不等于“角色不知道”。只有 `PASS` 的状态参与时间点回溯。

### 3. 生成 ChatGPT 叙事规划上下文

```bash
python scripts/story_arc_compiler.py make-prompt work/series.sqlite work/arc-prompt.md --series-id SERIES01 --character GCHR_001 --goal "从信任、发现背叛、决定复仇到复仇结果"
```

人物选择会扩展到其参与的已审核剧情线，并补充一跳因果邻居、相关伏笔、单集故事弧、关系历史和人物/观众认知状态。扩展只是上下文召回，不等于相关事件已经构成同一因果链。

将 `arc-prompt.md` 提供给 ChatGPT，要求输出符合 `references/story-arc-plan.schema.json` 的 JSON。格式示例见 `examples/story-arc-plan.example.json`。

### 4. 人工审核故事弧并编译

先用 `web/story-arc-workbench.html` 载入 plan JSON，逐段核对叙事角色、事件和证据，填写审核理由；所有段落通过后再确认计划级审核并导出。

```bash
python scripts/story_arc_compiler.py compile work/series.sqlite work/sources.json work/story-arc-plan.reviewed.json work/story-arc-edit.json
python scripts/story_arc_compiler.py compile work/series.sqlite work/sources.json work/story-arc-plan.reviewed.json work/story-arc-edit.json --require-release-ready
```

编译器默认只纳入 `PASS` 事件，并要求片段证据确实属于该段所选事件；连续性连接必须连接计划中实际选择的事件。`release_ready` 还要求必需角色齐全、计划/段落已审核、关键连接有依据、跨集因果已确认、没有未解除风险标记。可通过 `require_knowledge_coverage=true` 要求所有剪辑时点均有已审核的人物与观众认知记录。

每个片段附带 `perspective_knowledge`、`viewer_knowledge` 和 `knowledge_gap_flags`，标记观众是否先于角色知道、或角色是否掌握了观众尚未获得的信息。缺少认知记录时标记未知，不做反向推断。

编译后的项目可在 `web/series-workbench.html` 精修；工作台显示叙事角色、证据和信息差提示。最终仍通过 `render_series_project.py` 渲染。

详见 `references/story-arc-compilation-workflow.md`。


## v4.1 剧透安全语义检索与重复素材审核

### 截止集数语义检索

必须指定 `--as-of`，不允许默认检索全剧：

```bash
python scripts/series_search.py search work/series.sqlite "张三发现李四背叛后如何复仇" --series-id SERIES01 --as-of EP015 --limit 30 --output work/search-results.json
```

- 检索范围严格限定在截止集数及以前；不查询未来集的事件、分集人物别名、剧情线摘要或认知记录。
- 默认只纳入 `PASS` 对象；`--include-unreviewed` 仅用于草稿检索，不能当作已核实事实。
- 使用 BM25 + 中文字符 n-gram 和保守领域词典；可用 `make-expansion-prompt` 生成截止集数受限的 ChatGPT 同义词扩展提示，再通过 `--expansion-json` 输入扩展词。该版本不使用向量嵌入，不得声称已实现 embedding 语义检索。
- 人物视角可用 `--viewpoint-character GCHR_...`；该全局人物身份必须先有不晚于截止集数的 `PASS` 可见性声明，否则拒绝按该身份检索。没有视角参数时不混入角色私有认知，只检索已审核的观众认知。
- 严格剧透模式只使用分集本地人物名称和别名，不用全剧合并实体的后期身份映射反推早期身份。

生成受限扩展提示：

```bash
python scripts/series_search.py make-expansion-prompt work/series.sqlite "背叛" work/expansion-prompt.md --series-id SERIES01 --as-of EP015
```

可启动独立检索 API 和网页：

```bash
python scripts/series_search_server.py work/series.sqlite --series-id SERIES01 --port 8766
```

打开 `http://127.0.0.1:8766/`。HTTP 服务仅绑定 `127.0.0.1`；截止集数为必填。

### 声明跨集事实的观众可见时间

跨集人物身份、剧情线、伏笔和因果关联不能因为全剧数据库已经合并就自动出现在早期观看视角。只有能提供截止集或更早、且直接关联该实体的证据，才能声明该信息在某集已对观众可知：

```bash
python scripts/series_visibility.py work/series.sqlite --series-id SERIES01 --entity-type character --entity-id GCHR_001 --known-by-episode EP015 --evidence-ref EP012:EVD034 --reason "第12集画面和台词明确揭示身份" --reviewer "审核者"
```

`entity-type` 支持 `character / storyline / thread / causal_edge`。证据必须存在、不能晚于声明集数，并且必须与被声明实体直接关联。声明会写入 `series_visibility` 和 `visibility_audit`。没有声明时按未知处理，不推断已知或未知。

### 相似镜头与重复素材审核

```bash
python scripts/dedupe_clips.py work/series-edit-project.json work/dedupe-report.json --threshold 0.95
```

- 同一原片时间区间重叠、跨集多帧画面指纹相似、文本/台词相似会产生候选。
- 检测使用 FFmpeg 灰度 9x8 difference hash 和中文文本 n-gram；结果是相似性线索，不是语义重复结论。
- 只生成候选报告，绝不自动删除、禁用或重排素材。
- `web/dedupe-review.html` 可载入剪辑项目和报告，人工决定保留/禁用；导出的是剪辑项目副本，不改剧情事实档案。

### 服务入口

```bash
python scripts/serve_review.py --port 8765
```

打开 `web/series-search.html` 需运行独立 `series_search_server.py`；重复素材审核页 `web/dedupe-review.html` 可由本地静态服务器提供。具体边界见 `references/spoiler-safe-search-and-dedupe.md`。


## 智能剪辑边界与连续性质量检查 v3.9

不要把音量峰值或镜头切换直接当作精彩点。需要为已选中的剪辑片段提供边界建议时，先准备按 episode_id 索引的 cue bundle（每集包含 `cues[]`，字幕 cue 使用 `type=subtitle`、`start_sec`、`end_sec`；镜头候选使用 `type=shot_change`、`time_sec`；静音候选使用 `type=silence`、`time_sec`），再运行：

```bash
python scripts/edit_quality.py boundaries work/edit-project.json work/cue-bundle.json work/edit-project.boundaries.json
```

工具只在原片段范围内提出建议，不自动扩展或缩短已选片段；所有建议状态均为 `UNREVIEWED`。字幕边界和镜头边界只是技术信号，必须回看连续原片确认台词完整、反应镜头和动作连续。

渲染前运行：

```bash
python scripts/edit_quality.py qc work/edit-project.json work/edit-project.qc.json
```

`ERROR` 阻断项必须修复；`WARN` 和 `INFO` 必须在工作流中展示。QC 不会判断人物情绪、表演质量、因果或镜头审美，不得把技术检查结果描述成语义审片通过。


## AI 导演编排与成片复核 v4.0

v4.0 增加导演编排建议与渲染后技术复核。导演引擎不声称从像素直接识别微表情或心理状态；它读取经过人工/AI 审核的镜头观察，并结合故事弧叙事角色生成节奏建议：

```bash
python scripts/director_engine.py work/edit-project.json work/director-plan.json --profile balanced
```

可选 `--observations work/shot-observations.json`，其 `clip_observations[]` 以 `clip_id` 关联，字段包括 `visual_observation`、`performance_observation_status`、`action_continuity`、`dialogue_complete` 和 `reaction_complete`。缺失观察会显式产生风险标记。导演建议默认为 `UNREVIEWED`，目标时长和转场类型不是自动剪辑命令。

渲染完成后执行技术复核：

```bash
python scripts/post_render_review.py output/final.mp4 output/final.post-review.json
```

复核报告包含编码、时长、分辨率、帧率、黑帧/黑场候选和持续静音候选。黑场和静音可能是有意的艺术处理，因此仅生成检查结果，不自动修剪。技术检查通过不代表剧情、表演、声音混音或审美已通过人工审片。


## 镜头反应匹配与动作连续性 v4.1

在多模态采集后，如需提出对话正反打、人物反应镜头或动作连续性候选，运行：

```bash
python scripts/shot_continuity.py candidates work/episode.json work/episode.multimodal.json work/shot-observations.json work/shot-continuity.json
python scripts/shot_continuity.py qc work/shot-continuity.json work/shot-continuity.qc.json
```

`shot-observations.json` 是 ChatGPT/人工审核后的结构化观察，按镜头时间范围提供 `shot_id / start_sec / end_sec / visible_character_ids / speaker_character_id / gaze_direction / screen_position / action_phase / action_description / continuity_status / review_status`。不确定字段填 `null`，不得从相邻镜头自动推断身份或动作连续。

候选类型包括 `SHOT_REVERSE_SHOT / REACTION_SHOT / ACTION_CONTINUITY / DIALOGUE_TURN`。候选必须有可追溯镜头、人物/事件或字幕证据，默认 `UNREVIEWED`。相邻时间、同一地点或相似画面本身不能证明正反打、人物身份、视线匹配或动作连续。人工需回看前后连续原片，检查轴线、视线方向、人物位置、动作起止、台词轮次和反应对象。

## v4.0 导演编排与成片二次复核

`director_engine.py` 根据叙事角色和已审核观察提供节奏/转场建议；`post_render_review.py` 对渲染结果检查编码、时长、黑场与静音候选。两者均不自动裁切或删除素材。


## 镜头反应匹配与动作连续性 v4.1

在多模态采集后，如需提出对话正反打、人物反应镜头、对话轮次或动作连续性候选，先准备 `shot-observations.json`。每个镜头观察包含 `shot_id / start_sec / end_sec / visible_character_ids / speaker_character_id / gaze_direction / screen_position / action_phase / action_description / continuity_status / event_ids / evidence_ids / review_status`。这些观察必须来自 ChatGPT 对关键帧和连续原片的检查或人工标注；不确定字段填 `null`。

生成候选和检查：

```bash
python scripts/shot_continuity.py candidates work/episode.json work/episode.multimodal.json work/shot-observations.json work/shot-continuity.json
python scripts/shot_continuity.py qc work/shot-continuity.json work/shot-continuity.qc.json
```

打开 `web/shot-continuity-review.html` 导入候选 JSON，逐条审核并导出审核版。候选类型包括 `SHOT_REVERSE_SHOT / REACTION_SHOT / ACTION_CONTINUITY / DIALOGUE_TURN`，默认 `UNREVIEWED`。相邻时间、同一地点或相似画面本身不能证明正反打、人物身份、视线匹配或动作连续。人工必须回看连续原片，检查轴线、视线、人物位置、动作起止、台词轮次和反应对象。



## v4.2 审核驱动编排、版本与返工

在进行镜头替换、A/B 版本、反馈返工或新版本渲染前，阅读 [edit-version-workflow.md](references/edit-version-workflow.md)。沿用 dossier/SQLite/edit-project 分层，不让剪辑选择改变剧情事实。

- 使用连续性审核 UI 或 `shot_continuity.py review` 追加完整记录；PASS 必须包含审核者、理由、时间、内容快照及连续原片回看确认。旧版仅状态 PASS 必须补观察并重新审核。
- 向 `director_engine.py --continuity` 传入审核结果。只让完整、未过期 PASS 且证据/事件归属匹配的候选进入替代方案；仍需审核新的剪辑选择。
- 使用 `edit_versions.py ab/diff/merge/rework/approve` 创建不覆盖旧文件的新版本。明确 A/B 为确定性候选编排，无合格候选可保持相同内容。
- 返工反馈必须包含当前项目 fingerprint、clip_id 和具体问题；缺少合格替代或人工解决说明时保留 unresolved_issues，不编造修复。
- 新版本默认 UNREVIEWED。批准后发生内容变化必须重新审核；不因剪辑批准宣称剧情审查或成片审查通过。
- 使用 `review_cut.py` 重新渲染、运行技术复核、提取关键帧并映射问题回源 clip。完整成片、字幕、突兀切换及剧情连续性仍需人工审片。
- 发布前运行 `python scripts/build_release.py`；仅按真实测试结果报告数量和能力。
