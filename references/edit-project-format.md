# 剪辑项目 JSON 格式

剪辑项目不是剧情事实源。它引用剧情档案中的 `highlight_id`，保存用户对成片顺序和片段边界的选择。

```json
{
  "project_version": "1.0",
  "dossier_schema_version": "3.5.0",
  "episode_id": "E01",
  "title": "第一集",
  "source_video": "/path/to/episode.mp4",
  "preset": "60s",
  "target_duration_sec": 60,
  "estimated_duration_sec": 54.2,
  "sequence_strategy": "chronological",
  "created_at": "2026-10-31T00:00:00+00:00",
  "clips": [
    {
      "clip_id": "CLP_001",
      "highlight_id": "HL_xxx",
      "start_sec": 120.4,
      "end_sec": 128.8,
      "title": "身份揭示",
      "reason": "改变人物关系和后续目标",
      "event_ids": ["EVT_xxx"],
      "storyline_ids": ["STL_xxx"],
      "story_arc_ids": ["ARC_xxx"],
      "score": 86.4,
      "enabled": true
    }
  ]
}
```

约束：

- `start_sec >= 0` 且 `end_sec > start_sec`。
- `enabled=false` 的片段不参与渲染。
- `clip_id` 只在当前剪辑项目内稳定；真正的剧情稳定 ID 是 `highlight_id/event_id`。
- 人工重排后设置 `sequence_strategy=manual`。
- `source_video` 可以留空，渲染时用 `--video` 指定。
