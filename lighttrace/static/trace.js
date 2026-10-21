// Trace views: run tree, thread page (Trajectory / Turns / Details), standalone trace, compare.
"use strict";

const traceCache = new Map();
function fetchTrace(id, fresh) {
  if (fresh || !traceCache.has(id)) traceCache.set(id, api(`/api/traces/${enc(id)}`));
  return traceCache.get(id);
}

// ---------- run tree ----------

// A collapsible run tree with an optional waterfall. opts: { selectedId, onSelect(run), compact }
function RunTree(runs, opts = {}) {
  const byId = Object.fromEntries(runs.map((r) => [r.id, r]));
  const kids = {};
  for (const r of runs) if (r.parent_run_id && byId[r.parent_run_id]) (kids[r.parent_run_id] ||= []).push(r);
  const depth = (r) => { let d = 0; while (r.parent_run_id && byId[r.parent_run_id]) { r = byId[r.parent_run_id]; d++; } return d; };
  const now = Date.now() / 1000;
  const t0 = Math.min(...runs.map((r) => r.start_time ?? Infinity));
  const t1 = Math.max(...runs.map((r) => r.end_time ?? r.start_time ?? now));
  const span = Math.max(t1 - t0, 1e-6);
  const collapsed = new Set();
  let filter = "", selected = byId[opts.selectedId] || null;
  const el = h("div", { class: "tree" });
  const list = h("div", {});

  const ancestorsCollapsed = (r) => { while (r.parent_run_id && byId[r.parent_run_id]) { r = byId[r.parent_run_id]; if (collapsed.has(r.id)) return true; } return false; };
  function visible() {
    if (!filter) return runs.filter((r) => !ancestorsCollapsed(r));
    const keep = new Set();
    for (const r of runs) if ((r.name || "").toLowerCase().includes(filter) || (r.run_type || "").includes(filter)) {
      let x = r; keep.add(x.id); while (x.parent_run_id && byId[x.parent_run_id]) { x = byId[x.parent_run_id]; keep.add(x.id); }
    }
    return runs.filter((r) => keep.has(r.id));
  }
  function draw() {
    const wf = store("waterfall") !== false;
    el.classList.toggle("nowf", !wf);
    list.replaceChildren(...visible().map((r) => {
      const left = ((r.start_time ?? t0) - t0) / span * 100;
      const width = (((r.end_time ?? now) - (r.start_time ?? t0)) / span) * 100;
      const hasKids = !!kids[r.id];
      return h("div", { class: "wf-row" + (r === selected ? " sel" : ""), "data-id": r.id, onclick: () => select(r) },
        h("div", { class: "wf-name", style: `padding-left:${6 + depth(r) * 14}px` },
          h("span", { class: "twisty", onclick: hasKids ? (e) => { e.stopPropagation(); collapsed.has(r.id) ? collapsed.delete(r.id) : collapsed.add(r.id); draw(); } : null },
            hasKids ? (collapsed.has(r.id) ? "▶" : "▼") : ""),
          typeTag(r.run_type), h("span", { class: "n", title: r.name }, r.name),
          r.status === "error" ? h("span", { class: "err-dot", title: "error" }) : null,
          h("span", { class: "spacer" }),
          h("span", { class: "wf-meta" }, fmtSec(r.latency) || (r.end_time ? "" : "…"), r.total_tokens ? ` · ${fmtNum(r.total_tokens)}t` : "")),
        wf ? h("div", { class: "wf-track" }, h("div", { class: `wf-bar ${r.run_type} ${r.status}`, style: `left:${left}%;width:${Math.max(width, 0.3)}%` })) : null);
    }));
  }
  function select(r, silent) {
    selected = r;
    draw();
    list.querySelector(".wf-row.sel")?.scrollIntoView({ block: "nearest" });
    if (!silent && opts.onSelect) opts.onSelect(r);
  }
  const search = h("input", { type: "search", placeholder: "Filter runs…", class: "tree-search", oninput: (e) => { filter = e.target.value.trim().toLowerCase(); draw(); } });
  el.append(h("div", { class: "tree-bar" }, search,
    h("button", { class: "ghost small", title: "Expand all", onclick: () => { collapsed.clear(); draw(); } }, "Expand"),
    h("button", { class: "ghost small", title: "Collapse all", onclick: () => { runs.forEach((r) => kids[r.id] && r.parent_run_id && collapsed.add(r.id)); draw(); } }, "Collapse"),
    h("button", { class: "ghost small", title: "Toggle waterfall", onclick: () => { store("waterfall", store("waterfall") === false); draw(); } }, "Waterfall")), list);
  draw();
  return {
    el, select: (r, silent) => select(r, silent), selectById: (id, silent) => byId[id] && select(byId[id], silent),
    move(delta) {
      const v = visible(); const i = v.indexOf(selected);
      const next = v[Math.max(0, Math.min(v.length - 1, (i < 0 ? 0 : i + delta)))];
      if (next) select(next);
    },
    get selected() { return selected; },
  };
}

// ---------- standalone trace ----------

async function viewTrace(traceId, q) {
  const t = await fetchTrace(traceId, true);
  if (t.thread_id) {
    // LangSmith shows a threaded trace inside its conversation.
    const nq = new URLSearchParams({ view: "details", trace: traceId });
    if (q.get("run")) nq.set("run", q.get("run"));
    location.replace(href(["p", t.project, "thread", t.thread_id], nq));
    return;
  }
  const root = t.runs.find((r) => r.is_root) || t.runs[0];
  setCrumbs(h("a", { href: "#/" }, "Projects"), h("a", { href: href(["p", t.project]) }, t.project), h("span", {}, root.name));
  const panel = h("div", { class: "card side" });
  const tokens = t.runs.reduce((s, r) => s + (r.total_tokens || 0), 0);
  const cost = t.runs.reduce((s, r) => r.cost != null ? (s || 0) + r.cost : s, null);
  const tree = RunTree(t.runs, { selectedId: q.get("run") || root.id, onSelect: (r) => {
    replaceHash(["t", traceId], new URLSearchParams({ run: r.id }));
    panel.replaceChildren(...runPanel(r));
  } });
  $app.replaceChildren(
    h("div", { class: "page-h" }, h("h1", {}, root.name), badge(root.status),
      h("span", { class: "stat" }, "Latency ", h("b", {}, fmtSec(root.latency))),
      root.first_token_time != null ? h("span", { class: "stat" }, "First token ", h("b", {}, fmtSec(root.first_token_time))) : null,
      h("span", { class: "stat" }, "Tokens ", h("b", {}, fmtNum(tokens) || "0")),
      cost != null ? h("span", { class: "stat" }, "Cost ", h("b", {}, fmtCost(cost))) : null,
      h("span", { class: "stat" }, "Runs ", h("b", {}, t.runs.length)),
      h("span", { class: "stat" }, fmtTime(root.start_time)), h("span", { class: "spacer" }),
      h("span", { class: "idline" }, "Trace ", copyBtn(traceId, "copy id")),
      menu("⋯", [["Delete trace", async () => {
        if (!confirm("Delete this trace permanently?")) return;
        await api(`/api/traces/${enc(traceId)}`, { method: "DELETE" }); go(["p", t.project]);
      }, "danger"]])),
    h("div", { class: "split" }, h("div", { class: "card" }, tree.el), panel));
  tree.select(tree.selected || root);
  onKeys((e) => {
    if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); tree.move(1); }
    if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); tree.move(-1); }
  });
}

// ---------- thread page ----------

function plainText(v) {
  if (v == null) return "";
  if (typeof v === "string") return v;
  if (typeof v === "object" && !Array.isArray(v)) {
    const keys = Object.keys(v);
    if (keys.length === 1 && typeof v[keys[0]] === "string") return v[keys[0]];
  }
  return JSON.stringify(v, null, 2);
}

// Flat, ordered, de-duplicated messages across turns (LangSmith's "trajectory").
function trajectory(turns) {
  const seen = new Set(), out = [];
  turns.forEach((t, i) => {
    out.push({ turn: t, index: i });
    let msgs = [...(messagesOf(t.inputs) || []), ...(messagesOf(t.outputs) || [])];
    if (!msgs.length) {
      msgs = [{ type: "human", content: plainText(t.inputs) }];
      if (t.outputs != null) msgs.push({ type: "ai", content: plainText(t.outputs) });
    }
    for (const m of msgs) {
      const n = normMsg(m);
      const key = n.id || [n.role, contentText(n.content), n.toolCallId || "", n.toolCalls.map((c) => c.id || c.name).join(",")].join("|");
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ msg: m });
    }
  });
  return out;
}

function threadMarkdown(threadId, turns) {
  const lines = [`# Thread ${threadId}`, ""];
  for (const item of trajectory(turns)) {
    if (item.turn) { lines.push(`---`, `_Turn ${item.index + 1} · ${fmtTime(item.turn.start_time)}_`, ""); continue; }
    const n = normMsg(item.msg);
    const who = { human: "Human", ai: "AI", system: "System", tool: `Tool${n.name ? ` (${n.name})` : ""}` }[n.role];
    const text = contentText(n.content);
    if (text) lines.push(`**${who}:** ${text}`, "");
    for (const tc of n.toolCalls) lines.push(`> **Tool call** \`${tc.name}\` ${"```"}${JSON.stringify(tc.args)}${"```"}`, "");
  }
  return lines.join("\n");
}

function download(name, text) {
  const a = h("a", { href: URL.createObjectURL(new Blob([text], { type: "text/markdown" })), download: name });
  document.body.append(a); a.click(); a.remove();
}

async function viewThread(project, threadId, q) {
  const data = await api(`/api/projects/${enc(project)}/thread?id=${enc(threadId)}`);
  const turns = data.turns, st = data.stats;
  let view = q.get("view") || (turns.some((t) => messagesOf(t.inputs) || messagesOf(t.outputs)) ? "trajectory" : "details");
  setCrumbs(h("a", { href: "#/" }, "Projects"), h("a", { href: href(["p", project]) }, project),
    h("a", { href: href(["p", project, "threads"]) }, "Threads"), h("span", { class: "mono" }, threadId));

  const body = h("div", {});
  const viewBtns = h("span", { class: "segs big" });
  const setView = (v) => { view = v; q.set("view", v); replaceHash(["p", project, "thread", threadId], q); draw(); };
  const baseKeys = (e) => { if (e.key === "t") setView("trajectory"); if (e.key === "u") setView("turns"); if (e.key === "d") setView("details"); };
  function draw() {
    keyHandler = baseKeys;
    viewBtns.replaceChildren(...[["trajectory", "Trajectory", "T"], ["turns", "Turns", "U"], ["details", "Details", "D"]].map(([v, label, key]) =>
      h("button", { class: "seg" + (view === v ? " on" : ""), title: `${label} (${key})`, onclick: () => setView(v) }, label)));
    body.replaceChildren(view === "trajectory" ? trajectoryView() : view === "turns" ? turnsView() : detailsView());
  }
  const openDetails = (traceId, runId) => { q.set("trace", traceId); runId ? q.set("run", runId) : q.delete("run"); setView("details"); };

  function turnMeta(t, i) {
    const lastLlm = t.llm_runs?.at(-1);
    const models = [...new Set((t.llm_runs || []).map((x) => x.model).filter(Boolean))];
    return h("div", { class: "turn-meta" },
      h("b", {}, `Turn ${i + 1}`), h("span", {}, fmtTime(t.start_time)), badge(t.status),
      h("span", {}, fmtSec(t.latency)), t.first_token_time != null ? h("span", { title: "Time to first token" }, `first token ${fmtSec(t.first_token_time)}`) : null,
      t.total_tokens ? h("span", {}, `${fmtNum(t.total_tokens)} tokens`) : null,
      t.cost != null ? h("span", {}, fmtCost(t.cost)) : null, models.length ? h("span", { class: "mono" }, models.join(", ")) : null,
      h("span", { class: "spacer" }),
      lastLlm ? h("a", { href: "javascript:void 0", onclick: () => openDetails(t.id, lastLlm.id) }, "LLM call") : null,
      h("a", { href: "javascript:void 0", onclick: () => openDetails(t.id) }, "Details"));
  }

  function trajectoryView() {
    return h("div", { class: "trajectory" }, trajectory(turns).map((item) =>
      item.turn ? turnMeta(item.turn, item.index) : renderMsg(item.msg)),
      turns.at(-1)?.error ? h("pre", { class: "error" }, turns.at(-1).error) : null);
  }

  function turnsView() {
    return h("div", {}, turns.map((t, i) => h("details", { class: "card turn", open: true },
      h("summary", {}, turnMeta(t, i)),
      h("div", { class: "turn-io" },
        h("div", {}, h("h3", { class: "io-h" }, "Input"), prettyValue(t.inputs, t.run_type)),
        h("div", {}, h("h3", { class: "io-h" }, "Output"), t.error ? h("pre", { class: "error" }, t.error) : prettyValue(t.outputs, t.run_type))))));
  }

  // Details: every turn listed, each one's run tree collapsed until opened; the selected turn starts open.
  function detailsView() {
    const panel = h("div", { class: "card side" }, h("div", { class: "muted" }, "Select a run."));
    const openTurns = new Set([q.get("trace") || turns.at(-1).id]);
    const trees = {};
    let active = null;
    const left = h("div", { class: "card turns-list" });
    async function selectRun(traceId, run) {
      active = trees[traceId];
      for (const [id, tr] of Object.entries(trees)) if (id !== traceId) tr.tree.select(null, true);
      q.set("trace", traceId); q.set("run", run.id); replaceHash(["p", project, "thread", threadId], q);
      panel.replaceChildren(...runPanel(run));
    }
    function drawList() {
      left.replaceChildren(...turns.map((t, i) => {
        const open = openTurns.has(t.id);
        const slot = h("div", { class: "turn-tree" });
        if (open) {
          slot.append(h("div", { class: "muted pad" }, "Loading…"));
          fetchTrace(t.id).then((data) => {
            const tree = RunTree(data.runs, { onSelect: (r) => selectRun(t.id, r) });
            trees[t.id] = { tree, data };
            slot.replaceChildren(tree.el);
            if (q.get("trace") === t.id || (!q.get("trace") && i === turns.length - 1)) {
              const want = data.runs.find((r) => r.id === q.get("run")) || data.runs.find((r) => r.is_root) || data.runs[0];
              tree.select(want);
              slot.scrollIntoView({ block: "nearest" });
            }
          });
        }
        return h("div", { class: "turn-row" + (open ? " open" : "") },
          h("div", { class: "turn-head", onclick: () => { open ? openTurns.delete(t.id) : openTurns.add(t.id); drawList(); } },
            h("span", { class: "twisty" }, open ? "▼" : "▶"), h("b", {}, `Turn ${i + 1}`), badge(t.status),
            h("span", { class: "preview-inline" }, previewOf(t.inputs)), h("span", { class: "spacer" }),
            h("span", { class: "muted" }, fmtSec(t.latency)), t.total_tokens ? h("span", { class: "muted" }, ` · ${fmtNum(t.total_tokens)}t`) : null),
          open ? slot : null);
      }));
    }
    drawList();
    keyHandler = (e) => {
      baseKeys(e);
      if (active && (e.key === "ArrowDown" || e.key === "j")) { e.preventDefault(); active.tree.move(1); }
      if (active && (e.key === "ArrowUp" || e.key === "k")) { e.preventDefault(); active.tree.move(-1); }
    };
    return h("div", { class: "split" }, left, panel);
  }

  onLeave(() => { keyHandler = null; });
  $app.replaceChildren(
    h("div", { class: "page-h" }, h("h1", {}, "Thread ", h("span", { class: "mono thin" }, threadId)), copyBtn(threadId, "copy id"),
      h("span", { class: "spacer" }), viewBtns,
      h("button", { class: "ghost", title: "Download the conversation as Markdown", onclick: () => download(`thread-${threadId}.md`, threadMarkdown(threadId, turns)) }, "⬇ Markdown")),
    h("div", { class: "statrow" },
      statBlock("Turns", fmtNum(st.turns)), statBlock("First", fmtTime(st.first_start)), statBlock("Last", fmtTime(st.last_start)),
      statBlock("Latency P50", fmtSec(st.p50)), statBlock("Latency P99", fmtSec(st.p99)),
      statBlock("Tokens", fmtNum(st.total_tokens), `${fmtNum(st.input_tokens)} in / ${fmtNum(st.output_tokens)} out`),
      LT.me?.prices ? statBlock("Cost", fmtCost(st.cost)) : null, statBlock("Errors", fmtNum(st.errors))),
    body);
  draw();
}

function previewOf(v) {
  const msgs = messagesOf(v);
  const text = msgs ? contentText(normMsg(msgs.at(-1)).content) : plainText(v);
  return text.length > 120 ? text.slice(0, 119) + "…" : text;
}

// ---------- compare ----------

function pathKeys(runs) {
  const byId = Object.fromEntries(runs.map((r) => [r.id, r]));
  const keyOf = {}, count = {};
  for (const r of runs) {
    const parent = r.parent_run_id && byId[r.parent_run_id] ? keyOf[r.parent_run_id] : "";
    const base = `${parent}/${r.name}`;
    count[base] = (count[base] || 0) + 1;
    keyOf[r.id] = `${base}#${count[base]}`;
  }
  return keyOf;
}

async function viewCompare(a, b) {
  const [ta, tb] = await Promise.all([fetchTrace(a, true), fetchTrace(b, true)]);
  setCrumbs(h("a", { href: "#/" }, "Projects"), h("a", { href: href(["p", ta.project]) }, ta.project), h("span", {}, "Compare"));
  const cols = [ta, tb].map((t) => {
    const keys = pathKeys(t.runs);
    const byKey = Object.fromEntries(Object.entries(keys).map(([id, k]) => [k, t.runs.find((r) => r.id === id)]));
    const panel = h("div", { class: "cmp-panel" });
    return { t, keys, byKey, panel };
  });
  let syncing = false;
  cols.forEach((c, i) => {
    c.tree = RunTree(c.t.runs, { compact: true, onSelect: (r) => {
      c.panel.replaceChildren(...runPanel(r));
      if (syncing) return;
      const other = cols[1 - i], match = other.byKey[c.keys[r.id]];
      if (match) { syncing = true; other.tree.select(match); syncing = false; }
    } });
  });
  const head = (t) => {
    const root = t.runs.find((r) => r.is_root) || t.runs[0];
    return h("div", { class: "cmp-h" }, h("a", { href: href(["t", t.trace_id]) }, root.name), badge(root.status),
      h("span", { class: "stat" }, fmtSec(root.latency)),
      h("span", { class: "stat" }, `${fmtNum(t.runs.reduce((s, r) => s + (r.total_tokens || 0), 0))} tokens`),
      h("span", { class: "stat" }, fmtTime(root.start_time)));
  };
  $app.replaceChildren(h("div", { class: "page-h" }, h("h1", {}, "Compare traces"),
    h("span", { class: "muted" }, "Selecting a run selects the matching run in the other trace.")),
    h("div", { class: "cmp" }, cols.map((c) => h("div", { class: "card cmp-col" }, head(c.t), c.tree.el, c.panel))));
  cols[0].tree.select(cols[0].t.runs.find((r) => r.is_root) || cols[0].t.runs[0]);
}
