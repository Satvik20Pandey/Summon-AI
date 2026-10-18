/* Memory Lens: the developer's FalkorDB graph, live.
   - graph: cytoscape, incremental updates, type filters, derived edges for hidden hops
   - call tab: rings for mock calls and lets you answer; otherwise shows the last call
   - provenance tab: where a memory came from, who read it, what it replaced
   - timeline tab: server-sent events from the API */
(function () {
  "use strict";
  const S = window.Summon;
  const $ = (id) => document.getElementById(id);

  const TYPES = {
    Developer:    { color: "#ffffff", size: 46, on: true },
    Agent:        { color: "#e5e5e5", size: 34, on: true },
    Project:      { color: "#94a3b8", size: 30, on: true },
    Task:         { color: "#a3a3a3", size: 26, on: true },
    Call:         { color: "#60a5fa", size: 30, on: true },
    Decision:     { color: "#4ade80", size: 28, on: true },
    Instruction:  { color: "#fbbf24", size: 25, on: true },
    Answer:       { color: "#7dd3fc", size: 19, on: true },
    Preference:   { color: "#fb923c", size: 22, on: true },
    CallDecision: { color: "#e879f9", size: 18, on: false },
    Question:     { color: "#64748b", size: 16, on: false },
    Session:      { color: "#3b4a6b", size: 16, on: false },
    File:         { color: "#475569", size: 14, on: false },
  };
  const ORDER = Object.keys(TYPES);
  const AGENT_NAMES = { "claude-code": "Claude Code", codex: "Codex", cursor: "Cursor" };
  const PROV_TYPES = new Set(["Decision", "Instruction", "Preference", "Answer", "Call", "Task"]);

  let cy = null;
  let data = { nodes: [], edges: [] };
  let voice = "mock";
  let ringing = null;          // pending mock call being shown
  let selectedKey = null;
  let firstLayout = true;

  /* ---------------------------------------------------------------- labels */
  function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function label(n) {
    switch (n.label) {
      case "Developer": return n.name || "You";
      case "Agent": return AGENT_NAMES[n.name] || n.name;
      case "Project": return n.name || n.id;
      case "Task": return trunc(n.title, 34);
      case "Call": return (n.direction === "inbound" ? "Inbound · " : "Call · ") + S.fmtTime(n.at);
      case "Decision": return trunc(n.text, 46);
      case "Instruction": return trunc(n.text, 42);
      case "Answer": return trunc(n.text, 30);
      case "Preference": return n.key + ": " + n.value;
      case "CallDecision": return "policy: " + n.action;
      case "Question": return trunc(n.text, 30);
      case "Session": return trunc(n.id, 16);
      case "File": return (n.path || "").split("/").pop();
      default: return n.key || n.label;
    }
  }
  function fullText(n) {
    return n.text || n.title || n.summary || (n.key && n.value ? n.key + ": " + n.value : "") || n.name || n.id || n.path || "";
  }

  /* ---------------------------------------------------------------- graph */
  function visibleElements() {
    const on = (t) => (TYPES[t] ? TYPES[t].on : false);
    const byUid = new Map(data.nodes.map((n) => [n.uid, n]));
    const els = [];
    const vis = new Set();
    for (const n of data.nodes) {
      if (!on(n.label)) continue;
      vis.add(n.uid);
      els.push({ group: "nodes", data: { id: n.uid, key: n.key, type: n.label, label: label(n), raw: n } });
    }
    const seen = new Set();
    const push = (id, s, t, lbl, derived) => {
      if (seen.has(id) || !vis.has(s) || !vis.has(t) || s === t) return;
      seen.add(id);
      els.push({ group: "edges", data: { id, source: s, target: t, label: lbl }, classes: derived ? "derived" : "" });
    };
    for (const e of data.edges) push(e.id, e.source, e.target, e.type, false);
    // Hidden Session: connect Task -> Agent / Project directly so the picture stays whole.
    if (!on("Session")) {
      const runBy = new Map(), onProj = new Map();
      for (const e of data.edges) {
        if (e.type === "RUN_BY") runBy.set(e.source, e.target);
        if (e.type === "ON") onProj.set(e.source, e.target);
      }
      for (const e of data.edges) {
        if (e.type !== "IN" || !byUid.has(e.source) || byUid.get(e.source).label !== "Task") continue;
        const a = runBy.get(e.target), p = onProj.get(e.target);
        if (a) push(e.source + "|BY|" + a, e.source, a, "BY", true);
        if (p) push(e.source + "|ON|" + p, e.source, p, "ON", true);
      }
    }
    return els;
  }

  function initCy() {
    if (!window.cytoscape) {
      $("empty").classList.remove("hidden");
      $("empty").querySelector("h3").textContent = "Graph view failed to load.";
      return false;
    }
    cy = cytoscape({
      container: $("cy"),
      minZoom: 0.15, maxZoom: 3,
      style: [
        { selector: "node", style: {
          "background-color": (el) => (TYPES[el.data("type")] || {}).color || "#888",
          "width": (el) => (TYPES[el.data("type")] || {}).size || 20,
          "height": (el) => (TYPES[el.data("type")] || {}).size || 20,
          "label": "data(label)", "color": "#e8e8e8", "font-family": "Inter, sans-serif", "font-size": 11,
          "font-weight": 500, "text-wrap": "wrap", "text-max-width": 120, "min-zoomed-font-size": 6, "text-valign": "bottom",
          "text-margin-y": 5, "text-outline-color": "#0a0a0a", "text-outline-width": 2,
          "border-width": 2, "border-color": "#0a0a0a", "overlay-opacity": 0,
          "transition-property": "opacity, border-color, border-width", "transition-duration": "180ms",
        } },
        { selector: 'node[type = "Developer"]', style: { "border-color": "rgba(255,255,255,0.35)", "border-width": 6, "font-size": 11, "font-weight": 600 } },
        { selector: 'node[type = "Call"]', style: { "shape": "round-rectangle", "width": 34, "height": 24 } },
        { selector: 'node[type = "Agent"]', style: { "shape": "round-hexagon" } },
        { selector: 'node[type = "Decision"]', style: { "shape": "round-diamond" } },
        { selector: "edge", style: {
          "width": 1.1, "line-color": "rgba(255,255,255,0.16)", "target-arrow-color": "rgba(255,255,255,0.28)",
          "target-arrow-shape": "triangle", "arrow-scale": 0.7, "curve-style": "bezier",
          "label": "", "font-size": 8, "color": "#9a9a9a", "font-family": "Inter, sans-serif",
          "text-rotation": "autorotate", "text-background-color": "#0a0a0a", "text-background-opacity": 1,
          "text-background-padding": 1.5, "overlay-opacity": 0,
          "transition-property": "opacity, line-color, width", "transition-duration": "180ms",
        } },
        { selector: "edge.derived", style: { "line-style": "dashed", "line-dash-pattern": [4, 4] } },
        { selector: ".dim", style: { "opacity": 0.14 } },
        { selector: "node.hl", style: { "border-color": "#ffffff", "border-width": 4 } },
        { selector: "edge.hl", style: { "line-color": "#60a5fa", "target-arrow-color": "#60a5fa", "width": 2.6, "color": "#cfe3ff", "label": "data(label)" } },
        { selector: "node.hit", style: { "border-color": "#4ade80", "border-width": 4 } },
        { selector: "node.fresh", style: { "border-color": "#4ade80", "border-width": 5 } },
      ],
    });
    cy.on("tap", "node", (ev) => select(ev.target));
    cy.on("tap", (ev) => { if (ev.target === cy) clearHighlight(); });
    cy.on("mouseover", "node", (ev) => showTip(ev.target));
    cy.on("mouseout", "node", hideTip);
    cy.on("pan zoom", hideTip);
    $("zoom-in").onclick = () => cy.zoom({ level: cy.zoom() * 1.25, renderedPosition: center() });
    $("zoom-out").onclick = () => cy.zoom({ level: cy.zoom() / 1.25, renderedPosition: center() });
    $("zoom-fit").onclick = () => cy.animate({ fit: { padding: 50 } }, { duration: 300 });
    return true;
  }
  function center() { const c = $("cy"); return { x: c.clientWidth / 2, y: c.clientHeight / 2 }; }

  function render(freshUids) {
    if (!cy) return;
    const els = visibleElements();
    const want = new Set(els.map((e) => e.data.id));
    cy.batch(() => {
      cy.elements().forEach((el) => { if (!want.has(el.id())) el.remove(); });
      const add = [];
      for (const e of els) {
        const cur = cy.getElementById(e.data.id);
        if (cur.nonempty()) { cur.data(e.data); continue; }
        add.push(e);
      }
      if (add.length) {
        const added = cy.add(add);
        // place new nodes near a neighbour so the layout settles calmly
        added.nodes().forEach((n) => {
          const nb = n.neighborhood("node").filter((x) => !added.contains(x));
          if (nb.nonempty()) {
            const p = nb[0].position();
            n.position({ x: p.x + (Math.random() - 0.5) * 80, y: p.y + (Math.random() - 0.5) * 80 });
          }
        });
      }
    });
    const isEmpty = data.nodes.length <= 1;
    $("empty").classList.toggle("hidden", !isEmpty);
    $("cy").style.visibility = isEmpty ? "hidden" : "visible";
    const layout = cy.layout({
      name: "cose", animate: !firstLayout && !S.reduced, animationDuration: 500, randomize: firstLayout,
      fit: firstLayout, padding: 40, nodeRepulsion: () => 12000, idealEdgeLength: () => 70,
      nodeOverlap: 20, gravity: 0.45, numIter: firstLayout ? 2000 : 700, nodeDimensionsIncludeLabels: true,
      componentSpacing: 60,
    });
    if (firstLayout) {
      // never blow a small graph up to giant nodes
      layout.one("layoutstop", () => { if (cy.zoom() > 1.3) { cy.zoom(1.3); cy.center(); } });
    }
    layout.run();
    firstLayout = false;
    if (freshUids && freshUids.length) {
      const nodes = cy.nodes().filter((n) => freshUids.includes(n.id()));
      nodes.addClass("fresh");
      setTimeout(() => nodes.removeClass("fresh"), 2600);
    }
    if (selectedKey) {
      const n = cy.nodes().filter((x) => x.data("key") === selectedKey);
      if (n.nonempty()) n.addClass("hl");
    }
  }

  function renderFilters() {
    const counts = {};
    data.nodes.forEach((n) => { counts[n.label] = (counts[n.label] || 0) + 1; });
    $("filters").innerHTML = ORDER.filter((t) => counts[t]).map((t) =>
      '<button type="button" class="filter" data-type="' + S.esc(t) + '" aria-pressed="' + TYPES[t].on + '">' +
      '<i style="background:' + TYPES[t].color + '"></i>' + S.esc(t) + ' <span class="n">' + counts[t] + "</span></button>"
    ).join("");
    $("filters").querySelectorAll(".filter").forEach((b) => {
      b.onclick = () => { const t = b.dataset.type; TYPES[t].on = !TYPES[t].on; b.setAttribute("aria-pressed", TYPES[t].on); render(); };
    });
  }

  let loading = null;
  async function loadGraph() {
    if (loading) return loading;
    loading = (async () => {
      try {
        const before = new Set(data.nodes.map((n) => n.uid));
        data = await S.api("/api/memory/timeline");
        const fresh = before.size ? data.nodes.map((n) => n.uid).filter((u) => !before.has(u)) : [];
        renderFilters();
        render(fresh);
      } catch (e) {
        setConn(false, "Memory unreachable");
      } finally {
        loading = null;
      }
    })();
    return loading;
  }
  let reloadTimer = 0;
  function reloadSoon() { clearTimeout(reloadTimer); reloadTimer = setTimeout(loadGraph, 500); }

  /* ---------------------------------------------------------------- tooltip */
  function showTip(node) {
    const raw = node.data("raw") || {};
    const tip = $("tooltip");
    const p = node.renderedPosition();
    tip.innerHTML = "<b>" + S.esc(raw.label) + "</b>" + S.esc(trunc(fullText(raw), 220));
    tip.style.left = p.x + "px";
    tip.style.top = p.y - (node.renderedHeight() / 2) + "px";
    tip.classList.remove("hidden");
  }
  function hideTip() { $("tooltip").classList.add("hidden"); }

  /* ---------------------------------------------------------------- selection + provenance */
  function clearHighlight() {
    selectedKey = null;
    if (cy) cy.elements().removeClass("hl dim hit");
  }

  function highlightKeys(keys, cls) {
    if (!cy) return;
    cy.elements().removeClass("hl dim hit");
    const set = new Set(keys);
    const nodes = cy.nodes().filter((n) => set.has(n.data("key")));
    if (nodes.empty()) return;
    cy.elements().addClass("dim");
    nodes.removeClass("dim").addClass(cls || "hl");
    nodes.edgesWith(nodes).removeClass("dim").addClass("hl");
  }

  async function select(node) {
    const raw = node.data("raw") || {};
    selectedKey = raw.key;
    switchTab("prov");
    const body = $("prov-body");
    if (!PROV_TYPES.has(raw.label) || !raw.id) {
      highlightKeys([raw.key].concat(node.neighborhood("node").map((n) => n.data("key"))));
      const nb = node.neighborhood("node").map((n) => n.data("raw"));
      body.innerHTML = '<p class="panel-h">' + S.esc(raw.label) + "</p>" +
        '<div class="facts">' + fact("Name", fullText(raw)) +
        (nb.length ? '<div class="fact"><div class="lbl">Connected to</div><div class="readers">' +
          nb.slice(0, 24).map((n) => '<span class="chip"><i style="background:' + ((TYPES[n.label] || {}).color || "#888") + '"></i>' + S.esc(trunc(label(n), 34)) + "</span>").join("") +
          "</div></div>" : "") + "</div>";
      return;
    }
    body.innerHTML = '<p class="panel-h">' + S.esc(raw.label) + '</p><p class="hint">Tracing…</p>';
    let p;
    try { p = await S.api("/api/memory/provenance/" + encodeURIComponent(raw.id)); }
    catch (e) { body.innerHTML = '<p class="panel-h">' + S.esc(raw.label) + '</p><p class="hint">Could not load provenance.</p>'; return; }
    const path = p.path_to_developer || [];
    highlightKeys(path.length ? path : [raw.key]);
    const focus = cy && cy.nodes().filter((n) => (path.length ? path : [raw.key]).includes(n.data("key")));
    if (focus && focus.nonempty() && !S.reduced) cy.animate({ fit: { eles: focus, padding: 110 } }, { duration: 420 });
    const rows = [fact(raw.label, fullText(raw))];
    if (p.call) rows.push(fact("Said on", S.fmtTime(p.said_on) + " · " + (p.call.direction || "") + " call", p.call.id));
    if (p.during_task) rows.push(fact("While working on", p.during_task));
    if (p.while_agent) rows.push(fact("Agent running", AGENT_NAMES[p.while_agent] || p.while_agent));
    if (p.supersedes) rows.push(fact("Replaced", p.supersedes.text || ""));
    if (p.read_by && p.read_by.length) {
      rows.push('<div class="fact"><div class="lbl">Read since by</div><div class="readers">' +
        p.read_by.map((r) => '<span class="chip">' + S.esc(AGENT_NAMES[r.agent] || r.agent) + " · " + S.esc(S.fmtTime(r.at)) + "</span>").join("") + "</div></div>");
    }
    if (path.length) {
      rows.push('<div class="fact"><div class="lbl">Path in the graph</div><div class="path">' +
        path.map((k) => '<span class="node">' + S.esc(nodeName(k)) + "</span>").join('<span class="sep">→</span>') + "</div></div>");
    }
    if (p.recording_url) rows.push(fact("Recording", p.recording_url));
    if (raw.label === "Call" && Array.isArray(raw.transcript) && raw.transcript.length) {
      rows.push('<div class="fact"><div class="lbl">Transcript</div><div class="v" style="display:grid;gap:6px;margin-top:8px">' +
        raw.transcript.map((t) => '<div><span style="color:var(--muted)">' + (t.role === "user" ? "You" : "Summon") + ":</span> " + S.esc(t.content) + "</div>").join("") + "</div></div>");
    }
    body.innerHTML = '<p class="panel-h">Provenance</p><div class="facts">' + rows.join("") + "</div>";
  }

  function nodeName(key) {
    const n = data.nodes.find((x) => x.key === key);
    return n ? trunc(label(n), 28) : key;
  }
  function fact(lbl, v, code) {
    return '<div class="fact"><div class="lbl">' + S.esc(lbl) + '</div><div class="v">' + S.esc(v) +
      (code ? ' <code>' + S.esc(code) + "</code>" : "") + "</div></div>";
  }

  /* ---------------------------------------------------------------- search */
  let searchTimer = 0;
  function onSearch() {
    clearTimeout(searchTimer);
    const q = $("search").value.trim();
    searchTimer = setTimeout(async () => {
      if (!q) { clearHighlight(); return; }
      let res;
      try { res = await S.api("/api/memory/recall?q=" + encodeURIComponent(q)); } catch (e) { return; }
      const hits = res.hits || [];
      switchTab("prov");
      highlightKeys(hits.map((h) => h.key), "hit");
      $("prov-body").innerHTML = '<p class="panel-h">Search · ' + S.esc(q) + "</p>" + (hits.length
        ? '<div class="facts">' + hits.map((h, i) => '<button type="button" class="fact" style="text-align:left;width:100%" data-hit="' + i + '"><div class="lbl">' + S.esc(h.label) + '</div><div class="v">' + S.esc(trunc(h.text || h.summary || h.title || "", 220)) + "</div></button>").join("") + "</div>"
        : '<p class="hint">Nothing in memory matches that yet.</p>');
      $("prov-body").querySelectorAll("[data-hit]").forEach((b) => {
        b.onclick = () => {
          const h = hits[+b.dataset.hit];
          const n = cy && cy.nodes().filter((x) => x.data("key") === h.key);
          if (n && n.nonempty()) { cy.animate({ center: { eles: n }, zoom: 1.4 }, { duration: 300 }); select(n[0]); }
        };
      });
    }, 260);
  }

  /* ---------------------------------------------------------------- tabs */
  function switchTab(name) {
    ["call", "prov", "feed"].forEach((t) => {
      $("tab-" + t).setAttribute("aria-selected", String(t === name));
      $("panel-" + t).classList.toggle("hidden", t !== name);
    });
    if (name === "call") $("call-badge").classList.add("hidden");
  }

  /* ---------------------------------------------------------------- call tab */
  function setSpeaker(who, on, state) {
    $("sp-" + who).classList.toggle("is-speaking", on);
    $("wv-" + who).classList.toggle("is-active", on);
    if (state != null) $("st-" + who).textContent = state;
  }
  function turn(role, text, head) {
    const box = $("transcript");
    const d = document.createElement("div");
    d.className = "turn " + (role === "assistant" ? "turn-agent" : "turn-caller");
    d.innerHTML = '<div class="turn-head">' + S.esc(head || (role === "assistant" ? "Summon" : "You")) + "</div>" + S.esc(text);
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
  }
  function factLine(lbl, text) {
    const box = $("transcript");
    const d = document.createElement("div");
    d.className = "turn-fact";
    d.innerHTML = "<b>" + S.esc(lbl) + "</b> " + S.esc(text);
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
  }
  function callPills(agent, project) {
    const a = $("call-agent"), p = $("call-project");
    a.textContent = AGENT_NAMES[agent] || agent || "";
    p.textContent = project || "";
    a.classList.toggle("hidden", !a.textContent);
    p.classList.toggle("hidden", !p.textContent);
  }

  function showLastCall(call) {
    const card = $("call-card");
    card.classList.remove("ringing", "playing");
    $("transcript").innerHTML = "";
    $("answer-form").classList.add("hidden");
    setSpeaker("agent", false, "Idle");
    setSpeaker("caller", false, "—");
    const btn = $("call-btn");
    btn.disabled = true;
    btn.className = "play-btn dark";
    btn.innerHTML = "<span>" + (voice === "mock" ? "Waiting for an agent to finish…" : "Calls ring on your phone") + "</span>";
    if (!call) {
      $("call-tag-text").textContent = "NO CALL YET";
      callPills(null, null);
      $("call-note").textContent = voice === "mock"
        ? "Mock mode: when an agent finishes, Summon rings right here."
        : "Live mode: Summon calls your phone through Shravan.ai.";
      return;
    }
    $("call-tag-text").textContent = call.sandbox ? "SANDBOX CALL" : call.direction === "inbound" ? "YOU CALLED IN" : "LAST CALL";
    callPills(call.agent, call.project);
    (call.transcript || []).forEach((t) => turn(t.role, t.content));
    (call.decisions || []).forEach((d) => factLine("Decision saved", d.text));
    (call.instructions || []).forEach((i) => factLine("Instruction " + (i.status || "queued"), i.text));
    (call.preferences || []).forEach((p) => factLine("Preference learned", p.key + ": " + p.value));
    $("call-note").textContent = "Recorded " + S.fmtTime(call.at) + ". Click its Call node in the graph for provenance.";
  }

  async function loadLastCall() {
    try {
      const d = await S.api("/api/calls?limit=1");
      if (!ringing) showLastCall(d.calls && d.calls[0]);
    } catch (e) { if (!ringing) showLastCall(null); }
  }

  function ring(p) {
    if (ringing && ringing.call_id === p.call_id) return;
    ringing = p;
    const card = $("call-card");
    card.classList.add("ringing");
    $("call-tag-text").textContent = "INCOMING CALL";
    callPills(null, p.project);
    $("call-agent").textContent = p.bot || "Agent";
    $("call-agent").classList.remove("hidden");
    $("transcript").innerHTML = "";
    setSpeaker("agent", true, "Calling you");
    setSpeaker("caller", false, "Ringing…");
    (p.lines || []).forEach(([node, text]) => turn("assistant", text, /^ask_q\d/.test(node) ? "Question " + node.slice(5) : "Summon"));
    const form = $("answer-form");
    const qs = (p.questions || []).filter(Boolean);
    form.innerHTML = qs.map((q, i) =>
      '<label>' + S.esc(q) + '<input name="a' + i + '" placeholder="Your answer"></label>').join("") +
      '<label>Anything else for ' + S.esc(p.bot || "the agent") + ' to do next?<textarea name="more" rows="2" placeholder="e.g. Also add a deprecation header to the old route"></textarea></label>';
    form.classList.remove("hidden");
    const btn = $("call-btn");
    btn.disabled = false;
    btn.className = "play-btn";
    btn.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/></svg><span>Answer &amp; hang up</span>';
    btn.onclick = () => answer(qs.length);
    form.onsubmit = (e) => { e.preventDefault(); answer(qs.length); };
    $("call-note").textContent = "Mock mode: type what you would say on the phone.";
    const first = form.querySelector("input, textarea");
    if ($("tab-call").getAttribute("aria-selected") !== "true") $("call-badge").classList.remove("hidden");
    switchTab("call");
    if (first) setTimeout(() => first.focus(), 50);
  }

  async function answer(nq) {
    if (!ringing) return;
    const p = ringing;
    const form = $("answer-form");
    const answers = [];
    for (let i = 0; i < nq; i++) answers.push((form.elements["a" + i] || {}).value || "");
    const more = (form.elements.more || {}).value || "";
    const btn = $("call-btn");
    btn.disabled = true;
    btn.innerHTML = "<span>Hanging up…</span>";
    setSpeaker("agent", false, "Listening");
    setSpeaker("caller", true, "Answering");
    answers.forEach((a) => { if (a.trim()) turn("user", a); });
    if (more.trim()) turn("user", more);
    form.classList.add("hidden");
    try {
      const r = await fetch("/api/mock/reply", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ call_id: p.call_id, answers, more }) });
      if (!r.ok) throw new Error(String(r.status));
      const v = await r.json();
      (v.decisions || []).forEach((d) => factLine("Decision saved", d.text));
      (v.instructions || []).forEach((i) => factLine("Instruction queued", i.text));
      (v.preferences || []).forEach((x) => factLine("Preference learned", x.key + ": " + x.value));
      if (!(v.decisions || []).length && !(v.instructions || []).length) factLine("Call ended", "nothing new to remember");
      $("call-tag-text").textContent = "CALL ENDED";
      $("call-note").textContent = "Saved to FalkorDB. The agent continues with what you said.";
    } catch (e) {
      factLine("Error", "Could not deliver the answer (" + e.message + ").");
    }
    setSpeaker("caller", false, "Hung up");
    setSpeaker("agent", false, "Call ended");
    $("call-card").classList.remove("ringing");
    btn.className = "play-btn dark";
    btn.innerHTML = "<span>Call ended</span>";
    ringing = null;
    reloadSoon();
    setTimeout(() => { if (!ringing) loadLastCall(); }, 6000);
  }

  async function pollPending() {
    if (voice !== "mock") return;
    try {
      const d = await S.api("/api/mock/pending");
      if (d.pending && d.pending.length) ring(d.pending[0]);
    } catch (e) { /* ignore */ }
  }

  /* ---------------------------------------------------------------- timeline */
  const MARK = {
    "call.placed": "call", "call.inbound": "call", "call.completed": "call",
    "call.analyzed": "ok", "task.recorded": "task", "session.started": "task", "memory.note": "ok",
    "call.decision": "warn", "instruction.done": "ok", "instruction.running": "warn",
    "call.failed": "bad", "call.rejected": "bad",
  };
  function describe(e) {
    const ag = (a) => "<b>" + S.esc(AGENT_NAMES[a] || a || "agent") + "</b>";
    switch (e.kind) {
      case "session.started": return ag(e.agent) + " opened a session on " + S.esc(e.project);
      case "call.decision": return "Graph decided <b>" + S.esc(String(e.action).toUpperCase()) + "</b> · " + S.esc(e.reason);
      case "call.placed": return "Calling you" + (e.redial ? " again" : "") + " via " + S.esc(e.provider || "voice") + (e.agent ? " for " + ag(e.agent) : "");
      case "call.analyzed": return "Call analyzed · " + (e.answers || []).length + " answers, <b>" + (e.instructions || []).length + " instructions</b>, " + (e.decisions || []).length + " decisions";
      case "call.completed": return "Call completed";
      case "call.failed": return "Call failed · " + S.esc(e.reason);
      case "call.inbound": return "You phoned in · " + S.esc((e.instructions || []).join("; ") || "no instruction");
      case "call.rejected": return "Inbound call rejected · " + S.esc(e.reason);
      case "task.recorded": return ag(e.agent) + " finished “" + S.esc(trunc(e.title, 80)) + "”" + (e.instruction_id ? " · from your call" : "");
      case "memory.note": return ag(e.agent) + " remembered “" + S.esc(trunc(e.text, 90)) + "”";
      case "instruction.done": return "Instruction done";
      case "instruction.running": return "Instruction picked up";
      default: return S.esc(e.kind);
    }
  }
  function addEvent(e, prepend) {
    if (e.kind === "unit.test") return;
    $("feed-empty") && $("feed-empty").remove();
    const feed = $("feed");
    const d = document.createElement("div");
    d.className = "evt";
    d.innerHTML = '<span class="mk ' + (MARK[e.kind] || "") + '"></span><div><time>' + S.fmtClock(e.ts) + "</time>" + describe(e) + "</div>";
    if (prepend === false) feed.appendChild(d); else feed.prepend(d);
    while (feed.children.length > 80) feed.lastChild.remove();
  }

  /* ---------------------------------------------------------------- live */
  function setConn(ok, text) {
    $("conn-dot").classList.toggle("off", !ok);
    $("conn-text").textContent = text;
  }
  function connect() {
    let hadError = false;
    const es = new EventSource("/api/events");
    es.onopen = () => {
      setConn(true, "Live");
      if (hadError) { hadError = false; loadGraph(); loadLastCall(); pollPending(); }
    };
    es.onerror = () => { hadError = true; setConn(false, "Reconnecting…"); };
    es.onmessage = (m) => {
      let e;
      try { e = JSON.parse(m.data); } catch (err) { return; }
      addEvent(e);
      if (e.kind === "call.placed") {
        if (voice === "mock") pollPending();
        else {
          ringing = null;
          $("call-card").classList.add("ringing");
          $("call-tag-text").textContent = "CALLING YOUR PHONE";
          $("transcript").innerHTML = "";
          (e.lines || []).forEach(([, text]) => turn("assistant", text));
          $("call-note").textContent = "Pick up your phone. The answer appears here when the call is analyzed.";
        }
      }
      if (e.kind === "call.analyzed" || e.kind === "call.inbound") { if (!ringing) setTimeout(loadLastCall, 400); }
      if (["call.analyzed", "task.recorded", "memory.note", "call.inbound", "call.decision", "session.started", "instruction.done"].includes(e.kind)) reloadSoon();
    };
  }

  async function boot() {
    ["call", "prov", "feed"].forEach((t) => { $("tab-" + t).onclick = () => switchTab(t); });
    $("search").addEventListener("input", onSearch);
    try {
      const h = await S.api("/api/health");
      voice = h.voice;
      $("voice-chip").textContent = "voice: " + (h.voice === "shravan" ? "Shravan.ai (live)" : h.voice === "sandbox" ? "Shravan.ai sandbox" : "mock");
      setConn(!!h.falkordb, h.falkordb ? "FalkorDB connected" : "FalkorDB unreachable");
    } catch (e) {
      setConn(false, "API unreachable");
    }
    if (initCy()) await loadGraph();
    try {
      const r = await S.api("/api/events/recent?n=30");
      (r.events || []).slice().reverse().forEach((e) => addEvent(e, false));
    } catch (e) { /* ignore */ }
    await loadLastCall();
    pollPending();
    setInterval(pollPending, 4000);
    connect();
  }

  // Small handle for automated UI checks (and the curious): select a node by key.
  window.SummonLens = {
    get cy() { return cy; },
    selectKey(key) { const n = cy && cy.nodes().filter((x) => x.data("key") === key); if (n && n.nonempty()) select(n[0]); return !!(n && n.nonempty()); },
    firstOf(type) { const n = cy && cy.nodes().filter((x) => x.data("type") === type); return n && n.nonempty() ? n[0].data("key") : null; },
  };

  document.addEventListener("DOMContentLoaded", boot);
})();
