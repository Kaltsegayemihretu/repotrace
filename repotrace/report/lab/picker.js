// Live start: choose a project folder, choose two versions to compare (or none), wait for the analysis, then start the Lab on the result.
// Talks only to the local app (same origin) with the per-launch token. Nothing here reads files or runs code.
(function (root) {
  'use strict';
  const $ = id => document.getElementById(id);
  const h = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const token = (document.querySelector('meta[name=rt-token]') || {}).content || '';
  const ico = name => `<svg class="ico" aria-hidden="true" focusable="false"><use href="#i-${name}"/></svg>`;
  const calm = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // The steps, in the order the engine really runs them. A reversed run reads the newer version first, so the names say so.
  const STEPS = {check: 'Check the project', map: 'Map the working folder', analyze_before: 'Read the older version', analyze_after: 'Read the newer version',
    compare: 'Compare the two versions', impact: 'Find what could be affected', finish: 'Prepare the screens'};
  const REVERSED = {analyze_before: 'Read the starting version (the newer one)', analyze_after: 'Read the ending version (the older one)'};
  const FLOW = {compare: ['check', 'map', 'analyze_before', 'analyze_after', 'compare', 'impact', 'finish'], explore: ['check', 'map', 'finish']};
  const note = detail => {                                        // the part of the engine's sentence worth showing under a step
    const commit = String(detail || '').match(/\(([0-9a-f]{7,40})\)/i);
    if (commit) return `Commit ${commit[1].slice(0, 8)}`;
    const count = String(detail || '').match(/\((\d+) of (\d+)\)/);
    return count ? `${count[1]} of ${count[2]} changed definitions` : '';
  };
  const clock = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  const took = seconds => seconds < 60 ? `${seconds.toFixed(1)} s` : clock(seconds);

  async function api(path, body) {
    const res = await fetch(path, {method: body ? 'POST' : 'GET', headers: {'X-RepoTrace-Token': token, ...(body ? {'Content-Type': 'application/json'} : {})}, body: body ? JSON.stringify(body) : undefined});
    const data = await res.json().catch(() => ({error: 'The app answered in a way this page cannot read.'}));
    if (!res.ok) throw new Error(data.error || `The request failed (${res.status}).`);
    return data;
  }
  const page = html => { $('content').innerHTML = `<div class="compact picker">${html}</div>`; $('content').focus({preventScroll: true}); };
  const busy = on => { const shell = document.querySelector('.shell'); if (shell) { if (on) shell.dataset.busy = '1'; else delete shell.dataset.busy; } };
  // An error that has a way out. `back` says where the button goes.
  const failPage = (message, label, back) => {
    busy(false);
    page(`<div class="notice"><strong>${h(message)}</strong></div><div class="action-row"><button class="primary" id="again">${h(label)}</button></div>`);
    $('again').addEventListener('click', back);
  };
  const fail = message => { const box = $('picker-error'); if (box) { box.textContent = message; box.hidden = false; box.scrollIntoView({block: 'nearest'}); } else failPage(message, 'Back', () => (root.PROTO ? root.RTLab(root.PROTO) : start(root.RTLab))); };

  function start(boot) {
    $('snapshot').textContent = 'Local · nothing is uploaded';
    $('location').textContent = 'Open a project';
    $('navigation').innerHTML = '';
    for (const id of ['project-card', 'rail-label']) { const el = $(id); if (el) el.hidden = true; }      // no project yet, so no project card
    document.querySelectorAll('[data-project]').forEach(el => { el.textContent = ''; });
    $('back').disabled = true;
    stepOne(boot);
  }

  async function stepOne(boot) {
    let info = {recent: [], can_browse: false, initial_repo: null};
    try { info = await api('/api/state'); } catch (e) { /* the form still works without the recent list */ }
    const recent = info.recent.length ? `<section class="recent" aria-labelledby="recent-title"><h2 id="recent-title">Recent projects</h2><div class="recent-grid">${info.recent.map(r => `<button type="button" class="recent-card" data-path="${h(r.path)}"><span class="recent-icon" aria-hidden="true">${h((r.name || '?')[0].toUpperCase())}</span><span class="recent-text"><strong>${h(r.name)}</strong><small>${h(r.path)}</small></span><span class="recent-go" aria-hidden="true">${ico('arrow')}</span></button>`).join('')}</div></section>` : '';
    page(`<section class="open-hero"><span class="eyebrow">LOCAL · READ-ONLY</span><h1>Find what a change left behind.</h1>
      <p class="lead">Open a Python project. RepoTrace reads its code and Git history, then shows what a change could affect and which tests to run first. It never runs your code and never changes your repository.</p>
      <form id="open-form" class="search-form open-form"><span class="search-icon" aria-hidden="true">${ico('folder')}</span><input id="repo-path" aria-label="Path to the project folder" placeholder="/path/to/your/project" value="${h(info.initial_repo || '')}" autocomplete="off" required>${info.can_browse ? '<button type="button" class="secondary" id="browse">Browse…</button>' : ''}<button class="primary" type="submit">Open ${ico('arrow')}</button></form>
      <div class="open-alt"><span>No Git history, or just looking around?</span><button type="button" class="secondary" id="explore-only">Explore without comparing</button></div>
      <div class="notice" id="picker-error" hidden></div></section>
      ${recent}
      <section class="how" aria-label="How it works"><ol class="how-steps"><li><b aria-hidden="true">1</b><div><strong>Open a project</strong><span>Any folder with Python files. Git history is optional.</span></div></li><li><b aria-hidden="true">2</b><div><strong>Pick two versions</strong><span>Compare any two commits, in either direction.</span></div></li><li><b aria-hidden="true">3</b><div><strong>Read the review</strong><span>The code to inspect, then the tests to run first.</span></div></li></ol></section>`);
    const submit = document.querySelector('#open-form .primary');
    const open = async path => {
      $('picker-error').hidden = true;
      submit.disabled = true;
      try { stepTwo(boot, await api('/api/inspect', {repo: path})); } catch (e) { submit.disabled = false; fail(e.message); }
    };
    $('open-form').addEventListener('submit', ev => { ev.preventDefault(); open($('repo-path').value.trim()); });
    $('explore-only').addEventListener('click', async () => {                      // no Git needed: just the map of the folder
      const path = $('repo-path').value.trim(); $('picker-error').hidden = true;
      if (!path) { fail('Type the project folder first.'); return; }
      try { const {job} = await api('/api/lab', {repo: path}); waiting(job, {compare: false, reverse: false, back: () => stepOne(boot)}); } catch (e) { fail(e.message); }
    });
    const browse = $('browse'); if (browse) browse.addEventListener('click', async () => { try { const r = await api('/api/browse', {}); if (r.path) { $('repo-path').value = r.path; open(r.path); } } catch (e) { fail(e.message); } });
    document.querySelectorAll('[data-path]').forEach(b => b.addEventListener('click', () => { $('repo-path').value = b.dataset.path; open(b.dataset.path); }));
    $('repo-path').focus();
  }

  function stepTwo(boot, info) {
    const commits = info.commits || [];
    const label = c => `${c.short} · ${c.subject || '(no message)'} · ${c.date}`;
    const options = selected => commits.map((c, i) => `<option value="${h(c.commit)}" ${i === selected ? 'selected' : ''}>${h(label(c))}</option>`).join('');
    const can = commits.length >= 2;
    page(`<div class="page-header"><span class="eyebrow">${h(info.name)} · ${h(info.current_branch)}</span><h1>Choose what to compare</h1><p>${commits.length} commit${commits.length === 1 ? '' : 's'} found. ${info.dirty ? 'You have unsaved changes: the map shows your working folder; the comparison uses saved commits only. ' : ''}${h((info.source_roots || {}).reason || '')}</p></div>
      <form id="go-form" class="go-form">
      ${can ? `<section class="card" aria-labelledby="cmp-title"><div class="card-head"><h2 id="cmp-title">Check a change</h2><label class="check"><input type="checkbox" id="skip"><span>Only explore the map</span></label></div>
        <div class="pair" id="pair">
          <label class="slot slot-base"><span class="slot-label"><span>Older version</span><em class="role">Start</em></span><select id="base">${options(1)}</select></label>
          <button type="button" class="swap" id="swap" aria-label="Reverse the direction" title="Reverse the direction">${ico('swap')}</button>
          <label class="slot slot-head"><span class="slot-label"><span>Newer version</span><em class="role">End</em></span><select id="head">${options(0)}</select></label>
        </div>
        <fieldset class="reverse-choice"><legend>Reverse this change?</legend><div class="seg"><label><input type="radio" name="reverse" value="no" checked><span>No</span></label><label><input type="radio" name="reverse" value="yes"><span>Yes</span></label></div><p class="help-text" id="direction" aria-live="polite"></p></fieldset></section>`
        : '<div class="notice neutral"><strong>Only one commit</strong><p>There is nothing to compare yet. You can still explore the project.</p></div>'}
      <div class="notice neutral"><strong>Explore the code</strong><p>Always included: the map of every file, class and function in your working folder, with search.</p></div>
      <div class="notice" id="picker-error" hidden></div>
      <div class="action-bar"><button class="primary" type="submit">${can ? 'Analyse' : 'Explore'} ${ico('arrow')}</button><button class="secondary" type="button" id="back-to-open">Choose another project</button></div></form>`);
    const form = $('go-form'), pair = $('pair');
    const asked = () => document.querySelector('input[name="reverse"]:checked')?.value === 'yes';
    const describe = () => {
      if (!can) return;
      const reverse = asked();
      pair.classList.toggle('reversed', reverse);
      pair.querySelector('.slot-base .role').textContent = reverse ? 'End' : 'Start';
      pair.querySelector('.slot-head .role').textContent = reverse ? 'Start' : 'End';
      $('direction').textContent = reverse
        ? 'Yes. The same two versions, run backwards: from the newer one to the older one. Git is not modified.'
        : 'No. From the older version to the newer one, the way the change happened.';
    };
    // Moving the two versions is animated from where each one was, so the swap reads as a swap and not a jump.
    const flip = mutate => {
      if (!can) return;
      const parts = [...pair.children], from = parts.map(el => el.getBoundingClientRect());
      mutate();
      if (calm() || !pair.animate) return;
      parts.forEach((el, i) => { const to = el.getBoundingClientRect(), dx = from[i].left - to.left, dy = from[i].top - to.top; if (dx || dy) el.animate([{transform: `translate(${dx}px, ${dy}px)`}, {transform: 'none'}], {duration: 200, easing: 'cubic-bezier(.2,.7,.2,1)'}); });
    };
    const explore = () => { const off = !!$('skip')?.checked; pair.classList.toggle('off', off); pair.querySelectorAll('select,button').forEach(el => { el.disabled = off; }); document.querySelectorAll('input[name="reverse"]').forEach(el => { el.disabled = off; }); };
    if (can) {
      describe();
      form.addEventListener('change', ev => { if (ev.target.name === 'reverse') flip(describe); else if (ev.target.id === 'skip') explore(); });
      $('swap').addEventListener('click', () => { document.querySelector(`input[name="reverse"][value="${asked() ? 'no' : 'yes'}"]`).checked = true; flip(describe); });
    }
    $('back-to-open').addEventListener('click', () => stepOne(boot));
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      const compare = can && !$('skip').checked;
      const reverse = compare && asked();
      const body = {repo: info.repo, ...(compare ? {base: $('base').value, head: $('head').value, reverse} : {})};
      if (compare && body.base === body.head) { fail('Choose two different versions, or tick "Only explore the map".'); return; }
      try { const {job} = await api('/api/lab', body); waiting(job, {compare, reverse, back: () => stepTwo(boot, info)}); } catch (e) { fail(e.message); }
    });
  }

  // The steps are drawn once. Each poll only moves the highlight, the clock and the notes, so the page never jumps and the motion is real.
  function waiting(job, {compare, reverse, back}) {
    const order = compare ? FLOW.compare : FLOW.explore;
    const name = s => (reverse && REVERSED[s]) || STEPS[s] || s;
    busy(true);
    page(`<section class="run"><div class="run-head"><div><span class="eyebrow">IN PROGRESS</span><h1>${reverse ? 'Reading the project, backwards' : 'Reading the project'}</h1><p>${reverse ? 'The same two versions in the opposite direction. ' : ''}Everything stays on this computer. The highlighted step is the one running now.</p></div><div class="elapsed" role="timer" aria-label="Time so far"><b data-elapsed>0:00</b><span>elapsed</span></div></div>
      <div class="progress-track" aria-hidden="true"><span class="progress-fill" id="progress-fill"></span></div>
      <ol class="steps" id="progress-list" aria-live="polite">${order.map(s => `<li data-stage="${h(s)}"><span class="mark" aria-hidden="true"></span><span class="copy"><b>${h(name(s))}</b><small></small></span><span class="time"></span></li>`).join('')}</ol>
      <div class="action-row"><button type="button" class="secondary" id="cancel-run">Cancel</button><span class="help-text">Cancel stops at the end of the step that is running. Nothing is changed.</span></div></section>`);
    const t0 = performance.now(); let skew = 0, stopped = false, widest = 0, marks = {};
    const now = () => (performance.now() - t0) / 1000 + skew;
    const paint = (seen, stage, detail) => {
      const at = Math.max(0, order.indexOf(stage));
      widest = Math.max(widest, (at + 0.45) / order.length);                         // the bar only ever moves forward
      const fill = $('progress-fill'); if (fill) fill.style.transform = `scaleX(${widest})`;
      order.forEach((id, i) => {
        const item = document.querySelector(`#progress-list li[data-stage="${id}"]`); if (!item) return;
        const current = id === stage, finished = i < at;          // anything before the running step is finished, even a step the engine had nothing to do for
        item.classList.toggle('now', current);
        item.classList.toggle('done', finished);
        item.querySelector('small').textContent = current ? note(detail) : '';      // the line is always there, so nothing below it moves
        const next = marks[order[i + 1]];
        item.querySelector('.time').textContent = finished && marks[id] != null && next != null ? took(Math.max(0, next - marks[id])) : '';
      });
    };
    const tickClock = () => {
      const clockEl = document.querySelector('[data-elapsed]'); if (clockEl) clockEl.textContent = clock(now());
      const live = document.querySelector('#progress-list li.now .time'), item = live && live.parentElement;
      if (live && item && marks[item.dataset.stage] != null) live.textContent = took(Math.max(0, now() - marks[item.dataset.stage]));
    };
    const timer = setInterval(() => { if (stopped) clearInterval(timer); else tickClock(); }, 250);
    const leave = () => { stopped = true; clearInterval(timer); busy(false); };
    $('cancel-run').addEventListener('click', async () => {
      $('cancel-run').disabled = true; leave();
      try { await api('/api/cancel', {job}); } catch (e) { /* the job simply finishes unseen */ }
      back();
    });
    paint(['check'], 'check', '');
    const tick = async () => {
      if (stopped) return;
      try {
        const state = await api('/api/job?id=' + encodeURIComponent(job));
        if (stopped) return;
        if (state.state === 'done') { leave(); root.PROTO = state.view.data; root.RTLab(root.PROTO); return; }
        if (state.state === 'cancelled') { leave(); back(); return; }
        if (state.state === 'error') { leave(); failPage(state.error && state.error.message || 'The analysis failed.', 'Choose a project again', () => stepOne(root.RTLab)); return; }
        marks = state.marks || {}; skew = Math.max(0, state.seconds - (performance.now() - t0) / 1000);
        paint(state.seen || [], state.stage, state.detail);
        tickClock();
        setTimeout(tick, 500);
      } catch (e) { leave(); failPage(e.message, 'Try again', () => stepOne(root.RTLab)); }
    };
    setTimeout(tick, 300);
  }

  // From a finished review: run the same request again, with the direction turned around. The old review stays on screen until the new one is ready or the run is cancelled.
  function run(body) {
    const previous = root.PROTO;
    (async () => {
      try {
        const {job} = await api('/api/lab', body);
        waiting(job, {compare: !!(body.base && body.head), reverse: !!body.reverse, back: () => (previous ? root.RTLab(previous) : stepOne(root.RTLab))});
      } catch (e) { fail(e.message); }
    })();
  }
  root.RTPicker = {start, run};
})(typeof globalThis !== 'undefined' ? globalThis : this);
