#!/usr/bin/env python3
"""Reviewable character trajectories and conservative, as-of state story cuts.

SQLite's existing episode_payloads remains a rebuildable projection; no new DB
schema or replacement of knowledge_states is necessary.
"""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path

from character_state_model import COLLECTIONS, DIMENSIONS, number, usable, index, state_graph_issues
from dossier_v3 import load, migrate_to_v35, validate, add_review
from edit_versions import version, write_new
from series_memory import connect
from series_search import resolve_cutoff
from story_arc_compiler import _visible_by
from series_compiler import load_sources

VERSION = '4.3.0'


def import_candidates(dossier, payload):
    out = migrate_to_v35(dossier)
    if not isinstance(payload, dict) or payload.get('episode_id') != out['episode'].get('episode_id'):
        raise ValueError('候选 episode_id 必须与档案一致')
    for collection in COLLECTIONS:
        rows = payload.get(collection, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f'{collection} 必须是对象数组')
        out.setdefault(collection, []).extend({**deepcopy(row), 'review_status': 'UNREVIEWED'} for row in rows)
    errors = validate(out)
    if errors: raise ValueError('; '.join(errors))
    return out


def reviewed_dossier(dossier, object_id, status, reason, reviewer, source_checked=False):
    out = deepcopy(dossier)
    errors = validate(out)
    if errors: raise ValueError('; '.join(errors))
    if not any(row.get(field) == object_id for col, field in COLLECTIONS.items() for row in out.get(col, [])):
        raise ValueError('对象不是人物状态或状态变化')
    add_review(out, object_id, status, reason, reviewer=reviewer, source_checked=source_checked)
    errors = validate(out)
    if errors: raise ValueError('; '.join(errors))
    return out


def trajectory(connection, series_id, character_id, as_of, at_sec=None, dimension=None):
    if dimension is not None and dimension not in DIMENSIONS: raise ValueError('状态维度无效')
    cutoff, cutoff_ep = resolve_cutoff(connection, series_id, as_of)
    if at_sec is not None:
        if not number(at_sec): raise ValueError('at_sec 必须是有限非负数')
        duration = connection.execute('SELECT duration_sec FROM episodes WHERE series_id=? AND episode_id=?', (series_id, cutoff_ep)).fetchone()[0]
        if duration > 0 and at_sec > duration: raise ValueError('at_sec 超出截止集时长')
        if connection.execute('SELECT COUNT(*) FROM episodes WHERE series_id=? AND episode_number=(SELECT episode_number FROM episodes WHERE series_id=? AND episode_id=?)', (series_id, series_id, cutoff_ep)).fetchone()[0] != 1:
            raise ValueError('截止集序号存在歧义，无法应用集内时间边界')
    if not _visible_by(connection, series_id, 'character', character_id, cutoff):
        raise ValueError('人物跨集身份需要截止集之前已审核的 series_visibility 声明')
    visibility = connection.execute("SELECT evidence_refs_json FROM series_visibility WHERE series_id=? AND entity_type='character' AND entity_id=?", (series_id, character_id)).fetchone()
    for ref in json.loads(visibility['evidence_refs_json']):
        ep, eid = ref.split(':', 1)
        r = connection.execute('SELECT p.payload_json,e.episode_number FROM episode_payloads p JOIN episodes e USING(series_id,episode_id) WHERE p.series_id=? AND p.episode_id=?', (series_id, ep)).fetchone()
        evidence = index(json.loads(r['payload_json']), 'evidence', 'evidence_id').get(eid) if r else None
        if not evidence or evidence.get('review_status') != 'PASS' or r['episode_number'] > cutoff or (ep == cutoff_ep and at_sec is not None and evidence['end_sec'] > at_sec):
            raise ValueError('身份可见性证据已失效或晚于当前截止时点')
    rows = connection.execute('SELECT p.episode_id,p.payload_json,e.episode_number,m.local_character_id FROM episode_payloads p JOIN episodes e USING(series_id,episode_id) JOIN character_mentions m USING(series_id,episode_id) WHERE p.series_id=? AND e.episode_number<=? AND m.global_character_id=? AND m.review_status=\'PASS\' ORDER BY e.episode_number,p.episode_id,m.local_character_id', (series_id, cutoff, character_id)).fetchall()
    timeline, changes, conflicts, payloads = [], [], [], {}
    for row in rows:
        ep, local = row['episode_id'], row['local_character_id']
        data = json.loads(row['payload_json'])
        errors = validate(data)
        if errors: raise ValueError(f'{ep} 状态档案校验失败: ' + '; '.join(errors))
        payloads[ep] = data
        graph_issues = state_graph_issues(data, character_id=local, at_sec=at_sec if ep == cutoff_ep else None)
        for issue in graph_issues:
            conflicts.append({'episode_id': ep, **issue})
        bound = at_sec if at_sec is not None and ep == cutoff_ep else float('inf')
        states = [s for s in data.get('character_states', []) if s['character_id'] == local and (dimension is None or s['dimension'] == dimension) and s['playback_time'] <= bound and s['audience_available_sec'] <= bound and usable(data, s)]
        def qualified(obj):
            result = {**deepcopy(obj), 'episode_id': ep, 'episode_number': row['episode_number'], 'character_name': index(data, 'characters', 'character_id')[local].get('canonical_name', local)}
            target_local = obj.get('target_character_id')
            result['local_target_character_id'] = target_local
            result['global_target_character_id'] = None
            result['target_identity_status'] = 'NOT_APPLICABLE' if target_local is None else 'UNRESOLVED'
            if target_local is not None:
                mappings = connection.execute("SELECT global_character_id, review_status FROM character_mentions WHERE series_id=? AND episode_id=? AND local_character_id=?", (series_id, ep, target_local)).fetchall()
                approved = {m['global_character_id'] for m in mappings if m['review_status'] == 'PASS'}
                if len(approved) == 1:
                    result['global_target_character_id'] = next(iter(approved))
                    result['target_identity_status'] = 'PASS'
                elif len(approved) > 1:
                    result['target_identity_status'] = 'AMBIGUOUS'
            return result
        timeline.extend(qualified(s) for s in states)
        ids = {s['state_id'] for s in states}
        for change in data.get('character_state_changes', []):
            if change['character_id'] == local and {change['from_state_id'], change['to_state_id']}.issubset(ids) and change['playback_time'] <= bound and change['audience_available_sec'] <= bound and usable(data, change, 'character_state_changes'):
                changes.append(qualified(change))
        grouped = defaultdict(list)
        for state in states: grouped[(state['dimension'], state['aspect'], state.get('target_character_id'), state['playback_time'])].append(state)
        for group in grouped.values():
            if len({s['value'] for s in group}) > 1: conflicts.append({'episode_id': ep, 'state_ids': [s['state_id'] for s in group], 'code': 'CONFLICTING_STATE_AT_SAME_TIME'})
    timeline.sort(key=lambda s: (s['episode_number'], s['playback_time'], s['state_id']))
    changes.sort(key=lambda s: (s['episode_number'], s['playback_time'], s['change_id']))
    latest = defaultdict(list)
    for state in timeline:
        if state['episode_id'] != cutoff_ep: continue
        key = (state['character_id'], state['dimension'], state['aspect'], state.get('target_character_id'))
        if not latest[key] or state['playback_time'] > latest[key][0]['playback_time']: latest[key] = [state]
        elif state['playback_time'] == latest[key][0]['playback_time']: latest[key].append(state)
    end_point = at_sec if at_sec is not None else payloads.get(cutoff_ep, {}).get('episode', {}).get('duration_sec', float('inf'))
    latest_records = [group for group in latest.values() if any(s.get('valid_until_sec') is None or s['valid_until_sec'] > end_point for s in group)]
    result = {'version': VERSION, 'series_id': series_id, 'character_id': character_id,
        'spoiler_guard': {'enabled': True, 'max_episode_number': cutoff, 'cutoff_episode_id': cutoff_ep, 'at_sec': at_sec},
        'states': timeline, 'changes': changes, 'latest_in_cutoff_episode': latest_records, 'conflicts': conflicts,
        'limitations': ['状态是有证据的时点记录，不自动跨集延续', 'INTERPRETATION 和缺失/过期审核不进入自动素材选择', '变化的事件关联表示上下文，不自动证明因果', '心理和动机仍需结合连续原片人工核实']}
    return result, payloads


def compile_state_cut(db, sid, gid, as_of, sources_path, target_sec=180, at_sec=None, dimension=None):
    if not number(target_sec) or target_sec <= 0: raise ValueError('目标时长必须为正有限数')
    connection = connect(db)
    try: context, payloads = trajectory(connection, sid, gid, as_of, at_sec, dimension)
    finally: connection.close()
    sources = load_sources(sources_path)
    states = {(s['episode_id'], s['state_id']): s for s in context['states']}
    blocked = {(x['episode_id'], sid_) for x in context['conflicts'] for sid_ in x['state_ids']}
    units, covered, excluded = [], set(), []
    for change in context['changes']:
        keys = [(change['episode_id'], change[k]) for k in ('from_state_id', 'to_state_id')]
        covered.update(keys)
        if any(k in blocked for k in keys):
            excluded.append({'change_id': change['change_id'], 'reason': '前后状态存在同一时点冲突'}); continue
        endpoints = [states[k] for k in keys]
        if any(not s.get('event_ids') for s in endpoints):
            excluded.append({'change_id': change['change_id'], 'reason': '状态缺少事件锚点，不能丢弃铺垫后自动剪辑'}); continue
        units.append({'records': endpoints + [change], 'change_ids': [change['change_id']], 'priority': change.get('editorial_priority', 50)})
    for key, state in states.items():
        if key not in covered and key not in blocked and state.get('event_ids'):
            units.append({'records': [state], 'change_ids': [], 'priority': state.get('editorial_priority', 50)})
    def materialize(unit):
        records = unit['records']; ep = records[0]['episode_id']; data = payloads[ep]
        if ep not in sources: raise ValueError(f'{ep} 缺少原片路径，不能截去变化的一端')
        evidence = index(data, 'evidence', 'evidence_id'); events = index(data, 'events', 'event_id'); knowledge = index(data, 'knowledge_states', 'knowledge_id')
        out = {}
        for event_id in sorted({eid for r in records for eid in r.get('event_ids', [])}):
            event = events[event_id]
            relevant = [r for r in records if event_id in r.get('event_ids', [])]
            refs = list(dict.fromkeys(event.get('evidence_ids', []) + [eid for r in relevant for eid in r['evidence_ids']] + [eid for r in relevant for kid in r.get('knowledge_ids', []) for eid in knowledge[kid].get('evidence_ids', [])]))
            if not refs or any(evidence[eid].get('review_status') != 'PASS' for eid in refs): raise ValueError('关联事件证据未全部核实')
            start = min([event['playback_time']] + [evidence[eid]['start_sec'] for eid in refs])
            end = max([event['playback_time']] + [evidence[eid]['end_sec'] for eid in refs])
            bound = at_sec if at_sec is not None and ep == context['spoiler_guard']['cutoff_episode_id'] else data['episode']['duration_sec'] or end+1
            start, end = max(0, start-1), min(bound, end+1)
            if end <= start or event['modality'] != 'actual' or event['review_status'] != 'PASS': raise ValueError('关联事件不可剪辑')
            out[(ep, event_id)] = {'episode_id': ep, 'source_video': sources[ep], 'start_sec': start, 'end_sec': end, 'event_id': event_id, 'event_ids': [event_id], 'evidence_ids': refs,
                'state_evidence_ranges': [{'evidence_id': eid, 'start_sec': evidence[eid]['start_sec'], 'end_sec': evidence[eid]['end_sec']} for eid in refs],
                'title': event.get('action', event_id), 'character_state_ids': [r['state_id'] for r in relevant if 'state_id' in r], 'character_state_change_ids': unit['change_ids'],
                'reason': '已审核人物状态/变化的证据片段；保留变化两端，关联事件不等于因果', 'review_status': 'UNREVIEWED', 'enabled': True}
        return out
    selected, skipped = {}, []
    def combine(left, right):
        result = deepcopy(left)
        for key, clip in right.items():
            if key not in result: result[key] = deepcopy(clip); continue
            old = result[key]; old['start_sec'] = min(old['start_sec'], clip['start_sec']); old['end_sec'] = max(old['end_sec'], clip['end_sec'])
            for field in ('evidence_ids', 'character_state_ids', 'character_state_change_ids'): old[field] = list(dict.fromkeys(old[field]+clip[field]))
            ranges = {r['evidence_id']: r for r in old['state_evidence_ranges'] + clip['state_evidence_ranges']}
            old['state_evidence_ranges'] = list(ranges.values())
        return result
    for unit in sorted(units, key=lambda u: -u['priority']):
        material = materialize(unit); proposed = combine(selected, material)
        if sum(c['end_sec']-c['start_sec'] for c in proposed.values()) <= target_sec: selected = proposed
        else: skipped.append({'state_ids': [r['state_id'] for r in unit['records'] if 'state_id' in r], 'change_ids': unit['change_ids'], 'reason': '整组超出时长；不拆掉铺垫或结果'})
    if not selected: raise ValueError('没有完整且可在目标时长内使用的已审核状态素材；请补证或增加时长')
    order = {s['episode_id']: s['episode_number'] for s in context['states']}
    clips = sorted(selected.values(), key=lambda c: (order[c['episode_id']], c['start_sec'], c['event_id']))
    for i, clip in enumerate(clips, 1): clip['clip_id'] = f'STATE_CLP_{i:03d}'
    used_states = {(c['episode_id'], s) for c in clips for s in c['character_state_ids']}
    used_changes = {(c['episode_id'], s) for c in clips for s in c['character_state_change_ids']}
    # Export selected, cutoff-safe records only; do not carry unused future/late-state metadata.
    selected_context = {k: deepcopy(v) for k, v in context.items() if k not in {'states', 'changes', 'latest_in_cutoff_episode'}}
    selected_context['states'] = [s for s in context['states'] if (s['episode_id'], s['state_id']) in used_states]
    selected_context['changes'] = [s for s in context['changes'] if (s['episode_id'], s['change_id']) in used_changes]
    project = {'project_type': 'cross_episode_compilation', 'project_version': '2.0', 'compiler_version': VERSION,
        'series_id': sid, 'selector': {'type': 'character_state', 'id': gid, 'dimension': dimension}, 'title': '人物状态与变化素材候选',
        'sources': {ep: sources[ep] for ep in {c['episode_id'] for c in clips}}, 'clips': clips, 'target_duration_sec': target_sec,
        'estimated_duration_sec': sum(c['end_sec']-c['start_sec'] for c in clips), 'spoiler_guard': context['spoiler_guard'],
        'character_state_context': selected_context, 'compile_report': {'excluded': excluded, 'skipped_groups': skipped, 'review_status': 'UNREVIEWED', 'warnings': ['素材候选不保证完整成长弧线或最佳审美；需人工补齐连接和反应', '优先级来自审核记录，不由音量或表情自动推断']}}
    return version(project, 'Character-state story candidate')


def main():
    p = argparse.ArgumentParser(description='人物状态、审核、截止时点轨迹与状态变化剪辑')
    sub = p.add_subparsers(dest='cmd', required=True)
    q = sub.add_parser('import'); q.add_argument('dossier'); q.add_argument('candidates'); q.add_argument('output')
    q = sub.add_parser('review'); q.add_argument('dossier'); q.add_argument('object_id'); q.add_argument('output'); q.add_argument('--status', required=True, choices=['PASS','INCORRECT','NEED_EVIDENCE','DISPUTED']); q.add_argument('--reason', required=True); q.add_argument('--reviewer', required=True); q.add_argument('--source-checked', action='store_true')
    for cmd in ('timeline', 'compile'):
        q = sub.add_parser(cmd); q.add_argument('db'); q.add_argument('output'); q.add_argument('--series-id', required=True); q.add_argument('--character', required=True); q.add_argument('--as-of', required=True); q.add_argument('--at-sec', type=float); q.add_argument('--dimension', choices=sorted(DIMENSIONS))
        if cmd == 'compile': q.add_argument('--sources', required=True); q.add_argument('--target-sec', type=float, default=180)
    args = p.parse_args()
    try:
        if args.cmd == 'import': result = import_candidates(load(args.dossier), load(args.candidates))
        elif args.cmd == 'review': result = reviewed_dossier(load(args.dossier), args.object_id, args.status, args.reason, args.reviewer, args.source_checked)
        elif args.cmd == 'compile': result = compile_state_cut(args.db, args.series_id, args.character, args.as_of, args.sources, args.target_sec, args.at_sec, args.dimension)
        else:
            c = connect(args.db)
            try: result, _ = trajectory(c, args.series_id, args.character, args.as_of, args.at_sec, args.dimension)
            finally: c.close()
        write_new(args.output, result)
        print(json.dumps({'output': args.output, 'version': VERSION}, ensure_ascii=False))
    except (ValueError, OSError) as exc: raise SystemExit(str(exc))

if __name__ == '__main__': main()
