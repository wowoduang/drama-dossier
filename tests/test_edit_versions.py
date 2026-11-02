import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from continuity_review import normalize, review, approved_candidates, review_errors
from shot_continuity import candidates, qc
from director_engine import plan_director, shot_alternatives
from edit_versions import make_ab, diff_projects, merge_versions, rework, fingerprint, approve, require_approved, write_new
from review_cut import map_report, render_review
from render_series_project import active_clips
from render_project import enabled_clips


def fixture():
    d = {'episode': {'episode_id': 'EP1'}, 'events': [{'event_id': 'E1'}], 'evidence': [{'evidence_id': 'V1'}]}
    shots = [
        {'shot_id': 'S1', 'start_sec': 0, 'end_sec': 1, 'visible_character_ids': ['A'], 'speaker_character_id': 'A', 'gaze_direction': 'right', 'review_status': 'PASS', 'event_ids': ['E1'], 'evidence_ids': ['V1']},
        {'shot_id': 'S2', 'start_sec': 1, 'end_sec': 2, 'visible_character_ids': ['B'], 'action_phase': 'reaction', 'reaction_target_character_id': 'A', 'gaze_direction': 'left', 'review_status': 'PASS', 'event_ids': ['E1'], 'evidence_ids': ['V1']}]
    payload = candidates(d, {}, {'shots': shots})
    for c in list(payload['candidates']):
        payload = review(payload, c['candidate_id'], 'PASS', '回看连续原片，轴线及反应对象正确', 'tester', True)
    project = {'project_type': 'cross_episode_compilation', 'clips': [{'clip_id': 'C1', 'episode_id': 'EP1', 'source_video': 'episode.mp4', 'start_sec': 0, 'end_sec': 3, 'narrative_role': 'reaction', 'event_ids': ['E1'], 'evidence_ids': ['V1']}]}
    return project, payload


class ContinuityReviewTests(unittest.TestCase):
    def test_audit_transition_history_and_no_mutation(self):
        _, p = fixture(); original = copy.deepcopy(p); cid = p['candidates'][0]['candidate_id']
        for state in ['INCORRECT', 'NEED_EVIDENCE', 'DISPUTED', 'PASS', 'UNREVIEWED']:
            p = review(p, cid, state, '人工复核', 'editor', True)
            self.assertEqual(review_errors(p, p['candidates'][0]), [])
        h = p['candidates'][0]['review_history']
        self.assertEqual(len(h), 6); self.assertEqual(h[-1]['previous_status'], 'PASS')
        self.assertEqual(original['candidates'][0]['review_status'], 'PASS')

    def test_legacy_pass_is_preserved_but_cannot_be_consumed(self):
        _, p = fixture(); p.pop('shots')
        for c in p['candidates']: c['review_history'] = [{'status': 'PASS', 'reason': 'old', 'reviewed_at': '2026-01-01T00:00:00Z'}]
        upgraded = normalize(p)
        self.assertEqual(upgraded['candidates'][0]['review_history'], p['candidates'][0]['review_history'])
        self.assertEqual(approved_candidates(upgraded), [])
        self.assertFalse(qc(upgraded)['release_ready'])

    def test_missing_reason_actor_or_continuous_check_rejected(self):
        _, p = fixture(); cid = p['candidates'][0]['candidate_id']
        for args in [('PASS', '', 'editor', True), ('PASS', 'why', '', True), ('PASS', 'why', 'editor', False), ('FAKE', 'why', 'editor', True)]:
            with self.assertRaises(ValueError): review(p, cid, *args)

    def test_changed_candidate_or_shot_invalidates_review(self):
        _, p = fixture()
        for mutate in [lambda q: q['candidates'][0].update(reason='changed'), lambda q: q['shots'][0].update(gaze_direction='left'), lambda q: q.update(episode_id='EP2')]:
            q = copy.deepcopy(p); mutate(q)
            self.assertIn('REVIEW_CONTENT_CHANGED', review_errors(q, q['candidates'][0]))

    def test_unknown_ids_and_nonfinite_time_rejected(self):
        _, p = fixture()
        for val in [float('nan'), float('inf'), True, -1]:
            q = copy.deepcopy(p); q['shots'][0]['start_sec'] = val
            with self.assertRaises(ValueError): normalize(q)
        q = copy.deepcopy(p); q['candidates'].append(q['candidates'][0])
        with self.assertRaises(ValueError): normalize(q)

    def test_unreviewed_or_incorrect_not_consumed(self):
        project, p = fixture()
        for c in list(p['candidates']): p = review(p, c['candidate_id'], 'INCORRECT', 'not valid', 'tester')
        self.assertEqual(shot_alternatives(project, p), [])

    def test_missing_shot_evidence_and_reaction_target_blocked(self):
        project, p = fixture()
        p['shots'][1].pop('reaction_target_character_id')
        for c in list(p['candidates']): p = review(p, c['candidate_id'], 'PASS', 'checked', 'tester', True)
        alts = shot_alternatives(project, p)
        self.assertFalse(any(a['candidate_type'] == 'REACTION_SHOT' for a in alts))
        p['shots'][0]['evidence_ids'] = []
        for c in list(p['candidates']): p = review(p, c['candidate_id'], 'PASS', 'checked', 'tester', True)
        self.assertEqual(shot_alternatives(project, p), [])

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_browser_review_export_consumed_by_python(self):
        _, p = fixture()
        for c in p['candidates']: c['review_status'] = 'UNREVIEWED'; c['review_history'] = []
        js = "const api=require('./web/continuity-review.js');let p=JSON.parse(require('fs').readFileSync(0,'utf8'));for(const c of p.candidates)p=api.review(p,c.candidate_id,'PASS','连续原片已核实','browser-editor',true);process.stdout.write(JSON.stringify(p));"
        cp = subprocess.run(['node', '-e', js], input=json.dumps(p), cwd=ROOT, text=True, encoding='utf-8', errors='replace', capture_output=True, check=True)
        result = json.loads(cp.stdout)
        self.assertTrue(approved_candidates(result)); self.assertTrue(qc(result)['release_ready'])
        for c in result['candidates']: self.assertEqual(review_errors(result, c), [])


class VersionTests(unittest.TestCase):
    def test_director_is_scoped_to_episode_events_evidence_and_window(self):
        project, p = fixture()
        self.assertTrue(plan_director(project, continuity=p)['director_plan']['shot_alternatives'])
        for key, val in [('episode_id', 'EP2'), ('event_ids', ['OTHER']), ('evidence_ids', []), ('start_sec', 2.5)]:
            q = copy.deepcopy(project); q['clips'][0][key] = val
            self.assertEqual(shot_alternatives(q, p), [])

    def test_multi_episode_bundle_isolates_local_ids(self):
        p, c = fixture(); p2, c2 = fixture()
        c2['episode_id'] = 'EP2'
        for row in list(c2['candidates']):
            c2 = review(c2, row['candidate_id'], 'PASS', 'verified episode two', 'editor', True)
        p2['clips'][0].update(episode_id='EP2', clip_id='C2', source_video='two.mp4')
        p['clips'] += p2['clips']
        alts = shot_alternatives(p, {'episodes': [c, c2]})
        self.assertEqual({a['clip_id'] for a in alts}, {'C1', 'C2'})
        self.assertTrue(all(a['source_video'] == ('two.mp4' if a['episode_id'] == 'EP2' else 'episode.mp4') for a in alts))
        with self.assertRaises(ValueError): shot_alternatives(p, {'episodes': [c, c]})

    def test_rework_rejects_unknown_shot_even_without_replacement(self):
        p, c = fixture()
        f = {'project_fingerprint': fingerprint(p), 'issues': [{'clip_id': 'C1', 'shot_id': 'missing', 'message': '动作断了'}]}
        with self.assertRaisesRegex(ValueError, '无法定位'): rework(p, f, c)

    def test_version_cli_ab_and_selection_merge(self):
        p, c = fixture()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); write_new(root/'p.json', p); write_new(root/'c.json', c)
            script = str(ROOT/'scripts/edit_versions.py')
            subprocess.run([sys.executable, script, 'ab', str(root/'p.json'), str(root/'c.json'), str(root/'ab')], check=True)
            a = json.loads((root/'ab/a.json').read_text(encoding='utf-8')); b = json.loads((root/'ab/b.json').read_text(encoding='utf-8'))
            write_new(root/'choice.json', {'a_version_id': a['edit_version']['version_id'], 'b_version_id': b['edit_version']['version_id'], 'choose_b_clip_ids': ['C1']})
            subprocess.run([sys.executable, script, 'merge', str(root/'ab/a.json'), str(root/'ab/b.json'), str(root/'merge.json'), '--selection', str(root/'choice.json')], check=True)
            merged = json.loads((root/'merge.json').read_text(encoding='utf-8'))
            self.assertEqual(merged['clips'], b['clips'])

    def test_ab_keeps_original_and_requires_edit_review(self):
        p, continuity = fixture(); original = copy.deepcopy(p)
        result = make_ab(p, continuity)
        self.assertEqual(p, original); self.assertEqual(result['a']['clips'], p['clips'])
        self.assertTrue(result['diff']['changed'])
        self.assertNotEqual(result['a']['edit_version']['version_id'], result['b']['edit_version']['version_id'])
        for v in ['a', 'b']:
            with self.assertRaises(ValueError): require_approved(result[v])
        accepted = approve(result['b'], 'editor', '核实对白完整性并批准新入出点')
        require_approved(accepted)
        accepted['clips'][0]['end_sec'] += .1
        with self.assertRaises(ValueError): require_approved(accepted)

    def test_both_renderers_block_pending_version(self):
        p, c = fixture(); b = make_ab(p, c)['b']
        for fn in [active_clips, enabled_clips]:
            with self.assertRaisesRegex(ValueError, '尚未审核'): fn(b)

    def test_old_projects_keep_render_behavior(self):
        require_approved(fixture()[0])

    def test_no_approved_alternatives_produces_identical_b(self):
        p, c = fixture()
        for row in c['candidates']: row['review_status'] = 'UNREVIEWED'
        result = make_ab(p, c)
        self.assertFalse(result['diff']['changed'])

    def test_diff_add_remove_order_disabled_duration_and_source(self):
        p, _ = fixture(); q = copy.deepcopy(p)
        q['clips'].append({**q['clips'][0], 'clip_id': 'C2', 'enabled': False})
        q['clips'][0]['source_video'] = 'other.mp4'
        result = diff_projects(p, q)
        self.assertEqual(result['added'], ['C2']); self.assertEqual(result['duration_delta_sec'], 0)
        self.assertIn('source_video', result['changed'][0]['changes'])
        self.assertEqual(diff_projects(q, p)['removed'], ['C2'])

    def test_merge_selects_per_clip_and_creates_new_pending_version(self):
        p, c = fixture(); r = make_ab(p, c)
        merged = merge_versions(r['a'], r['b'], ['C1'])
        self.assertEqual(merged['clips'], r['b']['clips'])
        self.assertEqual(merged['edit_version']['review_status'], 'UNREVIEWED')
        with self.assertRaises(ValueError): merge_versions(r['a'], r['b'], ['unknown'])

    def test_rework_stale_or_unrelated_candidate_rejected(self):
        p, c = fixture()
        f = {'project_fingerprint': 'stale', 'issues': [{'clip_id': 'C1', 'message': '反应镜头不对'}]}
        with self.assertRaises(ValueError): rework(p, f, c)
        f['project_fingerprint'] = fingerprint(p)
        f['issues'][0]['alternative_id'] = 'missing'
        with self.assertRaises(ValueError): rework(p, f, c)

    def test_rework_unresolved_blocks_approval(self):
        p, c = fixture(); f = {'project_fingerprint': fingerprint(p), 'issues': [{'clip_id': 'C1', 'message': '台词被切断'}]}
        revised = rework(p, f, c)
        self.assertTrue(revised['edit_version']['unresolved_issues'])
        with self.assertRaises(ValueError): approve(revised, 'tester', 'approve')
        self.assertEqual(revised['clips'], p['clips'])

    def test_rework_selected_candidate_is_traceable(self):
        p, c = fixture(); aid = shot_alternatives(p, c)[0]['alternative_id']
        f = {'project_fingerprint': fingerprint(p), 'issues': [{'clip_id': 'C1', 'shot_id': 'S1', 'message': '动作断了', 'alternative_id': aid}]}
        revised = rework(p, f, c)
        self.assertFalse(revised['edit_version']['unresolved_issues'])
        self.assertEqual(revised['edit_version']['decisions'][0]['alternative_id'], aid)
        self.assertIn('version_qc', revised)

    def test_write_never_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'v.json'; write_new(path, {'x': 1})
            with self.assertRaises(FileExistsError): write_new(path, {'x': 2})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'x': 1})

    def test_report_mapping_across_cut_boundary_and_disabled_clips(self):
        p, _ = fixture()
        p['clips'].append({**p['clips'][0], 'clip_id': 'C2', 'start_sec': 5, 'end_sec': 7})
        p['clips'].append({**p['clips'][0], 'clip_id': 'C3', 'enabled': False})
        r = map_report(p, {'duration_sec': 5, 'issues': [{'code': 'BLACK_SEGMENT', 'segment': {'start_sec': 2.5, 'end_sec': 3.5}}]})
        refs = r['issues'][0]['source_clips']
        self.assertEqual([x['clip_id'] for x in refs], ['C1', 'C2']); self.assertEqual(refs[1]['source_start_sec'], 5)
        bad = map_report(p, {'duration_sec': 8, 'issues': []})
        self.assertFalse(bad['timeline_mapping_valid']); self.assertEqual(bad['technical_check'], 'FAIL')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_approved_version_render_and_keyframe_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); video = root/'source.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=160x120:d=3:r=30', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)], check=True)
            p, c = fixture(); p['clips'][0]['source_video'] = str(video)
            b = approve(make_ab(p, c)['b'], 'tester', 'approved source range')
            report = render_review(b, root/'cut', width=160, height=120)
            self.assertTrue(report['timeline_mapping_valid']); self.assertTrue(report['keyframes'])
            self.assertTrue(any(i['source_clips'] for i in report['issues']))
            with self.assertRaises(FileExistsError): render_review(b, root/'cut', width=160, height=120)

if __name__ == '__main__': unittest.main()
