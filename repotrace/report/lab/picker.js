// Live start: choose a project folder, choose two versions to compare (or none), wait for the analysis, then start the Lab on the result.
// Talks only to the local app (same origin) with the per-launch token. Nothing here reads files or runs code.
(function (root) {
  'use strict';
  const $ = id => document.getElementById(id);
  const h = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const token = (document.querySelector('meta[name=rt-token]') || {}).content || '';
  const STAGES = {check: 'Checking the project and the two versions', analyze_before: 'Reading and analysing the older version', analyze_after: 'Reading and analysing the newer version',
    compare: 'Comparing the two versions function by function', impact: 'Finding what could be affected', finish: 'Preparing the screens'};

  async function api(path, body) {
    const res = await fetch(path, {method: body ? 'POST' : 'GET', headers: {'X-RepoTrace-Token': token, ...(body ? {'Content-Type': 'application/json'} : {})}, body: body ? JSON.stringify(body) : undefined});
    const data = await res.json().catch(() => ({error: 'The app answered in a way this page cannot read.'}));
    if (!res.ok) throw new Error(data.error || `The request failed (${res.status}).`);
    return data;
  }
  const page = html => { $('content').innerHTML = `<div class="compact picker">${html}</div>`; $('content').focus({preventScroll: true}); };
  const fail = (message, again) => { const box = $('picker-error'); if (box) { box.textContent = message; box.hidden = false; } else page(`<div class="notice"><strong>${h(message)}</strong></div><button class="primary" id="again">${h(again || 'Start again')}</button>`); };

  function start(boot) {
    $('snapshot').textContent = 'Local · nothing is uploaded';
    $('location').textContent = 'Open a project';
    $('navigation').innerHTML = '';
    const card = document.querySelector('.project'); if (card) card.hidden = true;                   // no project yet
    document.querySelectorAll('[data-project]').forEach(el => { el.textContent = ''; });
    $('back').disabled = true;
    stepOne(boot);
  }

  async function stepOne(boot) {
    let info = {recent: [], can_browse: false, initial_repo: null};
    try { info = await api('/api/state'); } catch (e) { /* the form still works without the recent list */ }
    page(`<div class="page-header"><span class="eyebrow">OPEN A PROJECT</span><h1>Which Python project?</h1><p>RepoTrace reads the code and its Git history. It never runs your code and never changes your project.</p></div>
      <form id="open-form" class="search-form"><span class="search-icon" aria-hidden="true">⌂</span><input id="repo-path" aria-label="Path to the project folder" placeholder="/path/to/your/project" value="${h(info.initial_repo || '')}" autocomplete="off" required>${info.can_browse ? '<button type="button" class="secondary" id="browse">Browse…</button>' : ''}<button class="primary" type="submit">Open <span aria-hidden="true">→</span></button></form>
      <p class="help-text">No Git history, or you only want to look around? <button type="button" class="text-button" id="explore-only">Explore the code without comparing versions</button></p>
      <div class="notice" id="picker-error" hidden></div>
      ${info.recent.length ? `<div class="section-heading"><h2>Recent projects</h2></div><div class="stack">${info.recent.map(r => `<button class="result" data-path="${h(r.path)}"><span class="result-top"><strong>${h(r.name)}</strong></span><small>${h(r.path)}</small></button>`).join('')}</div>` : ''}`);
    const open = async path => {
      $('picker-error').hidden = true;
      try { stepTwo(boot, await api('/api/inspect', {repo: path})); } catch (e) { fail(e.message); }
    };
    $('open-form').addEventListener('submit', ev => { ev.preventDefault(); open($('repo-path').value.trim()); });
    $('explore-only').addEventListener('click', async () => {                      // no Git needed: just the map of the folder
      const path = $('repo-path').value.trim(); $('picker-error').hidden = true;
      if (!path) { fail('Type the project folder first.'); return; }
      try { const {job} = await api('/api/lab', {repo: path}); waiting(boot, job, false); } catch (e) { fail(e.message); }
    });
    const browse = $('browse'); if (browse) browse.addEventListener('click', async () => { try { const r = await api('/api/browse', {}); if (r.path) { $('repo-path').value = r.path; open(r.path); } } catch (e) { fail(e.message); } });
    document.querySelectorAll('[data-path]').forEach(b => b.addEventListener('click', () => { $('repo-path').value = b.dataset.path; open(b.dataset.path); }));
    $('repo-path').focus();
  }

  function stepTwo(boot, info) {
    const commits = info.commits || [];
    const label = c => `${c.short} · ${c.subject || '(no message)'} · ${c.date}`;
    const options = (selected) => commits.map((c, i) => `<option value="${h(c.commit)}" ${i === selected ? 'selected' : ''}>${h(label(c))}</option>`).join('');
    const can = commits.length >= 2;
    page(`<div class="page-header"><span class="eyebrow">${h(info.name)} · ${h(info.current_branch)}</span><h1>What should RepoTrace look at?</h1><p>${commits.length} commit${commits.length === 1 ? '' : 's'} found. ${info.dirty ? 'You have unsaved changes: the map shows your working folder; the comparison uses saved commits only. ' : ''}${h((info.source_roots || {}).reason || '')}</p></div>
      <form id="go-form"><div class="notice neutral"><strong>Explore the code</strong><p>Always included: the map of every file, class and function in your working folder, with search.</p></div>
      ${can ? `<div class="section-heading"><h2>Check a change</h2><span>Optional</span></div>
      <label class="field"><span>Newer version (after the change)</span><select id="head">${options(0)}</select></label>
      <label class="field"><span>Older version (before the change)</span><select id="base">${options(1)}</select></label>
      <label class="field-check"><input type="checkbox" id="skip"> Only explore. Do not compare two versions.</label>` : '<p class="help-text">This project has only one commit, so there is nothing to compare yet. You can still explore it.</p>'}
      <div class="notice" id="picker-error" hidden></div>
      <div class="action-row"><button class="primary" type="submit">Analyse <span aria-hidden="true">→</span></button><button class="secondary" type="button" id="back-to-open">Choose another project</button></div></form>`);
    $('back-to-open').addEventListener('click', () => stepOne(boot));
    $('go-form').addEventListener('submit', async ev => {
      ev.preventDefault();
      const compare = can && !$('skip').checked;
      const body = {repo: info.repo, ...(compare ? {base: $('base').value, head: $('head').value} : {})};
      if (compare && body.base === body.head) { fail('Choose two different versions, or tick "Only explore".'); return; }
      try { const {job} = await api('/api/lab', body); waiting(boot, job, compare); } catch (e) { fail(e.message); }
    });
  }

  function waiting(boot, job, compare) {
    const order = compare ? ['check', 'analyze_before', 'analyze_after', 'compare', 'impact', 'finish'] : ['check', 'analyze_after', 'finish'];
    const paint = (seen, stage, detail) => page(`<div class="page-header"><span class="eyebrow">ANALYSING</span><h1>Reading your project…</h1><p>Large projects can take a minute. Nothing leaves this computer.</p></div>
      <ol class="progress" aria-live="polite">${order.map(s => `<li class="${s === stage ? 'now' : seen.includes(s) ? 'done' : ''}">${h(STAGES[s] || s)}${s === stage && detail ? `<small>${h(detail)}</small>` : ''}</li>`).join('')}</ol>`);
    paint(['check'], 'check', '');
    const tick = async () => {
      try {
        const state = await api('/api/job?id=' + encodeURIComponent(job));
        if (state.state === 'done') { root.PROTO = state.view.data; root.RTLab(root.PROTO); return; }
        if (state.state === 'error') { fail(state.error && state.error.message || 'The analysis failed.', 'Choose a project again'); const again = $('again'); if (again) again.addEventListener('click', () => stepOne(boot)); return; }
        paint(state.seen || [], state.stage, state.detail);
        setTimeout(tick, 600);
      } catch (e) { fail(e.message); }
    };
    setTimeout(tick, 400);
  }

  root.RTPicker = {start};
})(typeof globalThis !== 'undefined' ? globalThis : this);
