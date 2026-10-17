/* Memory Lens: renders the developer's FalkorDB graph, streams events, answers mock calls. */
(function () {
  const COLORS = {
    Developer: "#f472b6", Agent: "#60a5fa", Session: "#3b4a6b", Project: "#94a3b8",
    Task: "#a78bfa", File: "#475569", Question: "#64748b", Answer: "#7dd3fc", Call: "#22d3ee",
    Instruction: "#fbbf24", Decision: "#34d399", Preference: "#fb923c", CallDecision: "#e879f9",
  };
  const SIZE = { Developer: 46, Call: 38, Decision: 34, Instruction: 32, Task: 32, Agent: 34 };
  const $ = (s) => document.querySelector(s);
  let cy;

  function label(n) {
    const t = n.label;
    if (t === "Decision" || t === "Instruction") return (n.text || "").slice(0, 60);
    if (t === "Task") return (n.title || "").slice(0, 40);
    if (t === "Call") return (n.direction || "call") + " " + (n.status || "") + "\n" + (n.at || "").slice(5, 16).replace("T", " ");
    if (t === "Preference") return n.key + ": " + n.value;
    if (t === "Agent") return n.name;
    if (t === "Developer") return n.name || "you";
    if (t === "CallDecision") return "policy: " + n.action;
    if (t === "File") return (n.path || "").split("/").pop();
    if (t === "Question") return (n.text || "").slice(0, 40);
    if (t === "Answer") return (n.text || "").slice(0, 40);
    if (t === "Session") return (n.id || "").slice(0, 14);
    if (t === "Project") return n.name || n.id;
    return n.key || n.id || t;
  }

  async function loadGraph() {
    const data = await fetch("/api/memory/timeline").then((r) => r.json());
    const seen = new Set();
    const els = [];
    for (const n of data.nodes) {
      if (seen.has(n.key)) continue;
      seen.add(n.key);
      els.push({ data: { id: n.key, label: label(n), type: n.label, raw: n } });
    }
    for (const e of data.edges) {
      if (!seen.has(e.source) || !seen.has(e.target)) continue;
      els.push({ data: { id: e.source + "|" + e.type + "|" + e.target, source: e.source, target: e.target, label: e.type } });
    }
    if (cy) cy.destroy();
    cy = cytoscape({
      container: $("#cy"), elements: els,
      style: [
        { selector: "node", style: {
          "background-color": (ele) => COLORS[ele.data("type")] || "#64748b",
          "label": "data(label)", "color": "#e8ebf2", "font-size": 9, "text-wrap": "wrap",
          "text-max-width": 110, "text-valign": "bottom", "text-margin-y": 4,
          "width": (ele) => SIZE[ele.data("type")] || 22, "height": (ele) => SIZE[ele.data("type")] || 22,
          "border-width": 2, "border-color": "#0b0d12",
        } },
        { selector: "edge", style: {
          "width": 1.2, "line-color": "#334155", "target-arrow-color": "#334155",
          "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.8,
          "label": "data(label)", "font-size": 7, "color": "#8d94a6", "text-rotation": "autorotate",
          "text-background-color": "#11141c", "text-background-opacity": 1, "text-background-padding": 1,
        } },
        { selector: ".hl", style: { "border-color": "#fff", "border-width": 4, "line-color": "#22d3ee", "target-arrow-color": "#22d3ee", "width": 3 } },
        { selector: ".dim", style: { "opacity": 0.25 } },
      ],
      layout: { name: "cose", animate: false, nodeRepulsion: 9000, idealEdgeLength: 90, padding: 20 },
    });
    cy.on("tap", "node", (evt) => showProvenance(evt.target.data("raw")));
    const stats = await fetch("/api/memory/stats").then((r) => r.json());
    $("#stats").innerHTML = Object.entries(stats.labels).slice(0, 7).map(([k, v]) => `<span><b>${v}</b> ${k}</span>`).join("");
  }

  async function showProvenance(n) {
    const body = $("#prov-body");
    if (!n.id || !["Decision", "Instruction", "Preference", "Answer", "Task", "Call"].includes(n.label)) {
      body.innerHTML = `<div class="row"><b>${n.label}</b>${label(n).replace(/\n/g, " ")}</div>`;
      return;
    }
    const p = await fetch("/api/memory/provenance/" + encodeURIComponent(n.id)).then((r) => r.ok ? r.json() : null);
    if (!p) { body.innerHTML = "<div class='row'>no provenance</div>"; return; }
    const rows = [];
    rows.push(`<div class="row"><b>${n.label}</b>${(n.text || n.title || n.value || n.status || "")}</div>`);
    if (p.call) rows.push(`<div class="row"><b>said on</b>${p.said_on || ""} (${p.call.direction} call ${p.call.id}${p.recording_url ? ", <a href='" + p.recording_url + "'>recording</a>" : ""})</div>`);
    if (p.during_task) rows.push(`<div class="row"><b>during</b>${p.during_task}</div>`);
    if (p.while_agent) rows.push(`<div class="row"><b>agent running</b>${p.while_agent}</div>`);
    if (p.supersedes) rows.push(`<div class="row"><b>replaced</b>“${p.supersedes.text}”</div>`);
    if (p.read_by && p.read_by.length) rows.push(`<div class="row"><b>read by</b>${p.read_by.map((r) => `${r.agent} (${(r.at || "").slice(0, 16).replace("T", " ")})`).join(", ")}</div>`);
    if (p.path_to_developer && p.path_to_developer.length) rows.push(`<div class="row"><b>path</b><code>${p.path_to_developer.join(" → ")}</code></div>`);
    if (n.label === "Call" && n.transcript) rows.push(`<div class="row"><b>transcript</b><pre style="white-space:pre-wrap;font-size:11px">${n.transcript.map((t) => (t.role === "user" ? "👤 " : "🤖 ") + t.content).join("\n")}</pre></div>`);
    body.innerHTML = rows.join("");
    if (cy) {
      cy.elements().removeClass("hl dim");
      const ids = new Set(p.path_to_developer || []);
      ids.add(n.id);
      cy.elements().addClass("dim");
      cy.nodes().filter((x) => ids.has(x.id())).removeClass("dim").addClass("hl");
      cy.edges().filter((e) => ids.has(e.source().id()) && ids.has(e.target().id())).removeClass("dim").addClass("hl");
    }
  }

  function addEvent(e) {
    const box = $("#events");
    const div = document.createElement("div");
    div.className = "evt " + e.kind;
    const t = new Date((e.ts || Date.now() / 1000) * 1000).toLocaleTimeString();
    let text = "";
    switch (e.kind) {
      case "session.started": text = `<b>${e.agent}</b> started a session on ${e.project}`; break;
      case "call.decision": text = `graph decided <b>${e.action}</b>: ${e.reason}`; break;
      case "call.placed": text = `📞 calling the developer about <b>${(e.lines && e.lines[0] && e.lines[0][1].split(".")[1]) || e.task_id}</b> via ${e.provider}${e.redial ? " (redial)" : ""}`; break;
      case "call.analyzed": text = `call analyzed: ${e.answers.length} answers, <b>${e.instructions.length} instructions</b>, ${e.decisions.length} decisions, ${e.preferences.length} preferences`; break;
      case "call.failed": text = `call failed: ${e.reason}`; break;
      case "call.inbound": text = `📲 the developer phoned in: ${(e.instructions || []).join("; ")}`; break;
      case "task.recorded": text = `<b>${e.agent}</b> finished “${e.title}”${e.instruction_id ? " (from a phoned-in instruction)" : ""}`; break;
      case "memory.note": text = `<b>${e.agent}</b> remembered: ${e.text}`; break;
      default: text = e.kind + (e.instruction_id ? " " + e.instruction_id : "");
    }
    div.innerHTML = `<small>${t}</small> ${text}`;
    box.prepend(div);
    while (box.children.length > 60) box.lastChild.remove();
  }

  async function refreshPhone() {
    const d = await fetch("/api/mock/pending").then((r) => r.json()).catch(() => ({ pending: [] }));
    const panel = $("#phone");
    if (!d.pending.length) { panel.classList.remove("ring"); return; }
    const c = d.pending[0];
    panel.classList.add("ring");
    $("#phone-lines").innerHTML = c.lines.map(([node, text]) => `<div class="line"><b>${node}</b> ${text}</div>`).join("");
    const qs = c.questions.filter(Boolean);
    $("#phone-form").innerHTML =
      qs.map((q, i) => `<label>${q}<input id="ans${i}" placeholder="your answer"></label>`).join("") +
      `<label>Anything else for the agent to do?<textarea id="more" rows="2" placeholder="e.g. Also add a deprecation header to /login"></textarea></label>
       <button class="btn" id="hangup">Answer & hang up</button>`;
    $("#hangup").onclick = async () => {
      const answers = qs.map((_, i) => $("#ans" + i).value);
      await fetch("/api/mock/reply", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ call_id: c.call_id, answers, more: $("#more").value }) });
      panel.classList.remove("ring");
      setTimeout(loadGraph, 800);
    };
  }

  function connect() {
    const es = new EventSource("/api/events");
    es.onopen = () => { $("#conn").innerHTML = "<span class='ok'>● live</span>"; };
    es.onerror = () => { $("#conn").innerHTML = "<span class='bad'>● reconnecting</span>"; };
    es.onmessage = (m) => {
      const e = JSON.parse(m.data);
      addEvent(e);
      if (e.kind === "call.placed") refreshPhone();
      if (["call.analyzed", "task.recorded", "memory.note", "call.inbound", "call.decision"].includes(e.kind)) setTimeout(loadGraph, 600);
    };
  }

  $("#refresh").onclick = loadGraph;
  fetch("/api/events/recent").then((r) => r.json()).then((d) => d.events.forEach(addEvent));
  loadGraph();
  refreshPhone();
  connect();
})();
