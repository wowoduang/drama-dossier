# v4.2 审核、导演候选与多轮返工

延续 v4.1 的 dossier JSON、SQLite 投影和 edit-project 派生层，不迁移剧情事实或数据库。新增审核字段和 `edit_version` 是独立的剪辑审计信息。

## 1. 连续性审核契约

`shot_continuity.py candidates` 保留规范化 `shots`。每条 candidate 有 `review_status`、`review_reason`、`reviewer`、`reviewed_at` 和追加式 `review_history`。历史项保存 previous_status/status、reason、reviewer、带时区的 reviewed_at、continuous_source_checked，以及 episode/candidate/关联 shots 的 snapshot。改变证据、事件、视线、人物、时间或理由会使旧 snapshot 失效。快照用于发现误改，不是防篡改签名，也不验证审核者身份。

UI：`web/shot-continuity-review.html`。每次保存都要求审核者和理由，PASS 还要求回看连续原片并确认列出的风险；导出 JSON 可直接交给导演引擎。CLI 等价操作：

```bash
python scripts/shot_continuity.py review continuity.json continuity.reviewed.json \
  --candidate-id SC_0001 --status PASS --reviewer editor \
  --reason "回看连续原片，确认反应对象、视线和轴线" --continuous-source-checked
python scripts/shot_continuity.py qc continuity.reviewed.json continuity.qc.json
```

兼容旧版：`migrate old.json upgraded.json` 保留原有历史，不补造审核者或镜头观察。旧版只有状态的 PASS 不可直接用于导演编排。旧文件没有 shots 时，先用原 dossier、多模态数据及 shot-observations 重新生成 v4.2 候选，保留旧文件备查，再人工复核。重新审核不会使缺失证据变成有效证据。

## 2. 导演接入与安全范围

```bash
python scripts/director_engine.py project.json director.json \
  --continuity continuity.reviewed.json --profile balanced
python scripts/edit_versions.py ab project.json continuity.reviewed.json work/versions-001
```

导演只读取当前审核完整的 PASS 候选，要求关联镜头 PASS、事件和证据非空且归属一致。候选按 episode 和 event/evidence 精确匹配已有 clip；替代窗口限制在该 clip 已选原片区间内。正反打/动作/对话保留整组区间；反应镜头只用于 reaction 叙事位置，并要求明确 reaction_target_character_id。不会生成未见过的反应、跨事件补画面、扩张剧透范围或自动删除台词。

跨集项目可传入 `{"episodes": [第1集审核结果, 第2集审核结果]}` bundle；每集 episode_id 必须唯一，局部人物/镜头/候选 ID 不跨集混用。

输出 `director_plan.shot_alternatives` 带来源镜头、候选 ID、理由、证据、已核实风险和新的剪辑风险。**镜头候选通过审核不等于新的剪辑选择通过审核。**

## 3. A/B、差异与选择

`ab` 创建全新目录：a.json 保留原片段，b.json 按候选顺序为每个片段选择第一个合格替代方案（无合格候选则 A/B 内容相同），另有 alternatives.json、diff.json。两个版本都标为 UNREVIEWED；不覆盖原项目。生成规则是保守可解释的确定性编排，非自动审美理解。

```bash
python scripts/edit_versions.py diff work/versions-001/a.json work/versions-001/b.json work/diff.json
python scripts/edit_versions.py merge work/versions-001/a.json work/versions-001/b.json \
  work/merged.json --choose-b C1 C3
```

打开 `web/version-compare.html` 载入 A/B，可查看增删、顺序、源片、入出点及总时长变化，勾选使用 B 的片段并导出 `version-selection.json`：

```bash
python scripts/edit_versions.py merge work/versions-001/a.json work/versions-001/b.json \
  work/merged.json --selection version-selection.json
```

合并保留 A 顺序，目前要求相同 clip ID 集合。增删和重排会显示在差异中，但需在已有剪辑工作台手工调整，再重新审核。每次 merge 都是新的待审版本。

## 4. 返工与人工批准

```bash
python scripts/edit_versions.py fingerprint work/merged.json
```

将输出填入反馈 JSON，防止把旧反馈用于已经变化的项目：

```json
{
  "project_fingerprint": "上述输出",
  "issues": [{
    "clip_id": "C1",
    "shot_id": "S1",
    "message": "反应镜头不对，请重新核实",
    "alternative_id": "C1:SC_0002"
  }]
}
```

`shot_id` 和 `alternative_id` 可省略；无选择时输出可追溯候选并保留 unresolved_issues，不猜测问题的解决方案。若人工确认无需改动，用 issue.resolution 填写理由。自由文本不是已解决问题的证明，最终版本仍需人工批准。

```bash
python scripts/edit_versions.py rework work/merged.json feedback.json \
  continuity.reviewed.json work/rework-002.json
python scripts/edit_versions.py approve work/rework-002.json work/rework-002.approved.json \
  --reviewer editor --reason "逐条核实返工问题、台词完整性和新入出点"
```

返工时重新读取连续性审核文件；过期、撤回或跨片段候选不可使用。决策记录包括修改前 clip、替代来源和反馈原文。未解决问题阻止批准。approve 运行基础剪辑 QC，并记录项目 SHA-256；批准后改变项目内容会使审核失效。两个渲染器均拦截未批准的新版本；无 edit_version 的旧项目保留原有行为。该批准只表示剪辑选择审核，不把剧情审核或 narrative_audit.release_ready 自动提升为通过。

## 5. 新版本渲染与复核

```bash
python scripts/review_cut.py work/rework-002.approved.json output/cut-002
```

输出 final.mp4、project.json、qc.json、review.json 和每个启用片段的成片中点关键帧。新目录必须不存在；执行实际 FFmpeg 渲染、FFprobe/黑场/静音检测，生成成片时间轴，并把跨剪辑边界的检测区间映射回每个 clip 的源片时间码。总时长不匹配时停止可信映射并报告错误。

重复源片段由基础 QC 提示；画面语义重复、突兀切换、字幕遮挡/同步、表演和剧情仍需人工完整审片。关键帧不是连续性证明，本版没有新增自动字幕识别、视觉理解或光流连续性检测。黑场与静音只给问题候选，不自动删除。修改后再次生成版本、批准、渲染复核，即可多轮迭代。

## 6. 验证和打包

```bash
python scripts/build_release.py
```

运行全量 unittest（含真实 FFmpeg 渲染）、Python 编译、全部内联/外部 JavaScript 语法检查，随后生成根目录 skill.zip 并检查 ZIP 完整性。归档不含 Git、缓存、用户素材和上个归档，成员按名称排序并使用固定时间戳。
