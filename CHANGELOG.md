# Changelog

## v4.3.0

- 新增可选人物状态及显式状态变化模型，覆盖目标、认知、处境、关系、外显反应和行动结果；沿用 dossier schema 3.5 与 SQLite 原有完整 JSON 投影，无需新建状态数据库。
- 新增候选事务式导入、连续原片审核确认、人物/事件/证据/认知依赖快照及状态审核页；心理解释保留但不进入自动选材。
- 新增截止集与集内时间受限的主角轨迹、状态冲突提示、身份可见性证据检查；替换分集后重开旧可见性声明并留审计。
- 新增保留变化两端的整组素材编译，沿用 v4.2 版本批准与渲染复核；导演及故事弧提示保留人物状态上下文和来源标识。
- 新增状态模型/审核、浏览器与 Python 契约、无未来信息泄露、冲突/过期/身份映射及真实状态素材渲染测试。
- 更新字段说明、JSON Schema、全待审虚构示例与完整操作流程。
- 验证：156 项测试全部通过（含真实状态素材渲染）；Python 与 12 处 Web JavaScript 语法检查通过，完整 ZIP 校验通过。


## v4.2.0

- 完整连续性审核契约：追加审核者、理由、时间、前后状态和关联内容快照；兼容旧历史但禁止无审计 PASS 自动进入导演编排。
- 导演引擎读取审核通过的正反打、反应、对话及动作连续候选，严格限制同集事件/证据和既有素材窗口，保留来源与风险。
- 新增 A/B、增删/顺序/时间差异、版本比较页面和逐 clip 选择合并，所有新版本待审且输出不覆盖。
- 新增带项目指纹的反馈返工、未解决问题阻塞、版本审计批准和两个渲染器的过期/未审拦截。
- 新增渲染复核编排、成片时间轴/关键帧、黑场/静音问题回溯；语义重复、字幕与突兀切换保留人工审核边界。
- 新增浏览器审核导出与 Python 消费的契约测试，以及真实版本渲染、关键帧与检测回溯测试。
- 新增可复现完整 ZIP 构建及回归/语法/归档检查脚本。
- 验证：基线 104 项、新版 128 项测试全部通过（含真实 FFmpeg）；Python 与 10 处 Web JavaScript 语法检查通过，完整 ZIP 校验通过。


## 3.8.0

- 新增 `series_search.py`：强制指定截止集数的剧透安全检索，按分集本地记录构建检索文档，避免全剧合并身份/后期别名倒灌到早期观看视角。
- 新增 BM25 + 中文字符 n-gram 检索和保守领域同义词；提供截止集数受限的 ChatGPT 查询扩展提示。明确本版未使用向量 embedding。
- 新增人物视角筛选；角色认知只使用已审核、时点有效的记录，默认不把角色私有认知混入普通观众视角；指定全局人物视角还必须通过截止集之前的身份可见性声明。
- 新增 `series_visibility.py`：人工声明人物、剧情线、伏笔或跨集因果何时对观众可见；必须提供不晚于声明集、且直接关联实体的证据，并写入审计。
- 新增 `series_search_server.py` 和 `web/series-search.html`：本地截止集数检索界面/API，强制填写 as-of。
- 新增 `dedupe_clips.py`：基于同源时间重叠、FFmpeg 灰度 dHash 多帧相似度、文本/台词 n-gram 的重复素材候选检测。
- 新增 `web/dedupe-review.html`：人工审核相似素材，导出剪辑项目副本；检测器不自动删除、禁用或改动剧情事实。
- 新增剧透边界、未来别名隔离、检索扩展提示限制、可见性证据归属、dHash 多帧匹配与人工去重策略测试。


## 3.7.0

- 将跨集剪辑提升为“故事弧编译”：使用明确叙事角色、事件 ID、证据引用和连续性依据构建 setup→peak→reaction→payoff 等完整故事计划。
- 新增 `story_arc_compiler.py make-prompt`：按人物/关系/剧情线/伏笔扩展已审核剧情线上下文，并带入一跳因果邻居、分集故事弧、关系历史及人物/观众认知状态，供 ChatGPT 规划。
- 加强叙事计划校验：连续性边必须连接计划中实际选择的事件；片段证据必须属于该段所选事件；不允许将 imagined/hypothetical 当作现实事件。
- 加强发布门槛：缺失叙事角色、未审核事件/段落/因果边、未解除风险、关键角色缺少证据或要求的认知覆盖不足，均阻止 `release_ready`。
- 每个剪辑片段新增 `knowledge_gap_flags`，识别观众先于角色知道、角色先于观众知道的信息差；未来认知不会倒灌到早期事件。
- 更换分集 dossier 后，触及该集的已通过跨集因果边自动重开为 `NEED_EVIDENCE` 并写入审计。
- 新增 `story-arc-workbench.html` 计划审核、理由记录、段落顺序调整和导出；全剧记忆页增加人物认知与跨集因果浏览；跨集剪辑工作台显示叙事角色与认知差异。
- 新增故事弧计划 JSON Schema、示例和操作流程文档。
- 新增认知时间不倒灌、证据归属、因果审核、缺失角色、非线性理由、计划发布门槛和分集替换重开因果审核等测试。

## 3.5.0

- 新增 `batch_media_scan.py`：整季原片逐集预扫描，生成多模态 cue、关键帧、联系表和 AI 审核提示。
- 批量扫描增加 SHA-256 清单、断点续跑、失败记录与源视频变化保护。
- 新增 `series_memory.py`：使用 SQLite 管理 series、episodes、人物全局实体、分集人物提及、事件时间线、关系历史、剧情线、伏笔和审计日志。
- 新增逐集 dossier 批量导入；每集独立事务、内容哈希去重、变化覆盖需显式 `--replace`。
- 新增跨集人物身份、剧情线与伏笔/线索精确匹配候选；默认不自动合并。
- 新增人工 `link-character` / `link-storyline` / `link-thread`，以及 `reject-candidate`；必须填写理由并保留审计记录。
- 新增跨集检索、进度状态、全剧记忆 JSON 导出、证据时间码索引与可视化审计日志。
- 新增 `web/series.html` 只读全剧记忆面板。
- 明确单集 dossier schema 保持 3.4.0；v3.5 series memory 为独立可重建投影，避免无必要的单集档案迁移。
- 新增系列记忆、批量导入、断点续跑、身份/剧情线链接与检索测试。


## 3.4.0

- 新增完整转录时间段 cue（`transcript_segment`），保留全部字幕/ASR 时间码；该 cue 不参与峰值锚点生成。
- 多模态扫描增加均匀关键帧覆盖策略：即使片中 cue 很多，也会保留全片分布式抽帧。
- 增加 RMS 音量峰值与持续低能量区间定位；明确不将它们解释为情绪。
- 增加 JPG 故事板联系表、HTML 故事板和 ChatGPT 视觉审核提示。
- 增加 AI 视觉审核 JSON Schema 与示例。
- 增加 `import_ai_review.py`，事务式导入视觉观察为 `VIS` 证据、事件候选为 `UNREVIEWED`；输入校验失败不改动原档案。
- 重构剧情弧线候选：`payoff` 仅在同剧情线或直接因果关系成立时提出；缺项进入 `missing_roles`。
- 音量峰值/镜头切换不再自动抬高情绪/视觉/剧情价值评分。
- 剪辑项目保留 `story_arc_ids`，让成片片段可追溯到剧情弧线。
- 审核台支持导入 AI 视觉审核 JSON；剪辑工作台增加音画线索轨道和可点击弧线段落。
- 加强档案校验：弧线锚点、段落角色、段落范围、引用和分数范围检查。
- 增加迁移、音频静音定位、AI 导入事务性、弧线 payoff 关联、剪辑来源追踪等测试。


## v3.6.0

- 新增 `series_compiler.py`：从全剧记忆库按人物、双人物关系、剧情线、伏笔或词面查询生成跨集剪辑项目。
- 默认只纳入审核通过的实际事件；保留集数、原片路径、时间码、事件、高光与证据来源。
- 增加目标时长完整片段选择；不通过硬截断单个事件凑时长；`--all-events` 保留全部合格事件。
- 新增 `render_series_project.py`：多源 FFmpeg 渲染，统一画幅/帧率/像素格式/音频规格，无音轨片段补静音。
- 新增 `web/series-workbench.html`：跨集多源视频预览、片段入点/出点精修、启用/禁用、排序、移除与 JSON 导出。
- 修复全剧记忆页面伏笔详情分支重复导致的 UI 错误，并增加进入跨集剪辑工作台的入口。
- 新增跨集编译、双人物关系、时长筛选、多源音轨兼容测试。

## v3.9.0
- 新增 evidence-aware cut-boundary suggestions from subtitle, shot-change and silence cues; suggestions remain unreviewed and do not mutate clips automatically.
- 新增 edit continuity QC for invalid ranges, source-duration overflow (when supplied by caller), source overlaps, missing traceability and unreviewed boundaries.
- 新增 `tests/test_edit_quality.py`.
- 95 regression tests pass.

## v4.0.0
- Added explainable director pacing/transition suggestions keyed to narrative roles and reviewed shot observations; no automatic trimming.
- Added post-render FFprobe/FFmpeg technical review for streams, duration, black segments and silence candidates.
- Added real generated H.264/AAC post-render integration test.
- 99 regression tests pass.

## v4.1.0
- Added evidence-linked shot/reverse-shot, reaction-shot, dialogue-turn and action-continuity candidate matching from reviewed shot observations and media cues.
- Added continuity risk checks for gaze direction, axis/position, action phase, dialogue overlap and missing evidence.
- Candidates remain UNREVIEWED; no face recognition or unsupported identity inference.
- 99 regression tests pass, including real FFmpeg H.264/AAC post-render review.

## v4.1.0
- Added evidence-linked shot/reverse-shot, reaction-shot, dialogue-turn and action-continuity candidates from reviewed shot observations.
- Added dedicated human review UI at `web/shot-continuity-review.html`.
- Added continuity QC for missing event/evidence links and unreviewed candidate states.
- All candidates remain UNREVIEWED; no face recognition or unsupported identity inference.
- 104 regression tests pass.



## v4.3.1 (patch candidate)

- Tighten state-change validation: each linked context event must occur within the inclusive interval between the source and destination state timestamps.
- Add regression tests for events before, after, and exactly at the transition endpoint.
- No dossier schema or SQLite migration; existing records remain readable, but invalid transition links must be corrected and re-reviewed.
- Full repository test/build validation is pending; this patch is published on a dedicated branch for review, not merged to main.


## v4.3.1 (patch candidate)

- Require state-change context events to fall within the inclusive source-to-destination state time interval.
- Add local/global target-character mapping fields to trajectory records; unreviewed or absent mappings remain `UNRESOLVED`. The current one-to-one local mention schema makes `AMBIGUOUS` unreachable, so that status was removed rather than expanding the database model.
- Add diagnostics for overlapping conflicting state validity intervals and transition graph forks/merges.
- Add a user-defined character-arc milestone coverage checker; no automatic claim of semantic growth.
- Added regression coverage for event timing and state graph diagnostics. Full repository validation remains pending.


## v4.3.1 WorkBuddy regression fixes (in progress)

- B1: state graph fork/merge diagnostics now expose a consistent `state_ids` field expected by the state-cut compiler.
- B2: correct the regression fixture to distinguish a timestamp strictly before the source state from the inclusive source-state endpoint; preserve closed-interval semantics.
- B3: scope state-graph diagnostics to the requested local character in trajectory queries.
- B4: close the FFmpeg filter-script temporary file before launching the subprocess on Windows, clean it up in `finally`, and preserve stderr in a CLI-friendly error.
- Added regression tests for B1/B2/B3. B4 still requires Windows execution in the reported environment. B5-B10 are not changed until their full report evidence is available.

- Fix arc integrity coverage to ignore disabled clips and reject unknown plan fields; reject unknown character-state fields in Python validation and JSON Schema.
- Add regression tests for review downgrade, enabled/disabled arc coverage, unknown fields, unresolved target mappings and Windows-safe FFmpeg temp-file lifecycle.


## v4.3.1 validation update

- Fix Windows review-cut temporary filter file lifecycle and UTF-8-safe FFmpeg stderr decoding.
- Switch single-episode and cross-episode renderers to FFmpeg 7.1+ `-/filter_complex`, addressing FFmpeg 9 removal of `-filter_complex_script` while retaining compatibility with FFmpeg 8.1.
- Correct the cross-character test fixture so each transition timestamp equals its destination state timestamp.
- CI result: 175 tests passed on Windows and 175 passed on Ubuntu; Python syntax checks and 12 Web JavaScript blocks/files passed on both; each build produced and verified a 100-file `skill.zip`.
- B8/B9 remain unassessed because their detailed repro steps were not included in the uploaded WorkBuddy handoff summary; do not infer their fixes.


- Clarify N5: the character-state JSON Schema validates candidate import payloads only; the complete dossier example is validated by the Python dossier validator. Schema description, README, SKILL and workflow documentation now state this mapping explicitly.
