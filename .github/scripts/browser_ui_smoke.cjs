const { chromium } = require('playwright');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '../..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'drama-dossier-ui-'));
const errors = [];
const writeJson = (name, value) => {
  const p = path.join(tmp, name);
  fs.writeFileSync(p, JSON.stringify(value, null, 2), 'utf8');
  return p;
};
const dossier = {
  schema_version: '3.5.0',
  episode: { episode_id: 'EP_UI', title: 'Browser smoke fixture', duration_sec: 100 },
  evidence: [{ evidence_id: 'V1', type: 'SUB', start_sec: 10, end_sec: 12, raw: '我相信你', quality: 'high', review_status: 'UNREVIEWED' }],
  characters: [{ character_id: 'A', canonical_name: '主角', aliases: [], identity_status: 'confirmed', evidence_ids: ['V1'], review_status: 'UNREVIEWED' }],
  appearances: [], scenes: [],
  events: [{ event_id: 'E1', scene_id: null, playback_time: 11, actor: 'A', patient: null, recipient: null, target: null, action: '相信同伴', modality: 'actual', polarity: 'positive', certainty: 'F1_DIRECT', evidence_ids: ['V1'], review_status: 'UNREVIEWED' }],
  relations: [], causal_edges: [], threads: [], storylines: [], highlights: [], media_cues: [], story_arcs: [],
  knowledge_states: [],
  character_states: [{ state_id: 'S1', character_id: 'A', dimension: 'goal', aspect: 'main', value: '相信同伴', description: '主角表达信任', basis: 'OBSERVATION', certainty: 'F1_DIRECT', quote: '我相信你', playback_time: 11, audience_available_sec: 12, event_ids: ['E1'], evidence_ids: ['V1'], review_status: 'UNREVIEWED' }],
  character_state_changes: [], review_history: []
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ acceptDownloads: true });
    page.on('pageerror', e => errors.push(String(e)));
    page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });

    // Full dossier review UI: load, select an event, review, persist via export.
    const dossierPath = writeJson('episode.json', dossier);
    await page.goto(pathToFileURL(path.join(root, 'web/review.html')).href);
    await page.setInputFiles('#jsonFile', dossierPath);
    await page.locator('#recordList button.record').first().click();
    await page.locator('#reviewer').fill('qa-smoke');
    await page.locator('#reviewReason').fill('Browser UI smoke: evidence checked');
    await page.locator('#reviewStatus').selectOption('NEED_EVIDENCE');
    await page.locator('#saveReview').click();
    await page.locator('#historyList').getByText('qa-smoke').waitFor();
    const dossierDownload = page.waitForEvent('download');
    await page.locator('#exportBtn').click();
    const dossierFile = path.join(tmp, 'review-export.json');
    await (await dossierDownload).saveAs(dossierFile);
    const reviewedDossier = JSON.parse(fs.readFileSync(dossierFile, 'utf8'));
    assert.equal(reviewedDossier.review_history.length, 1);
    assert.equal(reviewedDossier.review_history[0].reviewer, 'qa-smoke');
    assert.equal(reviewedDossier.events[0].review_status, 'NEED_EVIDENCE');

    // Character-state review UI: review a state and export the updated dossier.
    await page.goto(pathToFileURL(path.join(root, 'web/character-states.html')).href);
    await page.setInputFiles('#file', dossierPath);
    await page.locator('#list button[data-id="S1"]').click();
    await page.locator('#reviewer').fill('qa-state-smoke');
    await page.locator('#status').selectOption('PASS');
    await page.locator('#reason').fill('Browser UI smoke: source evidence checked');
    await page.locator('#checked').check();
    await page.locator('#save').click();
    await page.getByText('qa-state-smoke').waitFor();
    const stateDownload = page.waitForEvent('download');
    await page.locator('#export').click();
    const stateFile = path.join(tmp, 'state-review-export.json');
    await (await stateDownload).saveAs(stateFile);
    const reviewedState = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    assert.equal(reviewedState.character_states[0].review_status, 'PASS');
    assert.equal(reviewedState.review_history.length, 1);
    assert.equal(reviewedState.review_history[0].source_checked, true);

    // Series editing workbench: load, toggle, edit, and export a cut project.
    const project = {
      title: 'UI smoke project',
      sequence_strategy: 'chronological',
      clips: [
        { clip_id: 'C1', episode_id: 'EP01', source_video: 'ep1.mp4', start_sec: 0, end_sec: 2, enabled: true, title: '片段一' },
        { clip_id: 'C2', episode_id: 'EP02', source_video: 'ep2.mp4', start_sec: 0.5, end_sec: 2.5, enabled: true, title: '片段二' }
      ]
    };
    const projectPath = writeJson('project.json', project);
    await page.goto(pathToFileURL(path.join(root, 'web/series-workbench.html')).href);
    await page.setInputFiles('#projectFile', projectPath);
    await page.locator('#duration').getByText('启用 2/2 段 · 4.0 秒').waitFor();
    await page.locator('#allOff').click();
    await page.locator('#duration').getByText('启用 0/2 段 · 0.0 秒').waitFor();
    await page.locator('#allOn').click();
    await page.locator('#clips .clip[data-i="1"]').click();
    await page.locator('#start').fill('0.75');
    await page.locator('#end').fill('2.25');
    await page.locator('#title').fill('修改后的片段二');
    await page.locator('#apply').click();
    const projectDownload = page.waitForEvent('download');
    await page.locator('#save').click();
    const projectFile = path.join(tmp, 'project-export.json');
    await (await projectDownload).saveAs(projectFile);
    const savedProject = JSON.parse(fs.readFileSync(projectFile, 'utf8'));
    assert.equal(savedProject.clips[1].start_sec, 0.75);
    assert.equal(savedProject.clips[1].end_sec, 2.25);
    assert.equal(savedProject.clips[1].title, '修改后的片段二');
    assert.equal(savedProject.estimated_duration_sec, 3.5);

    assert.deepEqual(errors, [], 'browser console/page errors should be empty');
    process.stdout.write('Browser UI smoke passed: dossier review, character-state review, series workbench.\n');
  } finally {
    await browser.close();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
})().catch(err => {
  process.stderr.write(String(err && err.stack || err) + '\n');
  process.exitCode = 1;
});
