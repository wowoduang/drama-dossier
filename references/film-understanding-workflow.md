# 影片理解与多轮反馈（文件交换版）

本流程复用 `multimodal_scan.py` 生成的 JSON；本地生成分段提示及进度，用户将提示、字幕、联系表和需要的连续视频片段交给 AI，再将回答保存为 UTF-8 文本导入。程序不调用 AI，也不自动上传影片。缺少连续画面时，AI 应标明无法确认的内容。

```bash
python scripts/film_understanding.py init work/episode.multimodal.json work/understanding --segment-sec 300
python scripts/film_understanding.py materials work/understanding --choice skipped
```

若要审核材料，将第二条改为 `--choice reviewed --note "已检查字幕、关键帧及片源"`。选择 skipped 只表示跳过，不代表材料已核实。

为每段依次生成提示，将提示及相关片段交给支持该输入的 AI，保存回答为文本：

```bash
python scripts/film_understanding.py prompt work/understanding S0001 work/prompt-1.txt
python scripts/film_understanding.py answer work/understanding S0001 work/answer-1.txt
python scripts/film_understanding.py review work/understanding S0001 --decision revise --reason "人物关系不清楚"
python scripts/film_understanding.py followup work/understanding S0001 work/followup-1.txt --question "请核对人物关系并给出证据时间"
python scripts/film_understanding.py answer work/understanding S0001 work/answer-2.txt --feedback "请核对人物关系并给出证据时间"
python scripts/film_understanding.py review work/understanding S0001 --decision satisfied --reason "本段内容符合需求"
python scripts/film_understanding.py status work/understanding
```

每次输入新的 AI 回答会创建新的轮次；上一轮满意度不会自动沿用。可不限定轮数重复提问。随后为所有分段重复；状态里有结果表示收到文本，不证明 AI 实际看过全部画面。

生成整片综合提示，再将它交给 AI；`FILM` 表示整片报告，可与分段一样反复审核、反馈：

```bash
python scripts/film_understanding.py synthesis-prompt work/understanding work/film-prompt.txt
python scripts/film_understanding.py answer work/understanding FILM work/film-answer.txt
python scripts/film_understanding.py review work/understanding FILM --decision revise --reason "缺少伏笔回收"
python scripts/film_understanding.py followup work/understanding FILM work/film-followup.txt --question "核对伏笔并说明证据"
python scripts/film_understanding.py answer work/understanding FILM work/film-answer-2.txt --feedback "核对伏笔并说明证据"
python scripts/film_understanding.py review work/understanding FILM --decision satisfied --reason "报告符合需求"
python scripts/film_understanding.py aggregate work/understanding work/film-results.json
```

汇编输出保留缺失段落和当前满意状态，满意只是用户对报告的评价，不会把剧情事实自动标为 PASS。状态及报告均需人工审查；没有经过真实影片端到端验证。分集档案仍由现有 `import_ai_review.py` 等流程导入与审核，本功能不修改事实库。输出文件与轮次均禁止覆盖；扫描文件更改会要求建立新会话。工具不会自动截取或上传原片，也不能保证 AI 对每帧的覆盖。


## 本地分段材料包与过期提示

```bash
python scripts/film_understanding.py prepare work/understanding S0001 work/package-1 --include-video
```

该命令在新文件夹生成提示、对应关键帧、时间映射清单和本地分段 MP4；不上传文件。视频片段的时间须加上 manifest 中的 `video_start_offset_sec` 才是原片时间。扫描数据附带原片 SHA-256 时会验证；未附带时清单会标注未验证，用户需确认片源版本。字幕源仍需按提示另行提供；未找到的关键帧列入 missing_frames。省略 `--include-video` 只打包提示和已有关键帧。

综合提示生成时记录各段结果版本。任何分段结果在此后发生变化，整片报告会标记 `film_report_stale`，之前的满意状态不再有效；需重新生成综合提示并请 AI 更新整片报告。已有旧报告不会被覆盖。
