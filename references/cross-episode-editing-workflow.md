# 跨集剪辑工作流 v3.6

## 目标

从已审核的全剧记忆数据库中，按人物、双人物关系、剧情线、伏笔或文本查询检索跨集事件，生成保留来源追溯的多源剪辑项目，再由人工在跨集剪辑工作台精修并通过 FFmpeg 渲染。

## 1. 建立每集视频映射

创建 `work/sources.json`，键为 dossier 的 `episode.episode_id`，值为对应原片路径。示例见 `examples/sources.example.json`。路径可以是相对当前工作目录的路径，但建议在生成项目时使用稳定的绝对路径；移动原片后应更新映射并重新生成项目。

## 2. 选择剪辑主题

人物专辑：

```bash
python scripts/series_compiler.py work/series.sqlite work/sources.json work/person.json \
  --series-id SERIES01 --character GCHR_0123456789 --preset 5m
```

双人物关系专辑（两个已人工关联的全剧人物 ID）：

```bash
python scripts/series_compiler.py work/series.sqlite work/sources.json work/relationship.json \
  --series-id SERIES01 --pair GCHR_A,GCHR_B --preset 3m
```

剧情线专辑：

```bash
python scripts/series_compiler.py work/series.sqlite work/sources.json work/storyline.json \
  --series-id SERIES01 --storyline GSTL_0123456789 --preset 5m
```

伏笔/线索专辑：

```bash
python scripts/series_compiler.py work/series.sqlite work/sources.json work/thread.json \
  --series-id SERIES01 --thread GTHR_0123456789 --preset 3m
```

文本查询候选（仅 SQL LIKE 词面检索，不等于语义理解）：

```bash
python scripts/series_compiler.py work/series.sqlite work/sources.json work/theme-candidates.json \
  --series-id SERIES01 --query "背叛" --preset 5m
```

主题专辑应先由 ChatGPT 从全剧记忆中找出对应剧情线/事件并人工确认，再用剧情线 ID 编译。不要把词面命中直接当作语义主题分类。

## 3. 默认筛选规则

- 默认只纳入 `review_status=PASS` 的实际事件；排除 `INCORRECT`、`imagined`、`hypothetical`。
- 优先采用已审核高光的起止时间；没有高光时，按证据时间窗或事件时间加前后余量生成保守窗口。
- 缺少视频映射的集数会写入 `compile_report.missing_sources`，不会悄悄伪装成已剪入素材。
- `--include-unreviewed` 允许把未审核事件加入候选项目，但项目会保留审核状态；正式发布前必须人工核实。
- 默认按 `30s / 60s / 3m / 5m` 目标时长挑选完整片段，不切短单个事件来凑时长。若必须保留所有匹配事件，使用 `--all-events`。
- 目标时长筛选采用保守评分与剧情弧线角色加权，选后恢复原集数和原片时间顺序。它是候选剪辑策略，不保证自动构成完整故事；编辑者应检查铺垫、高潮、反应、结果是否缺失。

## 4. 人工精修

启动本地工作台：

```bash
python scripts/serve_review.py --port 8765
```

打开 `http://127.0.0.1:8765/web/series-workbench.html`：

1. 载入 `series-*.json` 剪辑项目。
2. 多选载入项目涉及的原片文件（浏览器仅本地预览，不上传）。
3. 选择片段，修改入点/出点、标题和剪辑理由。
4. 启用/禁用、上移/下移或移除片段。
5. 导出 `series-edit-project.json`。

## 5. 多源渲染

```bash
python scripts/render_series_project.py work/series-edit-project.json output/final.mp4
```

渲染器将不同集数的视频作为独立输入，统一输出画幅、帧率、像素格式和音频采样率；没有音轨的片段会补静音，保证 concat 时间轴一致。默认输出 H.264 + AAC。若原片路径失效，渲染会报出具体源路径。

## 6. 追溯与限制

每个 clip 保存 `episode_id / source_video / start_sec / end_sec / event_ids / highlight_id / evidence_ids / event_review_status / source_kind`。剪辑层只生成派生 edit project，不修改单集 dossier 或 Series Memory 事实。

当前 `--query` 是 SQLite 词面检索；人物关系选择只检索同一事件中两人共同参与的事件以及关系表中显式的关系起止事件。若档案没有记录这些事件，必须先补全/审核 dossier。剪辑顺序默认按原集数与时间排序；非线性叙事由人工在工作台调整。

多集视频的音量响度、色彩、帧率和画幅可能不同；本版统一技术规格，不做自动响度匹配、色彩匹配、转场、BGM、字幕烧录或平台适配。导出前需检查对白连续性、上下文完整性和素材发布权限。
