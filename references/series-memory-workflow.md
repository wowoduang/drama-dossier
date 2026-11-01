# Series Memory Engine v3.5

## Design principle

- Episode dossier JSON is the canonical, reviewable source for one episode.
- The SQLite series database is a rebuildable cross-episode projection, not a replacement for source dossiers.
- Original video remains local. Only compact contact sheets, selected short clips, subtitles/transcripts, and structured dossiers are sent to ChatGPT when semantic analysis is needed.
- Name/alias matches create suggestions only. They never merge people or storylines automatically.
- Every confirmed cross-episode link requires a human reason and is appended to `audit_log`.

## A. Batch-scan original videos locally

```bash
python scripts/batch_media_scan.py ./series_videos ./series_work/scans \
  --pattern "**/*.mp4" --subtitle-dir ./series_subtitles \
  --sample-interval 8 --max-frames 100
```

The scanner creates one folder per source file, containing `multimodal.json`, `frames/`, contact sheets, and `ai_review_prompt.md`. It stores a manifest and resumes unchanged files on the next run. If a source file changes, it reports `CHANGED_NEEDS_REPLACE`; rerun with `--replace` only after confirming the new source cut is intended.

This stage measures media cues and extracts frames. It does not identify characters, understand actions, assign motives, or create a confirmed story dossier. Missing subtitles can be generated separately with `transcribe.py` and passed on a later scan.

## B. Build and review each episode dossier

For each scan folder:

1. Provide the contact sheets, AI review prompt, subtitle/transcript and only the relevant continuous source clips to ChatGPT.
2. Ask for visual observations and event candidates in the documented AI review JSON format.
3. Import candidates into the episode dossier; do not promote them automatically to `PASS`.
4. Review character identity, action continuity, speaker attribution, event modality, causality and storyline membership.
5. Save the reviewed dossier as a stable per-episode JSON file, e.g. `series_work/dossiers/EP001/episode.json`.
6. Validate it with `python scripts/dossier_v3.py validate ...`.

A full-season batch can be prepared locally, but semantic interpretation still requires the model to inspect the selected evidence. Do not claim the local scanner has understood the episode.

## C. Initialize the series database

```bash
python scripts/series_memory.py init ./series_work/series.sqlite \
  --series-id SERIES01 --title "剧名"
```

## D. Batch import dossiers and resume

```bash
python scripts/series_memory.py batch-import ./series_work/series.sqlite \
  ./series_work/dossiers --series-id SERIES01 --pattern "**/episode.json"
```

The importer:

- Validates each dossier before importing it.
- Uses SHA-256 to skip unchanged files.
- Commits each episode independently, so an interrupted batch can resume.
- Requires `--replace` before replacing an already imported episode whose file hash changed.
- Keeps per-episode source path, source hash, event/character counts and import timestamp.
- Stores evidence timecodes and raw observations in the exported evidence index.

## E. Review cross-episode identity/storyline/foreshadowing suggestions

```bash
python scripts/series_memory.py status ./series_work/series.sqlite --series-id SERIES01
python scripts/series_memory.py candidates ./series_work/series.sqlite --series-id SERIES01
```

Exact canonical-name/alias, storyline-title and thread-description matches are candidate links only. Review the original evidence and narrative context before confirming:

```bash
python scripts/series_memory.py link-character ./series_work/series.sqlite \
  "EP001:CHR001" "EP008:CHR004" --series-id SERIES01 \
  --reason "姓名、外观线索及第8集身份揭示共同确认是同一人物" --reviewer "reviewer"

python scripts/series_memory.py link-storyline ./series_work/series.sqlite \
  "EP001:STL001" "EP008:STL003" --series-id SERIES01 \
  --reason "两集事件属于同一复仇主线" --reviewer "reviewer"
```

A cross-episode clue/foreshadowing can be linked similarly:

```bash
python scripts/series_memory.py link-thread ./series_work/series.sqlite \
  "EP002:TH001" "EP019:TH006" --series-id SERIES01 \
  --reason "第19集明确回收第2集埋下的信件线索" --reviewer "reviewer"
```

The first local entity is retained as the canonical global entity. All linked episode mentions and event/relationship projections are repointed. The action and reason are recorded in the audit log. Never merge based on name alone where homonyms, aliases, disguises, doubles or unreliable narration are possible. False matches can be rejected with:

```bash
python scripts/series_memory.py reject-candidate ./series_work/series.sqlite CANDIDATE_ID \
  --kind identity --series-id SERIES01 --reason "同名不同人，服饰与身份线索冲突" --reviewer "reviewer"
```


## F. Search and export full-series memory

```bash
python scripts/series_memory.py search ./series_work/series.sqlite "背叛" --series-id SERIES01
python scripts/series_memory.py export-memory ./series_work/series.sqlite \
  ./series_work/series-memory.json --series-id SERIES01
```

Open `web/series.html` in the local workbench and load the exported JSON. It shows episode coverage, global character timelines, storyline distribution, cross-episode foreshadowing, candidate links, per-episode provenance and the human audit log. It is a read-only view; execute approved merge commands in the CLI so audit history is retained.

## G. Cross-episode analysis protocol

When answering a question about the whole series:

1. Search the exported series memory for relevant people, events, storylines and threads.
2. Check each relevant object's review status; distinguish `PASS` from `UNREVIEWED`, `DISPUTED` and `NEED_EVIDENCE`.
3. Build the cross-episode timeline in episode order and preserve playback time within each episode.
4. Keep character knowledge state separate from audience knowledge; later revelations must not rewrite what a character knew earlier.
5. Cite local episode/event/evidence IDs and exact timecodes for key claims.
6. If a claim depends on visual identity, gesture, facial expression, action continuity or sound, request only the relevant original clip/keyframes for reinspection. The database is an index, not a substitute for the source video.
7. Never infer a causal edge solely from episode order or temporal proximity.

## H. Limits

- The database currently provides deterministic exact-name/alias, exact-storyline-title and exact-thread-description suggestions, not fuzzy or embedding-based entity resolution.
- Character identity and storyline linking are human-reviewed; no automatic identity merging.
- 伏笔/线索跨集候选目前仅按描述完全匹配提出；需通过 `link-thread` 人工确认，不做语义相似度自动合并。
- Search is SQLite text matching, not semantic vector search.
- A global timeline is only as complete as the imported and reviewed episode dossiers.
