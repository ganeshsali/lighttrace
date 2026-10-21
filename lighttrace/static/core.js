// lighttrace UI core: DOM helper, API, formatting, router, theme, login.
// All payload text goes through textContent (never innerHTML).
"use strict";

const $app = document.getElementById("app");
const $crumbs = document.getElementById("crumbs");
const PAGE = 50;
const LT = { me: null };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}
const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}

async function api(path, opts = {}) {
  const method = opts.method || "GET";
  const headers = { ...(opts.body ? { "content-type": "application/json" } : {}), ...(method !== "GET" ? { "x-lighttrace": "1" } : {}) };
  const r = await fetch(path, { method, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (r.status === 401 && !path.startsWith("/api/login")) { showLogin(); throw new Error("auth"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}
const enc = encodeURIComponent;

// ---------- formatting ----------

const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString() : "";
function fmtAgo(t) {
  if (!t) return "";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
function fmtSec(sec) {
  if (sec == null) return "";
  const ms = sec * 1000;
  return ms < 1000 ? `${Math.round(ms)}ms` : ms < 60000 ? `${(ms / 1000).toFixed(2)}s` : `${(ms / 60000).toFixed(1)}m`;
}
const fmtNum = (n) => n == null ? "" : Number(n).toLocaleString();
function fmtCost(c) {
  if (c == null) return "";
  return c === 0 ? "$0" : c < 0.01 ? `$${c.toPrecision(2)}` : `$${c.toFixed(c < 1 ? 4 : 2)}`;
}
const fmtPct = (x) => x == null ? "" : `${(x * 100).toFixed(x < 0.1 ? 1 : 0)}%`;

const badge = (status) => h("span", { class: `badge ${status}` }, status === "success" ? "ok" : status === "pending" ? "running" : status);
const typeTag = (t) => h("span", { class: `type ${t || "other"}` }, t || "run");

function copyBtn(text, label = "copy") {
  return h("button", { class: "ghost copy", title: "Copy to clipboard", onclick: (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(text).then(() => { e.target.textContent = "copied"; setTimeout(() => e.target.textContent = label, 1200); });
  } }, label);
}

function setCrumbs(...parts) {
  $crumbs.replaceChildren(...parts.flatMap((p, i) => [i ? h("span", { class: "sep" }, "/") : null, p]).filter(Boolean));
}

function store(key, val) {
  try { if (val === undefined) return JSON.parse(localStorage.getItem("lt-" + key)); localStorage.setItem("lt-" + key, JSON.stringify(val)); } catch { return null; }
}

// ---------- routing ----------

function parseHash() {
  const [path, qs] = location.hash.slice(1).split("?");
  return { parts: (path || "/").split("/").filter(Boolean).map(decodeURIComponent), q: new URLSearchParams(qs || "") };
}
function href(parts, q) {
  const qs = q && [...q].length ? "?" + q : "";
  return "#/" + parts.map(enc).join("/") + qs;
}
function go(parts, q) { location.hash = href(parts, q); }
function replaceHash(parts, q) { history.replaceState(null, "", href(parts, q)); }

let cleanup = [];
function onLeave(fn) { cleanup.push(fn); }

async function route() {
  cleanup.forEach((f) => f()); cleanup = [];
  const { parts, q } = parseHash();
  document.getElementById("logout").hidden = false;
  try {
    if (!LT.me) LT.me = await api("/api/me");
    const [a, b, c, d] = parts;
    if (a === "p" && b && c === "thread" && d) await viewThread(b, d, q);
    else if (a === "p" && b) await viewProject(b, c || "traces", q);
    else if (a === "t" && b) await viewTrace(b, q);
    else if (a === "compare" && b && c) await viewCompare(b, c, q);
    else await viewProjects(q);
  } catch (e) {
    if (e.message !== "auth") $app.replaceChildren(h("div", { class: "card empty" }, `Error: ${e.message}`));
  }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);

// keyboard shortcuts registered per view
let keyHandler = null;
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (keyHandler) keyHandler(e);
});
function onKeys(fn) { keyHandler = fn; onLeave(() => { keyHandler = null; }); }

// ---------- theme / logout / login ----------

const root = document.documentElement;
try { const t = localStorage.getItem("lt-theme"); if (t) root.dataset.theme = t; } catch {}
document.getElementById("theme").onclick = () => {
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("lt-theme", root.dataset.theme); } catch {}
};
document.getElementById("logout").onclick = async () => { await api("/api/logout", { method: "POST" }); LT.me = null; showLogin(); };

function showLogin() {
  document.getElementById("logout").hidden = true;
  setCrumbs();
  const err = h("div", { class: "err" });
  const pw = h("input", { type: "password", placeholder: "Password", autocomplete: "current-password", required: true });
  $app.replaceChildren(h("div", { class: "card login" },
    h("h1", {}, "Sign in"),
    h("form", { onsubmit: async (e) => {
      e.preventDefault(); err.textContent = "";
      try { await api("/api/login", { method: "POST", body: { password: pw.value } }); route(); }
      catch { err.textContent = "Wrong password"; }
    } }, pw, err, h("button", { type: "submit" }, "Sign in"))));
  pw.focus();
}

// ---------- small shared widgets ----------

function menu(label, items) {
  const pop = h("div", { class: "menu-pop", hidden: true }, items.map(([text, fn, cls]) =>
    h("button", { class: "menu-item " + (cls || ""), onclick: (e) => { e.stopPropagation(); pop.hidden = true; fn(); } }, text)));
  const btn = h("button", { class: "ghost", onclick: (e) => { e.stopPropagation(); pop.hidden = !pop.hidden; } }, label);
  const close = () => { pop.hidden = true; };
  document.addEventListener("click", close);
  onLeave(() => document.removeEventListener("click", close));
  return h("span", { class: "menu" }, btn, pop);
}

function statBlock(label, value, sub) {
  return h("div", { class: "statb" }, h("div", { class: "statb-l" }, label), h("div", { class: "statb-v" }, value || "—"), sub ? h("div", { class: "statb-s" }, sub) : null);
}
