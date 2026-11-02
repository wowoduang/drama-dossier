"""Optional dossier character-state extension; no inferred minds or implicit causes."""
from copy import deepcopy
from datetime import datetime
import math

COLLECTIONS = {'character_states': 'state_id', 'character_state_changes': 'change_id'}
DIMENSIONS = {'goal', 'knowledge', 'situation', 'relationship', 'emotion', 'action_result'}
BASES = {'OBSERVATION', 'EXPLICIT_STATEMENT', 'INTERPRETATION'}
DIRECT = {'F1_DIRECT', 'F2_CORROBORATED'}
INFERRED = {'I1_STRONG', 'I2_PLAUSIBLE', 'I3_SPECULATIVE', 'X_INTERPRETATION'}
STATUSES = {'UNREVIEWED', 'PASS', 'INCORRECT', 'NEED_EVIDENCE', 'DISPUTED'}


def number(x):
    return type(x) in (int, float) and math.isfinite(x) and x >= 0


def index(data, collection, field):
    return {x.get(field): x for x in data.get(collection, []) if isinstance(x, dict)}


def validation_errors(data):
    """Validate meaning boundaries and references, never assert a psychological truth."""
    errors = []
    chars = index(data, 'characters', 'character_id')
    events = index(data, 'events', 'event_id')
    evidence = index(data, 'evidence', 'evidence_id')
    knowledge = index(data, 'knowledge_states', 'knowledge_id')
    states = index(data, 'character_states', 'state_id')
    duration = data.get('episode', {}).get('duration_sec', 0)
    for collection, id_field in COLLECTIONS.items():
        rows = data.get(collection, [])
        if not isinstance(rows, list):
            errors.append(f'{collection} 必须为数组'); continue
        for row in rows:
            if not isinstance(row, dict):
                errors.append(f'{collection} 记录必须为对象'); continue
            label = str(row.get(id_field, collection))
            def error(message): errors.append(f'{label}: {message}')
            if row.get('character_id') not in chars: error('character_id 人物不存在')
            if row.get('review_status', 'UNREVIEWED') not in STATUSES: error('审核状态无效')
            t, available = row.get('playback_time'), row.get('audience_available_sec')
            if not number(t) or not number(available) or available < t:
                error('playback_time/audience_available_sec 无效或可见时间早于状态时间')
            elif number(duration) and duration > 0 and available > duration:
                error('可见时间超出本集时长')
            priority = row.get('editorial_priority', 50)
            if type(priority) is not int or not 0 <= priority <= 100: error('editorial_priority 必须为 0-100 整数')
            refs = {}
            for field, table in [('event_ids', events), ('evidence_ids', evidence), ('knowledge_ids', knowledge)]:
                values = row.get(field, [])
                if not isinstance(values, list) or any(not isinstance(x, str) or x not in table for x in values):
                    error(f'{field} 必须是有效 ID 数组'); refs[field] = []; continue
                if len(set(values)) != len(values): error(f'{field} 存在重复 ID')
                refs[field] = values
            if not refs.get('evidence_ids'): error('需要 evidence_ids，不能仅凭状态描述')
            for eid in refs.get('evidence_ids', []):
                end = evidence[eid].get('end_sec')
                if number(available) and number(end) and end > available: error('证据晚于观众可见时间')
            for eid in refs.get('event_ids', []):
                ev = events[eid]
                if row.get('character_id') not in [ev.get(k) for k in ('actor', 'patient', 'recipient', 'target')]:
                    error('关联事件未记录该人物参与；被提到不等于参与')
                if number(available) and number(ev.get('playback_time')) and ev['playback_time'] > available: error('关联事件晚于可见时间')
                for evid in ev.get('evidence_ids', []):
                    end = evidence.get(evid, {}).get('end_sec')
                    if number(available) and number(end) and end > available: error('事件证据晚于可见时间')
            if collection == 'character_states':
                if row.get('dimension') not in DIMENSIONS: error('状态维度无效')
                for field in ('aspect', 'value', 'description'):
                    if not isinstance(row.get(field), str) or not row[field].strip(): error(f'{field} 不能为空')
                target = row.get('target_character_id')
                if target is not None and (target not in chars or target == row.get('character_id')): error('关系对象无效')
                if row.get('dimension') == 'relationship' and target is None: error('关系状态需要 target_character_id')
                if row.get('basis') not in BASES: error('basis 无效')
                certainty = row.get('certainty')
                if row.get('basis') == 'INTERPRETATION':
                    if certainty not in INFERRED: error('心理解释不得标为直接事实')
                elif certainty not in DIRECT: error('观察/明确陈述需要直接或交叉核实等级')
                if row.get('basis') == 'EXPLICIT_STATEMENT':
                    quote = row.get('quote')
                    if not isinstance(quote, str) or not quote.strip() or not any(quote in str(evidence[x].get('raw', '')) for x in refs.get('evidence_ids', [])):
                        error('明确陈述必须引用原始证据中的原句 quote')
                end = row.get('valid_until_sec')
                if end is not None and (not number(end) or not number(t) or end <= t or (number(duration) and duration > 0 and end > duration)):
                    error('valid_until_sec 必须晚于状态时间且在本集内')
                if row.get('dimension') == 'knowledge' and not refs.get('knowledge_ids'): error('认知状态应引用已有 knowledge_ids')
                for kid in refs.get('knowledge_ids', []):
                    k = knowledge[kid]
                    if k.get('subject_type') != 'CHARACTER' or k.get('character_id') != row.get('character_id'): error('认知主体不匹配')
                    if number(t) and number(k.get('playback_time')) and k['playback_time'] > t: error('不得引用未来认知')
                    for evid in k.get('evidence_ids', []):
                        if number(available) and number(evidence.get(evid, {}).get('end_sec')) and evidence[evid]['end_sec'] > available: error('认知证据晚于可见时间')
            else:
                left, right = states.get(row.get('from_state_id')), states.get(row.get('to_state_id'))
                if not left or not right or row.get('from_state_id') == row.get('to_state_id'):
                    error('变化需要两个不同且存在的状态'); continue
                keys = ('character_id', 'dimension', 'aspect', 'target_character_id')
                if any(left.get(k) != right.get(k) for k in keys) or row.get('character_id') != left.get('character_id'): error('状态变化的人物、维度、主题和关系对象必须一致')
                if not number(left.get('playback_time')) or not number(right.get('playback_time')) or left['playback_time'] >= right['playback_time'] or t != right['playback_time']: error('状态变化必须按时间前进，变化时间等于后状态时间')
                if left.get('value') == right.get('value'): error('前后状态值相同，不构成状态变化')
                if row.get('link_type', 'CONTEXT') != 'CONTEXT': error('状态关联事件只表达 CONTEXT，不凭时间先后断言因果')
                if not refs.get('event_ids'): error('变化需要关联事件 event_ids')
                # A context event must belong to the transition interval; mere participation elsewhere in the episode is insufficient.
                if number(left.get('playback_time')) and number(right.get('playback_time')):
                    for event_id in refs.get('event_ids', []):
                        event_time = events[event_id].get('playback_time')
                        if not number(event_time) or event_time < left['playback_time'] or event_time > right['playback_time']:
                            error(f'关联事件 {event_id} 必须位于前后状态时点之间')
                if not isinstance(row.get('reason'), str) or not row['reason'].strip(): error('变化需要 reason')
                if number(available) and any(number(s.get('audience_available_sec')) and s['audience_available_sec'] > available for s in (left, right)): error('变化不能早于前后状态的可见时间')
    return errors



def state_graph_issues(data):
    """Return conservative lifecycle/transition diagnostics; do not infer persistence."""
    states = [s for s in data.get('character_states', []) if isinstance(s, dict)]
    changes = [c for c in data.get('character_state_changes', []) if isinstance(c, dict)]
    issues = []
    groups = {}
    for state in states:
        key = (state.get('character_id'), state.get('dimension'), state.get('aspect'), state.get('target_character_id'))
        groups.setdefault(key, []).append(state)
    for key, rows in groups.items():
        rows.sort(key=lambda x: (x.get('playback_time', -1), x.get('state_id', '')))
        for i, left in enumerate(rows):
            left_end = left.get('valid_until_sec')
            for right in rows[i+1:]:
                if left_end is None or not number(left_end):
                    continue
                if right.get('playback_time', float('inf')) < left_end and left.get('value') != right.get('value'):
                    issues.append({'code':'OVERLAPPING_STATE_LIFECYCLES','state_ids':[left.get('state_id'),right.get('state_id')],'severity':'WARN','message':'不同状态的有效期重叠；需确认是否为并存、矛盾或失效时间设置错误'})
    outgoing, incoming = {}, {}
    for change in changes:
        a, b = change.get('from_state_id'), change.get('to_state_id')
        if not a or not b: continue
        outgoing.setdefault(a, []).append(change.get('change_id'))
        incoming.setdefault(b, []).append(change.get('change_id'))
    for state_id, ids in outgoing.items():
        if len(ids) > 1:
            issues.append({'code':'STATE_TRANSITION_FORK','state_id':state_id,'change_ids':ids,'severity':'WARN','message':'同一状态存在多个后继变化；需人工确认是否为分支叙事或重复建模'})
    for state_id, ids in incoming.items():
        if len(ids) > 1:
            issues.append({'code':'STATE_TRANSITION_MERGE','state_id':state_id,'change_ids':ids,'severity':'WARN','message':'多个变化汇入同一状态；需人工确认是否为重复或合流'})
    return issues

def context_snapshot(data, obj, collection):
    """Capture only dependencies, not the whole episode or subsequent revelations."""
    states = index(data, 'character_states', 'state_id')
    related = [states[x] for x in (obj.get('from_state_id'), obj.get('to_state_id')) if x in states] if collection == 'character_state_changes' else []
    objects = [obj] + related
    def collect(field): return {x for o in objects for x in o.get(field, [])}
    evids, eventids, kids = collect('evidence_ids'), collect('event_ids'), collect('knowledge_ids')
    events = [x for x in data.get('events', []) if x.get('event_id') in eventids]
    knowledge = [x for x in data.get('knowledge_states', []) if x.get('knowledge_id') in kids]
    for x in events + knowledge: evids.update(x.get('evidence_ids', []))
    charids = {o.get('character_id') for o in objects} | {o.get('target_character_id') for o in objects}
    return deepcopy({'episode': data.get('episode'), 'object': obj, 'states': related,
        'characters': [x for x in data.get('characters', []) if x.get('character_id') in charids],
        'events': events, 'knowledge_states': knowledge,
        'evidence': [x for x in data.get('evidence', []) if x.get('evidence_id') in evids]})


def audit_valid(data, obj, collection):
    field = COLLECTIONS[collection]
    records = [r for r in data.get('review_history', []) if r.get('object_id') == obj.get(field)]
    if not records: return False
    last = records[-1]
    if last.get('status') != 'PASS' or obj.get('review_status') != 'PASS' or last.get('source_checked') is not True: return False
    if not all(isinstance(last.get(k), str) and last[k].strip() for k in ('reason', 'reviewer', 'timestamp')): return False
    try:
        if datetime.fromisoformat(last['timestamp'].replace('Z', '+00:00')).tzinfo is None: return False
    except (ValueError, TypeError): return False
    return last.get('new_value') == obj and last.get('context_snapshot') == context_snapshot(data, obj, collection)


def usable(data, obj, collection='character_states'):
    if not audit_valid(data, obj, collection): return False
    if collection == 'character_state_changes':
        states = index(data, 'character_states', 'state_id')
        if any(x not in states or not usable(data, states[x]) for x in (obj.get('from_state_id'), obj.get('to_state_id'))): return False
    elif obj.get('basis') == 'INTERPRETATION' or obj.get('certainty') not in DIRECT:
        return False
    snap = context_snapshot(data, obj, collection)
    if any(e.get('review_status') != 'PASS' or e.get('type') == 'INF' for e in snap['evidence']): return False
    if any(e.get('review_status') != 'PASS' or e.get('modality') != 'actual' or e.get('polarity') != 'positive' for e in snap['events']): return False
    if any(x.get('review_status') != 'PASS' for x in snap['knowledge_states'] + snap['characters']): return False
    return True
