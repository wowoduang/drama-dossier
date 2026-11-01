# v3.8 混合语义检索工作流

1. 必须选择截止集数 `--as-of EP015`，不能在剧透安全模式中省略。
2. 本地引擎只从截止集数及以前的 episode-local 对象构建文档；默认只检索 `PASS` 对象。
3. 中文字符 n-gram 与 BM25 提供可解释的本地词项召回；可选 ChatGPT 扩展词改善同义表达覆盖。
4. 查询扩展提示只包含截止范围内的候选上下文。模型输出的扩展词是检索线索，不是剧情事实。
5. 检索结果必须回到事件证据和原片核对；相关度分数不等于事实置信度。

```bash
python scripts/series_search.py search work/series.sqlite "背叛后如何复仇" --series-id SERIES01 --as-of EP015 --limit 30 --output work/search.json
python scripts/series_search.py make-expansion-prompt work/series.sqlite "背叛后如何复仇" work/expand.md --series-id SERIES01 --as-of EP015
python scripts/series_search_server.py work/series.sqlite --series-id SERIES01 --port 8766
```

该实现是 BM25 + 中文 n-gram 的语义辅助检索，不是向量嵌入模型。未来集数、全局未来别名和全局合并描述不得进入严格截止结果。
