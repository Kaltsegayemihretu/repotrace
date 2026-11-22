// Headless check of the live recommendation workflow. No screenshot dump.
//   node --experimental-websocket qa/browser/recs_verify.mjs URL PROJECT BASE HEAD
import {spawn} from 'node:child_process';
import {writeFileSync, mkdirSync, mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const [URL_, PROJECT, BASE, HEAD] = process.argv.slice(2);
const OUT = join('qa', 'ui_audit', 'verify');
mkdirSync(OUT, {recursive: true});
const sleep = ms => new Promise(r => setTimeout(r, ms));
const PORT = 9600 + Math.floor(Math.random() * 80);
const profile = mkdtempSync(join(tmpdir(), 'recs-verify-'));
const chrome = spawn(process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ['--headless=new', '--disable-gpu', '--hide-scrollbars', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, 'about:blank'], {stdio: 'ignore'});
const cleanup = () => { try { chrome.kill('SIGKILL'); } catch {} try { rmSync(profile, {recursive: true, force: true}); } catch {} };
process.on('exit', cleanup);
let target;
for (let i = 0; i < 60 && !target; i++) {
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(t => t.type === 'page'); } catch { await sleep(200); }
}
if (!target) { console.error('Chrome did not open a page'); process.exit(1); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r));
let id = 0; const pending = new Map(); const errors = [];
ws.addEventListener('message', ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push((m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text || '').split('\n')[0]);
  if (m.method === 'Network.responseReceived' && m.params.response.status >= 400) errors.push(`HTTP ${m.params.response.status} ${m.params.response.url}`);
});
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const read = async expr => {
  const r = await send('Runtime.evaluate', {expression: expr, returnByValue: true, awaitPromise: true});
  if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || 'evaluate failed');
  return r.result.result.value;
};
const mouse = async (x, y) => { for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', {type, x, y, button: 'left', clickCount: 1}); };
const click = async sel => {
  const b = await read(`(()=>{const e=[...document.querySelectorAll(${JSON.stringify(sel)})].find(e=>e.getClientRects().length&&!e.disabled); if(!e) return null; e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+Math.min(r.height/2, 20)}})()`);
  if (!b) throw new Error('nothing to click: ' + sel);
  await mouse(b.x, b.y); await sleep(400);
};
const waitFor = async (expr, label, ms = 120000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await read(expr)) return; await sleep(250); } throw new Error('timeout waiting for ' + label); };
function measure(doc) {
  const win = doc.defaultView, vw = doc.documentElement.clientWidth;
  const visible = e => { const r = e.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false; for (let n = e; n && n.nodeType === 1; n = n.parentElement) { const s = win.getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false; } return true; };
  const parse = c => { let m = c.match(/^rgba?\(([^)]+)\)$/); if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; } m = c.match(/^color\(srgb ([^)]+)\)$/); if (m) { const p = m[1].split(/[ /]+/).filter(Boolean).map(Number); return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1]; } return null; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const over = (top, under) => { const a = top[3]; return [top[0] * a + under[0] * (1 - a), top[1] * a + under[1] * (1 - a), top[2] * a + under[2] * (1 - a), 1]; };
  const backdrop = el => { const layers = []; for (let n = el; n && n.nodeType === 1; n = n.parentElement) { const s = win.getComputedStyle(n); if (s.backgroundImage !== 'none') return null; const c = parse(s.backgroundColor); if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } } let base = [255, 255, 255, 1]; for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base); return base; };
  let texts = 0, worst = 99, contrastFails = 0, under12 = 0, under24 = 0, small = 0, targets = 0;
  const sample = [], smallSample = [];
  for (const el of doc.body.querySelectorAll('*')) {
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) || !visible(el)) continue;
    const px = parseFloat(win.getComputedStyle(el).fontSize); if (px < 12) under12++;
    const s = win.getComputedStyle(el), fg = parse(s.color), bg = backdrop(el); if (!fg || !bg) continue;
    const text = over(fg, bg), r = ratio(text, bg), size = parseFloat(s.fontSize), large = size >= 24 || (size >= 18.66 && parseInt(s.fontWeight, 10) >= 700);
    texts++; worst = Math.min(worst, r); if (r < (large ? 3 : 4.5)) { contrastFails++; if (sample.length < 4) sample.push((el.textContent || '').trim().slice(0, 40) + ' ' + r.toFixed(2)); }
  }
  for (const e of [...doc.querySelectorAll('button,a[href],input:not([type=hidden]),select,textarea,summary,[role=button]')].filter(e => visible(e) && !e.matches('a.skip'))) {
    targets++; const r = e.getBoundingClientRect(), side = Math.min(r.width, r.height); if (side < 24) under24++; if (side < 44) { small++; if (smallSample.length < 6) smallSample.push({el: e.tagName + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : ''), text: (e.getAttribute('aria-label') || e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40), w: Math.round(r.width), h: Math.round(r.height)}); }
  }
  let bleed = 0;
  for (const el of doc.body.querySelectorAll('*')) {
    if (!visible(el)) continue; const r = el.getBoundingClientRect(); if (r.right <= vw + 1 || win.getComputedStyle(el).position === 'fixed') continue;
    let clipped = false;
    for (let n = el.parentElement; n && n !== doc.body; n = n.parentElement) { const s = win.getComputedStyle(n); if (s.overflowX !== 'visible' && n.getBoundingClientRect().right <= vw + 1) { clipped = true; break; } }
    if (!clipped) bleed++;
  }
  return {overflowX: Math.max(0, doc.documentElement.scrollWidth - vw), bleed, contrastFails, minContrast: +worst.toFixed(2), contrastSample: sample, under12, under24, smallTargets: small, smallSample, targets, texts};
}
const MEASURE = `(${measure.toString()})(document)`;
const report = {errors: [], checks: {}};
const fail = msg => { report.errors.push(msg); console.error('FAIL ' + msg); };
try {
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
  await send('Page.navigate', {url: URL_.replace(/\/$/, '') + '/lab'});
  await waitFor(`!!document.querySelector('#repo-path')`, 'picker', 20000);
  await read(`(()=>{const i=document.querySelector('#repo-path'); i.focus(); i.value=''; return 1})()`);
  await send('Input.insertText', {text: PROJECT});
  await send('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r'});
  await send('Input.dispatchKeyEvent', {type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13});
  await waitFor(`!!document.querySelector('#go-form')`, 'versions', 30000);
  await read(`(()=>{const set=(sel,needle)=>{const s=document.querySelector(sel),i=[...s.options].findIndex(o=>o.text.includes(needle)); if(i<0) throw new Error('no '+needle); s.selectedIndex=i; s.dispatchEvent(new Event('change',{bubbles:true}))}; set('#base',${JSON.stringify(BASE)}); set('#head',${JSON.stringify(HEAD)}); return 1})()`);
  await click('#go-form button[type=submit]');
  await waitFor(`!!window.PROTO && !!document.querySelector('.metrics')`, 'review', 300000);
  const opened = await read(`document.body.innerText.includes('Your comparison')`);
  if (!opened) fail('review did not say Your comparison');
  const summary = await read(`(()=>{const r=window.PROTO.reviews[0]; const items=r.recommendations||[]; const levels={}; items.forEach(i=>levels[i.level]=(levels[i.level]||0)+1); return {count:items.length, levels, priorities:items.map(i=>i.priority), has3:items.some(i=>i.level===3), generic:/improve performance|check your data/i.test(JSON.stringify(items)), score:/\\b0\\.\\d{2}\\b/.test(JSON.stringify(items)), selection:r.selection, reversed:!!r.reversed, repo:window.PROTO.project.repo}})()`);
  report.recommendations = summary;
  if (!summary.count) fail('the review has no recommendations');
  if (summary.has3) fail('a level 3 forecast was emitted');
  if (summary.generic) fail('generic advice was emitted');
  if (summary.score) fail('a numeric model score was emitted');
  await click('[data-action="review-tab"][data-tab="recommendations"]');
  await waitFor(`!!document.querySelector('.rec-card')`, 'recommendation card', 10000);
  const beforeId = await read(`document.querySelector('.rec-card').dataset.recId`);
  await click('.rec-card [data-action="rec-evidence"]');
  await click('.rec-card [data-action="rec-explain"]');
  await click('.rec-card [data-action="rec-plan"]');
  const expanded = await read(`(()=>{const card=document.querySelector('.rec-card'); const t=card.innerText; return {blocks:card.querySelectorAll('.rec-block').length, reasoning:/reasoning/i.test(t), risks:/Static analysis/.test(t), plan:/action plan/i.test(t), ai:/not an AI answer/i.test(document.querySelector('#content').innerText)}})()`);
  report.expanded = expanded;
  if (!expanded.reasoning || !expanded.risks || !expanded.plan || expanded.blocks < 2) fail('explain or action plan did not render the stored fields: ' + JSON.stringify(expanded));
  await read(`document.querySelectorAll('[data-action="rec-select"]')[0].click()`);
  await sleep(250);
  const picked = await read(`(()=>{const buttons=[...document.querySelectorAll('[data-action="rec-select"]')]; buttons[1].click(); return buttons.length})()`);
  await sleep(300);
  const compared = await read(`document.querySelectorAll('.compare-grid .rec-card').length`);
  report.compared = compared;
  if (picked < 2 || compared !== 2) fail('two recommendations were not compared side by side');
  const word = await read(`(()=>{const t=window.PROTO.reviews[0].recommendations[0].recommendation; const m=t.match(/[A-Za-z_][A-Za-z0-9_]{3,}/g)||[]; return m.find(w=>w.length>3)||m[0]||''})()`);
  await read(`(()=>{const i=document.querySelector('#rec-q'); i.value=${JSON.stringify(word)}; i.dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#rec-ask').requestSubmit(); return 1})()`);
  await sleep(400);
  const hit = await read(`document.querySelector('#rec-answer')?.innerText||''`);
  await read(`(()=>{const i=document.querySelector('#rec-q'); i.value='quantum blockchain'; document.querySelector('#rec-ask').requestSubmit(); return 1})()`);
  await sleep(400);
  const miss = await read(`document.querySelector('#rec-answer')?.innerText||''`);
  report.followup = {word, hit: hit.slice(0, 240), miss: miss.slice(0, 240)};
  if (!/not an AI answer/i.test(hit)) fail('a matching follow-up was not described as search: ' + hit.slice(0, 180));
  if (!/Nothing in this review/i.test(miss)) fail('an unknown follow-up invented an answer: ' + miss.slice(0, 180));
  const dismissedId = beforeId;
  await read(`document.querySelector('[data-rec-id="${dismissedId}"] [data-action="rec-dismiss"]').click()`);
  await sleep(300);
  const hidden = await read(`!document.querySelector('[data-rec-id="${dismissedId}"]')`);
  const storedBefore = await read(`(()=>{const key=Object.keys(localStorage).find(k=>k.startsWith('repotrace-recs:')); const row=key?JSON.parse(localStorage.getItem(key))[ ${JSON.stringify(dismissedId)} ]:null; return {key, dismissed:!!(row&&row.dismissed)}})()`);
  report.dismiss = {id: dismissedId, hidden, storedBefore};
  if (!hidden || !storedBefore.dismissed) fail('dismiss did not hide the item or did not save it');
  const stashed = await read(`(()=>{try{sessionStorage.setItem('rt-verify-proto', JSON.stringify(window.PROTO)); return sessionStorage.getItem('rt-verify-proto').length}catch(e){return 'ERR '+e.message}})()`);
  report.stashed = stashed;
  if (String(stashed).startsWith('ERR')) fail('could not keep the review across reload: ' + stashed);
  await send('Page.reload', {ignoreCache: true});
  await waitFor(`!!document.querySelector('#repo-path')`, 'picker after reload', 20000);
  const storedAfter = await read(`(()=>{const key=Object.keys(localStorage).find(k=>k.startsWith('repotrace-recs:')); const row=key?JSON.parse(localStorage.getItem(key))[${JSON.stringify(dismissedId)}]:null; return !!(row&&row.dismissed)})()`);
  report.dismiss.storedAfterReload = storedAfter;
  if (!storedAfter) fail('dismiss did not survive reload');
  await read(`(()=>{const raw=sessionStorage.getItem('rt-verify-proto'); if(!raw) throw new Error('stashed review missing'); window.PROTO=JSON.parse(raw); window.RTLab(window.PROTO); return 1})()`);
  await waitFor(`!!document.querySelector('[data-action="review-tab"][data-tab="recommendations"]')`, 'review after restore', 15000);
  await click('[data-action="review-tab"][data-tab="recommendations"]');
  await sleep(300);
  const stillHidden = await read(`!document.querySelector('[data-rec-id="${dismissedId}"]')`);
  await click('[data-action="rec-show-dismissed"]');
  await sleep(300);
  const restored = await read(`document.querySelector('[data-rec-id="${dismissedId}"]')?.dataset.dismissed||''`);
  report.dismiss.hiddenAfterReload = stillHidden;
  report.dismiss.shownDismissed = restored;
  if (!stillHidden || restored !== 'true') fail('the dismissed recommendation did not stay dismissed after reload');
  await click('#navigation [data-action="settings"], .dock [data-action="settings"]');
  await waitFor(`!!document.querySelector('#integrations')`, 'integrations', 10000);
  await waitFor(`/Connected to this local folder|Not a Git repository|Git status unavailable|nothing to inspect/.test(document.querySelector('#git-live')?.innerText||'')`, 'git status', 20000);
  const git = await read(`(()=>{const t=document.querySelector('#integrations').innerText; return {text:t.slice(0, 1200), buttons:[...document.querySelectorAll('#integrations button')].map(b=>b.textContent.trim()), connect:[...document.querySelectorAll('#integrations button')].some(b=>/connect/i.test(b.textContent)&&!/local git/i.test(b.textContent))}})()`);
  report.integrations = git;
  if (!git.text.includes(PROJECT)) fail('integrations does not show the opened folder');
  if (!/Connected to this local folder/.test(git.text)) fail('local git did not report connected: ' + git.text.slice(0, 400));
  if (!/Branch/.test(git.text) || !/Recent commits listed/.test(git.text)) fail('local git status is missing branch or commit count');
  if (/GitHub is connected|Connected to GitHub|GitHub connected/i.test(git.text)) fail('the page claims GitHub is connected');
  if (!/are not connected/i.test(git.text) || !/OAuth/i.test(git.text)) fail('the OAuth limitation is not stated');
  if (git.connect) fail('a Connect button is present');
  await read(`(()=>{window.__files=[]; const orig=URL.createObjectURL; URL.createObjectURL=function(blob){window.__files.push(blob); return orig.call(URL, blob)}; return 1})()`);
  await click('[data-action="export-json"]');
  await click('[data-action="export-csv"]');
  await sleep(300);
  const exported = await read(`(async()=>{const files=await Promise.all(window.__files.map(b=>b.text())); const json=files.find(t=>t.trim().startsWith('{')); const csv=files.find(t=>t.startsWith('section,')); let parsed=null; try{parsed=JSON.parse(json)}catch(e){} return {n:files.length, json:!!parsed, recommendations:parsed?parsed.recommendations.length:0, changed:parsed?parsed.changed.length:0, csv:!!csv, csvHasRecommendation:!!(csv&&csv.includes('\\nrecommendation,')), upload:/upload|github\\.com|api\\.github/i.test(files.join('\\n').slice(0, 500))}})()`);
  report.export = exported;
  if (!exported.json || !exported.csv || !exported.csvHasRecommendation || exported.recommendations < 1) fail('export did not contain the review: ' + JSON.stringify(exported));
  const ran = await read(`(()=>{const orig=RTPicker.run; let body=null; RTPicker.run=function(b){body=b}; const btn=document.querySelector('[data-action="run-again"]'); btn.click(); RTPicker.run=orig; return body})()`);
  report.runAgain = ran;
  if (!ran || ran.repo !== summary.repo || ran.base !== summary.selection.base || ran.head !== summary.selection.head || !!ran.reverse !== summary.reversed) fail('run again did not call RTPicker.run with the stored selection: ' + JSON.stringify(ran));
  report.measure = {};
  for (const [name, width, height, mobile] of [['desktop', 1440, 900, false], ['phone', 390, 844, true]]) {
    await send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: mobile ? 2 : 1, mobile});
    await sleep(400);
    await click('#navigation [data-action="reviews"], .dock [data-action="reviews"]');
    await waitFor(`!!document.querySelector('[data-action="review-tab"][data-tab="recommendations"]')`, name + ' review', 10000);
    await click('[data-action="review-tab"][data-tab="recommendations"]');
    await sleep(250);
    report.measure[name + '-recommendations'] = await read(MEASURE);
    await click('#navigation [data-action="settings"], .dock [data-action="settings"]');
    await sleep(250);
    report.measure[name + '-settings'] = await read(MEASURE);
  }
  await send('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
  await sleep(300);
  await click('#navigation [data-action="reviews"]');
  await waitFor(`!!document.querySelector('[data-action="review-tab"][data-tab="recommendations"]')`, 'review before themes', 10000);
  const themes = await read(`RTTheme.themes.map(t=>t.id)`);
  const saved = await read(`RTTheme.current()`);
  report.themes = {};
  for (const theme of themes) {
    await read(`RTTheme.apply(${JSON.stringify(theme)}); 1`);
    await sleep(120);
    await click('#navigation [data-action="reviews"]');
    await waitFor(`!!document.querySelector('[data-action="review-tab"][data-tab="recommendations"]')`, theme + ' review', 10000);
    await click('[data-action="review-tab"][data-tab="recommendations"]');
    const rec = await read(MEASURE);
    await click('#navigation [data-action="settings"]');
    const set = await read(MEASURE);
    report.themes[theme] = {recommendations: rec.contrastFails, settings: set.contrastFails, sample: rec.contrastSample.concat(set.contrastSample).slice(0, 3)};
  }
  await read(`RTTheme.apply(${JSON.stringify(saved)}); 1`);
  await send('Emulation.clearDeviceMetricsOverride');
  const pageErrors = errors.filter(e => !/favicon/i.test(e));
  report.console = pageErrors;
  if (pageErrors.length) fail('console or request errors: ' + pageErrors.slice(0, 6).join(' | '));
  for (const [name, row] of Object.entries(report.measure)) {
    if (row.overflowX || row.bleed) fail(name + ' scrolls sideways');
    if (row.under12) fail(name + ' has text under 12px: ' + row.under12);
    if (row.under24) fail(name + ' has a control under 24px');
    const mine = (row.smallSample || []).filter(s => /evidence|explain|compare|dismiss|save|search this review|refresh local|export review|run this comparison|mark completed|action plan|show dismissed|restore/i.test(s.text || ''));
    if (mine.length) fail(name + ' new controls under 44px: ' + JSON.stringify(mine));
  }
  const themeFails = Object.entries(report.themes).filter(([, row]) => row.recommendations || row.settings);
  report.themeFailures = themeFails.length;
  if (themeFails.length) fail('contrast failed: ' + themeFails.map(([t, row]) => t + ' rec ' + row.recommendations + ' settings ' + row.settings + ' ' + row.sample.join(';')).join(' | '));
} catch (e) {
  fail(e.message || String(e));
}
report.ok = report.errors.length === 0;
writeFileSync(join(OUT, 'report.json'), JSON.stringify(report, null, 1));
console.log(JSON.stringify({ok: report.ok, errors: report.errors, recommendations: report.recommendations, dismiss: report.dismiss, export: report.export, runAgain: report.runAgain, measure: report.measure, themeFailures: report.themeFailures, console: report.console}, null, 1));
cleanup();
process.exit(report.ok ? 0 : 1);
