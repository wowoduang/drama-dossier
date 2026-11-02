'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');

const server = spawn(process.env.PYTHON || 'python', ['-m', 'http.server', '8765', '--bind', '127.0.0.1'], {
  cwd: process.cwd(), stdio: 'ignore', windowsHide: true
});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitForServer() {
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch('http://127.0.0.1:8765/web/character-states.html'); if (r.ok) return; } catch {}
    await sleep(500);
  }
  throw new Error('Local web server did not start');
}
async function uploadJson(page, selector, name, value) {
  await page.locator(selector).setInputFiles({ name, mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(value), 'utf8') });
}
async function downloadJson(page, selector) {
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator(selector).click()]);
  const path = await download.path();
  return JSON.parse(await fs.readFile(path, 'utf8'));
}
async function main() {
  await waitForServer();
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ acceptDownloads: true });
  page.on('dialog', dialog => dialog.accept());
  try {
    // Character-state review: upload, review PASS with source check, export audit.
    const dossier = {
      episode: { episode_id: 'EP001', title: 'UI smoke', duration_sec: 60 },
      review_history: [],
      characters: [{ character_id: 'C1', canonical_name: '主角' }],
      evidence: [{ evidence_id: 'E1', start_sec: 9, end_sec: 12, review_status: 'PASS' }],
      events: [{ event_id: 'EV1', playback_time: 10, evidence_ids: ['E1'] }],
      knowledge_states: [],
      character_states: [{ state_id: 'S1', character_id: 'C1', dimension: 'goal', aspect: 'main', value: '信任朋友', playback_time: 10, audience_available_sec: 10, event_ids: ['EV1'], evidence_ids: ['E1'], review_status: 'UNREVIEWED' }],
      character_state_changes: []
    };
    await page.goto('http://127.0.0.1:8765/web/character-states.html');
    await uploadJson(page, '#file', 'episode.json', dossier);
    await page.locator('#list [data-id="S1"]').click();
    await page.locator('#reviewer').fill('browser-qa');
    await page.locator('#status').selectOption('PASS');
    await page.locator('#reason').fill('核对 UI 审核流程');
    await page.locator('#checked').check();
    await page.locator('#save').click();
    const reviewedDossier = await downloadJson(page, '#export');
    assert.equal(reviewedDossier.character_states[0].review_status, 'PASS');
    assert.equal(reviewedDossier.review_history.at(-1).reviewer, 'browser-qa');

    // Shot-continuity review: validate PASS gate and preserve review history.
    const continuity = { episode_id: 'EP001', candidates: [{ candidate_id: 'SC1', candidate_type: 'REACTION_SHOT', shot_ids: ['SH1'], start_sec: 1, end_sec: 3, reason: '测试候选', evidence_ids: ['E1'], review_status: 'UNREVIEWED' }], shots: [{ shot_id: 'SH1', start_sec: 1, end_sec: 3 }] };
    await page.goto('http://127.0.0.1:8765/web/shot-continuity-review.html');
    await uploadJson(page, '#file', 'continuity.json', continuity);
    await page.locator('#list [data-id="SC1"]').click();
    await page.locator('#reviewer').fill('browser-qa');
    await page.locator('#status').selectOption('PASS');
    await page.locator('#reason').fill('回看连续原片');
    await page.locator('#checked').check();
    await page.locator('#save').click();
    const reviewedContinuity = await downloadJson(page, '#export');
    assert.equal(reviewedContinuity.candidates[0].review_status, 'PASS');
    assert.equal(reviewedContinuity.candidates[0].review_history.length, 1);

    // Story-arc workbench: load a plan, confirm the plan, and export it.
    const roles = ['setup', 'peak', 'reaction', 'payoff'];
    const arc = { plan_id: 'ARC1', title: 'UI arc', goal: '测试成长弧审核', sequence_strategy: 'CHRONOLOGICAL', required_roles: roles, segments: roles.map((role, i) => ({ segment_id: 'SEG' + i, episode_id: 'EP001', event_ids: ['EV' + i], role, rationale: '测试段落', evidence_refs: ['EP001:E' + i], review_status: 'PASS' })) };
    await page.goto('http://127.0.0.1:8765/web/story-arc-workbench.html');
    await uploadJson(page, '#file', 'arc.json', arc);
    await page.locator('#reviewer').fill('browser-qa');
    await page.locator('#planReason').fill('核对完整叙事角色');
    await page.locator('#confirmPlan').click();
    assert.match(await page.locator('#status').textContent(), /PASS/);
    const reviewedArc = await downloadJson(page, '#export');
    assert.equal(reviewedArc.review_status, 'PASS');

    // Series editor: disable a clip and export the edited project.
    const project = { title: 'UI edit', clips: [{ clip_id: 'CLP1', episode_id: 'EP001', source_video: 'episode.mp4', start_sec: 1, end_sec: 4, enabled: true, title: '片段一' }] };
    await page.goto('http://127.0.0.1:8765/web/series-workbench.html');
    await uploadJson(page, '#projectFile', 'project.json', project);
    await page.locator('#toggle').click();
    const editedProject = await downloadJson(page, '#save');
    assert.equal(editedProject.clips[0].enabled, false);
    process.stdout.write('Browser UI smoke: 4 workflows passed\n');
  } finally {
    await browser.close();
    server.kill();
  }
}
main().catch(err => { process.stderr.write((err.stack || String(err)) + '\n'); server.kill(); process.exitCode = 1; });
