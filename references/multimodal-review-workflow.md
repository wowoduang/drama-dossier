# 多模态采集与视觉语义审核工作流

## 1. 自动采集能做什么

`scripts/multimodal_scan.py` 可提取：

- FFmpeg 镜头变化时间点；
- PCM RMS 音量峰值；
- 持续低能量/静音候选窗口；
- 字幕关键词、标点强调和长对白检索线索；
- 关键帧附近抽样与固定间隔均匀抽帧；
- HTML 故事板、JPG 联系表和 ChatGPT 视觉审核提示。

这些结果只用于定位。它不识别人脸、角色身份、动作语义、情绪、说话人或动机。

## 2. 推荐命令

```bash
python scripts/multimodal_scan.py episode.mp4 work/episode.multimodal.json \
  --subtitle work/episode.transcript.json --sample-interval 8 --max-frames 100
```

没有字幕时可以先运行 `transcribe.py`；已有 SRT/VTT 时直接传入。字幕/ASR 文本只证明转录轨道包含这些字词，重要对白必须核听。

## 3. 给 ChatGPT 的材料

至少提供：

1. `storyboard_XX.jpg` 联系表；
2. `*_ai_review_prompt.md`；
3. 与关键时间段对应的字幕/ASR；
4. 涉及动作连续、反应镜头或身份的原片短片段。

联系表的单帧只能描述可见状态。判断“拿起—递出—接过”这类动作链，必须查看连续视频；判断角色身份，必须有多镜头连续证据或剧情内明确指认。

## 4. AI 视觉审核 JSON

保存为 `ai_visual_review.json`：

```json
{
  "review_version": "1.0",
  "observations": [
    {
      "start_sec": 12.4,
      "end_sec": 16.8,
      "frame_refs": ["frame_00012.400_uniform_sample.jpg"],
      "visual_description": "画面右侧的人把桌面上的信封推向另一人。",
      "visible_person_descriptions": ["右侧穿深色衣服的人，身份未确认"],
      "actions": ["将信封推向对面人物"],
      "continuity_notes": "需回看相邻镜头确认是否由对方接过。",
      "confidence": "medium",
      "alternatives": ["也可能只是把信封移到桌子另一侧"]
    }
  ],
  "event_candidates": [
    {
      "playback_time": 14.1,
      "action": "将信封推向对面人物",
      "actor": null,
      "patient": null,
      "recipient": null,
      "target": null,
      "modality": "actual",
      "polarity": "positive",
      "certainty": "I2_PLAUSIBLE",
      "quote": "",
      "observation_index": 0,
      "evidence_ids": []
    }
  ],
  "scene_candidates": []
}
```

### 字段规则

- `confidence` 只能是 `high / medium / low`；这是观察者对描述的把握，不是事实等级。
- 未知人物槽位必须为 `null`；不可生成未存在的角色 ID。
- `certainty` 使用档案枚举，默认倾向 `I2_PLAUSIBLE`，只有画面/台词直接清楚支持时才考虑 `F1_DIRECT`。
- `observation_index` 从 0 开始，引用 `observations` 数组。
- `event_candidates[].evidence_ids` 可附加档案中已存在的证据 ID；视觉观察本身会自动生成新的 `VIS` 证据。
- 任何不确定的动作解释放在 `alternatives` 或 `semantic_note`，不要强行选一个。

## 5. 导入

```bash
python scripts/import_ai_review.py work/episode.json work/ai_visual_review.json
```

或在 `review.html` 点击“导入 AI 视觉审核”。导入对象一律为 `UNREVIEWED`，审核者回看原片后再决定 `PASS / NEED_EVIDENCE / INCORRECT / DISPUTED`。

## 6. 典型误差

- 镜头反打可能让两个人看起来在同一空间，但并不证明他们实际同场。
- 画面中出现一个人不等于此人说了当前字幕。
- 单帧中的手部姿势不证明完整动作链。
- 声音峰值可能来自配乐、爆炸、音效或混音，不自动代表人物情绪。
- 低能量窗口可能是弱对白、环境声或音轨问题，不自动代表沉默。
- 同一剧情线中的后续事件不必然是前一事件的“回收”；必须检查因果和叙事意义。
