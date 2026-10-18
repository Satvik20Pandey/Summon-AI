/* Landing page: replays the latest real call from the graph, live memory stats,
   the latest graph-made call decision, and a small animated memory graph. */
(function () {
  "use strict";
  const S = window.Summon;
  const $ = (id) => document.getElementById(id);

  // A sample used only when the graph has no analyzed call yet (fresh install).
  const SAMPLE = {
    agent: "claude-code", project: "demo-api", sample: true,
    transcript: [
      { role: "assistant", content: "Hi Satvik, this is Summon, an AI assistant calling for Claude Code. Claude Code has finished a task on demo-api. DELETE /items is in and all tests pass. I have one quick question, do you have a minute?" },
      { role: "user", content: "Yes, go ahead." },
      { role: "assistant", content: "Should DELETE return a 404 status instead of a 200 with an error body?" },
      { role: "user", content: "Yes, return a real 404 everywhere in this API." },
      { role: "assistant", content: "Anything else you want Claude Code to do next?" },
      { role: "user", content: "Change GET /items/{id} to return 404 the same way, and update the tests." },
      { role: "assistant", content: "Got it. I'll pass that on. Bye!" },
    ],
    decisions: [{ text: "Return a real 404 everywhere in this API." }],
    instructions: [{ text: "Change GET /items/{id} to return 404 the same way, and update the tests." }],
  };

  const AGENT_NAMES = { "claude-code": "Claude Code", codex: "Codex", cursor: "Cursor" };
  let speaking = 0;        // drives the signal field
  let playToken = 0;

  function setSpeaker(who, on, state) {
    const sp = $(who === "agent" ? "sp-agent" : "sp-caller");
    const wv = $(who === "agent" ? "wv-agent" : "wv-caller");
    sp.classList.toggle("is-speaking", on);
    wv.classList.toggle("is-active", on);
    if (state) $(who === "agent" ? "st-agent" : "st-caller").textContent = state;
  }

  function addTurn(t) {
    const box = $("transcript");
    const div = document.createElement("div");
    const agent = t.role === "assistant";
    div.className = "turn " + (agent ? "turn-agent" : "turn-caller");
    div.innerHTML = '<div class="turn-head">' + (agent ? "Summon" : S.esc($("caller-name").textContent)) + "</div>" + S.esc(t.content);
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  function addFact(label, text) {
    const box = $("transcript");
    const div = document.createElement("div");
    div.className = "turn-fact";
    div.innerHTML = "<b>" + S.esc(label) + "</b> " + S.esc(text);
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  async function play(call) {
    const token = ++playToken;
    const card = $("call-card");
    const btn = $("replay-btn");
    $("transcript").innerHTML = "";
    btn.disabled = true;
    card.classList.add("playing");
    const turns = (call.transcript || []).filter((t) => t && t.content);
    for (const t of turns) {
      if (token !== playToken) return;
      const who = t.role === "assistant" ? "agent" : "caller";
      setSpeaker(who, true, who === "agent" ? "Speaking" : "Answering");
      speaking = 1;
      const ms = S.reduced ? 0 : Math.min(2600, 500 + t.content.length * 22);
      addTurn(t);
      await wait(ms);
      setSpeaker(who, false, who === "agent" ? "Listening" : "On your phone");
      speaking = 0;
      await wait(S.reduced ? 0 : 220);
    }
    if (token !== playToken) return;
    (call.decisions || []).forEach((d) => addFact("Decision saved", d.text));
    (call.instructions || []).forEach((i) => addFact("Instruction queued", i.text));
    setSpeaker("agent", false, "Call ended");
    card.classList.remove("playing");
    btn.disabled = false;
  }

  async function loadCall() {
    let call = SAMPLE;
    try {
      const d = await S.api("/api/calls?limit=1");
      if (d.calls && d.calls.length && (d.calls[0].transcript || []).length) call = d.calls[0];
    } catch (e) { /* offline or empty: keep the sample */ }
    const agent = AGENT_NAMES[call.agent] || call.agent || "Summon";
    $("pill-agent").textContent = agent;
    $("pill-project").textContent = call.project || "inbox";
    $("call-tag-text").textContent = call.sample ? "SAMPLE CALL" : (call.direction === "inbound" ? "YOU CALLED IN" : "LAST CALL");
    if (!call.sample && call.at) $("call-note").textContent = "Recorded " + S.fmtTime(call.at) + " · every answer is now a node linked to this call.";
    $("replay-btn").onclick = () => play(call);
    play(call);
  }

  async function loadStats() {
    try {
      const d = await S.api("/api/memory/stats");
      document.querySelectorAll("[data-stat]").forEach((el) => {
        const n = d.labels[el.dataset.stat] || 0;
        countUp(el, n);
      });
      $("foot-status-text").textContent = "Memory online · " + Object.values(d.labels).reduce((a, b) => a + b, 0) + " nodes";
    } catch (e) {
      document.querySelectorAll("[data-stat]").forEach((el) => { el.textContent = "—"; });
      $("foot-status-text").textContent = "Memory offline";
    }
  }

  function countUp(el, n) {
    if (S.reduced || n < 2) { el.textContent = String(n); return; }
    const t0 = performance.now(), dur = 900;
    function step(now) {
      const p = Math.min(1, (now - t0) / dur);
      el.textContent = String(Math.round(n * (1 - Math.pow(1 - p, 3))));
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  async function loadPolicy() {
    try {
      const d = await S.api("/api/memory/timeline");
      const cds = d.nodes.filter((n) => n.label === "CallDecision" && n.inputs).sort((a, b) => (b.ts || 0) - (a.ts || 0));
      if (!cds.length) return;
      const x = cds[0], i = x.inputs || {};
      const rows = [
        ["Local time", i.local_time || "—"],
        ["Quiet hours", i.quiet_hours ? i.quiet_hours.replace("-", " – ") + " · learned on a call" : "none set"],
        ["Calls this hour", (i.calls_last_hour != null ? i.calls_last_hour : "—") + " of " + (i.max_per_hour || 6)],
        ["Missed this hour", i.failed_last_hour != null ? i.failed_last_hour : "—"],
        ["Task priority", i.task_priority || "normal"],
      ];
      $("policy-kv").innerHTML = rows.map(([k, v]) => "<dt>" + S.esc(k) + "</dt><dd>" + S.esc(v) + "</dd>").join("");
      const v = $("policy-verdict");
      const cls = x.action === "call" ? "call" : x.action === "skip" ? "skip" : "retry";
      v.className = "verdict " + cls;
      v.innerHTML = '<span class="act">' + S.esc(String(x.action || "").toUpperCase()) + '</span><span class="why">' + S.esc(x.reason || "") + "</span>";
      const title = v.closest(".console").querySelector(".title");
      if (title) title.textContent = "CALL DECISION · LIVE";
    } catch (e) { /* keep the example */ }
  }

  /* A small, calm graph: nodes drift, edges pulse toward the developer node. */
  function miniGraph() {
    const c = $("mini-graph");
    if (!c) return;
    const ctx = c.getContext("2d");
    const N = [
      { l: "You", r: 9, col: "#ffffff", x: 0.5, y: 0.52 },
      { l: "Call", r: 6, col: "#60a5fa", x: 0.33, y: 0.36 },
      { l: "Decision", r: 6, col: "#4ade80", x: 0.68, y: 0.3 },
      { l: "Instruction", r: 5, col: "#fbbf24", x: 0.72, y: 0.68 },
      { l: "Claude", r: 6, col: "#d4d4d4", x: 0.22, y: 0.66 },
      { l: "Codex", r: 6, col: "#d4d4d4", x: 0.86, y: 0.5 },
      { l: "Task", r: 5, col: "#a3a3a3", x: 0.42, y: 0.8 },
    ];
    const E = [[1, 0], [2, 1], [3, 1], [4, 6], [6, 1], [5, 2], [5, 3], [2, 0]];
    let w = 0, h = 0;
    function resize() {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      w = c.clientWidth; h = c.clientHeight;
      c.width = w * dpr; c.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    function frame(now) {
      const t = now / 1000;
      ctx.clearRect(0, 0, w, h);
      const P = N.map((n, k) => ({ x: (n.x + Math.sin(t * 0.5 + k) * 0.012) * w, y: (n.y + Math.cos(t * 0.45 + k * 1.7) * 0.014) * h }));
      E.forEach(([a, b], k) => {
        ctx.strokeStyle = "rgba(255,255,255,0.16)"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(P[a].x, P[a].y); ctx.lineTo(P[b].x, P[b].y); ctx.stroke();
        const p = ((t * 0.35 + k * 0.37) % 1);
        ctx.fillStyle = "rgba(255,255,255,0.75)";
        ctx.beginPath(); ctx.arc(P[a].x + (P[b].x - P[a].x) * p, P[a].y + (P[b].y - P[a].y) * p, 1.6, 0, Math.PI * 2); ctx.fill();
      });
      N.forEach((n, k) => {
        const g = ctx.createRadialGradient(P[k].x, P[k].y, 0, P[k].x, P[k].y, n.r * 4);
        g.addColorStop(0, n.col + "55"); g.addColorStop(1, "transparent");
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(P[k].x, P[k].y, n.r * 4, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = n.col; ctx.beginPath(); ctx.arc(P[k].x, P[k].y, n.r, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "rgba(255,255,255,0.72)"; ctx.font = "500 11px Inter, sans-serif"; ctx.textAlign = "center";
        ctx.fillText(n.l, P[k].x, P[k].y + n.r + 14);
      });
      if (!S.reduced) requestAnimationFrame(frame);
    }
    resize();
    window.addEventListener("resize", resize);
    requestAnimationFrame(frame);
  }

  function copyCmd() {
    const btn = $("copy-cmd");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText($("term-cmd").textContent);
        btn.textContent = "Copied";
      } catch (e) { btn.textContent = "Select & copy"; }
      setTimeout(() => { btn.textContent = "Copy"; }, 1600);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    S.headerBehaviour();
    S.reveals();
    S.signalField($("signal-field"), () => speaking);
    miniGraph();
    copyCmd();
    loadCall();
    loadStats();
    loadPolicy();
  });
})();
