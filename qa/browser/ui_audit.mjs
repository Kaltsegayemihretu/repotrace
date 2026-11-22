// Visual and measurable audit of the LIVE guided screens, in headless Chrome, at four screen sizes and every theme.
//   node --experimental-websocket qa/browser/ui_audit.mjs OUTDIR URL PROJECT_FOLDER [BASE_TEXT HEAD_TEXT]
//   PROTO_JSON=OUTDIR/proto.json node --experimental-websocket qa/browser/ui_audit.mjs OUTDIR URL     (skips the analysis: reuses a saved result)
// It opens the project, runs one real comparison, walks every screen at desktop, laptop, tablet and phone sizes, reverses the change,
// then sweeps all themes. For each screen it measures: sideways overflow, text contrast, tap-target size, tiny text and clipped text.
// Screenshots and report.json go to OUTDIR. Exit code 1 if a hard requirement fails.
import {spawn} from 'node:child_process';
import {writeFileSync, readFileSync, mkdtempSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const [OUT, URL_, PROJECT = '', BASE = '', HEAD = ''] = process.argv.slice(2);
const PROTO_JSON = process.env.PROTO_JSON || '';
mkdirSync(OUT, {recursive: true});
const sleep = ms => new Promise(r => setTimeout(r, ms));
const PORT = 9400 + Math.floor(Math.random() * 90);
const profile = mkdtempSync(join(tmpdir(), 'uiaudit-'));
const chrome = spawn(process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ['--headless=new', '--disable-gpu', '--hide-scrollbars', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, 'about:blank'], {stdio: 'ignore'});
const cleanup = () => { try { chrome.kill('SIGKILL'); } catch {} try { rmSync(profile, {recursive: true, force: true}); } catch {} };
process.on('exit', cleanup);
let target; for (let i = 0; i < 60 && !target; i++) { try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(t => t.type === 'page'); } catch { await sleep(200); } }
const ws = new WebSocket(target.webSocketDebuggerUrl); await new Promise(r => ws.addEventListener('open', r));
let id = 0; const pending = new Map(); const errors = [];
ws.addEventListener('message', ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description?.split('\n')[0] || m.params.exceptionDetails.text);
  if (m.method === 'Network.responseReceived' && m.params.response.status >= 400) errors.push(`HTTP ${m.params.response.status} ${m.params.response.url}`);
});
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({id: i, method, params})); });
const read = async e => { const r = await send('Runtime.evaluate', {expression: e, returnByValue: true, awaitPromise: true}); if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || 'evaluate failed'); return r.result.result.value; };
let shots = 0; const shot = async name => { const r = await send('Page.captureScreenshot', {format: 'png'}); writeFileSync(join(OUT, String(++shots).padStart(2, '0') + '-' + name + '.png'), Buffer.from(r.result.data, 'base64')); };
const mouse = async (x, y) => { for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', {type, x, y, button: 'left', clickCount: 1}); };
const KEYS = {Enter: 13, Escape: 27, Tab: 9};
const key = async k => { for (const type of ['keyDown', 'keyUp']) await send('Input.dispatchKeyEvent', {type, key: k, code: k, windowsVirtualKeyCode: KEYS[k], ...(k === 'Enter' && type === 'keyDown' ? {text: '\r'} : {})}); await sleep(250); };
const need = (ok, msg) => { if (!ok) throw new Error(msg); };
const click = async (sel, text) => {
  const b = await read(`(()=>{const e=[...document.querySelectorAll(${JSON.stringify(sel)})].filter(e=>e.getClientRects().length&&(!${JSON.stringify(text || '')}||(e.innerText||e.textContent||'').includes(${JSON.stringify(text || '')})))[0]; if(!e) return null; e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
  need(b, `nothing to click: ${sel} "${text || ''}"`); await sleep(80); await mouse(b.x, b.y); await sleep(650);
};
const waitFor = async (expr, label, ms = 120000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await read(expr)) return; await sleep(200); } throw new Error('timeout waiting for ' + label); };

// ---------------------------------------------------------------- measurements, run inside the page
function measure(doc) {
  const win = doc.defaultView, vw = doc.documentElement.clientWidth;
  const visible = e => { const r = e.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false; for (let n = e; n && n.nodeType === 1; n = n.parentElement) { const s = win.getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false; } return true; };
  const parse = c => { let m = c.match(/^rgba?\(([^)]+)\)$/); if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
    m = c.match(/^color\(srgb ([^)]+)\)$/); if (m) { const p = m[1].split(/[ /]+/).filter(Boolean).map(Number); return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1]; } return null; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const over = (top, under) => { const a = top[3]; return [top[0] * a + under[0] * (1 - a), top[1] * a + under[1] * (1 - a), top[2] * a + under[2] * (1 - a), 1]; };
  const backdrop = el => { const layers = []; for (let n = el; n && n.nodeType === 1; n = n.parentElement) { const s = win.getComputedStyle(n); if (s.backgroundImage !== 'none') return null; const c = parse(s.backgroundColor); if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } }
    let base = [255, 255, 255, 1]; for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base); return base; };
  const label = e => (e.getAttribute('aria-label') || e.title || e.textContent || e.value || e.tagName).trim().replace(/\s+/g, ' ').slice(0, 36);
  const where = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
  // 1 contrast of every visible piece of text against what is really behind it
  const fails = []; let texts = 0, worst = 99;
  for (const el of doc.body.querySelectorAll('*')) {
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    if (!visible(el) || el.closest('[disabled],[aria-disabled=true],option,script,style')) continue;
    const s = win.getComputedStyle(el), fg = parse(s.color), bg = backdrop(el); if (!fg || !bg) continue;
    const text = over(fg, bg), r = ratio(text, bg), size = parseFloat(s.fontSize), large = size >= 24 || (size >= 18.66 && parseInt(s.fontWeight) >= 700), need = large ? 3 : 4.5;
    texts++; worst = Math.min(worst, r);
    if (r < need) fails.push({el: where(el), text: label(el), ratio: +r.toFixed(2), need});
  }
  // 2 tap targets
  const small = [], targets = [...doc.querySelectorAll('button,a[href],input:not([type=hidden]),select,textarea,summary,[role=button]')].filter(e => visible(e) && !e.matches('a.skip'));
  let under24 = 0;
  for (const e of targets) { const r = e.getBoundingClientRect(), side = Math.min(r.width, r.height); if (side < 24) under24++; if (side < 44) small.push({el: where(e), text: label(e), w: Math.round(r.width), h: Math.round(r.height)}); }
  // 3 tiny text, 4 sideways overflow and clipped text
  const tiny = []; let under12 = 0; for (const el of doc.body.querySelectorAll('*')) { if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) || !visible(el)) continue; const px = parseFloat(win.getComputedStyle(el).fontSize); if (px < 12) under12++; if (px < 11) tiny.push({el: where(el), text: label(el), px}); }
  const bleed = []; for (const el of doc.body.querySelectorAll('*')) { if (!visible(el)) continue; const r = el.getBoundingClientRect(); if (r.right <= vw + 1 || win.getComputedStyle(el).position === 'fixed') continue; let clipped = false; for (let n = el.parentElement; n && n !== doc.body; n = n.parentElement) { const s = win.getComputedStyle(n); if (s.overflowX !== 'visible' && n.getBoundingClientRect().right <= vw + 1) { clipped = true; break; } } if (!clipped) bleed.push({el: where(el), right: Math.round(r.right)}); }
  const cut = []; for (const el of doc.body.querySelectorAll('*')) { if (!visible(el) || el.scrollWidth <= el.clientWidth + 1 || el.clientWidth < 20) continue; const s = win.getComputedStyle(el); if (['hidden', 'clip'].includes(s.overflowX) && s.textOverflow !== 'ellipsis' && (el.textContent || '').trim()) cut.push({el: where(el), text: label(el), box: el.clientWidth, content: el.scrollWidth}); }
  return {vw, overflowX: Math.max(0, doc.documentElement.scrollWidth - vw), texts, minContrast: +worst.toFixed(2), contrastFails: fails.length, contrastSample: fails.slice(0, 6),
    targets: targets.length, smallTargets: small.length, under24, smallSample: small.slice(0, 12), tinyText: tiny.length, under12: under12, tinySample: tiny.slice(0, 4), bleed: bleed.length, bleedSample: bleed.slice(0, 4), clipped: cut.length, clippedSample: cut.slice(0, 4)};
}
const MEASURE = `(${measure.toString()})(document)`;
const MEASURE_FRAME = `(()=>{const f=document.querySelector('#xp'); if(!f||!f.contentDocument||!f.contentDocument.body) return null; return (${measure.toString()})(f.contentDocument)})()`;

// ---------------------------------------------------------------- the run
const SIZES = {desktop: [1440, 900, 1, false], laptop: [1024, 768, 1, false], tablet: [768, 1024, 2, true], phone: [390, 844, 2, true]};
const setSize = async name => { const [width, height, deviceScaleFactor, mobile] = SIZES[name]; await send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor, mobile}); await sleep(450); };
const results = {}, log = [], hard = [];
const step = async (name, fn) => { try { await fn(); log.push('PASS ' + name); } catch (e) { log.push('FAIL ' + name + ' :: ' + e.message); hard.push(name); } };
const ensure = (ok, name, detail = '') => { log.push((ok ? 'PASS ' : 'FAIL ') + name + (ok ? '' : ' :: ' + detail)); if (!ok) hard.push(name); };

await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
await send('Page.addScriptToEvaluateOnNewDocument', {source: `window.__cls=0;try{new PerformanceObserver(l=>{for(const e of l.getEntries()){if(!e.hadRecentInput)window.__cls+=e.value}}).observe({type:'layout-shift',buffered:true})}catch(e){}`});
await setSize('desktop');
await send('Page.navigate', {url: URL_.replace(/\/$/, '') + '/lab'}); await sleep(1800);

const NAV = {start: '#navigation [data-action="start"], .dock [data-action="start"]', review: '#navigation [data-action="reviews"], .dock [data-action="reviews"]',
  explore: '#navigation [data-action="map-home"], .dock [data-action="map-home"]', files: '#navigation [data-action="files"], .dock [data-action="files"]', settings: '#navigation [data-action="settings"], .dock [data-action="settings"]'};
const openReview = async () => { await click(NAV.review); if (await read(`!!document.querySelector('[data-action="review"][data-index="0"]')&&!document.querySelector('[data-action="review-tab"]')`)) await click('[data-action="review"][data-index="0"]'); };
const SCREENS = [['start', async () => click(NAV.start)], ['review-overview', async () => { await openReview(); await click('[data-action="review-tab"][data-tab="overview"]'); }],
  ['review-changes', async () => click('[data-action="review-tab"][data-tab="changes"]')], ['review-tests', async () => click('[data-action="review-tab"][data-tab="tests"]')],
  ['review-limits', async () => click('[data-action="review-tab"][data-tab="limits"]')], ['explore', async () => { await click(NAV.explore); await sleep(1600); }],
  ['files', async () => click(NAV.files)], ['settings', async () => click(NAV.settings)]];

if (PROTO_JSON) {
  await waitFor(`!!document.querySelector('#repo-path')`, 'picker');
  await read(`window.PROTO=${readFileSync(PROTO_JSON, 'utf8')}; window.RTLab(window.PROTO); 1`); await sleep(900);
} else {
  await step('the first screen asks for a project and loads no data', async () => { need(await read(`!!document.querySelector('#repo-path')`), 'no folder box'); need(!(await read(`!!window.PROTO`)), 'data before choosing'); });
  results.open = {}; for (const size of Object.keys(SIZES)) { await setSize(size); await shot('open-' + size); results.open[size] = await read(MEASURE); } await setSize('desktop');
  await step('typing a folder lists its versions', async () => {
    await read(`(()=>{const i=document.querySelector('#repo-path'); i.focus(); i.value=''; return 1})()`); await send('Input.insertText', {text: PROJECT}); await key('Enter');
    await waitFor(`!!document.querySelector('#go-form')`, 'version step'); need(/commits? found/.test(await read(`document.querySelector('#content').innerText`)), 'no commit count');
  });
  results.versions = {}; for (const size of Object.keys(SIZES)) { await setSize(size); await shot('versions-' + size); results.versions[size] = await read(MEASURE); } await setSize('desktop');
  await step('versions: the swap control and the Yes/No answer stay in step', async () => {
    const say = () => read(`({yes:document.querySelector('input[name=reverse][value=yes]').checked, roles:[...document.querySelectorAll('#pair .role')].map(e=>e.textContent).join('>'), line:document.querySelector('#direction').textContent})`);
    const a = await say(); need(!a.yes && a.roles === 'Start>End' && /^No\./.test(a.line), 'starts as ' + JSON.stringify(a));
    await click('#swap'); const b = await say(); need(b.yes && b.roles === 'End>Start' && /^Yes\./.test(b.line) && /not modified/.test(b.line), 'after swap ' + JSON.stringify(b)); await shot('versions-reversed-desktop');
    await click('#swap'); const c = await say(); need(!c.yes && c.roles === 'Start>End', 'after second swap ' + JSON.stringify(c));
  });
  if (BASE && HEAD) await read(`(()=>{const set=(sel,needle)=>{const s=document.querySelector(sel),i=[...s.options].findIndex(o=>o.text.includes(needle)); if(i<0) throw new Error('no '+needle); s.selectedIndex=i; s.dispatchEvent(new Event('change',{bubbles:true}))}; set('#base',${JSON.stringify(BASE)}); set('#head',${JSON.stringify(HEAD)}); return 1})()`);
  // the progress screen: sample it for the whole run
  const samples = []; let clsBefore = await read('window.__cls');
  await click('#go-form button[type=submit]');
  await waitFor(`!!document.querySelector('#progress-list')`, 'progress screen', 10000);
  await read(`document.querySelector('#progress-list').dataset.probe='1'; 1`);
  let lastNow = -1, started = Date.now();
  while (true) {
    const s = await read(`(()=>{if(window.PROTO&&!document.querySelector('#progress-list')) return {finished:true}; const ol=document.querySelector('#progress-list'); if(!ol) return {gone:true}; return {probe:ol.dataset.probe==='1', now:[...ol.querySelectorAll('li')].findIndex(l=>l.classList.contains('now')), done:[...ol.querySelectorAll('li')].filter(l=>l.classList.contains('done')).length, total:ol.querySelectorAll('li').length, fill:parseFloat((document.querySelector('#progress-fill')?.style.transform||'scaleX(0)').replace(/[^0-9.]/g,'')), elapsed:document.querySelector('[data-elapsed]')?.textContent||''}})()`);
    if (s.gone) { const page = await read(`document.querySelector('#content').innerText.slice(0, 400)`); await shot('progress-vanished'); throw new Error('the progress screen vanished without a result. The page says: ' + page.replace(/\s+/g, ' ')); }
    if (s.finished) break; samples.push({t: Date.now() - started, ...s});
    if (s.now !== lastNow) { lastNow = s.now; await sleep(350); await shot('progress-step' + (s.now + 1)); }
    if (Date.now() - started > 300000) throw new Error('analysis took over five minutes');
    await sleep(220);
  }
  const clsDuring = (await read('window.__cls')) - clsBefore;
  const nows = samples.map(s => s.now), dones = samples.map(s => s.done), fills = samples.map(s => s.fill);
  const mono = a => a.every((v, i) => i === 0 || v >= a[i - 1]);
  results.progress = {samples: samples.length, seconds: Math.round((Date.now() - started) / 1000), steps: samples[0]?.total, distinctStates: new Set(samples.map(s => s.now + ':' + s.done)).size, listKeptItsElement: samples.every(s => s.probe), activeStepNeverMovesBack: mono(nows), doneStepsNeverUndone: mono(dones), barNeverMovesBack: mono(fills), clockValues: new Set(samples.map(s => s.elapsed)).size, stepsSeenActive: new Set(nows).size, cls: +clsDuring.toFixed(4)};
  ensure(results.progress.listKeptItsElement, 'progress: the list is updated in place, never rebuilt');
  ensure(results.progress.activeStepNeverMovesBack, 'progress: the active step only moves forward', JSON.stringify(nows));
  ensure(results.progress.doneStepsNeverUndone, 'progress: finished steps stay finished');
  ensure(results.progress.barNeverMovesBack, 'progress: the bar never moves back', JSON.stringify(fills.filter((v, i) => i && v < fills[i - 1])));
  ensure(results.progress.cls < 0.05, 'progress: no layout shift while it runs', String(results.progress.cls));
  ensure(results.progress.clockValues > 5, 'progress: the clock keeps counting', String(results.progress.clockValues));
  ensure(results.progress.stepsSeenActive >= 5, 'progress: each step is seen running, in order', JSON.stringify([...new Set(nows)]));
  await step('the analysis finishes and the review opens first', async () => { need(await read(`!!window.PROTO`), 'no result'); await waitFor(`!!document.querySelector('.metrics')`, 'review'); });
  writeFileSync(join(OUT, 'proto.json'), JSON.stringify(await read('window.PROTO')));
}

// every screen, at every size
for (const size of Object.keys(SIZES)) {
  await setSize(size);
  for (const [name, go] of SCREENS) {
    await step(`${size}: ${name} opens`, go); await sleep(500);
    await shot(`${name}-${size}`);
    (results[name] ||= {})[size] = await read(MEASURE);
    if (name === 'explore') results[name][size + 'Frame'] = await read(MEASURE_FRAME);
  }
}

// reverse: the same two commits, the other way, from the finished review
if (!PROTO_JSON) {
  await setSize('desktop'); await openReview();
  const before = await read(`(()=>{const r=window.PROTO.reviews[0]; return {base:r.base_commit, head:r.head_commit, reversed:!!r.reversed, selection:r.selection, label:r.label, changed:r.changed.map(c=>c.name+':'+c.change).sort().join(',')}})()`);
  await step('reverse: the review offers the opposite direction', async () => { need(await read(`!!document.querySelector('[data-action="reverse-change"]')`), 'no reverse button'); });
  await click('[data-action="reverse-change"]'); await waitFor(`!!document.querySelector('#progress-list')`, 'reverse progress', 10000); await shot('reverse-progress-desktop');
  await waitFor(`!document.querySelector('#progress-list') && !!document.querySelector('.metrics')`, 'reversed review');
  const after = await read(`(()=>{const r=window.PROTO.reviews[0]; return {base:r.base_commit, head:r.head_commit, reversed:!!r.reversed, selection:r.selection, label:r.label, changed:r.changed.map(c=>c.name+':'+c.change).sort().join(','), notice:document.querySelector('.reverse-banner')?.innerText||'', button:document.querySelector('[data-action="reverse-change"]')?.innerText||''}})()`);
  await shot('reverse-review-desktop'); results.reverse = {before, after};
  ensure(after.reversed && !before.reversed, 'reverse: the review says it is reversed');
  ensure(after.base === before.head && after.head === before.base, 'reverse: the commits were swapped exactly once', JSON.stringify({before, after}));
  ensure(JSON.stringify(after.selection) === JSON.stringify(before.selection), 'reverse: the chosen pair is remembered');
  ensure(/revers/i.test(after.notice) && /revers|original/i.test(after.button), 'reverse: the screen says so and offers the way back', after.notice + ' | ' + after.button);
  ensure(await read(`document.querySelector('[data-action="reverse-change"]').getAttribute('aria-checked')`) === 'true', 'reverse: the switch reads Yes');
  await click(NAV.explore); await waitFor(`!!(document.querySelector('#xp')&&document.querySelector('#xp').contentWindow.__xp)`, 'explorer after reverse', 20000); await sleep(1200);
  const xp = await read(`({holders:document.querySelectorAll('#xp-holder').length, frames:document.querySelectorAll('iframe#xp').length, reversed:!!document.querySelector('#xp').contentWindow.__xp.D.reviews[0].reversed, base:document.querySelector('#xp').contentWindow.__xp.D.reviews[0].base_commit})`);
  await shot('reverse-explore-desktop'); results.reverse.explorer = xp;
  ensure(xp.holders === 1 && xp.frames === 1, 'reverse: one explorer, no stale frame left behind', JSON.stringify(xp));
  ensure(xp.reversed && xp.base === after.base, 'reverse: the explorer shows the reversed comparison', JSON.stringify(xp));
  await setSize('phone'); await openReview(); await shot('reverse-review-phone'); results.reverse.phone = await read(MEASURE); await setSize('desktop'); await openReview();
  await click('[data-action="reverse-change"]'); await waitFor(`!!document.querySelector('#progress-list')`, 'return progress', 10000);
  await waitFor(`!document.querySelector('#progress-list') && !!document.querySelector('.metrics')`, 'original review');
  const back = await read(`(()=>{const r=window.PROTO.reviews[0]; return {base:r.base_commit, head:r.head_commit, reversed:!!r.reversed}})()`);
  ensure(!back.reversed && back.base === before.base && back.head === before.head, 'reverse: asking again returns to the original direction', JSON.stringify(back));
}

// every theme, on the screens that carry the most text
await setSize('desktop');
const themes = await read(`RTTheme.themes.map(t=>t.id)`); results.themes = {};
for (const theme of themes) {
  await read(`RTTheme.apply(${JSON.stringify(theme)}); 1`); await sleep(260);
  const row = {};
  for (const [name, go] of SCREENS.filter(([n]) => ['review-overview', 'review-changes', 'review-tests', 'settings'].includes(n))) { await go(); await sleep(260); const m = await read(MEASURE); row[name] = {fails: m.contrastFails, min: m.minContrast, sample: m.contrastSample.slice(0, 2)}; }
  results.themes[theme] = row;
}
await read(`RTTheme.apply('github-dark'); 1`);

// verdicts
const rows = [];
for (const [name, bySize] of Object.entries(results)) { if (!['start', 'review-overview', 'review-changes', 'review-tests', 'review-limits', 'explore', 'files', 'settings', 'open', 'versions'].includes(name)) continue;
  for (const [size, m] of Object.entries(bySize)) { if (!m || size.endsWith('Frame')) continue; rows.push({screen: name, size, overflowX: m.overflowX, bleed: m.bleed, clipped: m.clipped, contrastFails: m.contrastFails, minContrast: m.minContrast, smallTargets: m.smallTargets, under24: m.under24, targets: m.targets, tinyText: m.tinyText, under12: m.under12}); } }
const sum = k => rows.reduce((n, r) => n + r[k], 0);
ensure(rows.every(r => r.overflowX === 0 && r.bleed === 0), 'no screen scrolls sideways at any size', JSON.stringify(rows.filter(r => r.overflowX || r.bleed).map(r => `${r.screen}@${r.size}: ${r.overflowX}px/${r.bleed}`)));
const themeFails = Object.entries(results.themes).flatMap(([t, row]) => Object.entries(row).filter(([, v]) => v.fails > 0).map(([s, v]) => `${t}/${s}: ${v.fails} (min ${v.min})`));
ensure(themeFails.length === 0, 'text contrast passes WCAG AA on every theme and screen', themeFails.join('; '));
ensure(errors.length === 0, 'no script errors or failed requests', errors.slice(0, 5).join(' | '));
const summary = {screens: rows.length, totalContrastFails: sum('contrastFails'), totalSmallTargets: sum('smallTargets'), totalTargets: sum('targets'), totalUnder24: sum('under24'), totalTinyText: sum('tinyText'), totalUnder12: sum('under12'), totalClipped: sum('clipped'), phoneSmallTargets: rows.filter(r => r.size === 'phone').reduce((n, r) => n + r.smallTargets, 0), phoneTargets: rows.filter(r => r.size === 'phone').reduce((n, r) => n + r.targets, 0), themeFailures: themeFails.length, errors: errors.length};
writeFileSync(join(OUT, 'report.json'), JSON.stringify({summary, rows, results, log, errors}, null, 1));
console.log(log.join('\n')); console.log('\nSUMMARY ' + JSON.stringify(summary)); console.table(rows.filter(r => ['desktop', 'phone'].includes(r.size)));
if (results.progress) console.log('PROGRESS ' + JSON.stringify(results.progress));
cleanup(); process.exit(hard.length ? 1 : 0);
