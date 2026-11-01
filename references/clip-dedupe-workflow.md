# v3.8 相似镜头与重复素材审核

```bash
python scripts/dedupe_clips.py work/series-edit-project.json work/dedupe-report.json --threshold 0.95
```

工具从每个剪辑片段提取多个灰度 dHash 帧指纹，结合文本相似度与同源时间重叠，输出重复候选对和候选组。相似度阈值越高，召回越保守。

报告只建议人工检查，不会自动删除或禁用片段。回忆、闪回、监控回放、重复播放、平行剪辑、反打镜头和视觉呼应可能是有意叙事。

打开 `web/dedupe-review.html`，载入 edit-project JSON 与 dedupe-report JSON；人工判断后禁用其中一段，导出新项目。原始剧情事实档案不会被修改。
