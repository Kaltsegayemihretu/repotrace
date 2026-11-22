// Drives the LIVE guided screens (python3 -m repotrace app --lab) in headless Chrome with real clicks, typing and keys:
// open a project folder, choose two versions, analyse, then do the three jobs. Run it on one project or several.
//   node qa/browser/lab_live.mjs OUTDIR URL PROJECT_FOLDER BASE_TAG HEAD_TAG [EXPECT_JSON]
// EXPECT_JSON (optional) e.g. '{"concerns":1,"tests":12,"mustMention":["fetch_course_by_id"]}'. Without it the checks are generic.
import {spawn} from 'node:child_process';
import {writeFileSync, mkdtempSync, mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const [OUT, URL_, PROJECT, BASE, HEAD, EXPECT_RAW] = process.argv.slice(2);
const SAVED = process.env.SAVED_URL || '';                       // the saved demo (prototype/lab): no project picker, data comes from data.js
const EXPECT = EXPECT_RAW ? JSON.parse(EXPECT_RAW) : {};
mkdirSync(OUT, {recursive: true});
const PORT = 9340 + Math.floor(Math.random() * 50);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const chrome = spawn(process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', ['--headless=new', '--disable-gpu', '--hide-scrollbars', `--remote-debugging-port=${PORT}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), 'lablive-'))}`, 'about:blank'], {stdio: 'ignore'});
let target; for (let i = 0; i < 50 && !target; i++) { try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(t => t.type === 'page'); } catch { await sleep(200); } }
const ws = new WebSocket(target.webSocketDebuggerUrl); await new Promise(r => ws.addEventListener('open', r));
let id = 0; const pending = new Map(), errors = [];
ws.addEventListener('message', ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description?.split('\n')[0] || m.params.exceptionDetails.text);
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push('console: ' + m.params.args.map(a => a.value || a.description).join(' ').slice(0, 600));
  if (m.method === 'Network.responseReceived' && m.params.response.status >= 400) errors.push(`HTTP ${m.params.response.status} ${m.params.response.url}`); });
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const read = async e => { const r = await send('Runtime.evaluate', {expression: e, returnByValue: true, awaitPromise: true}); if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || 'evaluate failed'); return r.result.result.value; };
let shots = 0; const shot = async name => { const r = await send('Page.captureScreenshot', {format: 'png'}); writeFileSync(join(OUT, String(++shots).padStart(2, '0') + '-' + name + '.png'), Buffer.from(r.result.data, 'base64')); };
const mouse = async (x, y) => { for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', {type, x, y, button: 'left', clickCount: 1}); };
const KEYS = {Enter: 13, Escape: 27, Tab: 9};
const key = async (k) => { for (const type of ['keyDown', 'keyUp']) await send('Input.dispatchKeyEvent', {type, key: k, code: k, windowsVirtualKeyCode: KEYS[k], ...(k === 'Enter' && type === 'keyDown' ? {text: '\r'} : {})}); await sleep(250); };
const need = (ok, msg) => { if (!ok) throw new Error(msg); };
const click = async (sel, text) => {
  const b = await read(`(()=>{const e=[...document.querySelectorAll(${JSON.stringify(sel)})].filter(e=>e.getClientRects().length&&(!${JSON.stringify(text || '')}||(e.innerText||e.textContent||'').includes(${JSON.stringify(text || '')})))[0]; if(!e) return null; e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
  need(b, `nothing to click: ${sel} "${text || ''}"`); await sleep(100); await mouse(b.x, b.y); await sleep(700);
};
const text = sel => read(`(document.querySelector(${JSON.stringify(sel)})||{innerText:''}).innerText`);
const waitFor = async (expr, label, ms = 60000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await read(expr)) return; await sleep(250); } throw new Error('timeout waiting for ' + label); };
const log = [];
const step = async (name, fn) => { try { await fn(); log.push('PASS ' + name); } catch (e) { log.push('FAIL ' + name + ' :: ' + e.message); } };
let projectName = '';

await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
await send('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
await send('Page.navigate', {url: SAVED || URL_ + 'lab'}); await sleep(2000);

if (SAVED) {
  await step('saved demo: opens with its saved data and no project picker', async () => { await waitFor(`!!window.PROTO`, 'saved data'); projectName = await read(`window.PROTO.project.name`); need(!(await read(`!!document.querySelector('#repo-path')`)), 'a picker is shown'); await shot('start'); });
} else {
await step('start: the project picker is shown, with no project data loaded yet', async () => {
  need(await read(`!!document.querySelector('#repo-path')`), 'no folder box');
  need(!(await read(`!!window.PROTO`)), 'data present before choosing a project');
  await shot('picker');
});
await step('type the project folder and press Enter: its versions are listed', async () => {
  await read(`(()=>{const i=document.querySelector('#repo-path'); i.focus(); i.value=''; return 1})()`);
  await send('Input.insertText', {text: PROJECT}); await key('Enter');
  await waitFor(`!!document.querySelector('#go-form')`, 'version step');
  need(/commits? found/.test(await text('#content')), 'no commit count');
  await shot('versions');
});
await step('choose the two versions and press Analyse; progress is shown; the guided screens open', async () => {
  const pick = await read(`(()=>{const find=(sel,tag)=>{const s=document.querySelector(sel); const o=[...s.options].find(o=>o.text.toLowerCase().includes(${JSON.stringify('')}+tag)); return o}; return JSON.stringify({base:[...document.querySelector('#base').options].map(o=>o.text), head:[...document.querySelector('#head').options].map(o=>o.text)})})()`);
  const opts = JSON.parse(pick);
  const choose = async (sel, needle) => { const i = opts[sel === '#base' ? 'base' : 'head'].findIndex(t => t.toLowerCase().includes(needle.toLowerCase())); need(i >= 0, `no version matching "${needle}" in ${sel}`);
    await read(`(()=>{const s=document.querySelector(${JSON.stringify(sel)}); s.selectedIndex=${i}; s.dispatchEvent(new Event('change',{bubbles:true})); return 1})()`); };   // a native dropdown cannot be clicked headless
  await choose('#base', BASE); await choose('#head', HEAD);
  await click('#go-form button[type=submit]');
  await waitFor(`!!window.PROTO || !!document.querySelector('#picker-error:not([hidden])')`, 'analysis');
  need(await read(`!!window.PROTO`), 'analysis failed: ' + await text('#content'));
  await waitFor(`/Start|Find your way/.test(document.querySelector('#location').innerText + document.body.innerText)`, 'start screen');
  projectName = await read(`window.PROTO.project.name`);
  await shot('start');
});
await step('wording is live, never "saved example", and the project name is the real one', async () => {
  const t = await text('body');
  need(!/Saved example|saved example|practice project/.test(t.replace(/\n/g, ' ')), 'prototype wording leaked: ' + (t.match(/Saved example|saved example|practice project/) || [''])[0]);
  need(t.includes(projectName), 'project name missing');
  need(/Your comparison/.test(t), 'live wording missing');
  if (projectName !== 'StudentManagement') need(!/StudentManagement/.test(t), 'another project\'s name appears on screen');
});
}
// ---- the Explore page is the Obsidian-style explorer, running in a frame. xp(code) reads inside it; its dots are clicked at their real positions.
const xp = code => read(`(()=>{const f=document.querySelector('#xp'); if(!f||!f.contentWindow||!f.contentWindow.__xp) return null; const w=f.contentWindow, d=w.document, X=w.__xp; return (${code})})()`);
const frameRect = () => read(`(()=>{const r=document.querySelector('#xp').getBoundingClientRect(); return {x:r.left,y:r.top}})()`);
const xpDots = async () => { const o = await frameRect(), d = await xp(`X.inspect()`); return (d || []).map(x => ({...x, x: x.x + o.x, y: x.y + o.y})); };
const xpText = sel => xp(`(d.querySelector(${JSON.stringify(sel)})||{innerText:''}).innerText`);
const xpClickText = async (sel, needle) => {
  const o = await frameRect();
  const b = await xp(`(()=>{const e=[...d.querySelectorAll(${JSON.stringify(sel)})].filter(e=>e.getClientRects().length&&(e.innerText||e.textContent||'').includes(${JSON.stringify(needle || '')}))[0]; if(!e) return null; e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
  need(b, `nothing to click in the explorer: ${sel} "${needle || ''}"`); await sleep(100); await mouse(b.x + o.x, b.y + o.y); await sleep(700);
};
const xpClickDot = async (pred, what) => {
  await sleep(1700);                                                   // let the layout settle before aiming
  const all = await xpDots(); const o = await frameRect();
  const inCanvas = d => d.x > o.x + 330 && d.x < o.x + 930 && d.y > o.y + 140 && d.y < o.y + 700;           // the canvas, clear of the zoom buttons and the hint
  const d = all.filter(pred).filter(inCanvas).sort((a, b) => Math.hypot(a.x - (o.x + 640), a.y - (o.y + 420)) - Math.hypot(b.x - (o.x + 640), b.y - (o.y + 420)))[0];   // nearest the middle
  need(d, `no dot to click for ${what}; visible: ` + all.map(x => x.k + ':' + x.q).slice(0, 10).join(', '));
  await mouse(d.x, d.y); await sleep(900); return d;
};
const openExplorer = async () => { await click('nav button[title="Explore the project"]'); await waitFor(`!!(document.querySelector('#xp')&&document.querySelector('#xp').contentWindow.__xp)`, 'the explorer'); await sleep(1200); };

await step('EXPLORE: the page is the Obsidian prototype: left controls, the graph, the panel, the breadcrumb bar', async () => {
  await click('nav button[title="Start"]'); await openExplorer();
  const left = await xpText('#left'), top = await xpText('#top'), bottom = await xpText('#bottom');
  for (const w of ['Physical', 'Logical', 'Conceptual', 'EXPERIMENTAL', 'Sure links', 'Maybe links', 'All test code', 'HOW TO READ IT']) need(left.includes(w), 'left controls miss "' + w + '"');
  need(/Explore/.test(top) && /Review a change/.test(top) && /Find a function, or ask a question/.test(top), 'top bar misses its tabs or the search button');
  need(/Back/.test(bottom) && /Traced:/.test(bottom), 'bottom bar misses Back or the traced line: ' + bottom);
  const d = await xpDots(); need(d.length >= 2 && d.every(x => x.k === 'dir'), 'expected folder dots first');
  need(await read(`document.querySelector('.shell').classList.contains('xp-wide')`), 'the explorer did not get the full width');
  await shot('explorer-start');
});
await step('EXPLORE: JOB 1, find code: the ⌘K palette, a question, Enter; the matches are highlighted with file and line', async () => {
  const q = await read(`window.PROTO.questions[0]`); need(q, 'no sample question was derived');
  await send('Input.dispatchKeyEvent', {type: 'keyDown', key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, modifiers: 4}); await send('Input.dispatchKeyEvent', {type: 'keyUp', key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, modifiers: 4}); await sleep(500);
  need(await xp(`!d.querySelector('#palette').hidden`), '⌘K did not open the palette');
  await send('Input.insertText', {text: q}); await sleep(300); await key('Enter'); await sleep(1500);
  const panel = await xpText('#right'); need(/Found in the code/.test(panel), 'no answer panel: ' + panel.slice(0, 80));
  const first = await xp(`d.querySelector('#right .card .btn.link')?.innerText`); need(first, 'no match listed');
  log.push(`   question "${q}" -> first match ${first}`); await shot('explorer-ask');
});
await step('EXPLORE: JOB 2, understand: open a match; its real callers and callees are on the graph as functions; follow one; Esc steps back inside the explorer', async () => {
  await xpClickText('#right .card .btn.link', ''); await sleep(1500);
  const sel = (await xpDots()).find(x => x.selected); need(sel && sel.k === 'fn', 'the match was not selected as a function');
  const panel = await xpText('#right'); need(/it calls/i.test(panel) && /called by/i.test(panel) && /the code/i.test(panel), 'callers, callees and code not all shown');
  const listed = await xp(`[...d.querySelectorAll('#right .btn.link')].map(b=>b.innerText)`); log.push('   listed: ' + listed.slice(0, 5).join(', '));
  const neighbours = await xp(`X.neighbourIds(X.S.selected)`); const onGraph = new Set((await xpDots()).map(x => x.id));
  need(neighbours.every(i => onGraph.has(i)), 'a neighbour is missing from the graph');
  await shot('explorer-function');
  const here = sel.id;
  if (neighbours.length) { await xpClickText('#right ul.list .btn.link', ''); await sleep(1200); need((await xpDots()).find(x => x.selected)?.id !== here, 'following a connection did not move the selection'); }
  await key('Escape'); await sleep(700);
  need(/Explore the project/.test(await text('#location')), 'Esc left the explorer while there was still something to undo');
});
await step('EXPLORE: click down through the dots like a person: folder, file, class, function', async () => {
  await click('nav button[title="Start"]'); await openExplorer();
  let file = null;
  for (let tries = 0; tries < 8 && !file; tries++) {                    // open on-screen folders until one holds a file with definitions in it
    await xpClickDot(x => x.k === 'dir' && !x.open && x.contents > 0, 'a folder');
    const now = await xpDots(); need(now.some(x => x.k === 'file'), 'the folder did not open into files');
    file = now.find(x => x.k === 'file' && x.contents > 0);
  }
  need(file, 'no file with definitions in the first folders'); await xpClickDot(x => x.k === 'file' && x.contents > 0, 'a file');
  const afterFile = await xpDots(); need(afterFile.some(x => x.k === 'fn' || x.k === 'class'), 'the file did not open into definitions');
  if (!afterFile.some(x => x.k === 'fn')) await xpClickDot(x => x.k === 'class' && x.contents > 0, 'a class');
  await xpClickDot(x => x.k === 'fn', 'a function');
  need((await xpDots()).find(x => x.selected)?.k === 'fn', 'the function was not selected');
  need(/›/.test(await xpText('#crumbs')), 'the breadcrumb bar shows no path');
  await shot('explorer-dots');
});
await step('EXPLORE: the three views and the filters: Conceptual is marked experimental and lists its files; Sure links off removes sure lines', async () => {
  await xpClickText('#left label', 'Conceptual'); await sleep(1200);
  const topics = await xpText('#topicsBox'); need(/EXPERIMENTAL/.test(topics) && /e\.g\./.test(topics), 'topics not shown with example files'); await shot('explorer-conceptual');
  await xpClickText('#left label', 'Logical'); await sleep(500); await xpClickText('#left label', 'Physical'); await sleep(500);
  await xpClickText('#left label', 'Sure links'); await sleep(500); need((await xp(`X.S.edges.filter(e=>e.sure>0).length`)) === 0, 'sure lines remain with Sure links off');
  await xpClickText('#left label', 'Sure links'); await sleep(300);
});
await step('EXPLORE: reading view and back: the explorer keeps what you had open', async () => {
  const f = (await xpDots()).find(x => x.selected) || (await xpDots()).find(x => x.k === 'fn'); need(f, 'no function to open');
  if (!f.selected) await xpClickDot(x => x.id === f.id, 'a function');
  const openBefore = await xp(`X.S.open.size`), picked = await xp(`X.S.selected`);
  await xpClickText('#right .btn', 'Open the reading view'); await sleep(900);
  need(/Read the code/.test(await text('#location')) && await read(`!!document.querySelector('#content .source-box')`), 'the reading view did not open');
  await click('#back'); await waitFor(`!!(document.querySelector('#xp')&&document.querySelector('#xp').contentWindow.__xp)`, 'the explorer again'); await sleep(1500);
  need((await xp(`X.S.selected`)) === picked && (await xp(`X.S.open.size`)) === openBefore, 'the explorer did not come back as you left it');
  await shot('explorer-restored');
});
await step('TRANSITIONS: the explorer stays alive between tabs, nothing stale shows, the rail eases in and out', async () => {
  await click('nav button[title="Start"]'); await sleep(500);
  const vis = () => read(`(()=>{const hd=document.getElementById('xp-holder'),c=document.getElementById('content');return {holder:!hd.hidden,content:!c.hidden,loc:document.getElementById('location').textContent,rail:Math.round(document.querySelector('.rail').getBoundingClientRect().width),wide:document.querySelector('.shell').classList.contains('xp-wide')}})()`);
  let v = await vis(); need(!v.holder && v.content && v.loc === 'Start' && !v.wide && v.rail > 150, 'Start must show its own page and the full rail: ' + JSON.stringify(v));
  await read(`document.getElementById('xp').dataset.mark='kept'`); const picked = await xp(`X.S.selected`);
  // sample inside the page on every frame, so the check does not depend on how fast the test's own clicks are
  const seen = await read(`new Promise(res=>{const out=[],r=document.querySelector('.rail'),t0=performance.now();document.querySelector('nav button[title="Explore the project"]').click();const tick=()=>{out.push(Math.round(r.getBoundingClientRect().width));performance.now()-t0<450?requestAnimationFrame(tick):res(out)};requestAnimationFrame(tick)})`);
  await sleep(500); v = await vis();
  need(v.holder && !v.content && v.wide && v.rail === 64 && v.loc === 'Explore the project', 'explorer page state: ' + JSON.stringify(v));
  need(new Set(seen).size > 4 && seen[0] > 100, 'the rail jumped instead of easing: ' + seen.join(','));
  need(await read(`document.getElementById('xp').dataset.mark==='kept'`), 'the explorer was rebuilt instead of kept');
  need((await xp(`X.S.selected`)) === picked, 'the explorer lost its selection when you came back');
  await click('nav button[title="Check a change"]'); await sleep(500); v = await vis();
  need(!v.holder && v.content && !v.wide && v.rail > 150 && /Check a change/.test(v.loc), 'leaving the explorer: ' + JSON.stringify(v));
  await click('nav button[title="Explore the project"]'); await sleep(900); v = await vis();
  need(v.holder && !v.content && v.rail === 64, 'second return: ' + JSON.stringify(v));
  need((await xp(`X.S.selected`)) === picked, 'selection lost on the second return');
});
await step('EXPLORE: Esc with nothing left to undo goes back a page', async () => {
  await click('nav button[title="Start"]'); await openExplorer();
  // coming back keeps what you had open, so the first Esc presses undo that; once nothing is left, Esc goes back a page
  let left = false; for (let i = 0; i < 25 && !left; i++) { await key('Escape'); await sleep(250); left = !/Explore the project/.test(await text('#location')); }
  need(left, 'Esc never went back a page');
});
await step('JOB 3: the review shows the changed code, the line to check, and the tests with run commands', async () => {
  await click('nav button[title="Check a change"]'); await click('[data-action=review][data-index="0"]'); await sleep(900);
  const overview = await text('#content');
  if (EXPECT.concerns !== undefined) need(new RegExp(`${EXPECT.concerns}\\s*(place|places)?`).test(await read(`document.querySelector('.tabs').innerText`)), 'concern count tab');
  let seen = overview;
  if (EXPECT.concerns) { await click('[data-action=show-evidence]'); need(await read(`!!document.querySelector('#review-evidence .code-line.highlight')`), 'the evidence line is not highlighted'); seen += await text('#content'); }
  for (const w of EXPECT.mustMention || []) need(seen.includes(w), 'the review never mentions "' + w + '"');
  await shot('review');
  await click('[data-action=review-tab][data-tab=tests]'); await sleep(700);
  const n = await read(`document.querySelectorAll('.test-item').length`);
  if (EXPECT.tests !== undefined) need(n === EXPECT.tests, `expected ${EXPECT.tests} test cards, found ${n}`);
  log.push(`   test cards: ${n}`);
  if (n) { need(await read(`!![...document.querySelectorAll('.test-item code')].some(c=>/python3 -m (unittest|pytest)/.test(c.innerText))`), 'no valid run command');
    await click('[data-action=copy-test]'); need(/Command copied|Copy unavailable/.test(await text('#toast')), 'copy gave no feedback'); }
  await shot('tests');
  await click('[data-action=review-tab][data-tab=limits]'); need(/could not|cannot|not checked|No findings/i.test(await text('#content')), 'limits not shown');
});
await step('EXPLORE: from a review, "See this change on the map" opens the explorer in review mode, ringed; "Open the full review" returns', async () => {
  await click('[data-action=review-tab][data-tab=overview]'); await click('[data-action=map-home]');
  await waitFor(`!!(document.querySelector('#xp')&&document.querySelector('#xp').contentWindow.__xp)`, 'the explorer'); await sleep(1800);
  need((await xp(`X.S.mode`)) === 'review', 'the explorer did not open in review mode');
  const panel = await xpText('#right'); need(/review/i.test(panel) && /to inspect/i.test(panel) && /(tests to look at|worth running first)/i.test(panel), 'the review panel is incomplete');
  need(await xp(`X.S.review.changed.length > 0 && X.inspect().length > 0`), 'nothing on the graph');
  if (EXPECT.concerns) need(/to inspect \(1\)/i.test(panel), 'the finding is not listed');
  await shot('explorer-review');
  await xpClickText('#right .btn', 'Open the full review'); await sleep(900);
  need(/Check a change/.test(await text('#location')) && await read(`!!document.querySelector('.tabs')`), 'the full review did not open');
});
await step('keyboard: Cmd/Ctrl+K on the app opens the explorer palette; no sideways page scroll', async () => {
  await click('nav button[title="Start"]');
  await send('Input.dispatchKeyEvent', {type: 'keyDown', key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, modifiers: 4}); await send('Input.dispatchKeyEvent', {type: 'keyUp', key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, modifiers: 4});
  await waitFor(`!!(document.querySelector('#xp')&&document.querySelector('#xp').contentWindow.__xp)`, 'the explorer'); await sleep(1500);
  need(await xp(`!d.querySelector('#palette').hidden`), 'the palette is not open'); await key('Escape');
  need(!(await read(`document.documentElement.scrollWidth > innerWidth + 1`)), 'page scrolls sideways');
});
console.log(`== ${projectName || PROJECT} ${BASE} -> ${HEAD}`); console.log(log.join('\n')); console.log(errors.length ? 'PAGE/HTTP ERRORS:\n' + [...new Set(errors)].join('\n') : 'no page errors'); console.log('screenshots:', OUT);
chrome.kill(); process.exit(log.some(l => l.startsWith('FAIL')) || errors.length ? 1 : 0);
