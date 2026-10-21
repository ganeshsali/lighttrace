// Project views: projects list, Threads / Traces / Runs tables with filters + stats, Dashboard.
"use strict";

const RANGES = { "1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400, all: 0 };
const RANGE_LABEL = { "1h": "Last hour", "24h": "Last 24 hours", "7d": "Last 7 days", "30d": "Last 30 days", "90d": "Last 90 days", all: "All time" };
const sinceOf = (range) => RANGES[range] ? Date.now() / 1000 - RANGES[range] : null;

function rangeSelect(value, onchange, opts = Object.keys(RANGES)) {
  return h("select", { onchange: (e) => onchange(e.target.value), title: "Time range" },
    opts.map((k) => h("option", { value: k, selected: k === value }, RANGE_LABEL[k])));
}

// ---------- projects ----------

async function viewProjects(q) {
  setCrumbs(h("a", { href: "#/" }, "Projects"));
  const range = q.get("range") || "7d";
  const since = sinceOf(range);
  const projects = await api(`/api/projects${since ? `?since=${since}` : "?since=0"}`);
  const prices = LT.me?.prices;
  const cols = ["Name", "Traces", "Threads", "Error rate", "P50", "P99", "Tokens", ...(prices ? ["Cost"] : []), "Last activity"];
  $app.replaceChildren(
    h("div", { class: "page-h" }, h("h1", {}, "Tracing projects"), h("span", { class: "spacer" }),
      h("button", { class: "ghost", onclick: () => {
        const name = (prompt("New project name (you'll create its API key next):") || "").trim();
        if (name) go(["p", name, "keys"]);
      } }, "+ New project"),
      rangeSelect(range, (v) => go([], new URLSearchParams({ range: v })), ["24h", "7d", "30d", "90d", "all"])),
    h("div", { class: "card" }, projects.length ? h("table", {},
      h("thead", {}, h("tr", {}, cols.map((c, i) => h("th", { class: i && i < cols.length - 1 ? "num" : "" }, c)))),
      h("tbody", {}, projects.map((p) => {
        const s = p.stats;
        return h("tr", { class: "click", onclick: () => go(["p", p.name]) },
          h("td", {}, h("a", { href: href(["p", p.name]) }, p.name), h("div", { class: "muted small" }, `${fmtNum(p.trace_count)} traces all time`)),
          h("td", { class: "num" }, fmtNum(s.traces)), h("td", { class: "num" }, fmtNum(s.threads)),
          h("td", { class: "num" }, s.errors ? h("span", { class: "badge error" }, fmtPct(s.error_rate)) : fmtPct(s.error_rate)),
          h("td", { class: "num" }, fmtSec(s.p50)), h("td", { class: "num" }, fmtSec(s.p99)),
          h("td", { class: "num" }, fmtNum(s.total_tokens)), prices ? h("td", { class: "num" }, fmtCost(s.cost)) : null,
          h("td", { title: fmtTime(p.last_activity), class: "muted" }, fmtAgo(p.last_activity)));
      })))
      : h("div", { class: "empty" }, "No projects yet. Create one with “+ New project” to get an API key, then point an app at this server.")));
}

// ---------- project page ----------

const TABS = [["threads", "Threads"], ["traces", "Traces"], ["runs", "Runs"], ["dashboard", "Dashboard"], ["keys", "Keys"]];

function filterParams(q, tab) {
  const p = new URLSearchParams();
  const since = sinceOf(q.get("range") || "7d");
  if (since) p.set("since", since);
  for (const k of ["status", "q", "name", "tag", "min_latency", "max_latency", "min_tokens"]) if (q.get(k)) p.set(k, q.get(k));
  if (tab === "runs" && q.get("run_type")) p.set("run_type", q.get("run_type"));
  for (const m of q.getAll("meta")) p.append("meta", m);
  return p;
}

const ADV = [  // advanced filters shown as chips: [param, label]
  ["name", "Name contains"], ["run_type", "Run type"], ["tag", "Tag"], ["min_latency", "Latency ≥ (s)"], ["max_latency", "Latency ≤ (s)"],
  ["min_tokens", "Tokens ≥"],
];

function columnsFor(tab, project) {
  const prices = LT.me?.prices;
  const open = (item) => tab === "threads" ? href(["p", project, "thread", item.thread_id]) : tab === "runs" ? href(["t", item.trace_id], new URLSearchParams({ run: item.id })) : href(["t", item.id]);
  const name = { id: "name", label: "Name", render: (x) => h("a", { href: open(x) }, x.name || "(unnamed)") };
  const input = { id: "input", label: "Input", cls: "preview", render: (x) => h("span", { title: x.input_preview }, x.input_preview) };
  const output = { id: "output", label: "Output", cls: "preview", render: (x) => x.error ? h("span", { class: "errtext", title: x.error }, x.error.split(/\n|Traceback \(/)[0]) : h("span", { title: x.output_preview }, x.output_preview) };
  const status = { id: "status", label: "Status", render: (x) => badge(x.status) };
  const latency = { id: "latency", label: "Latency", num: true, render: (x) => fmtSec(x.latency) || (x.end_time ? "" : "…") };
  const ftt = { id: "ftt", label: "First token", num: true, off: true, render: (x) => fmtSec(x.first_token_time) };
  const tokens = { id: "tokens", label: "Tokens", num: true, render: (x) => fmtNum(x.total_tokens) };
  const cost = prices ? { id: "cost", label: "Cost", num: true, render: (x) => fmtCost(x.cost) } : null;
  const tags = { id: "tags", label: "Tags", off: true, render: (x) => h("span", { class: "tags" }, (x.tags || []).map((t) => h("span", { class: "tag" }, t))) };
  const thread = { id: "thread", label: "Thread", off: true, render: (x) => x.thread_id ? h("a", { class: "mono", href: href(["p", project, "thread", x.thread_id]), onclick: (e) => e.stopPropagation() }, x.thread_id) : "" };
  const start = { id: "start", label: "Start", render: (x) => h("span", { class: "muted", title: fmtTime(x.start_time) }, fmtAgo(x.start_time)) };
  if (tab === "threads") return [
    { id: "thread", label: "Thread", render: (x) => h("a", { class: "mono", href: open(x) }, x.thread_id) },
    { id: "first", label: "First input", cls: "preview", render: (x) => h("span", { title: x.first_input }, x.first_input) },
    { id: "last", label: "Last output", cls: "preview", render: (x) => h("span", { title: x.last_output }, x.last_output) },
    { id: "turns", label: "Turns", num: true, render: (x) => fmtNum(x.turns) },
    { id: "errors", label: "Errors", num: true, render: (x) => x.errors ? h("span", { class: "badge error" }, x.errors) : "0" },
    { id: "p50", label: "P50", num: true, render: (x) => fmtSec(x.p50) }, { id: "p99", label: "P99", num: true, render: (x) => fmtSec(x.p99) },
    tokens, cost,
    { id: "firststart", label: "First start", off: true, render: (x) => h("span", { class: "muted" }, fmtTime(x.first_start)) },
    { id: "laststart", label: "Last start", render: (x) => h("span", { class: "muted", title: fmtTime(x.last_start) }, fmtAgo(x.last_start)) },
  ].filter(Boolean);
  if (tab === "runs") return [name, { id: "type", label: "Type", render: (x) => typeTag(x.run_type) }, input, output, status, latency, ftt, tokens, cost,
    { id: "model", label: "Model", render: (x) => h("span", { class: "mono small" }, x.model || "") }, tags, start].filter(Boolean);
  return [name, input, output, status, latency, ftt, tokens, cost, tags, thread, start].filter(Boolean);
}

async function viewProject(project, tab, q) {
  if (!TABS.some(([t]) => t === tab)) tab = "traces";
  setCrumbs(h("a", { href: "#/" }, "Projects"), h("a", { href: href(["p", project]) }, project));
  const tabs = h("div", { class: "tabs" }, TABS.map(([t, label]) => h("a", { href: href(["p", project, t]), class: t === tab ? "on" : "" }, label)),
    h("span", { class: "spacer" }),
    menu("⋯", [["Delete project…", async () => {
      const typed = prompt(`This deletes every trace and API key in "${project}". Type the project name to confirm:`);
      if (typed !== project) return;
      await api(`/api/projects/${enc(project)}`, { method: "DELETE" }); go([]);
    }, "danger"]]));
  const head = h("div", { class: "page-h" }, h("h1", {}, project));
  if (tab === "dashboard") return viewDashboard(project, q, head, tabs);
  if (tab === "keys") return viewKeys(project, head, tabs);

  const range = q.get("range") || "7d";
  const update = (changes) => { for (const [k, v] of Object.entries(changes)) v === "" || v == null ? q.delete(k) : q.set(k, v); q.delete("page"); go(["p", project, tab], q); };

  // --- filter bar
  const search = h("input", { type: "search", placeholder: "Search inputs, outputs, errors…", value: q.get("q") || "", class: "grow",
    onkeydown: (e) => e.key === "Enter" && update({ q: e.target.value.trim() }) });
  const advanced = h("div", { class: "card adv", hidden: true });
  const fields = {};
  const field = (k, el) => { fields[k] = el; el.value = q.get(k) || ""; return el; };
  const metaKey = h("input", { placeholder: "key (e.g. user_id)", size: 16 });
  const metaVal = h("input", { placeholder: "value", size: 14 });
  advanced.append(
    h("div", { class: "adv-grid" },
      h("label", {}, "Name contains", field("name", h("input", { size: 16 }))),
      tab === "runs" ? h("label", {}, "Run type", field("run_type", h("select", {}, ["", "agent", "chain", "llm", "tool", "retriever", "prompt", "parser", "embedding"].map((t) => h("option", { value: t }, t || "any"))))) : null,
      h("label", {}, "Tag", field("tag", h("input", { size: 12 }))),
      h("label", {}, "Latency ≥ s", field("min_latency", h("input", { size: 6, type: "number", step: "any" }))),
      h("label", {}, "Latency ≤ s", field("max_latency", h("input", { size: 6, type: "number", step: "any" }))),
      h("label", {}, "Tokens ≥", field("min_tokens", h("input", { size: 7, type: "number" }))),
      h("label", {}, "Metadata", h("span", { class: "row" }, metaKey, metaVal))),
    h("div", { class: "row" }, h("span", { class: "spacer" }),
      h("button", { class: "ghost", onclick: () => { advanced.hidden = true; } }, "Cancel"),
      h("button", { onclick: () => {
        const ch = {};
        for (const [k, el] of Object.entries(fields)) ch[k] = el.value.trim();
        if (metaKey.value.trim() && metaVal.value.trim()) q.append("meta", `${metaKey.value.trim()}:${metaVal.value.trim()}`);
        update(ch);
      } }, "Apply")));

  const chips = [];
  for (const [k, label] of ADV) if (q.get(k) && !(k === "run_type" && tab !== "runs")) {
    chips.push(h("span", { class: "chip" }, `${label}: ${q.get(k)}`, h("button", { class: "chip-x", onclick: () => update({ [k]: "" }) }, "×")));
  }
  q.getAll("meta").forEach((m) => chips.push(h("span", { class: "chip" }, `metadata ${m}`, h("button", { class: "chip-x", onclick: () => {
    const rest = q.getAll("meta").filter((x) => x !== m); q.delete("meta"); rest.forEach((x) => q.append("meta", x)); update({});
  } }, "×"))));
  if (q.get("status")) chips.push(h("span", { class: "chip" }, `status: ${q.get("status")}`, h("button", { class: "chip-x", onclick: () => update({ status: "" }) }, "×")));

  const saved = await api(`/api/projects/${enc(project)}/filters`).catch(() => []);
  const savedMenu = menu("Saved filters ▾", [
    ...saved.map((s) => [s.name, () => go(["p", project, tab], new URLSearchParams(s.query))]),
    ["Save current as…", async () => {
      const name = prompt("Name for this filter:");
      if (!name) return;
      const query = {}; for (const [k, v] of q) if (k !== "page") query[k] = v;
      await api(`/api/projects/${enc(project)}/filters`, { method: "POST", body: { name, query } }); route();
    }],
    ...saved.map((s) => [`Delete "${s.name}"`, async () => { await api(`/api/filters/${enc(s.id)}`, { method: "DELETE" }); route(); }, "danger"]),
  ]);

  const cols = columnsFor(tab, project);
  const hidden = new Set(store(`cols-${tab}`) || cols.filter((c) => c.off).map((c) => c.id));
  const colMenu = h("span", { class: "menu" });
  const colPop = h("div", { class: "menu-pop", hidden: true }, cols.map((c) => h("label", { class: "menu-item" },
    h("input", { type: "checkbox", checked: !hidden.has(c.id), onchange: (e) => { e.target.checked ? hidden.delete(c.id) : hidden.add(c.id); store(`cols-${tab}`, [...hidden]); loadTable(); } }), " ", c.label)));
  colMenu.append(h("button", { class: "ghost", onclick: (e) => { e.stopPropagation(); colPop.hidden = !colPop.hidden; } }, "Columns"), colPop);
  colPop.addEventListener("click", (e) => e.stopPropagation());
  const closeCols = () => { colPop.hidden = true; };
  document.addEventListener("click", closeCols); onLeave(() => document.removeEventListener("click", closeCols));

  const auto = h("label", { class: "muted small autoref", title: "Refresh every 10 seconds" },
    h("input", { type: "checkbox", checked: !!store("autorefresh"), onchange: (e) => { store("autorefresh", e.target.checked); setAuto(); } }), " Live");
  let timer = null;
  const setAuto = () => { clearInterval(timer); timer = store("autorefresh") ? setInterval(() => { loadTable(); loadStats(); }, 10000) : null; };
  onLeave(() => clearInterval(timer));

  const filterBar = h("div", { class: "filters" },
    rangeSelect(range, (v) => update({ range: v })),
    h("select", { onchange: (e) => update({ status: e.target.value }), title: "Status" },
      [["", "Any status"], ["success", "Success"], ["error", "Error"], ["pending", "Running"]].map(([v, l]) => h("option", { value: v, selected: v === (q.get("status") || "") }, l))),
    search,
    h("button", { class: "ghost", onclick: () => { advanced.hidden = !advanced.hidden; } }, "+ Filter"),
    savedMenu, colMenu, auto,
    h("button", { class: "ghost", title: "Refresh", onclick: () => { loadTable(); loadStats(); } }, "↻"));

  const tableBox = h("div", { class: "card" }, h("div", { class: "empty" }, "Loading…"));
  const statsBox = h("aside", { class: "card statside" }, h("div", { class: "muted" }, "…"));
  const selection = new Set();
  const actions = h("div", { class: "bulk", hidden: true });

  function drawActions() {
    actions.hidden = selection.size === 0;
    const ids = [...selection];
    actions.replaceChildren(h("span", {}, `${ids.length} selected`),
      ids.length === 2 ? h("button", { onclick: () => go(["compare", ids[0], ids[1]]) }, "Compare") : h("span", { class: "muted small" }, "Select 2 to compare"),
      h("button", { class: "ghost danger", onclick: async () => {
        if (!confirm(`Delete ${ids.length} trace(s) permanently?`)) return;
        for (const id of ids) await api(`/api/traces/${enc(id)}`, { method: "DELETE" });
        selection.clear(); drawActions(); loadTable(); loadStats();
      } }, "Delete"),
      h("button", { class: "ghost", onclick: () => { selection.clear(); drawActions(); loadTable(); } }, "Clear"));
  }

  async function loadTable() {
    const page = +(q.get("page") || 0);
    const params = filterParams(q, tab);
    params.set("limit", PAGE); params.set("offset", page * PAGE);
    if (tab !== "threads") params.set("scope", tab);
    const data = await api(`/api/projects/${enc(project)}/${tab === "threads" ? "threads" : "runs"}?${params}`);
    const shown = cols.filter((c) => !hidden.has(c.id));
    const selectable = tab === "traces";
    const pager = h("div", { class: "pager" },
      h("span", { class: "muted" }, `Page ${page + 1}`),
      h("button", { class: "ghost", disabled: page === 0, onclick: () => { q.set("page", page - 1); go(["p", project, tab], q); } }, "← Prev"),
      h("button", { class: "ghost", disabled: !data.has_more, onclick: () => { q.set("page", page + 1); go(["p", project, tab], q); } }, "Next →"));
    const rowHref = (x) => tab === "threads" ? href(["p", project, "thread", x.thread_id]) : tab === "runs" ? href(["t", x.trace_id], new URLSearchParams({ run: x.id })) : href(["t", x.id]);
    tableBox.replaceChildren(...(data.items.length ? [h("div", { class: "tablewrap" }, h("table", {},
      h("thead", {}, h("tr", {}, selectable ? h("th", { class: "sel" }) : null, shown.map((c) => h("th", { class: c.num ? "num" : "" }, c.label)))),
      h("tbody", {}, data.items.map((x) => h("tr", { class: "click", onclick: () => { location.hash = rowHref(x); } },
        selectable ? h("td", { class: "sel", onclick: (e) => e.stopPropagation() }, h("input", { type: "checkbox", checked: selection.has(x.id), onchange: (e) => {
          e.target.checked ? selection.add(x.id) : selection.delete(x.id); drawActions();
        } })) : null,
        shown.map((c) => h("td", { class: (c.num ? "num " : "") + (c.cls || "") }, c.render(x)))))))), pager]
      : [h("div", { class: "empty" }, tab === "threads"
        ? "No threads. Traces join a thread when their metadata has thread_id, session_id or conversation_id."
        : "No matching " + tab + ".")]));
  }

  async function loadStats() {
    const since = sinceOf(range);
    const s = await api(`/api/projects/${enc(project)}/stats${since ? `?since=${since}` : ""}`);
    statsBox.replaceChildren(h("div", {},
      h("div", { class: "statside-h" }, RANGE_LABEL[range]),
      statBlock("Traces", fmtNum(s.traces)), statBlock("Threads", fmtNum(s.threads)),
      statBlock("Error rate", fmtPct(s.error_rate), s.errors ? `${fmtNum(s.errors)} errors` : null),
      statBlock("Latency P50", fmtSec(s.p50)), statBlock("Latency P99", fmtSec(s.p99)),
      statBlock("Tokens", fmtNum(s.total_tokens), s.median_tokens != null ? `median ${fmtNum(Math.round(s.median_tokens))} / trace` : null),
      LT.me?.prices ? statBlock("Cost", fmtCost(s.cost)) : null));
  }

  $app.replaceChildren(...[head, tabs, filterBar, advanced, chips.length ? h("div", { class: "chips" }, chips,
    h("button", { class: "ghost small", onclick: () => go(["p", project, tab], new URLSearchParams(range !== "7d" ? { range } : {})) }, "Clear all")) : null,
    actions, h("div", { class: "withside" }, tableBox, statsBox)].filter(Boolean));
  setAuto();
  await Promise.all([loadTable(), loadStats()]);
}

// ---------- api keys ----------

async function viewKeys(project, head, tabs) {
  const keys = await api(`/api/projects/${enc(project)}/keys`);
  const name = h("input", { placeholder: "Key name (e.g. prod server)", size: 28, maxlength: 100 });
  const create = async (e) => {
    e.preventDefault();
    const k = await api(`/api/projects/${enc(project)}/keys`, { method: "POST", body: { name: name.value.trim() } });
    await viewKeys(project, head, tabs);
    document.getElementById("newkey").replaceChildren(h("div", { class: "card newkey" },
      h("b", {}, "Copy this key now — it won't be shown again."),
      h("div", { class: "row" }, h("code", { class: "mono" }, k.key), copyBtn(k.key)),
      h("div", { class: "muted small" }, `It can only send traces to “${project}”. Put it in the app as OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<key>.`)));
  };
  $app.replaceChildren(head, tabs, h("div", { id: "newkey" }),
    h("form", { class: "filters", onsubmit: create }, name, h("button", { type: "submit" }, "Create key")),
    h("div", { class: "card" }, keys.length ? h("table", {},
      h("thead", {}, h("tr", {}, ["Name", "Key", "Created", "Last used", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, keys.map((k) => h("tr", {},
        h("td", {}, k.name), h("td", { class: "mono" }, `${k.prefix}…`),
        h("td", { class: "muted", title: fmtTime(k.created_at) }, fmtAgo(k.created_at)),
        h("td", { class: "muted", title: fmtTime(k.last_used_at) }, k.last_used_at ? fmtAgo(k.last_used_at) : "never"),
        h("td", {}, h("button", { class: "ghost danger", onclick: async () => {
          if (!confirm(`Revoke key “${k.name}”? Apps using it will stop sending traces.`)) return;
          await api(`/api/keys/${enc(k.id)}`, { method: "DELETE" }); viewKeys(project, head, tabs);
        } }, "Revoke"))))))
      : h("div", { class: "empty" }, "No keys for this project yet. A key created here can only send traces to this project.")));
  name.focus();
}

// ---------- dashboard ----------

async function viewDashboard(project, q, head, tabs) {
  const range = q.get("range") || "7d";
  const span = RANGES[range] || 30 * 86400;
  const until = Date.now() / 1000, since = until - span;
  const [d, s] = await Promise.all([
    api(`/api/projects/${enc(project)}/dashboard?since=${since}&until=${until}`),
    api(`/api/projects/${enc(project)}/stats?since=${since}`)]);
  const ser = d.series;
  const charts = [
    chartCard("Traces", fmtNum(s.traces), s.errors ? `${fmtPct(s.error_rate)} errors` : "no errors", ser, d.bucket, "bar", [
      { key: "ok", label: "Success", color: "var(--series-1)", get: (b) => b.traces - b.errors },
      { key: "errors", label: "Errors", color: "var(--critical)", get: (b) => b.errors }], fmtNum),
    chartCard("Latency", fmtSec(s.p50), `P99 ${fmtSec(s.p99)}`, ser, d.bucket, "line", [
      { key: "p50", label: "P50", color: "var(--series-1)", get: (b) => b.p50 },
      { key: "p99", label: "P99", color: "var(--series-2)", get: (b) => b.p99 }], fmtSec),
    chartCard("Tokens", fmtNum(s.total_tokens), s.median_tokens != null ? `median ${fmtNum(Math.round(s.median_tokens))} / trace` : "", ser, d.bucket, "bar", [
      { key: "tokens", label: "Tokens", color: "var(--series-1)", get: (b) => b.tokens }], fmtNum),
    LT.me?.prices ? chartCard("Cost", fmtCost(s.cost), "from LIGHTTRACE_PRICES", ser, d.bucket, "bar", [
      { key: "cost", label: "Cost", color: "var(--series-1)", get: (b) => b.cost }], fmtCost) : null,
  ];
  const topTable = (title, rows, showTokens) => h("div", { class: "card topt" }, h("h3", {}, title), rows?.length ? h("table", { class: "mini" },
    h("thead", {}, h("tr", {}, ["Name", "Runs", "Errors", "P50", "P99", ...(showTokens ? ["Tokens"] : [])].map((c, i) => h("th", { class: i ? "num" : "" }, c)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "mono small" }, r.name || "(unnamed)"), h("td", { class: "num" }, fmtNum(r.count)),
      h("td", { class: "num" }, r.errors ? h("span", { class: "badge error" }, r.errors) : "0"),
      h("td", { class: "num" }, fmtSec(r.p50)), h("td", { class: "num" }, fmtSec(r.p99)), showTokens ? h("td", { class: "num" }, fmtNum(r.tokens)) : null))))
    : h("div", { class: "muted" }, "None in this range."));
  $app.replaceChildren(head, tabs,
    h("div", { class: "filters" }, rangeSelect(range, (v) => go(["p", project, "dashboard"], new URLSearchParams({ range: v })), ["1h", "24h", "7d", "30d", "90d"]),
      h("span", { class: "muted small" }, `${ser.length} buckets of ${fmtSec(d.bucket)}`)),
    h("div", { class: "charts" }, charts.filter(Boolean).map((c) => c.el)),
    h("div", { class: "tops" }, d.top.agent ? topTable("Agents", d.top.agent) : null, topTable("Tools", d.top.tool), topTable("LLM calls by model", d.top.llm, true),
      topTable("Chains", d.top.chain), d.top.retriever ? topTable("Retrievers", d.top.retriever) : null));
  charts.filter(Boolean).forEach((c) => c.draw());
}

function bucketLabel(t, bucket, full) {
  const d = new Date(t * 1000);
  if (bucket >= 86400) return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return full || bucket >= 3600 ? `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${time}` : time;
}

// One chart card: title, hero number, legend, SVG (bar or line) with a per-bucket hover readout, and a table view.
function chartCard(title, hero, sub, series, bucket, kind, keys, fmt) {
  const plot = h("div", { class: "plot" });
  const tip = h("div", { class: "tip", hidden: true });
  const tableBtn = h("button", { class: "ghost small" }, "Table");
  const tableView = h("div", { class: "tablewrap", hidden: true }, h("table", { class: "mini" },
    h("thead", {}, h("tr", {}, h("th", {}, "Time"), keys.map((k) => h("th", { class: "num" }, k.label)))),
    h("tbody", {}, series.map((b) => h("tr", {}, h("td", {}, bucketLabel(b.t, bucket, true)), keys.map((k) => h("td", { class: "num" }, fmt(k.get(b)))))))));
  tableBtn.onclick = () => { tableView.hidden = !tableView.hidden; plot.hidden = !tableView.hidden; tableBtn.textContent = tableView.hidden ? "Table" : "Chart"; };
  const legend = keys.length > 1 ? h("div", { class: "legend" }, keys.map((k) => h("span", { class: "lg" }, h("i", { style: `background:${k.color}` }), k.label))) : null;
  const el = h("div", { class: "card chart viz-root" },
    h("div", { class: "chart-h" }, h("div", {}, h("div", { class: "chart-t" }, title), h("div", { class: "hero" }, hero || "—"), h("div", { class: "muted small" }, sub || "")),
      h("span", { class: "spacer" }), tableBtn),
    legend, h("div", { class: "plotwrap" }, plot, tip), tableView);

  function draw() {
    const W = Math.max(plot.clientWidth, 280), H = 170, L = 52, R = 10, T = 8, B = 22;
    const n = series.length, cw = (W - L - R) / n;
    const vals = series.map((b) => keys.map((k) => k.get(b)));
    const raw = Math.max(1e-9, ...series.map((b, i) => kind === "bar" ? vals[i].reduce((s, v) => s + (v || 0), 0) : Math.max(0, ...vals[i].map((v) => v ?? 0))));
    // Nice axis: 1/2/5 x 10^k steps, three of them.
    const mag = 10 ** Math.floor(Math.log10(raw / 3));
    const step = [1, 2, 5, 10].map((m) => m * mag).find((st) => st * 3 >= raw);
    const max = step * 3;
    const y = (v) => T + (H - T - B) * (1 - v / max);
    const g = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${title} over time` });
    for (let i = 0; i <= 3; i++) {
      const v = step * i;
      g.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: i ? "grid" : "axis" }),
        svg("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", class: "axlabel" }, fmt(v) || "0"));
    }
    [0, Math.floor(n / 2), n - 1].forEach((i, j) => g.append(svg("text", { x: L + cw * i + cw / 2, y: H - 6, "text-anchor": j === 0 ? "start" : j === 2 ? "end" : "middle", class: "axlabel" }, bucketLabel(series[i].t, bucket))));
    if (kind === "bar") {
      const bw = Math.max(1, cw - 2);
      series.forEach((b, i) => {
        let base = 0;
        keys.forEach((k, j) => {
          const v = vals[i][j] || 0;
          if (!v) return;
          const y0 = y(base), y1 = y(base + v), top = j === keys.length - 1 || !vals[i].slice(j + 1).some(Boolean);
          const hgt = Math.max(1, y0 - y1 - (base ? 1 : 0)), x = L + cw * i + 1, r = top ? Math.min(3, bw / 2, hgt) : 0;
          g.append(svg("path", { d: `M${x},${y0 - (base ? 1 : 0)} V${y0 - (base ? 1 : 0) - hgt + r} Q${x},${y0 - (base ? 1 : 0) - hgt} ${x + r},${y0 - (base ? 1 : 0) - hgt} H${x + bw - r} Q${x + bw},${y0 - (base ? 1 : 0) - hgt} ${x + bw},${y0 - (base ? 1 : 0) - hgt + r} V${y0 - (base ? 1 : 0)} Z`, fill: k.color }));
          base += v;
        });
      });
    } else {
      keys.forEach((k, j) => {
        let d = "", pen = false;
        series.forEach((b, i) => {
          const v = vals[i][j];
          if (v == null) { pen = false; return; }
          d += `${pen ? "L" : "M"}${L + cw * i + cw / 2},${y(v)} `; pen = true;
        });
        if (d) g.append(svg("path", { d, fill: "none", stroke: k.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
        series.forEach((b, i) => {  // isolated points would be invisible as a path
          const v = vals[i][j], prev = vals[i - 1]?.[j], next = vals[i + 1]?.[j];
          if (v != null && prev == null && next == null) g.append(svg("circle", { cx: L + cw * i + cw / 2, cy: y(v), r: 3, fill: k.color }));
        });
      });
    }
    const hair = svg("line", { y1: T, y2: H - B, class: "hair", visibility: "hidden" });
    g.append(hair);
    const hit = svg("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" });
    const show = (e) => {
      const rect = g.getBoundingClientRect();
      const i = Math.max(0, Math.min(n - 1, Math.floor((e.clientX - rect.left - L) / cw)));
      const cx = L + cw * i + cw / 2;
      hair.setAttribute("x1", cx); hair.setAttribute("x2", cx); hair.setAttribute("visibility", "visible");
      tip.replaceChildren(h("div", { class: "tip-t" }, bucketLabel(series[i].t, bucket, true)),
        ...keys.map((k, j) => h("div", { class: "tip-r" }, h("i", { style: `background:${k.color}` }), h("span", {}, k.label), h("b", {}, fmt(vals[i][j]) || "—"))));
      tip.hidden = false;
      tip.style.left = `${Math.min(cx + 12, W - 170)}px`; tip.style.top = "8px";
    };
    hit.addEventListener("pointermove", show);
    hit.addEventListener("pointerleave", () => { tip.hidden = true; hair.setAttribute("visibility", "hidden"); });
    g.append(hit);
    plot.replaceChildren(g);
  }
  return { el, draw };
}
