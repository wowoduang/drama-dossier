# v3.8 剧透边界工作流

## 原则

- 截止集数是硬过滤，不是排序偏好。
- 只使用截止集数以内的分集事件、分集本地别名、分集剧情线摘要、伏笔描述、关系和认知状态。
- 全剧数据库中的全局身份、剧情线、伏笔和跨集因果可能是在后续集数才被发现；只有 `series_visibility` 中存在 `PASS` 声明且 `effective_episode_number <= cutoff` 才允许在截止模式中使用。
- 声明时间必须来自故事内容证据，不得使用数据库导入时间或人工合并时间替代。
- 缺少声明时宁可漏掉跨集关联，也不能猜测它在何时已为观众所知。

## 声明可见时间

```bash
python scripts/series_visibility.py work/series.sqlite --series-id SERIES01 --entity-type character --entity-id GCHR_001 --known-by-episode EP015 --evidence-ref EP015:EVD003 --reason "第15集原片明确揭示身份" --reviewer "审核者"
```

类型：`character`、`storyline`、`thread`、`causal_edge`。证据集数不得晚于声明集数；每次声明写入 `visibility_audit`。

## 生成截至某集的故事弧

```bash
python scripts/story_arc_compiler.py make-prompt work/series.sqlite work/arc-prompt.md --series-id SERIES01 --character GCHR_001 --goal "截至第15集的关系发展" --as-of EP015
```

全剧剧透模式必须显式选择 `--full-series`。计划中加入：

```json
"spoiler_guard": {"enabled": true, "max_episode_number": 15}
```

编译器拒绝未来事件及未声明可见时间的跨集链接。严格模式不允许把未来才确认的身份、剧情线或因果关系倒灌进早期叙事。
