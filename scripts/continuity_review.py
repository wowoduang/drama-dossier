"""Shared v4.2 continuity review contract; legacy documents require re-review.

Snapshots detect accidental changes, not malicious tampering or reviewer identity.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math

STATUSES = {'UNREVIEWED', 'PASS', 'INCORRECT', 'NEED_EVIDENCE', 'DISPUTED'}
AUDIT_FIELDS = {'review_status', 'review_history', 'review_reason', 'reviewer', 'reviewed_at'}


def now():
    return datetime.now(timezone.utc).isoformat()


def valid_range(item):
    a, b = item.get('start_sec'), item.get('end_sec')
    return all(type(x) in (int, float) and math.isfinite(x) for x in (a, b)) and 0 <= a < b


def snapshot(payload, candidate):
    ids = candidate.get('shot_ids', [])
    return {'episode_id': payload.get('episode_id'),
            'candidate': {k: deepcopy(v) for k, v in candidate.items() if k not in AUDIT_FIELDS},
            'shots': [deepcopy(s) for s in payload.get('shots', []) if s.get('shot_id') in ids]}


def normalize(payload):
    """Non-destructive upgrade: retain old audit entries without inventing actors."""
    out = deepcopy(payload)
    if not isinstance(out.get('candidates'), list):
        raise ValueError('candidates 必须是数组')
    ids = [c.get('candidate_id') for c in out['candidates']]
    if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('candidate_id 必须唯一且非空')
    shots = out.setdefault('shots', [])
    if not isinstance(shots, list):
        raise ValueError('shots 必须是数组')
    sids = [s.get('shot_id') for s in shots]
    if any(not x or not valid_range(s) for x, s in zip(sids, shots)) or len(set(sids)) != len(sids):
        raise ValueError('镜头 ID/时间范围无效或重复')
    for c in out['candidates']:
        if c.setdefault('review_status', 'UNREVIEWED') not in STATUSES:
            raise ValueError('无效审核状态')
        if not isinstance(c.setdefault('review_history', []), list):
            raise ValueError('review_history 必须是数组')
    out['version'] = '4.2.0'
    out['candidate_count'] = len(ids)
    out['all_candidates_unreviewed'] = all(c['review_status'] == 'UNREVIEWED' for c in out['candidates'])
    return out


def review(payload, candidate_id, status, reason, reviewer, continuous_source_checked=False):
    if status not in STATUSES or not reason.strip() or not reviewer.strip():
        raise ValueError('需要有效状态、审核理由和审核者')
    out = normalize(payload)
    c = next((c for c in out['candidates'] if c['candidate_id'] == candidate_id), None)
    if c is None:
        raise ValueError('候选不存在')
    if status == 'PASS' and not continuous_source_checked:
        raise ValueError('PASS 必须回看连续原片并确认全部风险')
    entry = {'previous_status': c['review_status'], 'status': status,
             'reason': reason.strip(), 'reviewer': reviewer.strip(), 'reviewed_at': now(),
             'continuous_source_checked': bool(continuous_source_checked), 'snapshot': snapshot(out, c)}
    c['review_history'].append(entry)
    c.update(review_status=status, review_reason=entry['reason'], reviewer=entry['reviewer'], reviewed_at=entry['reviewed_at'])
    out['all_candidates_unreviewed'] = all(c['review_status'] == 'UNREVIEWED' for c in out['candidates'])
    return out


def review_errors(payload, c):
    errors = []
    status = c.get('review_status', 'UNREVIEWED')
    if status not in STATUSES:
        return ['INVALID_REVIEW_STATUS']
    hist = c.get('review_history', [])
    if status == 'UNREVIEWED' and not hist:
        return []
    if not isinstance(hist, list) or not hist or not isinstance(hist[-1], dict):
        return ['REVIEW_AUDIT_MISSING']
    last = hist[-1]
    if last.get('status') != status or not all(isinstance(last.get(k), str) and last[k].strip() for k in ('reason', 'reviewer', 'reviewed_at')):
        errors.append('REVIEW_AUDIT_INCOMPLETE')
    try:
        dt = datetime.fromisoformat(last.get('reviewed_at', '').replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError()
    except (TypeError, ValueError):
        errors.append('REVIEW_TIME_INVALID')
    if last.get('snapshot') != snapshot(payload, c):
        errors.append('REVIEW_CONTENT_CHANGED')
    if status == 'PASS' and not last.get('continuous_source_checked'):
        errors.append('CONTINUOUS_SOURCE_NOT_CHECKED')
    return errors


def approved_candidates(payload):
    """Fail closed on legacy, stale, ambiguous or unlinked observations."""
    p = normalize(payload)
    by_shot = {s['shot_id']: s for s in p['shots']}
    out = []
    for c in p['candidates']:
        if c['review_status'] != 'PASS' or review_errors(p, c):
            continue
        ids = c.get('shot_ids', [])
        if len(ids) < 2 or len(set(ids)) != len(ids) or any(x not in by_shot for x in ids):
            continue
        shots = [by_shot[x] for x in ids]
        if not c.get('event_ids') or not c.get('evidence_ids') or not valid_range(c):
            continue
        if any(s.get('review_status') != 'PASS' or not s.get('event_ids') or not s.get('evidence_ids') for s in shots):
            continue
        if any(not set(s['event_ids']).issubset(c['event_ids']) or not set(s['evidence_ids']).issubset(c['evidence_ids']) for s in shots):
            continue
        if any(s['start_sec'] < c['start_sec'] or s['end_sec'] > c['end_sec'] for s in shots):
            continue
        if c.get('candidate_type') == 'REACTION_SHOT':
            a, b = shots[:2]
            if not a.get('speaker_character_id') or b.get('reaction_target_character_id') != a['speaker_character_id']:
                continue
        out.append(c)
    return out
