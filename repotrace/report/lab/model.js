(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.RTModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const words = text => String(text || '').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase().match(/[a-z0-9]+/g) || [];
  const stop = new Set('a an the is are do does did how where what when why which in of to for and or with by it this that happens happen handled handle implemented checked'.split(' '));
  const groups = [ ['check','checks','checked','valid','validate','validation','rule','rules'], ['save','saved','store','stored','storage','persist','write','json','file'], ['student','students'], ['course','courses'], ['enroll','enrol','enrolls','enrollment','enrollments','enrolment','join'], ['full','capacity','seats','seat','maximum'], ['email','emails'], ['create','created','creates','add','added','new'], ['delete','deleted','deletes','remove','removed'], ['find','fetch','get','read','lookup','look'], ['update','updates','change','edit'], ['list','all','show','print'], ['phone','number'] ];
  const dunder = /^__.*__$/;
  // How much other project code uses a definition (calls into it, tests excluded). Breaks ties between equal word matches.
  function inboundCounts(graph) {
    if (graph._inbound) return graph._inbound;
    const counts = {};
    for (const [from, to] of graph.links) if (!graph.nodes[from].test) counts[to] = (counts[to] || 0) + 1;
    return graph._inbound = counts;
  }
  function search(graph, query, limit = 40) {
    const terms = [...new Set(words(query).filter(w => !stop.has(w)))];
    if (!terms.length) return [];
    const variants = terms.map(term => ({term, all: groups.find(g => g.includes(term)) || [term]}));
    const inbound = inboundCounts(graph);
    return graph.nodes.filter(n => n.k === 'fn' || n.k === 'class' || n.k === 'file').map(n => {
      const nameTokens = new Set(words(n.n)), qualTokens = new Set(words(n.q || n.n));
      const fields = [[nameTokens, 9], [qualTokens, 6], [new Set(words(n.file)), 3], [new Set(words(n.doc)), 2.5], [new Set(words(n.code)), 0.7]];
      const matched = [];
      let score = 0;
      for (const {term, all} of variants) {
        let best = 0;
        for (const [tokens, weight] of fields) if (all.some(t => tokens.has(t))) best = Math.max(best, weight);
        if (best) { score += best; matched.push(term); }
      }
      if (!score) return {node:n, score:0, matched};
      if ((n.q || n.n).toLowerCase() === query.trim().toLowerCase()) score += 50;
      if (matched.length === terms.length) score += 12;
      // A definition other code uses a lot is a better starting point than a helper nobody calls.
      score += Math.min(6, Math.log2(1 + (inbound[n.i] || 0)) * 2);
      if (n.test) score *= 0.65;
      if (n.gone) score *= 0.8;
      if (dunder.test(n.n) || /^(to|from)_dict$/.test(n.n)) score *= 0.7;        // plumbing, rarely what a question is about
      if (/(^|\/)demo\.py$/.test(n.file) || /^demo_/.test(n.n)) score *= 0.75;  // demo scripts restate the real code
      return {node:n, score, matched};
    }).filter(r => r.score > 0).sort((a,b) => b.score-a.score || ({fn:0,class:1,file:2}[a.node.k]-{fn:0,class:1,file:2}[b.node.k]) || a.node.q.length-b.node.q.length || a.node.q.localeCompare(b.node.q)).slice(0,limit);
  }
  function neighbors(graph, id, opts = {}) {
    const incoming = [], outgoing = [];
    for (const [from,to,strength,count,ver=0] of graph.links) {
      if (from !== id && to !== id) continue;
      if (opts.possible === false && strength === 1) continue;
      if (opts.old === false && ver === 1) continue;                // links that existed only in the older version
      const other = graph.nodes[from === id ? to : from];
      if (!other || (opts.tests === false && other.test)) continue;
      // strength (0 sure, 1 maybe) and ver (1 = only in the older version) are separate facts; `tier` is just a sort key
      const edge = {node:other, strength, ver, tier: ver === 1 ? 2 : strength, count, from, to};
      if (to === id) incoming.push(edge);
      if (from === id) outgoing.push(edge);
    }
    const sort = (a,b) => a.tier-b.tier || Number(a.node.test)-Number(b.node.test) || a.node.q.localeCompare(b.node.q);
    return {incoming:incoming.sort(sort),outgoing:outgoing.sort(sort)};
  }
  function children(graph, id) { return graph.nodes.filter(n => n.p === id); }
  function parents(graph, id) {
    const result = [], seen = new Set();
    let n = graph.nodes[id];
    while(n && !seen.has(n.i)) { seen.add(n.i); result.unshift(n); n = n.p == null ? null : graph.nodes[n.p]; }
    return result;
  }
  // Tests whose code reaches this function by following recorded calls backwards. A unittest setUp/setUpClass hook
  // that reaches it counts for every test of its class (the runner calls the hook before each test). pytest fixtures
  // are not in this saved data, so tests that reach the function only through a fixture are missing here.
  const HOOKS = new Set(['setUp','setUpClass','asyncSetUp','setup_method','setup_class','setUpModule','setup_module','setup_function']);
  function testPaths(graph, id) {
    const seen = new Set([id]), queue = [{id,chain:[id],tier:0}], found=[], have=new Set();
    const add = path => { if(!have.has(path.id)) { have.add(path.id); found.push(path); } };
    while(queue.length) {
      const item=queue.shift();
      for(const edge of neighbors(graph,item.id,{old:false}).incoming) {          // an old-version-only link does not reach this function now
        const n=edge.node;
        if(seen.has(n.i)) continue;
        seen.add(n.i);
        const path={id:n.i,chain:[n.i,...item.chain],tier:Math.max(item.tier,edge.strength)};
        if(n.test && /^test/.test(n.n)) add(path);
        else if(n.test && HOOKS.has(n.n)) {
          const scope = n.p==null ? graph.nodes.filter(t=>t.file===n.file) : children(graph,n.p);
          for(const t of scope) if(/^test/.test(t.n) && t.k==='fn') add({id:t.i,chain:[t.i,...path.chain],tier:path.tier,via:n.q});
          queue.push(path);
        }
        else queue.push(path);
      }
    }
    return found.sort((a,b)=>a.tier-b.tier||a.chain.length-b.chain.length||graph.nodes[a.id].q.localeCompare(graph.nodes[b.id].q));
  }
  class Navigation {
    constructor(initial) {this.current={...initial};this.past=[];}
    go(next,scroll=0) {this.past.push({...this.current,scroll});this.current={...this.current,...next,scroll:0};return this.current;}
    back() {if(this.past.length)this.current=this.past.pop();return this.current;}
    patch(values) {Object.assign(this.current,values);}
  }
  return {search,neighbors,children,parents,testPaths,Navigation,words};
});
