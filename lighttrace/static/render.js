// Renderers: messages as chat, JSON trees, run inputs/outputs, and the run side panel.
"use strict";

// ---------- messages ----------

const ROLE = { human: "human", user: "human", HumanMessage: "human", HumanMessageChunk: "human", ai: "ai", assistant: "ai",
  AIMessage: "ai", AIMessageChunk: "ai", system: "system", SystemMessage: "system", tool: "tool", ToolMessage: "tool",
  function: "tool", FunctionMessage: "tool", developer: "system", chat: "human", ChatMessage: "human" };

function normMsg(m) {
  if (Array.isArray(m) && m.length === 2 && typeof m[0] === "string" && ROLE[m[0]]) return { role: ROLE[m[0]], content: m[1], toolCalls: [] };
  if (!m || typeof m !== "object" || Array.isArray(m)) return null;
  let k = m, role;
  if (m.lc && m.kwargs) { k = m.kwargs; role = ROLE[k.type] || ROLE[(m.id || []).at(-1)]; }
  else role = ROLE[m.type] || ROLE[m.role];
  if (!role || !("content" in k)) return null;
  let toolCalls = k.tool_calls || [];
  if (!toolCalls.length && k.additional_kwargs?.tool_calls) {
    toolCalls = k.additional_kwargs.tool_calls.map((tc) => ({ name: tc.function?.name, id: tc.id, args: tryJson(tc.function?.arguments) }));
  }
  return { role, content: k.content, name: k.name, toolCalls, toolCallId: k.tool_call_id, id: k.id, usage: k.usage_metadata };
}
const tryJson = (s) => { try { return JSON.parse(s); } catch { return s; } };
const isMsgList = (v) => Array.isArray(v) && v.length > 0 && v.every((m) => normMsg(m));

function contentText(c) {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.map((p) => typeof p === "string" ? p : p?.type === "text" ? p.text : "").join("");
  return c == null ? "" : JSON.stringify(c);
}

function renderContent(c) {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.map((p) => typeof p === "string" ? p : p?.type === "text" ? p.text : h("div", {}, jsonTree(p, 0)));
  return jsonTree(c, 1);
}

function renderMsg(m, opts = {}) {
  const n = normMsg(m);
  return h("div", { class: `msg ${n.role}` },
    h("div", { class: "msg-role" }, n.role, n.name ? h("span", { class: "mono" }, n.name) : null,
      n.toolCallId ? h("span", { class: "mono" }, `← ${n.toolCallId}`) : null, opts.extra || null),
    h("div", { class: "msg-body" }, renderContent(n.content)),
    n.toolCalls.map((tc) => h("details", { class: "toolcall", open: true },
      h("summary", {}, h("span", { class: "tc-name" }, `${tc.name}()`), tc.id ? h("span", { class: "muted mono" }, `  ${tc.id}`) : null),
      jsonTree(tc.args, 2))));
}
const chat = (msgs) => h("div", {}, msgs.map((m) => renderMsg(m)));

// The message list inside a run's inputs/outputs, if there is one.
function messagesOf(v) {
  if (!v || typeof v !== "object") return null;
  if (isMsgList(v)) return v;
  let msgs = v.messages;
  if (Array.isArray(msgs) && msgs.length === 1 && Array.isArray(msgs[0]) && !normMsg(msgs[0])) msgs = msgs[0];
  if (isMsgList(msgs)) return msgs;
  const gens = (v.generations || []).flat().map((g) => g?.message).filter((m) => m && normMsg(m));
  if (gens.length) return gens;
  if (v.output && normMsg(v.output)) return [v.output];
  return null;
}

// ---------- json ----------

function jsonTree(v, openDepth) { return h("div", { class: "json" }, jsonNode(v, openDepth)); }
function jsonNode(v, openDepth) {
  if (v === null || v === undefined) return h("span", { class: "nl" }, "null");
  if (typeof v === "string") return h("span", { class: "s" }, v.length > 120 || v.includes("\n") ? v : JSON.stringify(v));
  if (typeof v !== "object") return h("span", { class: "nm" }, String(v));
  const entries = Array.isArray(v) ? v.map((x, i) => [i, x]) : Object.entries(v);
  if (!entries.length) return h("span", { class: "nl" }, Array.isArray(v) ? "[]" : "{}");
  const label = Array.isArray(v) ? `[${entries.length} items]` : `{${entries.length} keys}`;
  return h("details", { open: openDepth > 0 }, h("summary", {}, label),
    entries.map(([k, x]) => h("div", {}, h("span", { class: "k" }, `${k}: `), jsonNode(x, openDepth - 1))));
}

// ---------- inputs / outputs ----------

function documentsOf(v) {
  const docs = Array.isArray(v) ? v : v?.documents || v?.output;
  if (!Array.isArray(docs) || !docs.length) return null;
  const norm = docs.map((d) => d?.kwargs || d).filter((d) => d && typeof d === "object" && "page_content" in d);
  return norm.length === docs.length ? norm : null;
}

function prettyValue(v, runType) {
  if (v == null) return h("span", { class: "muted" }, "—");
  if (runType === "retriever") {
    const docs = documentsOf(v);
    if (docs) return h("div", {}, docs.map((d, i) => h("div", { class: "doc" },
      h("div", { class: "doc-h" }, `Document ${i + 1}`), h("div", { class: "msg-body" }, d.page_content),
      d.metadata && Object.keys(d.metadata).length ? jsonTree(d.metadata, 0) : null)));
  }
  const msgs = messagesOf(v);
  if (msgs) return chat(msgs);
  if (Array.isArray(v?.prompts)) return h("div", {}, v.prompts.map((p) => h("div", { class: "msg human" },
    h("div", { class: "msg-role" }, "prompt"), h("div", { class: "msg-body" }, p))));
  if (typeof v === "string") return h("div", { class: "json" }, h("span", { class: "s" }, v));
  if (typeof v === "object" && !Array.isArray(v)) {
    const keys = Object.keys(v);
    if (keys.length === 1 && typeof v[keys[0]] === "string") return h("div", { class: "json" }, h("span", { class: "k" }, keys[0] + ": "), h("span", { class: "s" }, v[keys[0]]));
    if (keys.length === 1 && normMsg(v[keys[0]])) return renderMsg(v[keys[0]]);
  }
  return jsonTree(v, runType === "tool" ? 3 : 2);
}

// Input/Output section with a Pretty | JSON toggle (remembered) and a copy button.
function ioSection(title, value, runType, emptyText) {
  const body = h("div", {});
  let mode = store("io-mode") || "pretty";
  const draw = () => body.replaceChildren(value == null ? h("span", { class: "muted" }, emptyText || "—")
    : mode === "json" ? h("pre", { class: "rawjson" }, JSON.stringify(value, null, 2)) : prettyValue(value, runType));
  const btn = (m, label) => h("button", { class: "seg" + (mode === m ? " on" : ""), onclick: (e) => {
    mode = m; store("io-mode", m); e.target.parentNode.querySelectorAll(".seg").forEach((b) => b.classList.toggle("on", b === e.target)); draw();
  } }, label);
  draw();
  return h("div", { class: "sec" },
    h("div", { class: "sec-h" }, h("h3", {}, title), h("span", { class: "spacer" }),
      h("span", { class: "segs" }, btn("pretty", "Pretty"), btn("json", "JSON")),
      value != null ? copyBtn(JSON.stringify(value, null, 2)) : null),
    body);
}

const section = (title, ...body) => h("div", { class: "sec" }, h("div", { class: "sec-h" }, h("h3", {}, title)), ...body);

// ---------- run panel ----------

// ctx: { extraActions: [nodes] }
function runPanel(r, ctx = {}) {
  let tab = store("run-tab") === "meta" ? "meta" : "run";
  const body = h("div", {});
  const tabs = h("div", { class: "tabs small" });
  const tabBtn = (id, label) => h("a", { href: "javascript:void 0", class: tab === id ? "on" : "", onclick: () => { tab = id; store("run-tab", id); draw(); } }, label);
  function draw() {
    tabs.replaceChildren(tabBtn("run", "Run"), tabBtn("meta", "Metadata"));
    body.replaceChildren(...(tab === "meta" ? metaTab(r) : runTab(r)));
  }
  draw();
  const model = r.model;
  return [
    h("div", { class: "panel-h" }, typeTag(r.run_type), h("h2", {}, r.name), badge(r.status), h("span", { class: "spacer" }), ...(ctx.extraActions || [])),
    h("div", { class: "kv" },
      h("span", { class: "stat" }, "Latency ", h("b", {}, fmtSec(r.latency) || (r.end_time ? "" : "running…"))),
      r.first_token_time != null ? h("span", { class: "stat" }, "First token ", h("b", {}, fmtSec(r.first_token_time))) : null,
      r.total_tokens != null ? h("span", { class: "stat" }, "Tokens ", h("b", {}, fmtNum(r.total_tokens)),
        ` (${fmtNum(r.input_tokens)} in / ${fmtNum(r.output_tokens)} out)`) : null,
      r.cost != null ? h("span", { class: "stat" }, "Cost ", h("b", {}, fmtCost(r.cost))) : null,
      model ? h("span", { class: "stat" }, "Model ", h("b", {}, model)) : null,
      h("span", { class: "stat" }, fmtTime(r.start_time))),
    h("div", { class: "idline" }, "Run ID ", h("span", { class: "mono" }, r.id), copyBtn(r.id)),
    tabs, body,
  ];
}

function runTab(r) {
  const out = [];
  if (r.error) out.push(section("Error", h("pre", { class: "error" }, r.error)));
  out.push(ioSection("Input", r.inputs, r.run_type));
  out.push(ioSection("Output", r.outputs, r.run_type, r.end_time ? "—" : "running…"));
  return out;
}

function metaTab(r) {
  const ex = r.extra || {};
  const timing = { start: fmtTime(r.start_time), end: fmtTime(r.end_time), latency: fmtSec(r.latency),
    first_token: fmtSec(r.first_token_time) || null };
  return [
    r.tags?.length ? section("Tags", h("div", { class: "tags" }, r.tags.map((t) => h("span", { class: "tag" }, t)))) : null,
    section("Metadata", jsonTree(ex.metadata || {}, 1)),
    ex.invocation_params ? section("Invocation params", jsonTree(ex.invocation_params, 1)) : null,
    section("Timing", jsonTree(timing, 1)),
    section("IDs", jsonTree({ run_id: r.id, trace_id: r.trace_id, parent_run_id: r.parent_run_id, thread_id: r.thread_id }, 1)),
    ex.runtime ? section("Runtime", jsonTree(ex.runtime, 0)) : null,
    r.events?.length ? section(`Events (${r.events.length})`, jsonTree(r.events, 0)) : null,
  ].filter(Boolean);
}
