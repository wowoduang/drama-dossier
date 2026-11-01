#!/usr/bin/env python3
"""Render a new cut, run technical QC and map findings to source clips."""
import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile

from continuity_review import valid_range
from edit_versions import clip_index, fingerprint, require_approved, write_new
from edit_quality import continuity_qc, read_json
from post_render_review import inspect_video
from render_series_project import build_command


def timeline(project):
    clip_index(project)
    t, rows = 0.0, []
    for c in project.get('clips', []):
        if c.get('enabled', True) is False: continue
        length = c['end_sec'] - c['start_sec']
        rows.append({'clip_id': c['clip_id'], 'episode_id': c.get('episode_id'),
            'source_video': c.get('source_video'), 'source_start_sec': c['start_sec'],
            'source_end_sec': c['end_sec'], 'start_sec': t, 'end_sec': t+length,
            'shot_ids': c.get('shot_ids', [])})
        t += length
    return rows


def map_report(project, report):
    rows = timeline(project)
    out = deepcopy(report)
    out['timeline'] = rows
    out['project_fingerprint'] = fingerprint(project)
    out['edit_qc'] = continuity_qc(project)
    expected = rows[-1]['end_sec'] if rows else 0
    out['expected_duration_sec'] = expected
    out['timeline_mapping_valid'] = abs(expected - report['duration_sec']) <= max(.15, len(rows)/30)
    for issue in out.get('issues', []):
        segment = issue.get('segment')
        issue['source_clips'] = []
        if segment and valid_range(segment) and out['timeline_mapping_valid']:
            for r in rows:
                start, end = max(r['start_sec'], segment['start_sec']), min(r['end_sec'], segment['end_sec'])
                if start < end:
                    issue['source_clips'].append({'clip_id': r['clip_id'], 'episode_id': r['episode_id'],
                        'source_start_sec': r['source_start_sec'] + start-r['start_sec'],
                        'source_end_sec': r['source_start_sec'] + end-r['start_sec']})
    if not out['timeline_mapping_valid']:
        out.setdefault('issues', []).append({'severity': 'ERROR', 'code': 'TIMELINE_DURATION_MISMATCH', 'source_clips': []})
        out['technical_check'] = 'FAIL'
        out['release_blockers'] = out.get('release_blockers', 0)+1
    out['manual_checks'] = ['重复镜头与剧情语境', '突兀切换、轴线与动作连续', '字幕完整性、遮挡与同步', '完整成片审看']
    return out


def render_review(project, directory, width=1920, height=1080, fps=30):
    require_approved(project)
    qc = continuity_qc(project)
    if not qc['release_ready']: raise ValueError('剪辑 QC 阻塞渲染')
    root = Path(directory)
    # Build/validate first; never overwrite an earlier cut.
    cmd, filters = build_command(project, root/'final.mp4', width=width, height=height, fps=fps)
    root.mkdir(parents=True, exist_ok=False)
    write_new(root/'project.json', project)
    with tempfile.NamedTemporaryFile('w', suffix='.ffscript', encoding='utf-8') as f:
        f.write(filters); f.flush()
        subprocess.run([f.name if x == 'FILTER_FILE' else x for x in cmd], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    report = map_report(project, inspect_video(root/'final.mp4'))
    frames = []
    for i, row in enumerate(report['timeline']):
        dst = root/f'frame-{i+1:04d}.jpg'
        subprocess.run([cmd[0], '-hide_banner', '-loglevel', 'error', '-ss', str((row['start_sec']+row['end_sec'])/2), '-i', str(root/'final.mp4'), '-frames:v', '1', '-vf', 'scale=480:-2', str(dst)], check=True)
        if not dst.is_file(): raise RuntimeError('关键帧提取失败')
        frames.append({'clip_id': row['clip_id'], 'path': dst.name})
    report['keyframes'] = frames
    write_new(root/'review.json', report)
    write_new(root/'qc.json', qc)
    return report


def main():
    p = argparse.ArgumentParser(description='渲染新版本、成片技术复核、关键帧与问题源片段映射')
    p.add_argument('project'); p.add_argument('output_dir'); p.add_argument('--width', type=int, default=1920); p.add_argument('--height', type=int, default=1080); p.add_argument('--fps', type=int, default=30)
    a = p.parse_args()
    if min(a.width, a.height, a.fps) <= 0 or a.width % 2 or a.height % 2: p.error('画幅必须为正偶数，帧率必须为正数')
    try: render_review(read_json(a.project), a.output_dir, a.width, a.height, a.fps)
    except (ValueError, OSError, subprocess.CalledProcessError) as e: raise SystemExit(str(e))

if __name__ == '__main__': main()
