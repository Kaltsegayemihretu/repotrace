// Drives the prototype in headless Chrome the way a person would: real key presses, typing and mouse clicks only.
// It READS page state to verify results, but never calls the page's own functions to act.
// Usage: node prototype/check.mjs OUTDIR      (needs Google Chrome and Node 22+)
import {spawn} from 'node:child_process';
import {writeFileSync, mkdtempSync, mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

const OUT = process.argv[2] || mkdtempSync(join(tmpdir(), 'proto-')); mkdirSync(OUT, {recursive: true});
const URL_ = 'file://' + resolve(dirname(fileURLToPath(import.meta.url)), 'index.html');
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--remote-debugging-port=9334', `--user-data-dir=${mkdtempSync(join(tmpdir(), 'proto-qa-'))}`, '--window-size=1500,950', 'about:blank'], {stdio: 'ignore'});
let target;
for (let i = 0; i < 50 && !target; i++) { try { target = (await (await fetch('http://127.0.0.1:9334/json/list')).json()).find(t => t.type === 'page'); } catch { await sleep(200); } }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r));
let id = 0; const pending = new Map(); const errors = [];
ws.addEventListener('message', ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push('EXC ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
  if (m.method === 'Runtime.consoleAPICalled' && ['error', 'assert'].includes(m.params.type)) errors.push('CONSOLE ' + m.params.args.map(a => a.value || a.description).join(' '));
});
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const read = async expr => { const r = await send('Runtime.evaluate', {expression: expr, returnByValue: true}); if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails.exception?.description || r.result.exceptionDetails)); return r.result.result.value; };
const shot = async name => { const r = await send('Page.captureScreenshot', {format: 'png'}); writeFileSync(join(OUT, name + '.png'), Buffer.from(r.result.data, 'base64')); };
const log = [];
const step = async (name, fn) => { try { await fn(); log.push('PASS ' + name); } catch (e) { log.push('FAIL ' + name + ' :: ' + e.message); } };
const need = (ok, msg) => { if (!ok) throw new Error(msg); };

// ---- real input only
const mouse = async (x, y) => { for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', {type, x, y, button: 'left', clickCount: 1}); };
const KEYS = {Enter: 13, Escape: 27, k: 75};
const press = async (key, modifiers = 0) => { for (const type of ['keyDown', 'keyUp']) await send('Input.dispatchKeyEvent', {type, key, code: key.length === 1 ? 'Key' + key.toUpperCase() : key, windowsVirtualKeyCode: KEYS[key], modifiers}); };
const type = text => send('Input.insertText', {text});
// click the element the page shows: find it by text, scroll it into view, click its centre with the mouse
const clickText = async (selector, text, nth = 0) => {
  const box = await read(`(()=>{const e=[...document.querySelectorAll(${JSON.stringify(selector)})].filter(e=>e.getClientRects().length && e.textContent.includes(${JSON.stringify(text)}))[${nth}]; if(!e) return null; e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
  need(box, `nothing to click: ${selector} "${text}"`); await sleep(120); await mouse(box.x, box.y);
};
// click a dot on the canvas, found by its label, at the place it is drawn
const clickDot = async (pred) => {
  const p = await read(`(()=>{const P=__proto, v=P.S.vis.find(v=>{const n=P.N[v]; return ${pred};}); if(v==null) return null; const p=P.S.pos.get(v), r=document.querySelector('#cv').getBoundingClientRect(), w=P.S.view; return {x:r.left+p.x*w.k+w.x+r.width/2, y:r.top+p.y*w.k+w.y+r.height/2}})()`);
  need(p, 'no dot where ' + pred); await mouse(p.x, p.y);
};
const panel = () => read(`document.querySelector('#right').innerText`);
const settle = (ms = 1600) => sleep(ms);

await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', {width: 1500, height: 950, deviceScaleFactor: 1, mobile: false});
await send('Page.navigate', {url: URL_}); await settle(1500);


// What each practice project's three tasks look like (the prototype shows whichever project data.js was made from).
const TASKS = {
  'StudentManagement': {
    ask: 'where is an email checked?', askFile: 'validation/rules.py', match: 'is_valid_email',
    path: [`n.k==='dir' && n.q==='studentapp/services'`, `n.k==='file' && n.n==='enrollment_service.py'`, `n.k==='class' && n.q==='EnrollmentService'`, `n.k==='fn' && n.q==='EnrollmentService.check_enrollment_rules'`],
    fn: 'EnrollmentService.check_enrollment_rules',
    review: {graph: 'step-06..step-07', ver: 'step-07', checkNode: 'CourseRepository.fetch_course_by_id', checkCode: 'fetch_course_by_id', minConcerns: 1,
      line: 'course = self.course_repository.read_course_by_id(course_id)', sig: ['def read_course_by_id(self, course_id):', '(removed)'],
      test: 'test_delete_student_removes_enrollments', testRun: 'python3 -m unittest tests.test_students.TestStudents.test_delete_student_removes_enrollments'},
    removed: {cmp: '0', graph: 'step-06..step-07', count: 1, name: 'read_course_by_id()', code: 'def read_course_by_id('},
    goto: {type: 'is_valid_email', name: 'is_valid_email'}},
  'test project 3': {
    ask: 'where are retries handled?', askFile: 'runtime/retry/policies.py', match: 'RetryPolicy.should_retry',
    path: [`n.k==='dir' && n.q==='queuely/core'`, `n.k==='file' && n.n==='job.py'`, `n.k==='class' && n.q==='Job'`, `n.k==='fn' && n.q==='Job.__post_init__'`],
    fn: 'Job.__post_init__',
    review: {graph: 'step-11..edit-1', ver: 'edit-1', checkNode: 'clamp', checkCode: 'mode', minConcerns: 6,
      line: 'self.priority = clamp(self.priority, MIN_PRIORITY, MAX_PRIORITY)', sig: ['def clamp(value, low, high):', 'def clamp(value, low, high, mode):'],
      test: 'test_clamp', testLine: 'assert clamp(value, low, high) == expected', second: 'FixedDelay.delay'},
    removed: {cmp: '2', graph: 'step-08..step-09', count: 2, name: 'short()', code: 'def short('},
    goto: {type: 'clamp', name: 'clamp'}},
};
const project = await read(`__proto.D.project.name`);
const T = TASKS[project];
if (!T) { console.log('no tasks written for project ' + project); chrome.kill(); process.exit(1); }
log.push('project: ' + project);

await step('start: folders only, version named, coverage worded as "traced" not "trust"', async () => {
  const o = await read(`({vis: __proto.S.vis.length, ver: document.querySelector('#ver').innerText, text: document.body.innerText})`);
  need(o.vis >= 2 && o.vis <= 20, 'visible dots ' + o.vis);
  need(/working folder/.test(o.ver), 'version not shown: ' + o.ver);
  need(/what the analysis could trace/i.test(o.text) && !/trust/i.test(o.text), 'coverage wording');
  await shot('01-start');
});

await step(`TASK 1  find code: ⌘K, type "${T.ask}", Enter`, async () => {
  await press('k', 4); await sleep(250);
  need(!(await read(`document.querySelector('#palette').hidden`)), 'palette did not open');
  await type(T.ask); await sleep(200); await press('Enter'); await settle(1800);
  const t = await panel();
  need(t.includes('Found in the code') && t.includes(T.askFile), 'expected file not cited: ' + t.slice(0, 300));
  const lit = await read(`__proto.S.ask.hits.filter(h=>__proto.S.vis.includes(h.node)).length`);
  need(lit >= 2, 'matches not on the graph as their own dots: ' + lit);
  log.push('   matches: ' + (await read(`__proto.S.ask.hits.slice(0,5).map(h=>__proto.N[h.node].q).join(', ')`)));
  await shot('02-ask');
});

await step('TASK 1b  click a match: the function and its real neighbours appear as functions', async () => {
  await clickText('#right .card button.btn.link', T.match); await settle(1800);
  const o = await read(`(()=>{const P=__proto, s=P.S.selected, nb=P.neighbourIds(s); return {sel: P.N[s].q, nb: nb.length, shown: nb.filter(i=>P.S.vis.includes(i)).length}})()`);
  need(o.sel === T.match, 'selected ' + o.sel);
  need(o.shown === o.nb, `neighbours on graph ${o.shown}/${o.nb}`);
  await shot('03-match-selected');
  await press('Escape'); await settle(800);
});

await step('TASK 2  click down: folder → file → class → method, with neighbours shown as functions', async () => {
  await clickText('#crumbs button', project); await settle(1500);
  for (const p of T.path) { await clickDot(p); await settle(1800); }
  const o = await read(`(()=>{const P=__proto, s=P.S.selected, nb=P.neighbourIds(s); return {sel: P.N[s].q, names: nb.map(i=>P.N[i].q), shown: nb.filter(i=>P.S.vis.includes(i)).length, crumbs: document.querySelector('#crumbs').innerText}})()`);
  need(o.sel === T.fn, 'selected ' + o.sel);
  need(o.shown === o.names.length && o.names.length >= 2, `neighbours shown ${o.shown}/${o.names.length}`);
  const t = await panel();
  for (const nm of o.names.slice(0, 3)) need(t.includes(nm), 'panel misses ' + nm);
  log.push(`   neighbours of ${T.fn}: ` + o.names.join(', ') + ' | path: ' + o.crumbs.replace(/\n/g, ' '));
  await shot('04-function-neighbours');
});

await step('filters: with "Sure links" off, no arrow is drawn as sure', async () => {
  await clickText('#left label', 'Sure links'); await settle(500);
  need((await read(`__proto.S.edges.filter(e=>e.sure>0).length`)) === 0, 'edges still count sure links');
  await clickText('#left label', 'Sure links'); await settle(300);
});

await step('conceptual view is marked experimental and lists example files', async () => {
  await clickText('#left label', 'Conceptual'); await settle(1500);
  const t = await read(`document.querySelector('#topicsBox').innerText`);
  need(/EXPERIMENTAL/.test(t) && /e\.g\./.test(t), t.slice(0, 120));
  log.push('   topics: ' + (await read(`__proto.G.concepts.join(' | ')`)));
  await shot('05-conceptual');
  await clickText('#left label', 'Physical'); await settle(400);
});

await step('TASK 3  review a change: graph, code and evidence come from the same version', async () => {
  const R_ = T.review;
  await clickText('#top button', 'Review a change'); await settle(2200);
  const o = await read(`(()=>{const P=__proto, c=P.N.find(n=>n.q===${JSON.stringify(R_.checkNode)}); return {gk: P.GK, ver: document.querySelector('#ver').innerText, code: c ? c.code.split('\\n')[0] : ''}})()`);
  need(o.gk === R_.graph && o.ver.includes(R_.ver), 'wrong version ' + o.gk + ' ' + o.ver);
  need(o.code.includes(R_.checkCode), 'graph code is not the after-version: ' + o.code);
  need((await read(`document.querySelectorAll('#right .row.open').length`)) === 1, 'expected exactly 1 finding open');
  const t = await panel();
  need(new RegExp(R_.minConcerns + '\\s*to inspect').test(t), 'summary line: ' + t.slice(0, 200));
  need(t.includes(R_.line), 'the problem line is not shown');
  for (const s of R_.sig) need(t.includes(s), 'before/after missing ' + s);
  await shot('06-review-first-finding');
});

await step('TASK 3b  focus the relationship, then inspect a test and its run command', async () => {
  const R_ = T.review;
  if (R_.second) { await clickText('#right .row[data-kind=concern] button.head', R_.second); await settle(1200); }
  await clickText('#right .row.open button.btn', 'Focus this relationship'); await settle(1400);
  need((await read(`__proto.S.rfocus.length`)) >= 2, 'relationship not focused');
  await shot('07-review-focus');
  await clickText('#right .row[data-kind=test] button.head', R_.test); await settle(1200);
  const t = await panel();
  if (R_.testLine) need(t.includes(R_.testLine), 'test line missing');
  if (R_.testRun) need(t.includes(R_.testRun), 'run command missing');
  await clickText('#right .row.open button.btn', 'Inspect this test'); await settle(1400);
  need((await read(`!!document.querySelector('#right .row.open details[open] pre')`)), 'test code not shown');
  await shot('08-review-test');
});

await step('removed code is drawn from the old version, with its old callers', async () => {
  const X = T.removed;
  await read(`(()=>{const s=document.querySelector('#cmp'); s.value=${JSON.stringify(X.cmp)}; s.dispatchEvent(new Event('change')); return 1})()`);   // a native dropdown cannot be clicked headless
  await settle(2200);
  const o = await read(`(()=>{const P=__proto; const g=P.N.filter(n=>n.gone); return {gk: P.GK, gone: g.map(n=>n.q), shown: g.filter(n=>P.S.vis.includes(n.i)).length}})()`);
  need(o.gk === X.graph && o.gone.length === X.count && o.shown === X.count, JSON.stringify(o));
  await clickText('#right .row[data-kind=changed] button.head', X.name); await settle(1400);
  const t = await panel();
  need(t.includes(X.code) && t.includes('(removed)'), 'removed function evidence missing');
  const callers = await read(`__proto.S.rfocus.slice(1).filter(i=>__proto.S.vis.includes(i)).map(i=>__proto.N[i].q)`);
  need(callers.length >= 1, 'old callers not shown');
  log.push('   removed: ' + o.gone.join(', ') + ' | old callers shown: ' + callers.join(', '));
  await shot('09-review-removed');
});

await step(`Go to (⌘K, type "${T.goto.type}", Enter) returns to the working-folder version`, async () => {
  await press('k', 4); await sleep(200); await type(T.goto.type); await sleep(200); await press('Enter'); await settle(1800);
  const o = await read(`({gk: __proto.GK, sel: __proto.N[__proto.S.selected].q})`);
  need(o.gk === 'main' && o.sel === T.goto.name, JSON.stringify(o));
  await shot('10-go-to');
});

console.log(log.join('\n')); console.log(errors.length ? 'PAGE ERRORS:\n' + errors.join('\n') : 'no page errors'); console.log('screenshots:', OUT);
chrome.kill(); process.exit(log.some(l => l.startsWith('FAIL')) || errors.length ? 1 : 0);
