/* Summon AI — shared helpers for the landing page and the Memory Lens.
   Everything that came from the graph (transcripts, decisions, names) goes through
   esc() before it touches innerHTML. */
(function () {
  "use strict";

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ESC[c]);
  }

  async function api(path, opts) {
    const r = await fetch(path, Object.assign({ headers: { Accept: "application/json" } }, opts || {}));
    if (!r.ok) throw new Error(path + " -> " + r.status);
    return r.json();
  }

  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return String(iso);
    return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  function fmtClock(ts) {
    const d = ts ? new Date(ts * 1000) : new Date();
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Dot-grid "signal field" — the ambient background of the Shravan.ai ecosystem.
     energy() returns 0..1 and makes the field swell while someone is speaking. */
  function signalField(canvas, energy) {
    if (!canvas || reduced) return;
    const ctx = canvas.getContext("2d");
    const CELL = 18;
    let w = 0, h = 0, dots = [], cols = 0, e = 0, last = 0, raf = 0, visible = true;

    function resize() {
      const dpr = Math.min(1.5, window.devicePixelRatio || 1);
      w = canvas.clientWidth; h = canvas.clientHeight;
      if (!w || !h) return;
      canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cols = Math.ceil(w / CELL) + 1;
      const rows = Math.ceil(h / CELL) + 1, cx = w * 0.5, cy = h * 0.42;
      dots = [];
      for (let i = 0; i < cols; i++) {
        for (let j = 0; j < rows; j++) {
          const x = i * CELL, y = j * CELL;
          const dx = (x - cx) / w, dy = (y - cy) / h;
          const dist = Math.sqrt(dx * dx + dy * dy), fall = 1 - dist * 1.6;
          if (fall > 0) dots.push({ i, x, y, dist, fall });
        }
      }
    }
    const wave = new Float32Array(1024);
    function draw(now) {
      raf = 0;
      if (!visible) return;
      const t = now * 0.00048;
      const dt = last ? Math.min(4, (now - last) / 16.67) : 1;
      last = now;
      e += ((energy ? energy() : 0) - e) * (1 - Math.pow(0.96, dt));
      for (let i = 0; i < cols && i < wave.length; i++) {
        wave[i] = Math.sin(i * 0.28 + t * 2.2) * 0.5 + Math.sin(i * 0.11 - t * 1.3) * 0.5;
      }
      ctx.clearRect(0, 0, w, h);
      const buckets = new Map();
      for (const d of dots) {
        const ripple = Math.sin(d.dist * 22 - t * 3.4) * 0.5 + 0.5;
        const base = 0.028 + d.fall * (0.066 + ripple * 0.08 * (0.35 + e));
        const a = Math.min(0.55, base + wave[d.i] * d.fall * (0.035 + e * 0.2));
        if (a < 0.03) continue;
        const key = Math.round(a * 40) / 40;
        let p = buckets.get(key);
        if (!p) { p = new Path2D(); buckets.set(key, p); }
        const r = 0.9 + d.fall * (0.9 + e * 1.1);
        p.moveTo(d.x + r, d.y);
        p.arc(d.x, d.y, r, 0, Math.PI * 2);
      }
      buckets.forEach((p, a) => { ctx.fillStyle = "rgba(255,255,255," + a + ")"; ctx.fill(p); });
      raf = requestAnimationFrame(draw);
    }
    resize();
    window.addEventListener("resize", resize);
    const io = new IntersectionObserver((ents) => {
      visible = ents[0].isIntersecting;
      if (visible && !raf) raf = requestAnimationFrame(draw);
    });
    io.observe(canvas);
    raf = requestAnimationFrame(draw);
  }

  /* Header hides while scrolling down, returns when scrolling up (as on shravan-ai.tech). */
  function headerBehaviour() {
    const hdr = document.getElementById("hdr");
    if (!hdr) return;
    hdr.addEventListener("animationend", () => hdr.classList.add("settled"), { once: true });
    let lastY = window.scrollY;
    window.addEventListener("scroll", () => {
      const y = window.scrollY;
      hdr.classList.toggle("is-hidden", y > lastY && y > 140);
      lastY = y;
    }, { passive: true });
    const links = Array.from(hdr.querySelectorAll('.nav-link[href^="#"]'));
    const targets = links.map((a) => document.querySelector(a.getAttribute("href"))).filter(Boolean);
    if (!targets.length) return;
    const spy = new IntersectionObserver((ents) => {
      ents.forEach((en) => {
        if (!en.isIntersecting) return;
        links.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + en.target.id));
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    targets.forEach((t) => spy.observe(t));
  }

  function reveals() {
    const els = document.querySelectorAll(".reveal");
    if (reduced || !("IntersectionObserver" in window)) { els.forEach((el) => el.classList.add("in")); return; }
    const io = new IntersectionObserver((ents) => {
      ents.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
    }, { rootMargin: "0px 0px -8% 0px" });
    els.forEach((el) => io.observe(el));
  }

  window.Summon = { esc, api, fmtTime, fmtClock, reduced, signalField, headerBehaviour, reveals };
})();
