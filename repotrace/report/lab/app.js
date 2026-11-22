// The Lab screens. Started with data: window.RTLab(data). Saved data starts it at load; the live app starts it after the project is analysed.
const LAB_BASE = (document.currentScript && document.currentScript.src) ? document.currentScript.src.replace(/[^/]*$/, '') : '/lab/';
window.RTLab = (D) => {
  'use strict';
  const M=window.RTModel;
  const $=id=>document.getElementById(id);
  const h=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  if(!D||!M){$('content').innerHTML='<h1>The project data could not load.</h1><p>Reload the page. If it keeps failing, keep index.html and the .js and .css files together.</p>';return;}
  if(window.RTLab.current)window.RTLab.current.dispose();            // a second run (a reversed comparison) replaces the first: no listeners or frames are left behind
  const ac=new AbortController();window.RTLab.current={dispose(){ac.abort();}};
  document.querySelectorAll('#xp-holder').forEach(el=>el.remove());
  const ico=name=>`<svg class="ico" aria-hidden="true" focusable="false"><use href="#i-${name}"/></svg>`;
  const busy=()=>!!document.querySelector('.shell').dataset.busy;      // an analysis is running: the page is not navigable until it ends or is cancelled
  const first=D.reviews&&D.reviews[0];const nav=new M.Navigation({page:first?'review':'home',graph:first?first.graph:'main',query:'',node:null,review:first?0:null,tab:'overview',possible:true,tests:false,expanded:false,walk:false,explore:null,exploreKeep:false,exploreReview:null,exploreFocus:null,exploreQuery:'',palette:false,scroll:0,});
  const TARGET_ORIGIN=location.protocol==='file:'?'*':location.origin;
  const state=()=>nav.current, graph=()=>D.graphs[state().graph], review=()=>D.reviews[state().review];
  const labelKind=n=>({fn:n.test?'Test / function':'Function',class:'Class',file:'File',dir:'Folder'}[n.k]);
  const kindIcon=n=>`<span class="kind ${h(n.k)}">${({fn:'ƒ',class:'C',file:'py',dir:'▱'})[n.k]}</span>`;
  const tierLabel=tier=>tier===0?'Sure: written in the code':'Maybe: depends on an assumption';
  const LIVE=D.project.mode==='live';                       // live: the user's own project, analysed just now; otherwise saved prototype data
  const PROJECT=D.project.name, COMMIT=(D.graphs.main.version.match(/commit ([0-9a-f]+)/)||[])[1]||'', VERSION=COMMIT?`working · ${COMMIT}`:'working folder';
  const token=(document.querySelector('meta[name=rt-token]')||{}).content||'';
  const recUI={showDismissed:false, selected:[], open:{}, query:'', answer:null, git:null, gitError:null, gitLoading:false};
  // The guided first steps are built from the data, never typed in by hand: the first sample question, its best match,
  // and the first caller of that match that is not test code.
  const walk=(()=>{const g=D.graphs.main,q=D.questions[0]||'';const hit=M.search(g,q,10).find(r=>r.node.k==='fn'&&!r.node.test);if(!hit)return null;const target=hit.node;const caller=M.neighbors(g,target.i).incoming.find(e=>!e.node.test)?.node||null;return {q,target,caller,path:M.parents(g,target.i)};})();
  // Tests the change may have cut off: connected to the changed code before it, but not after. These deserve a look first.
  const lostTests=r=>r.tests.filter(t=>t.seen_in&&t.seen_in.length===1&&t.seen_in[0]==='before');
  const seenText=t=>!t.seen_in||t.seen_in.length===2?'Connected before and after the change':t.seen_in[0]==='before'?'Connected only before the change: RepoTrace cannot follow this path in the new code':'Connected only in the new code';
  const viaText=t=>t.via?(t.via.kind==='setup'?`Reached through ${t.via.name}, which the test runner calls before each test of that class.`:`Reached through the pytest fixture ${t.via.name}${t.via.autouse?' (runs for every test in its folder)':''}.`):'';
  const tierTag=tier=>`<span class="tag ${tier===0?'supported':'possible'}">${tierLabel(tier)}</span>`;
  // a link is described by two separate facts: how sure it is, and whether it exists only in the older version
  const linkTag=e=>tierTag(e.strength)+(e.ver===1?'<span class="tag before">Old version only</span>':'');
  const shortFile=n=>n.file.split('/').pop();
  const reviewTitle=r=>r.label;
  const filename=n=>`${h(n.file)}${n.line?':'+n.line:''}`;
  function go(next){if($('map-inspector'))nav.patch({mapScroll:$('map-inspector').scrollTop});nav.go(next,$('content').scrollTop);render();}
  function start(){go({page:'home',graph:'main',review:null,node:null,query:'',walk:false});}
  function openNode(id){if(!graph().nodes[id])return;openMap(id);}
  function walkStep(n){ if(!state().walk||!walk)return '';
    if(n==null)return `<div class="walkthrough"><strong>01 / Find the code</strong><p>Open <code>${h(walk.target.q)}</code> below. Next, you’ll see its source and who calls it.</p></div>`;
    if(n.i===walk.target.i)return `<div class="walkthrough"><strong>02 / Read it, then follow a caller</strong><p>${walk.target.doc?h(walk.target.doc)+' ':''}Read the source, then open <b>${walk.caller?h(walk.caller.q):'a caller'}</b> under “Called by”.</p></div>`;
    return `<div class="walkthrough"><strong>03 / You’re following the code</strong><p>You followed a real recorded connection. Read this function to see its role. Use Back to return to the exact place you came from.</p></div>`; }
  // "the map" is the Obsidian-style explorer. In a review it opens in review mode on that comparison's graph.
  function openMap(id=null,extra={}){
    const review=extra.review!==undefined?extra.review:state().review;
    go({page:'explore',review,graph:review!=null?D.reviews[review].graph:'main',exploreReview:review,exploreFocus:id,exploreQuery:extra.query||'',explore:null,exploreKeep:!!extra.keep,palette:!!extra.palette,walk:!!extra.walk,node:null});
  }
  function doSearch(query){openMap(null,{query:query.trim()});}
  function openPalette(){const f=$('xp');if(state().page==='explore'&&f&&f.contentWindow){f.focus();f.contentWindow.postMessage({rt:'palette'},TARGET_ORIGIN);}else openMap(null,{palette:true});}
  function back(){nav.back();render();}
  function crumbs(n){return `<div class="breadcrumbs"><button data-action="files">Files</button>${M.parents(graph(),n.i).map((p,i,a)=>`<span aria-hidden="true">/</span>${i===a.length-1?`<span>${h(p.n)}</span>`:`<button data-action="node" data-id="${p.i}">${h(p.n)}</button>`}`).join('')}</div>`;}
  function row(n,why=''){if(!n)return '';return `<button class="result" data-action="node" data-id="${n.i}"><span class="result-top">${kindIcon(n)}<strong>${h(n.q||n.n)}</strong><span class="arrow" aria-hidden="true">↗</span></span><small>${filename(n)}${n.gone?' · removed in this comparison':''}</small>${why?`<span class="why">${h(why)}</span>`:''}</button>`;}
  function source(n,highlight=0,snippet=null){
    const lines=snippet || String(n.code||'').split('\n').map((s,i)=>[n.line+i,s]);
    if(!n.code&&!snippet)return '<div class="empty-small">Source text is not included for this item in the saved analysis.</div>';
    return `<div class="source-box"><div class="source-heading"><span>${filename(n)}${n.gone?' · BEFORE version':''}</span><span>PYTHON</span></div><div class="source" tabindex="0" aria-label="Source code for ${h(n.q||n.n)}">${lines.map(([line,text])=>`<span class="code-line${line===highlight?' highlight':''}"><span class="line-no">${line}</span><code>${highlightCode(text)}</code></span>`).join('')}</div></div>`;
  }
  function highlightCode(text){
    return String(text).split(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|#.*$|\b(?:def|class|return|if|else|elif|for|in|while|import|from|as|raise|try|except|with|is|not|and|or|None|True|False|async|await|pass|self)\b|\b\d+\b)/g).map(t=>{
      const cls=/^#/.test(t)?'tok-comment':/^["']/.test(t)?'tok-str':/^(def|class|return|if|else|elif|for|in|while|import|from|as|raise|try|except|with|is|not|and|or|None|True|False|async|await|pass|self)$/.test(t)?'tok-key':/^\d+$/.test(t)?'tok-num':'';
      return cls?`<span class="${cls}">${h(t)}</span>`:h(t);
    }).join('');
  }
  function searchForm(value='',home=false){return `<form class="search-form" id="search-form"><span class="search-icon" aria-hidden="true">${ico('search')}</span><input id="query" aria-label="Find a feature, function or file" type="search" value="${h(value)}" placeholder="${home?'Where is an email checked?':'Function, file, or a question about the code…'}" autocomplete="off" required maxlength="200"><button class="primary" type="submit">Find code ${ico('arrow')}</button></form>`;}
  function reviewEntry(){const r=D.reviews[0];if(!r)return `<p>No two versions were chosen, so there is no change to check here. Explore the project, or start again and pick a base and a head version.</p>`;
    return `<p>${LIVE?'Your comparison':'Try a saved example'}: ${h(reviewTitle(r))}. ${r.concerns.length?`RepoTrace found ${r.concerns.length} place${r.concerns.length===1?'':'s'} to check.`:''}</p><button class="secondary" data-action="review" data-index="0">Open this example <span aria-hidden="true">→</span></button><button class="home-map-link" data-action="reviews">${LIVE?'See the comparison':`See all ${D.reviews.length} saved changes`} ↗</button>`;}
  function home(){
    const r=D.reviews[0];
    if(!r){
      return `<div class="compact"><div class="page-header"><span class="eyebrow">THIS PROJECT</span><h1>Explore ${h(PROJECT)}.</h1><p>No two versions were compared, so there is no change to review. The map shows the working folder. Nothing is uploaded and nothing is edited.</p></div><div class="action-row"><button class="primary" data-action="map-home">Open the map</button><button class="secondary" data-action="files">Browse files</button><button class="secondary" data-action="settings">Settings</button></div></div>`;
    }
    const plan=r.test_plan;
    const shown=r.tests.length;
    const total=plan?plan.total_candidates:shown;
    const outside=plan?plan.omitted:0;
    const card=(icon,title,text,attrs)=>`<button type="button" class="entry-card" ${attrs}><span class="entry-tile" aria-hidden="true">${ico(icon)}</span><span class="entry-copy"><strong>${title}</strong><span>${text}</span></span><span class="entry-go" aria-hidden="true">${ico('arrow')}</span></button>`;
    return `<div class="compact home-page"><div class="page-header"><span class="eyebrow">${LIVE?'YOUR COMPARISON':'SAVED COMPARISON'}</span><h1>What should you check?</h1><p>${r.reversed?'Reversed. ':''}${h(r.base)} → ${h(r.head)} · ${h(PROJECT)}. ${shown} of ${total} connected tests are listed${outside?`, and ${outside} more sit outside that list`:''}.</p></div>${metrics(r)}<div class="entry-cards">${card('review','Open the review','The lines to inspect, and the tests connected to the change.','data-action="review" data-index="0"')}${card('check','Tests to run first','The shortlist, with a command to copy. Nothing is run for you.','data-action="review" data-index="0" data-tab="tests"')}${card('map','Explore the map','Follow one connection at a time through files, classes and functions.','data-action="map-home"')}${card('search','Recommendations','What to open or copy next, from the evidence in this review.','data-action="review" data-index="0" data-tab="recommendations"')}</div><p class="help-text">Start with the review. The map is there when you need to follow one connection further. Nothing runs your code or edits the repository.</p></div>`;
  }
  function contextNote(){return `<div class="note-section"><span class="step-label">YOUR CONTEXT</span><p class="wrap">${h(scopeText())}</p>${state().review!==null?'<p>Search stays inside this comparison. Removed definitions are marked as before-version code.</p><button class="small-button" data-action="review-tab" data-tab="overview">Return to change review</button>':'<p>The source and connections come from the same analysis of the working folder.</p>'}</div>`;}
  function browsePage(){
    const n=state().node==null?null:graph().nodes[state().node];
    const list=n?M.children(graph(),n.i):graph().nodes.filter(x=>x.k==='dir');
    return `<div class="compact">${n?crumbs(n):''}<div class="page-header"><span class="eyebrow">BROWSE THE PROJECT</span><h1>${h(n?n.n:'Files, without the guessing.')}</h1><p>${n?`${h(labelKind(n))} · ${h(n.file)}`:'Open a folder, then a file, then a function. Each step keeps your place.'}</p></div><div class="split"><section><div class="${n?'stack':'folder-grid'}">${list.map(x=>row(x,x.k==='dir'?`${M.children(graph(),x.i).length} files`:x.k==='file'?`${x.lines||0} lines`:'')).join('')||'<div class="empty-small">No child definitions are included in this saved analysis.</div>'}</div>${n?.code?`<div class="section-heading"><h2>Source</h2></div>${source(n)}`:''}</section><aside class="side-notes"><div class="note-section"><span class="step-label">FOLLOW THE STRUCTURE</span><h3>Where the code lives.</h3><p>Folders contain files. Files contain classes and functions. Once you open a function, the connection map answers a different question: what uses it?</p><button class="small-button" data-action="find">Find by name instead</button></div>${contextNote()}</aside></div></div>`;
  }
  function graphSide(edges,title){
    const visible=state().expanded?edges:edges.slice(0,3);
    return `<div class="node-column"><span class="column-label">${title} · ${edges.length}</span>${visible.map(e=>`<button class="graph-node" data-action="node" data-id="${e.node.i}"><strong>${h(e.node.q)}</strong><small>${h(shortFile(e.node))}:${e.node.line}</small>${linkTag(e)}</button>`).join('')||'<div class="empty-small">None in this view.<br>Not proof that none exist.</div>'}${edges.length>visible.length?`<button class="text-button" data-action="expand">Show ${edges.length-visible.length} more ↓</button>`:''}</div>`;
  }
  function functionPage(){
    const n=graph().nodes[state().node];if(!n)return '<h1>Function not found in this snapshot.</h1>';
    const links=M.neighbors(graph(),n.i,{possible:state().possible,tests:state().tests});
    const all=M.neighbors(graph(),n.i),next=all.incoming.find(e=>!e.node.test)||all.outgoing[0];
    const trail=nav.past.filter(p=>p.page==='function'&&p.graph===state().graph).slice(-4);
    const paths=M.testPaths(graph(),n.i);
    const r=review(), shortlist=r?r.tests.filter(t=>t.node===n.i||(t.reasons||[]).some(x=>x.target===n.i||(x.chain||[]).includes(n.q))):[];
    return `<div class="compact">${crumbs(n)}<div class="page-header"><span class="eyebrow">${labelKind(n).toUpperCase()}${n.gone?' · REMOVED IN THIS COMPARISON':''}</span><h1>${h(n.q)}</h1><p class="wrap">${filename(n)} · ${h(scopeText())}</p></div><div class="split"><section>${walkStep(n)}<div class="graph-heading"><h2>How it connects</h2><div class="filters"><label><input type="checkbox" data-filter="possible" ${state().possible?'checked':''}>Possible links</label><label><input type="checkbox" data-filter="tests" ${state().tests?'checked':''}>Test code</label></div></div><div class="local-graph" aria-label="Local call connections, not an execution timeline">${graphSide(links.incoming,'Called by')}<div class="connector" aria-hidden="true"></div><div class="node-column"><span class="column-label">YOU ARE HERE</span><div class="graph-node selected"><strong>${h(n.q)}</strong><small>${h(shortFile(n))}:${n.line}</small><span class="tag supported">Selected function</span></div></div><div class="connector" aria-hidden="true"></div>${graphSide(links.outgoing,'Calls')}</div><p class="graph-note">Arrows mean “calls”, not the order code runs. Click a connected function to follow it.${n.unres?` <span style="color:var(--amber)">${n.unres} calls here could not be traced.</span>`:''}</p><div class="section-heading"><h2>Read the code</h2><span>${n.gone?'Before version':'Analysed source'}</span></div>${source(n)}${n.doc?`<details class="section-line"><summary>Docstring</summary><p class="help-text">${h(n.doc)}</p></details>`:''}${r?`<details class="section-line" open><summary>Tests worth running for this change <span class="count-pill">${shortlist.length}</span></summary><p class="help-text">From the comparison’s test shortlist: the ones whose path reaches this function.</p><div class="stack">${shortlist.slice(0,12).map(t=>row(graph().nodes[t.node],`${seenText(t)} · ${tierLabel(t.tier)}`)).join('')||'<p class="subtle">None of the shortlisted tests reach this function.</p>'}</div><button class="small-button" data-action="review-tab" data-tab="tests">Open the ranked test list →</button></details>`
      :`<details class="section-line"><summary>Tests that reach this function <span class="count-pill">${paths.length}</span></summary><p class="help-text">Found by following recorded calls backwards from this function, including unittest setUp hooks. pytest fixtures are not followed here, so a test that reaches it only through a fixture is missing. Nothing was run.</p><div class="stack">${paths.slice(0,12).map(p=>row(graph().nodes[p.id],`${p.chain.length-1} link(s) away · ${tierLabel(p.tier)}${p.via?' · through '+p.via:''}`)).join('')||'<p class="subtle">No paths found. This does not mean the function is untested.</p>'}</div>${paths.length>12?`<p class="subtle">Showing the nearest 12 of ${paths.length}.</p>`:''}</details>`}</section><aside class="side-notes"><div class="note-section"><span class="step-label">NEXT USEFUL STEP</span><h3>${all.incoming.some(e=>!e.node.test)?'See where this is used.':'Follow what it uses.'}</h3><p>Open one connection, not the entire repository.</p>${next?`<button class="context-link" data-action="node" data-id="${next.node.i}">${h(next.node.q)} ↗</button>`:'<p>No next connection is recorded. Browse the containing file for context.</p>'}</div><div class="note-section"><span class="step-label">YOUR TRAIL</span>${trail.map(p=>`<button class="trail-item" data-action="node" data-id="${p.node}">↳ ${h(graph().nodes[p.node]?.q)}</button>`).join('')}<span class="trail-item">● ${h(n.q)}</span><p>Back restores the previous view, filters and scroll position.</p></div><div class="note-section"><span class="step-label">SEE IT ON THE MAP</span><p>The same connections, drawn as a web you can walk through.</p><button class="small-button" data-action="node" data-id="${n.i}">Show on the map ↗</button></div><div class="note-section"><span class="step-label">HOW MUCH TO TRUST IT</span><p><b>Sure</b>: the call is written in the code.<br><b>Maybe</b>: one step depends on an assumption, for example a call through <code>self</code> that a subclass could override.</p><p>Neither proves the code runs. Calls that could not be traced stay unknown.</p></div>${state().review!==null?contextNote():''}</aside></div></div>`;
  }
  function reviewsPage(){if(!D.reviews.length)return `<div class="compact"><div class="page-header"><span class="eyebrow">CHECK A CHANGE</span><h1>No comparison was chosen.</h1><p>To see what a change could affect, start again and pick two versions of the project (a base and a head). You can still explore the project as it is now.</p></div><button class="primary" data-action="map-home">Explore the project <span aria-hidden="true">→</span></button></div>`;
    return `<div class="compact"><div class="page-header"><span class="eyebrow">CHECK A CHANGE</span><h1>What needs a second look?</h1><p>${LIVE?'The comparison you chose. These are places to inspect, not proof that anything is wrong.':`These are examples from ${h(PROJECT)}’s saved history, not new warnings about your current code.`}</p></div><div class="split"><section>${D.reviews.map((r,i)=>`<button class="review-row" data-action="review" data-index="${i}"><span class="review-number">0${i+1}</span><div><h2>${h(reviewTitle(r))}</h2><p>${h(r.base)} → ${h(r.head)} · ${r.changed.length} changed definitions</p></div><span class="tag ${r.concerns.length?'warn':''}">${r.concerns.length?r.concerns.length+(r.concerns.length===1?' place':' places')+' to check':'No flagged concerns'}</span>${lostTests(r).length?`<span class="tag before">${lostTests(r).length} tests lost their path</span>`:''}<span class="review-arrow" aria-hidden="true">→</span></button>`).join('')}<p class="help-text">“No flagged concerns” does not mean “safe to merge”. The checks may have missed connections.</p></section><aside class="side-notes"><div class="note-section"><span class="step-label">FIRST TIME?</span><h3>${h(reviewTitle(D.reviews[0]))}</h3><p>RepoTrace compares the code before and after a change and points to the places that still depend on what changed.</p><button class="small-button" data-action="review" data-index="0">Show this example →</button></div><div class="note-section"><h3>What this gives you</h3><p>What happened.<br>Why it matters.<br>The line to check.<br>Related tests.</p></div></aside></div></div>`;}
  function overview(r){
    const paired=r.changed.filter(c=>c.pair&&c.pair.role==='new');
    const renameIntro=paired.map(c=>`<div class="plain-rename"><p><b>What happened:</b> <code>${h(c.pair.other)}</code> was ${c.pair.how==='renamed'?'renamed to':'moved to'} <code>${h(c.name)}</code>${c.pair.how==='moved'?' in '+h(c.file):''}. The body is otherwise identical, so RepoTrace treats the two as the same function.</p><div class="rename-steps"><div><small>${c.pair.how==='renamed'?'OLD NAME':'OLD PLACE'}</small><code class="old">${h(c.pair.how==='renamed'?c.pair.other.split('.').pop()+'()':c.pair.other_file)}</code></div><span aria-hidden="true">→</span><div><small>${c.pair.how==='renamed'?'NEW NAME':'NEW PLACE'}</small><code class="new">${h(c.pair.how==='renamed'?c.name.split('.').pop()+'()':c.file)}</code></div></div></div>`).join('');
    const lost=lostTests(r);
    const lostNote=lost.length?`<div class="notice"><strong>${lost.length} test${lost.length===1?'':'s'} lost ${lost.length===1?'its':'their'} path to the changed code.</strong><p>Before the change RepoTrace could follow ${lost.length===1?'this test':'these tests'} to the changed code; after it, it cannot. That often means the change broke the connection. Run ${lost.length===1?'it':'them'} first.</p><div class="stack">${lost.slice(0,3).map(t=>row(graph().nodes[t.node],t.chain.join(' → '))).join('')}</div>${lost.length>3?`<button class="small-button" data-action="review-tab" data-tab="tests">See all ${lost.length} →</button>`:''}</div>`:'';
    return `<div class="split"><section>${r.concerns.length?`${renameIntro}<div class="finding"><span class="step-label">WHAT NEEDS ATTENTION</span><h2>${r.concerns.length===1?'One place may need updating.':r.concerns.length+' places may need updating.'}</h2>${r.concerns.map(c=>`<p>${h(c.what)} ${h(c.why)}</p>`).join('')}<div class="action-row"><button class="primary" data-action="show-evidence" aria-expanded="${!!state().evidenceOpen}" aria-controls="review-evidence">${state().evidenceOpen?'Hide that line':'Show me that line'} ${state().evidenceOpen?'↑':'↓'}</button></div>${state().evidenceOpen?`<section id="review-evidence" tabindex="-1">${r.concerns.map(c=>`<div class="section-heading"><h2>The line to check, in ${h(c.file.split('/').pop())}</h2><span>${h(c.file)}:${c.line}</span></div>${source(graph().nodes[c.node]||{file:c.file,line:c.line,q:c.title},c.line,c.snippet)}<p class="help-text"><b>What to check:</b> ${h(c.check)} ${LIVE?'RepoTrace does not edit your code.':'This prototype does not edit your code.'}</p><div class="action-row"><button class="secondary" data-action="map-node" data-id="${c.node}">Follow this on the map →</button><button class="small-button" data-action="review-tab" data-tab="changes">See the name change</button></div>`).join('')}</section>`:''}<p class="help-text">This is static evidence, not a test result. The connection is inferred and still needs your review.</p></div>${lostNote}`:`<div class="notice neutral"><strong>No concerns were flagged by these checks.</strong><p>This does not prove the change is safe. Check the changed code and consider the tests; some connections could not be followed.</p></div><div class="finding"><h2>Start with what changed.</h2><p>${r.changed.length} definition${r.changed.length===1?'':'s'} changed in this comparison. Open one to see its code and connections.</p><div class="stack">${r.changed.slice(0,3).map(c=>row(graph().nodes[c.node],c.change)).join('')}</div><div class="action-row"><button class="primary" data-action="review-tab" data-tab="changes">See all changes →</button></div></div>${lostNote}`}<div class="section-line"><div class="section-heading"><h2>After checking the code, consider the tests.</h2></div><p class="help-text">${r.tests.length?`${r.tests.length} of ${(r.test_plan&&r.test_plan.total_candidates)||r.tests.length} connected tests are listed to run first.${r.test_plan&&r.test_plan.omitted?` ${r.test_plan.omitted} more are outside this list. Other tests may still matter.`:''} None was run here.`:'We could not connect tests to this change. Run your usual suite. An empty list means no path was found.'}</p><button class="secondary" data-action="review-tab" data-tab="tests">Show related tests →</button></div></section><aside class="side-notes"><div class="note-section"><span class="step-label">NEXT</span><h3>Inspect one line, then one test.</h3><p>The review is the result. The map is optional.</p></div><div class="note-section"><span class="step-label">${LIVE?'THIS COMPARISON':'SAVED EXAMPLE'}</span><p>${h(r.base)} → ${h(r.head)}<br>${LIVE?'Compared by RepoTrace on this computer.':r.example?'An edit made for this demo.':'This happened in the project’s history.'}<br>No tests are running here.</p><button class="small-button" data-action="map-home">See this change on the map ↗</button></div></aside></div>`;
  }
  function changes(r){return `<div class="split"><section><p class="help-text">Changed definitions in this comparison. A rename pairing is evidence from the saved comparison, not a guarantee of identical behavior.</p>${r.changed.map((c,i)=>`<details class="change-item" ${i===0?'open':''}><summary><strong>${h(c.name)}</strong><span class="tag">${h(c.change)}</span></summary><p>${h(c.file)}${c.pair?`<br>${h(c.pair.how)} · ${c.pair.role==='old'?'paired with new':'paired with old'} definition: ${h(c.pair.other)}`:''}</p>${c.diff?`<div class="diff" tabindex="0">${c.diff.split('\n').map(line=>`<div class="${line.startsWith('+')?'add':line.startsWith('-')?'remove':''}">${h(line)||' '}</div>`).join('')}</div>`:`<div class="diff">${h(c.before_sig||c.after_sig||'No signature text saved.')}</div>`}<div class="action-row"><button class="small-button" data-action="node" data-id="${c.node}">Open ${c.change==='deleted'?'before-version':'saved'} source & connections ↗</button></div></details>`).join('')}</section><aside class="side-notes"><div class="note-section"><span class="step-label">READING THIS VIEW</span><p>A line that starts with + was added. A line that starts with − was removed. Added lines are also tinted, and removed lines are also tinted, so the mark is not colour alone.</p><p>Removed definitions use before-version source. Their old links are labelled separately.</p></div>${contextNote()}</aside></div>`;}
  function testCard(t,i){
    const reasons=(t.reasons&&t.reasons.length?t.reasons:[{for:'the change',tier:t.tier,seen_in:t.seen_in,via:t.via,chain:t.chain||[t.name]}]);
    const why=reasons.map(x=>`<div class="reason"><div class="reason-head"><b>For ${h(x.for||'the change')}</b>${tierTag(x.tier)}<span class="tag ${x.seen_in&&x.seen_in.length===1&&x.seen_in[0]==='before'?'before':''}">${h(seenText(x))}</span></div>${x.via?`<p class="help-text">${h(viaText(x))}</p>`:''}<div class="test-chain" aria-label="How the test reaches the change">${(x.chain||[]).map((c,j)=>`${j?'<span class="dim" aria-hidden="true">→</span>':''}<span>${h(c)}</span>`).join('')}</div></div>`).join('');
    const best=reasons[0]||{};
    const strong=best.tier===0?'Sure. The call is written in the code.':'Maybe. One step depends on an assumption, such as a call through self that a subclass could override.';
    const targets=[...new Map(reasons.filter(x=>x.target!=null).map(x=>[x.target,x])).values()].slice(0,3);
    return `<article class="test-item"><p class="field-label">Which test</p><h3>${h(t.name)}</h3><div class="file-line">${h(t.file)}${t.line?':'+t.line:''}</div><p class="field-label">Why</p>${reasons.length>1?`<p class="help-text">This test reaches ${reasons.length} changed functions. Each path is kept.</p>`:''}${why}<p class="field-label">How strong</p><p class="help-text">${h(strong)} ${h(seenText(best))}</p><p class="field-label">How to run</p><div class="command-row"><code>${h(t.run||'No command is available for this test.')}</code>${t.run?`<button class="small-button" data-action="copy-test" data-index="${i}">Copy command</button>`:''}</div><p class="help-text">Copying does not run the test. Use the command in this project’s own environment.</p><div class="action-row" style="margin-bottom:0">${t.node!=null?`<button class="text-button" data-action="node" data-id="${t.node}">Open test source ↗</button>`:''}${targets.map(x=>`<button class="text-button" data-action="node" data-id="${x.target}">Inspect ${h(x.for)} ↗</button>`).join('')}</div></article>`;
  }
  function tests(r){
    const removed=r.removed_tests||[], plan=r.test_plan||null;
    const lost=lostTests(r), rest=r.tests.filter(t=>lost.indexOf(t)<0);
    const first=r.tests.find(t=>t.run);
    const count=plan?`<p>Showing ${plan.selected} of ${plan.total_candidates} connected tests. ${plan.omitted} more are outside this list. Other tests may still matter.</p><p>${h(plan.ranking)}</p><p>${h(plan.warning||'')}</p>`:'';
    const lead=r.tests.length
      ?`<div class="notice neutral"><strong>Tests to run first</strong><p>These tests are connected to your change. Run them first, then continue your normal test checks.</p>${count}<p>Nothing here runs a test.</p>${first?`<div class="action-row"><button class="primary" data-action="copy-test" data-index="${r.tests.indexOf(first)}">Copy the first command</button></div>`:''}</div>`
      :`<div class="notice"><strong>We could not connect tests to this change.</strong><p>Run your usual test suite, or check the integration tests for this feature. That is guidance, kept separate from tests RepoTrace connected.</p><p>An empty list means no path was found. It does not mean other tests can be skipped.</p></div>`;
    const group=(title,note,items)=>items.length?`<div class="section-heading"><h2>${title} <span class="count-pill">${items.length}</span></h2></div><p class="help-text">${note}</p>${items.map(t=>testCard(t,r.tests.indexOf(t))).join('')}`:'';
    return `<div class="split"><section>${lead}${group('Run these first','The path was found before the change and not after. The test is still there. Read what the edit cut off.',lost)}${group(lost.length?'Also connected':'Connected tests','A recorded path reaches the changed code. The order is a reason to look, not a prediction that the test will fail.',rest)}${removed.length?`<div class="section-heading"><h2>Tests that no longer exist <span class="count-pill">${removed.length}</span></h2></div><p class="help-text">These were connected before the change and are gone in the new version. There is nothing to run.</p>${removed.map(t=>`<p class="file-line">${h(t.name)} · ${h(t.file)}</p>`).join('')}`:''}${r.tests.length?'':'<div class="empty"><h2>No connected test.</h2><p>Check the project’s existing tests yourself. A missing path is a hole in the map.</p><button class="secondary" data-action="files">Browse the comparison’s files</button></div>'}</section><aside class="side-notes"><div class="note-section"><span class="step-label">ONE NEXT STEP</span><h3>Copy one command, then read its path.</h3><p>The path is why the test is listed. A fixture or a setUp hook is named when the path goes through it.</p><p>A listed test can still pass. An unlisted test can still matter.</p></div><div class="note-section"><h3>Where to run it</h3><p>Run the command on ${h(r.head)}, in the environment this project already uses. RepoTrace does not start the command.</p></div></aside></div>`;
  }
  function limits(r){return `<div class="split"><section><h2>What this review cannot establish</h2><ul class="limit-list">${r.gaps.map(g=>`<li>${h(g)}</li>`).join('')}</ul><details class="section-line"><summary>History hints · ${r.history.length} changed files</summary><p class="help-text">Files edited together in the saved history window. This is correlation, not a call link or a prediction that both files must change.</p>${r.history.map(item=>`<div class="change-item"><strong>${h(item.file)}</strong><p>${item.changed_in} changes in the analyzed window</p>${item.with.map(w=>`<p>↳ ${h(w.file)} · together ${w.together} of ${w.of} times</p>`).join('')}</div>`).join('')||'<p class="help-text">Not enough saved history for a useful hint.</p>'}</details></section><aside class="side-notes"><div class="note-section"><span class="step-label">A REVIEW AID, NOT A VERDICT</span><h3>Use evidence to decide.</h3><p>A missed path is not an absent dependency. A suggested test is not proof of coverage. A clean review is not proof of a safe change.</p></div></aside></div>`;}
  // The numbers that open a review. Zero is not always good news: no connected tests is a gap, so it is marked.
  function metrics(r){
    const plan=r.test_plan||{}, n=r.concerns.length, t=r.tests.length;
    const items=[[r.changed.length,r.changed.length===1?'definition changed':'definitions changed',''],
      [n,n===1?'place to inspect':n?'places to inspect':'places flagged',n?'warn':''],
      [t,t===1?'test listed':t?'tests listed':'tests connected',t?'':'warn']];
    if(plan.omitted)items.push([plan.omitted,'more outside the list','']);
    return `<div class="metrics" role="list" aria-label="Summary of this comparison">${items.map(([count,text,tone])=>`<div class="metric ${tone}" role="listitem"><b>${count}</b><span>${text}</span></div>`).join('')}</div>`;
  }
  function direction(r){
    const route=`<div class="route" role="img" aria-label="Runs from ${h(r.base)} to ${h(r.head)}"><span class="end"><small>Start</small><code>${h(r.base)}</code></span>${ico('arrow')}<span class="end"><small>End</small><code>${h(r.head)}</code></span></div>`;
    const can=!!(LIVE&&D.project&&D.project.repo&&r.selection);
    const toggle=can?`<button type="button" class="switch" role="switch" aria-checked="${!!r.reversed}" data-action="reverse-change"><span class="switch-text">Reverse this change</span><span class="track" aria-hidden="true"></span><span class="switch-state">${r.reversed?'Yes':'No'}</span></button>`:'';
    return `<div class="direction">${route}${toggle}</div>`;
  }
  function recReview(){if(state().review!=null&&D.reviews[state().review])return D.reviews[state().review];return (D.reviews||[])[0]||null;}
  function recKey(){const r=recReview(), repo=(D.project&&D.project.repo)||(D.project&&D.project.name)||'project', sel=(r&&r.selection)||{};
    const base=sel.base||(r&&r.base_commit)||(r&&r.base)||'', head=sel.head||(r&&r.head_commit)||(r&&r.head)||'';
    return 'repotrace-recs:'+repo+':'+base+':'+head;}
  function recStore(){try{const v=JSON.parse(localStorage.getItem(recKey())||'{}'); return v&&typeof v==='object'&&!Array.isArray(v)?v:{};}catch(e){return {};}}
  function recPatch(id, patch){const all=recStore(); all[id]=Object.assign({dismissed:false,saved:false,completed:false}, all[id]||{}, patch); try{localStorage.setItem(recKey(), JSON.stringify(all));}catch(e){}}
  function rerenderHere(){nav.patch({scroll:$('content').scrollTop});render();}
  function levelText(level){return {1:'Level 1 · Immediate',2:'Level 2 · Correlation',3:'Level 3 · Hypothesis, not a measurement',4:'Level 4 · Choice'}[level]||('Level '+level);}
  function actionLabel(action){if(!action)return 'Next'; if(action.kind==='copy-test')return 'Copy test command'; if(action.kind==='node')return 'Open this definition';
    const names={overview:'What to check',changes:'Changed code',tests:'Related tests',limits:'Limits and history',recommendations:'Recommendations'}; return 'Open '+(names[action.target]||'the review');}
  function answerRecs(r, question){
    const items=(r&&r.recommendations)||[], stop=new Set('the a an of to in and or for is are be this that it with as on by from at what which how do does did was were not'.split(' '));
    const tokens=(String(question||'').toLowerCase().match(/[a-z0-9_]+/g)||[]).filter(word=>word.length>2&&!stop.has(word));
    if(!tokens.length) return {matched:false, note:'Type a word from a file, a test, or a recommendation. This searches this review; it is not an AI answer.', hits:[]};
    const hits=[];
    items.forEach(item=>{const ev=item.evidence||{};
      const blob=[item.recommendation,item.reasoning,ev.summary,...(ev.facts||[]),...(ev.names||[]),...(ev.files||[]),item.implementation,...(item.risks||[]),...(item.dependencies||[]),item.expected_impact].join(' ').toLowerCase();
      const found=tokens.filter(word=>blob.includes(word));
      if(found.length) hits.push({id:item.id, recommendation:item.recommendation, evidence:ev.summary||'', matched:found});});
    hits.sort((a,b)=>b.matched.length-a.matched.length||String(a.id).localeCompare(String(b.id)));
    if(!hits.length) return {matched:false, note:'Nothing in this review matches those words.', hits:[]};
    return {matched:true, note:'Matched by searching this review’s recommendations. This is not an AI answer.', hits:hits.slice(0,3)};
  }
  function evidenceBlock(ev){
    const facts=(ev.facts||[]).map(fact=>`<li>${h(fact)}</li>`).join(''), names=(ev.names||[]).filter(Boolean).join(', '), files=(ev.files||[]).filter(Boolean).join(', ');
    const counts=Object.keys(ev.counts||{}).map(key=>key+' '+ev.counts[key]).join(' · ');
    return `<div class="rec-block"><p class="field-label">${h(ev.label||'Evidence')}</p><p>${h(ev.summary||'')}</p>${facts?`<ul class="limit-list">${facts}</ul>`:''}${names?`<p class="file-line">${h(names)}</p>`:''}${files?`<p class="file-line">${h(files)}</p>`:''}${counts?`<p class="file-line">${h(counts)}</p>`:''}</div>`;
  }
  function explainBlock(item){const risks=(item.risks||[]).map(risk=>`<li>${h(risk)}</li>`).join('');
    return `<div class="rec-block"><p class="field-label">Reasoning</p><p>${h(item.reasoning||'')}</p>${evidenceBlock(item.evidence||{})}<p class="field-label">Risks</p><ul class="limit-list">${risks}</ul></div>`;}
  function planBlock(item){
    const steps=[]; if(item.implementation) steps.push(item.implementation); (item.dependencies||[]).forEach(step=>steps.push(step));
    const action=item.next_action||{}; if(action.kind) steps.push(actionLabel(action)+(action.kind==='copy-test'?': '+action.target:''));
    return `<div class="rec-block"><p class="field-label">Action plan</p><ol class="limit-list">${steps.map(step=>`<li>${h(step)}</li>`).join('')}</ol></div>`;
  }
  function recCard(item, st){
    const id=h(item.id), ev=item.evidence||{}, conf=item.confidence||{}, open=recUI.open[item.id]||{}, on=recUI.selected.indexOf(item.id)>=0;
    const tags=`<span class="tag">${h(levelText(item.level))}</span><span class="tag">${h(item.priority)}</span><span class="tag">${h(ev.label||'')}</span>${st.saved?'<span class="tag">Saved</span>':''}${st.completed?'<span class="tag supported">Completed</span>':''}${st.dismissed?'<span class="tag">Dismissed</span>':''}`;
    const buttons=[
      `<button type="button" class="small-button" data-action="rec-evidence" data-rec="${id}" aria-expanded="${open.evidence?'true':'false'}">${open.evidence?'Hide evidence':'Evidence'}</button>`,
      `<button type="button" class="small-button" data-action="rec-explain" data-rec="${id}" aria-expanded="${open.explain?'true':'false'}">Explain</button>`,
      `<button type="button" class="small-button" data-action="rec-plan" data-rec="${id}" aria-expanded="${open.plan?'true':'false'}">Action plan</button>`,
      `<button type="button" class="primary" data-action="rec-go" data-rec="${id}">${h(actionLabel(item.next_action))}</button>`,
      `<button type="button" class="small-button" data-action="rec-select" data-rec="${id}" aria-pressed="${on?'true':'false'}">${on?'Selected':'Compare'}</button>`,
      `<button type="button" class="small-button" data-action="rec-save" data-rec="${id}" aria-pressed="${st.saved?'true':'false'}">${st.saved?'Saved':'Save'}</button>`,
      `<button type="button" class="small-button" data-action="rec-done" data-rec="${id}" aria-pressed="${st.completed?'true':'false'}">${st.completed?'Completed':'Mark completed'}</button>`,
      st.dismissed?`<button type="button" class="small-button" data-action="rec-undismiss" data-rec="${id}">Restore</button>`:`<button type="button" class="small-button" data-action="rec-dismiss" data-rec="${id}">Dismiss</button>`
    ].join('');
    return `<article class="rec-card${st.completed?' is-done':''}${st.dismissed?' is-dismissed':''}" id="rec-${id}" data-rec-id="${id}" data-dismissed="${st.dismissed?'true':'false'}"><div class="rec-top">${tags}</div><h3>${h(item.recommendation)}</h3><p>${h(item.expected_impact)}</p><p class="help-text">Confidence ${h(conf.level||'')}. ${h(conf.basis||'')}</p><div class="action-row">${buttons}</div>${open.evidence?evidenceBlock(ev):''}${open.explain?explainBlock(item):''}${open.plan?planBlock(item):''}</article>`;
  }
  function compareHtml(items){
    if(recUI.selected.length<2) return '';
    const pair=recUI.selected.map(id=>items.find(item=>item.id===id)).filter(Boolean); if(pair.length<2) return '';
    const col=item=>{const risks=(item.risks||[]).map(risk=>`<li>${h(risk)}</li>`).join('');
      return `<div class="rec-card"><p class="field-label">Comparing</p><h3>${h(item.recommendation)}</h3><p>${h(item.expected_impact)}</p><p class="help-text">${h(levelText(item.level))} · ${h(item.priority)} · confidence ${h((item.confidence||{}).level||'')}</p><p>${h(item.reasoning||'')}</p><ul class="limit-list">${risks}</ul></div>`;};
    return `<div class="compare-grid" aria-label="Two recommendations side by side">${pair.map(col).join('')}</div>`;
  }
  function answerHtml(ans){
    if(!ans) return '';
    const hits=(ans.hits||[]).map(hit=>`<li><p>${h(hit.recommendation)}</p><p class="file-line">${h(hit.evidence||'')}</p><button type="button" class="small-button" data-action="rec-focus" data-rec="${h(hit.id)}">Show this recommendation</button></li>`).join('');
    return `<div class="notice neutral" id="rec-answer"><strong>${ans.matched?'Matches in this review':'No match'}</strong><p>${h(ans.note)}</p>${hits?`<ul class="limit-list">${hits}</ul>`:''}</div>`;
  }
  function recommendationsPage(r){
    const all=r.recommendations||[], store=recStore(), visible=all.filter(item=>recUI.showDismissed||!(store[item.id]&&store[item.id].dismissed));
    const dismissed=all.filter(item=>store[item.id]&&store[item.id].dismissed).length, counts={now:0,soon:0,later:0};
    all.forEach(item=>{if(counts[item.priority]!=null) counts[item.priority]++;});
    const note=`<p class="help-text">Level 1 is a concern, a test that lost its path, or a changed definition with no connected test. Level 2 is a history correlation, not a cause. Level 3 is not shown: this review has no probability model, so there is no forecast. Level 4 is one choice between two actions already listed. The box below searches these items. It is not an AI answer. Dismissed, saved and completed stay on this computer, in this browser, for this folder and this base and head. They are not uploaded.</p>`;
    const bar=`<div class="action-row"><button type="button" class="small-button" data-action="rec-show-dismissed" aria-pressed="${recUI.showDismissed?'true':'false'}">${recUI.showDismissed?'Hide dismissed':'Show dismissed'}</button><span class="help-text">${all.length} recommendation${all.length===1?'':'s'} · ${counts.now} now · ${counts.soon} soon · ${counts.later} later${dismissed?` · ${dismissed} dismissed on this computer`:''}</span></div>`;
    const ask=`<form id="rec-ask" class="ask-row" role="search"><label class="field-label" for="rec-q">Ask a follow-up</label><input id="rec-q" type="search" maxlength="200" autocomplete="off" value="${h(recUI.query)}" placeholder="A file, a test, or a word from a recommendation" aria-label="Search this review"><button class="primary" type="submit">Search this review</button></form>`;
    const body=!all.length?`<div class="empty"><h2>No recommendation met the evidence rules.</h2><p>Nothing here was invented to fill the list. A forecast is not shown, because RepoTrace has no probability model.</p></div>`
      :!visible.length?`<div class="empty"><h2>Every recommendation is dismissed.</h2><p>Show dismissed to bring them back. That choice stays on this computer.</p></div>`
      :`<div class="rec-list">${visible.map(item=>recCard(item, store[item.id]||{})).join('')}</div>`;
    return `${note}${bar}${ask}${answerHtml(recUI.answer)}${compareHtml(all)}${body}`;
  }
  function canRunAgain(){const r=recReview(); return !!(window.RTPicker&&D.project&&D.project.repo&&r&&r.selection&&r.selection.base&&r.selection.head);}
  function runAgain(){const r=recReview(); if(!canRunAgain()){toast('This page has no stored comparison to run again.');return;} RTPicker.run({repo:D.project.repo, base:r.selection.base, head:r.selection.head, reverse:!!r.reversed});}
  function analysisRows(){
    const repo=(D.project&&D.project.repo)||'', version=(D.graphs&&D.graphs.main&&D.graphs.main.version)||'', r=(D.reviews||[])[0];
    const rows=[['Folder', repo||'No folder path is stored with this analysis'], ['Working copy, from the analysis', version||'Not recorded']];
    if(r){rows.push(['Comparison start', (r.base||'')+(r.base_commit?' · '+r.base_commit:'')]); rows.push(['Comparison end', (r.head||'')+(r.head_commit?' · '+r.head_commit:'')]);}
    return rows;
  }
  function gitTable(rows){return `<dl class="git-grid">${rows.map(([key,value])=>`<dt>${h(key)}</dt><dd>${h(value)}</dd>`).join('')}</dl>`;}
  function integrationsSection(){
    const ready=!!(D.reviews||[])[0];
    return `<section id="integrations" aria-labelledby="integrations-title"><div class="section-heading"><h2 id="integrations-title">Integrations</h2></div><p class="help-text">The local folder below is the only Git connection. Disconnect does not apply: the folder is already on this computer, and closing the page is what stops RepoTrace from reading it.</p>${gitTable(analysisRows())}<div id="git-live"><p class="help-text">Local Git status has not been read yet.</p></div><div class="action-row"><button type="button" class="secondary" data-action="git-refresh">Refresh local Git</button><button type="button" class="secondary" data-action="export-json" ${ready?'':'disabled'}>Export review JSON</button><button type="button" class="secondary" data-action="export-csv" ${ready?'':'disabled'}>Export review CSV</button></div><p class="help-text">The file is built in this browser from the review already on screen. Nothing is uploaded.</p><p class="help-text">GitHub, GitLab, Bitbucket, Slack, Discord, Notion, Jira, Trello, Linear, Google, Microsoft 365, databases and webhooks are not connected. This app has no OAuth client id or client secret, and sending a repository to those services would leave this computer. Analysis stays here.</p></section>`;
  }
  function automationSection(){
    const ready=canRunAgain();
    return `<section id="automation" aria-labelledby="automation-title"><div class="section-heading"><h2 id="automation-title">Run this comparison again</h2></div><p class="help-text">Re-runs the saved base and head on this computer. It does not change the Git repository, and it does not run tests. Copying a test command is still the way to take one away.</p><div class="action-row"><button type="button" class="primary" data-action="run-again" id="run-again" ${ready?'':'disabled'}>Run this comparison again</button></div>${ready?'':'<p class="help-text">This page has no stored base and head to run again.</p>'}</section>`;
  }
  function paintGit(){
    const box=$('git-live'); if(!box) return;
    if(recUI.gitLoading){box.innerHTML='<p class="help-text">Reading the local Git repository…</p>'; return;}
    if(recUI.gitError){const notGit=/not a Git project|not a git repository/i.test(recUI.gitError); box.innerHTML=`<p><strong>${notGit?'Not a Git repository':'Git status unavailable'}</strong></p><p>${h(recUI.gitError)}</p>`; return;}
    if(!recUI.git){box.innerHTML='<p class="help-text">Local Git status has not been read yet.</p>'; return;}
    const g=recUI.git, branch=g.current_branch==='HEAD'?'detached HEAD':(g.current_branch||''), listed=String(g.commit_count)+(Number(g.commit_count)>=80?' (inspect returns at most 80)':'');
    box.innerHTML=gitTable([['Status','Connected to this local folder'],['Branch', branch],['Dirty working tree', g.dirty?'Yes, uncommitted changes':'No uncommitted changes'],['HEAD', g.head||''],['Recent commits listed', listed]]);
  }
  async function refreshGit(force){
    const repo=D.project&&D.project.repo;
    if(!repo){recUI.git=null; recUI.gitError='No local folder is stored with this page, so there is nothing to inspect.'; recUI.gitLoading=false; paintGit(); return;}
    if(recUI.gitLoading) return;
    if((recUI.git||recUI.gitError)&&!force){paintGit(); return;}
    recUI.gitLoading=true; recUI.gitError=null; paintGit();
    try{
      const res=await fetch('/api/inspect',{method:'POST',headers:{'X-RepoTrace-Token':token,'Content-Type':'application/json'},body:JSON.stringify({repo})});
      const data=await res.json().catch(()=>({error:'The app answered in a way this page cannot read.'}));
      if(!res.ok) throw new Error(data.error||('The request failed ('+res.status+').'));
      recUI.git=data; recUI.gitError=null;
    }catch(e){recUI.git=null; recUI.gitError=e.message||String(e);}
    finally{recUI.gitLoading=false;}
    paintGit();
  }
  function bindSettings(){bindThemes(); if(recUI.gitLoading) paintGit(); else refreshGit(false);}
  function csvCell(value){const text=String(value==null?'':value); return /[",\n]/.test(text)?'"'+text.replace(/"/g,'""')+'"':text;}
  function reviewCsv(r){
    const lines=['section,name,file,detail,extra'], row=(...cells)=>lines.push(cells.map(csvCell).join(','));
    (r.changed||[]).forEach(change=>row('changed', change.name, change.file, change.change, change.line||''));
    (r.concerns||[]).forEach(concern=>row('concern', concern.title, concern.file, concern.what, concern.line||''));
    (r.tests||[]).forEach(test=>row('test', test.name, test.file, test.run||'', (test.seen_in||[]).join(' ')));
    (r.recommendations||[]).forEach(item=>row('recommendation', item.recommendation, ((item.evidence&&item.evidence.files)||[]).join(' '), item.priority, String(item.level)+' '+((item.confidence&&item.confidence.level)||'')));
    return lines.join('\n')+'\n';
  }
  function reviewJson(r){
    return JSON.stringify({project:{name:D.project.name, repo:D.project.repo||null, made_at:D.project.made_at||null}, label:r.label, base:r.base, head:r.head,
      base_commit:r.base_commit||null, head_commit:r.head_commit||null, reversed:!!r.reversed, selection:r.selection||null, changed:r.changed||[], concerns:r.concerns||[],
      tests:r.tests||[], removed_tests:r.removed_tests||[], gaps:r.gaps||[], history:r.history||[], test_plan:r.test_plan||null, unresolved:r.unresolved||[], recommendations:r.recommendations||[]}, null, 1);
  }
  function download(filename, mime, text){
    const blob=new Blob([text],{type:mime}), url=URL.createObjectURL(blob), link=document.createElement('a');
    link.href=url; link.download=filename; document.body.appendChild(link); link.click(); link.remove();
    setTimeout(()=>URL.revokeObjectURL(url), 1500); toast('Download started. Nothing was uploaded.');
  }
  function exportReview(kind){
    const r=recReview(); if(!r){toast('There is no review to export.');return;}
    const slug=String((D.project&&D.project.name)||'review').replace(/[^A-Za-z0-9._-]+/g,'-').slice(0,40)||'review';
    if(kind==='csv') download(slug+'-review.csv','text/csv;charset=utf-8',reviewCsv(r));
    else download(slug+'-review.json','application/json',reviewJson(r));
  }
  function followAction(action){
    if(!action) return;
    if(action.kind==='review-tab') go({page:'review', tab:action.target||'overview', node:null, walk:false});
    else if(action.kind==='node') openNode(Number(action.target));
    else if(action.kind==='copy-test') copyText(String(action.target||''));
  }
  function reviewPage(){
    const r=review();
    const banner=r.reversed?`<div class="reverse-banner" role="note">${ico('undo')}<div><strong>This review is reversed.</strong><p>It runs the same two versions backwards, from ${h(r.base)} to ${h(r.head)}. What the later version added shows as removed, and what it removed shows as added. Your Git repository was not changed.</p></div></div>`:'';
    const again=canRunAgain()?'<button class="small-button" data-action="run-again">Run this comparison again</button>':'';
    const others=D.reviews.length>1?'<button class="small-button" data-action="reviews">Other saved changes</button>':'';
    const more=(again||others)?`<div class="action-row">${others}${again}</div>`:'';
    const tabs=[['overview','What to check',r.concerns.length],['changes','Changed code',r.changed.length],['tests','Related tests',r.tests.length],['limits','Limits & history',null],['recommendations','Recommendations',(r.recommendations||[]).length]];
    const panel=({overview,changes,tests,limits,recommendations:recommendationsPage})[state().tab]||overview;
    return `<div class="compact review"><header class="review-top"><div class="review-id"><span class="eyebrow">${LIVE?'Your comparison':'Saved example · not a live warning'} · ${h(PROJECT)}</span><h1>${h(reviewTitle(r))}</h1></div>${direction(r)}</header>${banner}${metrics(r)}${more}<nav class="tabs" aria-label="Change review sections">${tabs.map(([tab,title,count])=>`<button data-action="review-tab" data-tab="${tab}" ${state().tab===tab?'aria-current="page"':''}>${title}${count===null?'':`<span class="count-pill">${count}</span>`}</button>`).join('')}</nav>${panel(r)}</div>`;
  }
  function scopeText(){return state().review===null?(COMMIT?`Working snapshot · ${COMMIT}`:'Working folder, as analysed'):`${review().head} + marked before-version evidence`;}
  // The explorer lives in one frame that is created on the first visit and only hidden afterwards, so coming back is instant and keeps its zoom and selection.
  const holder=document.createElement('div');holder.id='xp-holder';holder.hidden=true;$('content').after(holder);
  let xpReady=false,lastPage=null;
  function ensureExplorer(){
    if($('xp'))return;
    const f=document.createElement('iframe');f.id='xp';f.className='xp-frame';f.src=LAB_BASE+'explore.html';
    f.title='Explore the project: a connected graph of your code. The same information is in the lists beside it.';
    holder.appendChild(f);
  }
  function showExplorer(s,entering){
    holder.hidden=false;ensureExplorer();
    if(xpReady&&entering||xpReady&&(s.exploreFocus!=null||s.exploreQuery||s.palette)){
      const f=$('xp'),intent=s.exploreFocus!=null||!!s.exploreQuery||s.palette;
      f.contentWindow.postMessage({rt:'navigate',review:s.exploreReview,focus:s.exploreFocus,query:s.exploreQuery,palette:!!s.palette,keep:!!s.exploreKeep&&!intent,restore:entering&&!intent&&!s.exploreKeep?s.explore:null},TARGET_ORIGIN);
      nav.patch({exploreFocus:null,exploreQuery:'',palette:false,exploreKeep:false});f.focus();
    }
  }

  function settingsPage(){
    const themes=window.RTTheme.themes, saved=window.RTTheme.current(), active=window.RTTheme.get(saved);
    const cards=themes.map(t=>`<button type="button" class="theme-card${t.id===saved?' is-saved':''}" data-theme-id="${h(t.id)}" data-action="choose-theme" aria-pressed="${t.id===saved}" aria-label="${h(t.name)}, ${t.kind} theme"><span class="theme-swatch" style="background:${t.bg};color:${t.text};border-color:${t.border}"><span class="theme-dots" aria-hidden="true"><i style="background:${t.accent}"></i><i style="background:${t.success}"></i><i style="background:${t.warning}"></i><i style="background:${t.error}"></i></span><strong>Aa</strong><small style="color:${t.muted}">${h(t.name)}</small></span></button>`).join('');
    return `<div class="compact settings-page"><div class="page-header"><span class="eyebrow">SETTINGS</span><h1>Settings</h1><p>Appearance is saved on this computer and applied the next time RepoTrace opens.</p></div><section aria-labelledby="appearance-title"><div class="section-heading"><h2 id="appearance-title">Appearance</h2><span id="theme-caption">${h(active.name)} · saved</span></div><div class="appearance-stage" aria-hidden="true"><div class="preview-window"><div class="preview-top"><span></span><span></span><span></span><b>RepoTrace</b></div><p>Find what a change left behind.</p><div class="preview-chips"><span class="chip accent">Accent</span><span class="chip ok">Success</span><span class="chip warn">Warning</span><span class="chip bad">Error</span></div></div></div><div class="theme-grid" role="list">${cards}</div></section>${integrationsSection()}${automationSection()}</div>`;
  }
  function bindThemes(){
    const caption=$('theme-caption');
    const label=(id,mode)=>{const theme=window.RTTheme.get(id); if(caption) caption.textContent=`${theme.name} · ${mode}`;};
    document.querySelectorAll('[data-theme-id]').forEach(card=>{
      const id=card.dataset.themeId;
      card.addEventListener('pointerenter',()=>{window.RTTheme.preview(id); label(id,'preview');});
      card.addEventListener('pointerleave',()=>{const saved=window.RTTheme.restore(); label(saved.id,'saved');});
      card.addEventListener('focus',()=>{window.RTTheme.preview(id); label(id,'preview');});
      card.addEventListener('blur',()=>{const saved=window.RTTheme.restore(); label(saved.id,'saved');});
    });
  }
  function render(){
    const s=state();
    $('navigation').innerHTML=[['start','home','Start','home'],['map-home','map','Explore the project','explore'],['reviews','review','Check a change','reviews'],['files','folder','File list','browse'],['settings','sliders','Settings','settings']].map(([action,icon,text,page])=>`<button class="nav-item ${s.page===page||action==='reviews'&&s.page==='review'?'active':''}" data-action="${action}" title="${text}" ${s.page===page?'aria-current="page"':''}><span class="nav-icon" aria-hidden="true">${ico(icon)}</span><span class="nav-text">${text}</span>${action==='reviews'&&D.reviews.length?`<span class="nav-count">${D.reviews.length}</span>`:''}</button>`).join('');
    $('back').disabled=!nav.past.length;
    $('location').textContent=({home:'Start',search:'Find code',explore:'Explore the project',browse:'Browse files',function:'Read the code',reviews:'Check a change',review:'Check a change',settings:'Settings'})[s.page];
    $('snapshot').textContent=s.review===null?VERSION:review().base+' → '+review().head;
    $('status-context').textContent=s.review===null?(LIVE?'Working folder, as analysed':'Saved working snapshot'):review().head+' comparison';
    const entering=s.page==='explore'&&lastPage!=='explore';lastPage=s.page;
    if(s.page==='explore'){$('content').hidden=true;showExplorer(s,entering);}
    else{
      holder.hidden=true;$('content').hidden=false;
      try{$('content').innerHTML=({home,browse:browsePage,function:functionPage,reviews:reviewsPage,review:reviewPage,settings:settingsPage})[s.page]();}
      catch(err){console.error(String(err&&err.stack||err).split('\n').slice(0,4).join(' | '));$('content').innerHTML='<div class="compact"><div class="notice"><strong>This page could not be drawn.</strong><p>Go back, or open Start from the left. Nothing was changed in your project.</p></div><button class="secondary" data-action="start">Go to Start</button></div>';}
      const c=$('content');c.classList.remove('page-in');void c.offsetWidth;c.classList.add('page-in');
    }
    document.title=`${$('location').textContent} · RepoTrace`;
    if(s.page!=='explore'){$('content').scrollTop=s.scroll||0;$('content').focus({preventScroll:true});}
    if(s.page==='settings')bindSettings();
    const form=$('search-form');if(form)form.addEventListener('submit',event=>{event.preventDefault();doSearch($('query').value);});
    const askForm=$('rec-ask');if(askForm)askForm.addEventListener('submit',event=>{event.preventDefault();const box=$('rec-q');recUI.query=box?box.value:'';recUI.answer=answerRecs(recReview(), recUI.query);rerenderHere();const again=$('rec-q');if(again){again.focus();const n=again.value.length;try{again.setSelectionRange(n,n);}catch(e){}}});
    document.querySelector('.shell').classList.toggle('xp-wide',s.page==='explore');
    document.querySelectorAll('.dock [data-action]').forEach(btn=>{
      const action=btn.dataset.action;
      const on=(action==='start'&&s.page==='home')||(action==='reviews'&&(s.page==='review'||s.page==='reviews'))||(action==='map-home'&&s.page==='explore')||(action==='files'&&(s.page==='browse'||s.page==='function'))||(action==='settings'&&s.page==='settings');
      btn.classList.toggle('active',on);
      if(on)btn.setAttribute('aria-current','page'); else btn.removeAttribute('aria-current');
    });       // the explorer gets the whole width
  }
  let toastTimer;
  function toast(message){$('toast').textContent=message;$('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('show'),3000);}
  async function copyText(command){
    if(!command){toast('No command is stored for this item.');return;}
    try{await navigator.clipboard.writeText(command);toast('Command copied. Nothing was executed.');}
    catch(e){const area=document.createElement('textarea');area.value=command;area.style.position='fixed';area.style.left='-10000px';document.body.appendChild(area);area.select();let ok=false;try{ok=document.execCommand('copy');}catch(err){}area.remove();toast(ok?'Command copied. Nothing was executed.':'Copy unavailable here. Select the visible command and copy it.');}
  }
  function copyCommand(index){copyText(review()?.tests[index]?.run);}
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-action]');if(!button||button.disabled||busy())return;
    const action=button.dataset.action;
    if(action==='start')start();
    else if(action==='back')back();
    else if(action==='find')openPalette();
    else if(action==='email'){openMap(null,{query:walk?walk.q:'',graph:'main',review:null,walk:true});}
    else if(action==='ask')doSearch(button.dataset.query);
    else if(action==='node'||action==='map-node')openNode(Number(button.dataset.id));
    else if(action==='map-home')openMap(null,{walk:false,keep:true});
    else if(action==='read-code')go({page:'function',node:Number(button.dataset.id)});
    else if(action==='show-evidence'){nav.patch({evidenceOpen:!state().evidenceOpen,scroll:$('content').scrollTop});render();if(state().evidenceOpen)$('review-evidence')?.focus({preventScroll:true});}
    else if(action==='files')go({page:'browse',node:null,walk:false});
    else if(action==='reviews'){if(D.reviews.length===1)go({page:'review',graph:D.reviews[0].graph,review:0,tab:'overview',node:null,query:'',walk:false,evidenceOpen:false});else go({page:'reviews',graph:'main',review:null,node:null,walk:false});}
    else if(action==='review'){const i=Number(button.dataset.index);go({page:'review',graph:D.reviews[i].graph,review:i,tab:button.dataset.tab||'overview',node:null,query:'',walk:false,evidenceOpen:false});}
    else if(action==='review-tab')go({page:'review',tab:button.dataset.tab,node:null,walk:false});
    else if(action==='expand'){nav.patch({expanded:true,scroll:$('content').scrollTop});render();}
    else if(action==='copy-test')copyCommand(Number(button.dataset.index));
    else if(action==='rec-evidence'||action==='rec-explain'||action==='rec-plan'){const id=button.dataset.rec, part=action.slice(4), cur=recUI.open[id]||{}; recUI.open[id]=Object.assign({}, cur, {[part]:!cur[part]}); rerenderHere();}
    else if(action==='rec-select'){const id=button.dataset.rec, at=recUI.selected.indexOf(id); if(at>=0)recUI.selected.splice(at,1); else{recUI.selected.push(id); if(recUI.selected.length>2)recUI.selected.shift();} rerenderHere();}
    else if(action==='rec-dismiss'){recPatch(button.dataset.rec,{dismissed:true}); rerenderHere();}
    else if(action==='rec-undismiss'){recPatch(button.dataset.rec,{dismissed:false}); rerenderHere();}
    else if(action==='rec-save'){const id=button.dataset.rec; recPatch(id,{saved:!(recStore()[id]&&recStore()[id].saved)}); rerenderHere();}
    else if(action==='rec-done'){const id=button.dataset.rec; recPatch(id,{completed:!(recStore()[id]&&recStore()[id].completed)}); rerenderHere();}
    else if(action==='rec-show-dismissed'){recUI.showDismissed=!recUI.showDismissed; rerenderHere();}
    else if(action==='rec-go'){const item=((recReview()&&recReview().recommendations)||[]).find(row=>row.id===button.dataset.rec); if(item) followAction(item.next_action);}
    else if(action==='rec-focus'){const id=button.dataset.rec; if(recStore()[id]&&recStore()[id].dismissed) recUI.showDismissed=true; recUI.open[id]=Object.assign({}, recUI.open[id], {explain:true}); rerenderHere(); const el=document.getElementById('rec-'+id); if(el) el.scrollIntoView({block:'nearest'});}
    else if(action==='git-refresh') refreshGit(true);
    else if(action==='export-json') exportReview('json');
    else if(action==='export-csv') exportReview('csv');
    else if(action==='run-again') runAgain();
    else if(action==='reverse-change'){
      const current=review(), chosen=current&&current.selection;
      if(!window.RTPicker||!D.project||!D.project.repo||!chosen){toast('This comparison cannot be reversed from here.');return;}
      RTPicker.run({repo:D.project.repo,base:chosen.base,head:chosen.head,reverse:!current.reversed});
    }
    else if(action==='settings')go({page:'settings',node:null,walk:false});
    else if(action==='choose-theme'){const chosen=window.RTTheme.apply(button.dataset.themeId);toast(chosen.name+' saved');render();}
    else if(action==='about')$('about-dialog').showModal();
    else if(action==='close-about')$('about-dialog').close();
  },{signal:ac.signal});
  document.addEventListener('change',event=>{if(event.target.dataset.filter){nav.patch({[event.target.dataset.filter]:event.target.checked,scroll:$('content').scrollTop,mapScroll:$('map-inspector')?.scrollTop||0});render();}},{signal:ac.signal});
  document.addEventListener('keydown',event=>{
    if(busy())return;
    const typing=/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'||event.key==='/'&&!typing){event.preventDefault();if(!$('about-dialog').open)openPalette();}
    if(event.altKey&&event.key==='ArrowLeft'&&!$('about-dialog').open){event.preventDefault();back();}
    if(event.key==='Escape'&&typing&&!$('about-dialog').open){document.activeElement.blur();return;}   // first Esc leaves the box, the next goes back
    if(event.key==='Escape'&&!$('about-dialog').open&&!typing&&nav.past.length)back();
  },{signal:ac.signal});
  for(const el of document.querySelectorAll('[data-project]'))el.textContent=PROJECT;
  const card=document.querySelector('.project');if(card)card.hidden=false;const railLabel=$('rail-label');if(railLabel)railLabel.hidden=false;
  const setText=(id,text)=>{const el=$(id);if(el)el.textContent=text;};
  setText('project-icon',(PROJECT||'?')[0].toUpperCase());
  setText('project-sub',LIVE?'Python · your project':'Python · saved project');
  setText('rail-title',LIVE?'Analysed on this computer':'Interactive prototype');
  setText('rail-note',LIVE?'Nothing is uploaded.\nNo repository is changed.':'Real code. Saved analysis.\nNo repository is changed.');
  const note=$('rail-note');if(note)note.innerHTML=LIVE?'Nothing is uploaded.<br>No repository is changed.':'Real code. Saved analysis.<br>No repository is changed.';
  setText('about-eyebrow',LIVE?'WHAT THIS SHOWS':'PROTOTYPE BOUNDARY');
  if(!LIVE){const intro=$('about-intro');if(intro)intro.innerHTML='This is a separate UX prototype using saved RepoTrace analysis of <span id="about-source">a project</span>. It does not upload, scan, edit, or execute a repository.';const cmp=$('about-compare');if(cmp)cmp.textContent='Change review and test lists come from the saved comparisons. Commands are copied, never executed here.';}
  const about=$('about-source');if(about)about.textContent=`${PROJECT}, analysed on ${D.project.made_at||'an earlier date'}`;
  window.addEventListener('message',ev=>{                                    // the explorer (in its frame) talking to the app
    const frame=$('xp');if(!frame||ev.source!==frame.contentWindow)return;
    const m=ev.data||{},s=state(),here=s.page==='explore';
    if(m.rt==='ready'){frame.contentWindow.postMessage({rt:'init',data:D,theme:(window.RTTheme?RTTheme.current():document.documentElement.getAttribute('data-theme')),review:s.exploreReview,focus:s.exploreFocus,query:s.exploreQuery,restore:s.explore,palette:!!s.palette},TARGET_ORIGIN);nav.patch({exploreFocus:null,exploreQuery:'',palette:false,exploreKeep:false});xpReady=true;holder.classList.add('ready');if(here)frame.focus();return;}
    if(m.rt==='open-review'&&D.reviews.length){const i=D.reviews[m.index]?m.index:0, r=D.reviews[i]; go({page:'review',graph:r.graph,review:i,tab:m.tab||'overview',node:null,query:'',walk:false,evidenceOpen:false}); return;}
    if(!here)return;                                                          // a hidden explorer never moves the app
    if(m.rt==='state'&&m.snapshot)nav.patch({explore:m.snapshot,exploreReview:m.snapshot.review,review:m.snapshot.review,graph:m.snapshot.graph});
    else if(m.rt==='read')go({page:'function',graph:m.graph,review:m.review,node:m.id,walk:false});
    else if(m.rt==='review'&&D.reviews[m.index]){const r=D.reviews[m.index];go({page:'review',graph:r.graph,review:m.index,tab:'overview',node:null,query:'',walk:false,evidenceOpen:false});}
    else if(m.rt==='escape')back();
  },{signal:ac.signal});
  render();
};
if(window.PROTO)window.RTLab(window.PROTO);                           // saved data (the prototype)
else if(window.RTPicker)window.RTPicker.start(window.RTLab);       // live: choose a project, analyse it, then start
