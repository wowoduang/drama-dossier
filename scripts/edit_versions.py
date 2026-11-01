#!/usr/bin/env python3
"""Immutable edit versions, per-clip comparisons and evidence-linked rework.

No source dossier writes. Proposed edits always require explicit edit approval.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

from continuity_review import now, valid_range
from director_engine import shot_alternatives
from edit_quality import continuity_qc, read_json

VERSION = '4.2.0'


def fingerprint(project):
    data = {k: v for k, v in project.items() if k not in {'edit_version', 'version_qc'}}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def clip_index(project):
    clips = project.get('clips', [])
    ids = [c.get('clip_id') for c in clips]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('clip_id 必须唯一且非空')
    if any(not valid_range(c) for c in clips):
        raise ValueError('片段范围无效')
    return dict(zip(ids, clips))


def version(project, label, parent=None, decisions=None, unresolved=None):
    clip_index(project)
    out = deepcopy(project)
    out['edit_version'] = {'schema_version': VERSION, 'version_id': str(uuid.uuid4()), 'label': label,
        'parent_version_id': (parent or {}).get('edit_version', {}).get('version_id'),
        'parent_fingerprint': fingerprint(parent) if parent is not None else None,
        'created_at': now(), 'review_status': 'UNREVIEWED', 'review_history': [],
        'decisions': deepcopy(decisions or []), 'unresolved_issues': deepcopy(unresolved or [])}
    out['version_qc'] = continuity_qc(out)
    return out


def diff_projects(a, b):
    left, right = clip_index(a), clip_index(b)
    fields = ['episode_id', 'source_video', 'start_sec', 'end_sec', 'enabled', 'narrative_role', 'event_id', 'event_ids', 'evidence_ids', 'shot_ids']
    changed = []
    for cid in left.keys() & right.keys():
        changes = {k: {'before': left[cid].get(k), 'after': right[cid].get(k)} for k in fields if left[cid].get(k) != right[cid].get(k)}
        if changes:
            changed.append({'clip_id': cid, 'changes': changes})
    def duration(p):
        return sum(c['end_sec']-c['start_sec'] for c in p['clips'] if c.get('enabled', True))
    return {'added': [i for i in right if i not in left], 'removed': [i for i in left if i not in right],
        'order_before': list(left), 'order_after': list(right), 'changed': sorted(changed, key=lambda c: c['clip_id']),
        'duration_before_sec': duration(a), 'duration_after_sec': duration(b), 'duration_delta_sec': duration(b)-duration(a)}


def apply_alternatives(project, alternatives, selected):
    out = deepcopy(project)
    clips = clip_index(out)
    by_id = {a['alternative_id']: a for a in alternatives}
    if len(set(selected)) != len(selected) or any(i not in by_id for i in selected):
        raise ValueError('替代候选不存在或重复')
    targets = [by_id[i]['clip_id'] for i in selected]
    if len(set(targets)) != len(targets):
        raise ValueError('每个片段只能选择一个替代镜头')
    decisions = []
    for aid in selected:
        a = by_id[aid]
        c = clips[a['clip_id']]
        decisions.append({'alternative_id': aid, 'before': deepcopy(c), 'alternative': deepcopy(a)})
        c.update(start_sec=a['start_sec'], end_sec=a['end_sec'], shot_ids=a['selected_shot_ids'])
        c['continuity_selection'] = deepcopy(a)
    if decisions:
        # Existing timing/technical results are stale after any source-window change.
        for k in ('director_plan', 'boundary_suggestions', 'post_render_review'):
            out.pop(k, None)
        for c in out['clips']:
            c.pop('boundary_suggestion', None)
    return out, decisions


def make_ab(project, continuity):
    alternatives = shot_alternatives(project, continuity)
    selected, seen = [], set()
    for a in alternatives:
        if a['clip_id'] not in seen:
            selected.append(a['alternative_id']); seen.add(a['clip_id'])
    b, decisions = apply_alternatives(project, alternatives, selected)
    a = version(project, 'A — original', parent=project)
    b = version(b, 'B — reviewed continuity proposal', parent=a, decisions=decisions)
    return {'a': a, 'b': b, 'alternatives': alternatives, 'diff': diff_projects(a, b)}


def merge_versions(a, b, selected_clip_ids):
    """Keep A order; choose B ranges per stable clip ID. No hidden insertions."""
    left, right = clip_index(a), clip_index(b)
    if set(left) != set(right) or any(i not in left for i in selected_clip_ids):
        raise ValueError('合并要求相同片段集合，选择的 clip_id 必须存在')
    out = deepcopy(a)
    out['clips'] = [deepcopy(right[c['clip_id']] if c['clip_id'] in selected_clip_ids else c) for c in a['clips']]
    return version(out, 'Merged', parent=a, decisions=[{'from_version_id': b.get('edit_version', {}).get('version_id'), 'selected_clip_ids': selected_clip_ids}])


def rework(project, feedback, continuity):
    clips = clip_index(project)
    if feedback.get('project_fingerprint') != fingerprint(project):
        raise ValueError('返工反馈已过期：project_fingerprint 不匹配')
    alternatives = shot_alternatives(project, continuity)
    selected, unresolved, issues = [], [], feedback.get('issues', [])
    if not isinstance(issues, list) or not issues:
        raise ValueError('需要返工 issues')
    for i, issue in enumerate(issues):
        cid = issue.get('clip_id')
        if cid not in clips or not str(issue.get('message', '')).strip():
            raise ValueError('返工问题需要有效 clip_id 和问题说明')
        options = [a for a in alternatives if a['clip_id'] == cid and (not issue.get('shot_id') or issue['shot_id'] in a['source_shot_ids'])]
        if issue.get('shot_id') and issue['shot_id'] not in set(clips[cid].get('shot_ids', [])) | {sid for a in options for sid in a['source_shot_ids']}:
            raise ValueError('问题镜头无法定位到该片段')
        aid = issue.get('alternative_id')
        if aid and aid not in [a['alternative_id'] for a in options]:
            raise ValueError('替代镜头不属于问题片段/镜头，或其审核已失效')
        if aid:
            selected.append(aid)
        elif not str(issue.get('resolution', '')).strip():
            unresolved.append({'issue_index': i, 'clip_id': cid, 'message': issue['message'], 'alternatives': options})
    out, decisions = apply_alternatives(project, alternatives, selected)
    decisions.append({'feedback': deepcopy(feedback)})
    return version(out, 'Rework', parent=project, decisions=decisions, unresolved=unresolved)


def approve(project, reviewer, reason):
    if not reviewer.strip() or not reason.strip():
        raise ValueError('需要审核者和理由')
    clip_index(project)
    out = deepcopy(project)
    meta = out.get('edit_version')
    if not meta or meta.get('unresolved_issues'):
        raise ValueError('缺少版本记录或存在未解决问题')
    qc = continuity_qc(out)
    if not any(c.get('enabled', True) for c in out['clips']):
        raise ValueError('没有启用的剪辑片段')
    if not qc['release_ready']:
        raise ValueError('剪辑 QC 有阻塞问题')
    meta['review_history'].append({'status': 'PASS', 'reviewer': reviewer.strip(), 'reason': reason.strip(), 'reviewed_at': now(), 'project_fingerprint': fingerprint(out)})
    meta['review_status'] = 'PASS'
    out['version_qc'] = qc
    return out


def require_approved(project):
    meta = project.get('edit_version')
    if meta is None:
        return  # Existing v4.1 projects keep their behavior.
    history = meta.get('review_history') or []
    if meta.get('review_status') != 'PASS' or not history or meta.get('unresolved_issues'):
        raise ValueError('剪辑版本尚未审核通过或存在未解决问题')
    last = history[-1]
    if last.get('status') != 'PASS' or not last.get('reviewer') or not last.get('reason') or last.get('project_fingerprint') != fingerprint(project):
        raise ValueError('剪辑版本内容已变化或审核记录不完整，请重新审核')


def write_new(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def main():
    p = argparse.ArgumentParser(description='A/B 剪辑版本、差异、选择合并与返工；输出禁止覆盖')
    sub = p.add_subparsers(dest='cmd', required=True)
    q = sub.add_parser('ab'); q.add_argument('project'); q.add_argument('continuity'); q.add_argument('output_dir')
    q = sub.add_parser('diff'); q.add_argument('a'); q.add_argument('b'); q.add_argument('output')
    q = sub.add_parser('merge'); q.add_argument('a'); q.add_argument('b'); q.add_argument('output'); q.add_argument('--choose-b', nargs='*', default=[]); q.add_argument('--selection', help='版本比较页面导出的选择 JSON')
    q = sub.add_parser('rework'); q.add_argument('project'); q.add_argument('feedback'); q.add_argument('continuity'); q.add_argument('output')
    q = sub.add_parser('approve'); q.add_argument('project'); q.add_argument('output'); q.add_argument('--reviewer', required=True); q.add_argument('--reason', required=True)
    q = sub.add_parser('fingerprint'); q.add_argument('project')
    args = p.parse_args()
    try:
        if args.cmd == 'ab':
            result = make_ab(read_json(args.project), read_json(args.continuity))
            directory = Path(args.output_dir); directory.mkdir(parents=True, exist_ok=False)
            for k, v in result.items(): write_new(directory / (k + '.json'), v)
        elif args.cmd == 'diff': write_new(args.output, diff_projects(read_json(args.a), read_json(args.b)))
        elif args.cmd == 'merge':
            a, b = read_json(args.a), read_json(args.b)
            selected = args.choose_b
            if args.selection:
                choice = read_json(args.selection)
                if choice.get('a_version_id') != a.get('edit_version', {}).get('version_id') or choice.get('b_version_id') != b.get('edit_version', {}).get('version_id'):
                    raise ValueError('选择文件与版本 ID 不匹配')
                selected = choice['choose_b_clip_ids']
            write_new(args.output, merge_versions(a, b, selected))
        elif args.cmd == 'rework': write_new(args.output, rework(read_json(args.project), read_json(args.feedback), read_json(args.continuity)))
        elif args.cmd == 'approve': write_new(args.output, approve(read_json(args.project), args.reviewer, args.reason))
        else: print(fingerprint(read_json(args.project)))
    except (ValueError, OSError) as e: raise SystemExit(str(e))

if __name__ == '__main__': main()
