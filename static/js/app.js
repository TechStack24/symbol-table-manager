"use strict";

/* ===================================================================
   Helpers
   =================================================================== */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const LINE_HEIGHT = 20;

// Build DOM nodes without innerHTML so program text can never inject markup.
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") el.className = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : value);
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child);
  }
  return el;
}

async function api(path, body) {
  const options = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, options);
  if (res.status === 401) { window.location.href = "/login"; throw new Error("Please log in again."); }
  let data = {};
  try { data = await res.json(); } catch (_) { /* non-JSON error body */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function toast(message) {
  const el = h("div", { class: "toast", role: "alert" }, message);
  document.body.append(el);
  setTimeout(() => el.remove(), 3500);
}

const badge = (text, cls) => h("span", { class: `badge ${cls || text}` }, text);
const yesNo = (value) => h("span", { class: value ? "yes" : "no" }, value ? "yes" : "no");

/* ===================================================================
   Tabs
   =================================================================== */
$$(".tab-btn").forEach((btn) => btn.addEventListener("click", () => {
  $$(".tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${btn.dataset.tab}`));
}));

$$(".sub-btn").forEach((btn) => btn.addEventListener("click", () => {
  $$(".sub-btn").forEach((b) => b.classList.toggle("active", b === btn));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${btn.dataset.sub}`));
}));

/* ===================================================================
   Shared renderers
   =================================================================== */
function symbolRow(sym) {
  return h("tr", null,
    h("td", null, h("span", { class: "mono" }, sym.name), sym.builtin ? h("span", { class: "builtin-tag" }, "built-in") : null),
    h("td", null, badge(sym.kind)),
    h("td", null, h("code", null, sym.signature)),
    h("td", { class: "num" }, sym.builtin ? "-" : sym.line),
    h("td", { class: "num" }, sym.kind === "function" ? "-" : sym.size),
    h("td", { class: "num" }, sym.offset === null ? "-" : sym.offset),
    h("td", null, sym.kind === "function" ? h("span", { class: sym.defined ? "yes" : "no" }, sym.defined ? "defined" : "prototype") : yesNo(sym.initialized)),
    h("td", null, yesNo(sym.used)),
    h("td", { class: "mono" }, sym.refs.length ? sym.refs.join(", ") : "-"),
  );
}

function scopeCard(scope, showBuiltins, lineLabel) {
  const symbols = scope.symbols.filter((s) => showBuiltins || !s.builtin);
  const head = h("header", null,
    h("h3", null, scope.name),
    h("span", { class: "chip" }, scope.kind),
    h("span", { class: "chip" }, `level ${scope.level}`),
    h("span", { class: "chip" }, `scope #${scope.id}`),
    h("span", { class: "chip" }, `${symbols.length} symbol${symbols.length === 1 ? "" : "s"}`),
    h("span", { class: "chip" }, `${scope.storage_bytes} bytes`),
    scope.opened_line ? h("span", { class: "chip" }, `${lineLabel} ${scope.opened_line}`) : null,
  );
  const body = symbols.length
    ? h("div", { class: "tbl-wrap" }, h("table", null,
        h("thead", null, h("tr", null,
          ["Name", "Kind", "Declaration", lineLabel, "Size", "Offset", "Init", "Used", "References"].map((t, i) =>
            h("th", { class: i >= 3 && i <= 5 ? "num" : "" }, t)))),
        h("tbody", null, symbols.map(symbolRow))))
    : h("p", { class: "empty", style: "padding:0 12px" }, "No symbols declared in this scope.");
  const card = h("section", { class: "scope" }, head, body);
  card.style.marginLeft = `${Math.min(scope.level, 6) * 14}px`;
  return card;
}

function renderScopes(container, scopes, showBuiltins, lineLabel = "Line") {
  container.replaceChildren(...scopes.map((s) => scopeCard(s, showBuiltins, lineLabel)));
}

function renderBuckets(container, scopes, showBuiltins) {
  const blocks = scopes.map((scope) => {
    const st = scope.stats;
    const grid = h("div", { class: "bucket-grid" }, scope.buckets.map((b) => {
      const chain = b.chain.filter((n) => showBuiltins || !n.builtin);
      const nodes = [];
      chain.forEach((node, i) => {
        if (i) nodes.push(h("span", { class: "arrow" }, "→"));
        nodes.push(h("span", { class: "node" }, node.name));
      });
      const cls = ["bucket", chain.length ? "used" : "", chain.length > 1 ? "collision" : ""].join(" ");
      return h("div", { class: cls }, h("span", { class: "idx" }, `[${b.index}]`), nodes);
    }));
    return h("section", { class: "scope" },
      h("header", null, h("h3", null, scope.name), h("span", { class: "chip" }, `level ${scope.level}`)),
      h("div", { style: "padding:0 12px 12px" },
        h("p", { class: "stat-line" },
          `capacity ${st.capacity} · entries ${st.count} · load ${st.load_factor} · used buckets ${st.used_buckets} · ` +
          `collisions ${st.collisions} · longest chain ${st.longest_chain} · resizes ${st.resizes}`),
        grid));
  });
  container.replaceChildren(...blocks);
}

function renderLog(container, report) {
  if (!report.log.length) {
    container.replaceChildren(h("p", { class: "empty" }, "No operations recorded."));
    return;
  }
  const rows = report.log.map((e) => h("tr", null,
    h("td", { class: "num" }, e.step),
    h("td", null, badge(e.op, "variable")),
    h("td", { class: "mono" }, e.name),
    h("td", { class: "mono" }, e.scope),
    h("td", null, e.ok ? badge("ok", "pass") : badge("fail", "fail")),
    h("td", null, e.detail),
    h("td", { class: "num" }, e.line ?? "-"),
  ));
  const note = report.log_truncated ? h("p", { class: "empty" }, "Log truncated (too many operations).") : null;
  container.replaceChildren(
    h("div", { class: "tbl-wrap" }, h("table", null,
      h("thead", null, h("tr", null, ["#", "Op", "Name", "Scope", "Result", "Detail", "Line"].map((t) => h("th", null, t)))),
      h("tbody", null, rows))),
    note);
}

/* ===================================================================
   Analyzer tab
   =================================================================== */
const sourceEl = $("#source");
const gutterEl = $("#gutter");
let lastReport = null;
let lineMarks = new Map();

function renderGutter() {
  const total = Math.max(1, sourceEl.value.split("\n").length);
  const nodes = [];
  for (let n = 1; n <= total; n++) {
    nodes.push(h("div", { class: lineMarks.get(n) || "" }, String(n)));
  }
  gutterEl.replaceChildren(...nodes);
  gutterEl.scrollTop = sourceEl.scrollTop;
}

function updateCursor() {
  const before = sourceEl.value.slice(0, sourceEl.selectionStart).split("\n");
  $("#cursor-pos").textContent = `Ln ${before.length}, Col ${before[before.length - 1].length + 1}`;
}

sourceEl.addEventListener("input", () => { lineMarks = new Map(); renderGutter(); updateCursor(); });
sourceEl.addEventListener("scroll", () => { gutterEl.scrollTop = sourceEl.scrollTop; });
["keyup", "click"].forEach((ev) => sourceEl.addEventListener(ev, updateCursor));
sourceEl.addEventListener("keydown", (e) => {
  if (e.key === "Tab") {
    e.preventDefault();
    sourceEl.setRangeText("    ", sourceEl.selectionStart, sourceEl.selectionEnd, "end");
    sourceEl.dispatchEvent(new Event("input"));
  } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    analyze();
  }
});

function jumpToLine(line, col) {
  const lines = sourceEl.value.split("\n");
  let start = 0;
  for (let i = 0; i < line - 1 && i < lines.length; i++) start += lines[i].length + 1;
  const length = (lines[line - 1] || "").length;
  sourceEl.focus();
  sourceEl.setSelectionRange(start, start + length);
  sourceEl.scrollTop = Math.max(0, (line - 4) * LINE_HEIGHT);
  gutterEl.scrollTop = sourceEl.scrollTop;
  updateCursor();
}

function renderSummary(report) {
  const s = report.summary;
  const card = (value, label, cls) => h("div", { class: `card ${cls || ""}` }, h("b", null, value), h("span", null, label));
  $("#summary").replaceChildren(
    card(s.symbols, "symbols"),
    card(s.scopes, "scopes"),
    card(s.tokens, "tokens"),
    card(s.errors, "errors", s.errors ? "bad" : "good"),
    card(s.warnings, "warnings", s.warnings ? "warn" : ""),
    card(s.infos, "notes"),
  );
}

function renderDiagnostics(report) {
  const items = report.diagnostics;
  $("#diag-count").textContent = items.length ? String(items.length) : "";
  const container = $("#view-diagnostics");
  if (!items.length) {
    container.replaceChildren(h("p", { class: "empty" }, "No problems found. The program passed lexical, syntax and semantic checks."));
    return;
  }
  container.replaceChildren(...items.map((d) => {
    const go = () => { jumpToLine(d.line, d.col); };
    return h("div", { class: "diag", tabindex: "0", role: "button", onclick: go,
        onkeydown: (e) => { if (e.key === "Enter") go(); } },
      badge(d.severity),
      h("span", { class: "loc" }, `${d.line}:${d.col}`),
      h("span", null, d.message),
      h("span", { class: "phase" }, d.phase));
  }));
}

function renderTokens(report) {
  const container = $("#view-tokens");
  const rows = report.tokens.map((t) => h("tr", null,
    h("td", { class: "mono" }, `${t.line}:${t.col}`),
    h("td", null, badge(t.kind, t.kind === "IDENT" ? "variable" : t.kind === "KEYWORD" ? "function" : "array")),
    h("td", { class: "mono" }, t.value)));
  container.replaceChildren(
    h("div", { class: "tbl-wrap" }, h("table", null,
      h("thead", null, h("tr", null, ["Position", "Kind", "Lexeme"].map((t) => h("th", null, t)))),
      h("tbody", null, rows))),
    report.tokens_truncated ? h("p", { class: "empty" }, "Token list truncated.") : null);
}

function markGutter(report) {
  lineMarks = new Map();
  const rank = { info: 1, warning: 2, error: 3 };
  for (const d of report.diagnostics) {
    const current = lineMarks.get(d.line);
    const cls = d.severity === "error" ? "err" : d.severity;
    if (!current || rank[d.severity] > rank[current === "err" ? "error" : current]) lineMarks.set(d.line, cls);
  }
  renderGutter();
}

function renderAnalyzer() {
  if (!lastReport) return;
  const show = $("#show-builtins").checked;
  renderScopes($("#view-symbols"), lastReport.scopes, show);
  renderBuckets($("#view-buckets"), lastReport.scopes, show);
}

async function analyze() {
  const btn = $("#analyze-btn");
  btn.disabled = true;
  try {
    lastReport = await api("/api/analyze", { source: sourceEl.value });
    renderSummary(lastReport);
    renderDiagnostics(lastReport);
    renderTokens(lastReport);
    renderLog($("#view-log"), lastReport);
    renderAnalyzer();
    markGutter(lastReport);
    $("#export-csv").disabled = false;
    if (lastReport.summary.errors) {
      $$(".sub-btn").find((b) => b.dataset.sub === "diagnostics").click();
    }
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
}

function exportCsv() {
  if (!lastReport) return;
  const show = $("#show-builtins").checked;
  const header = ["scope", "level", "name", "kind", "type", "declaration", "line", "size", "offset", "initialized", "used", "references"];
  const quote = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [header.join(",")];
  for (const scope of lastReport.scopes) {
    for (const s of scope.symbols) {
      if (s.builtin && !show) continue;
      lines.push([scope.name, scope.level, s.name, s.kind, s.type, s.signature, s.line, s.size,
        s.offset, s.initialized, s.used, s.refs.join(" ")].map(quote).join(","));
    }
  }
  const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  const link = h("a", { href: url, download: "symbol_table.csv" });
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

$("#analyze-btn").addEventListener("click", analyze);
$("#export-csv").addEventListener("click", exportCsv);
$("#show-builtins").addEventListener("change", renderAnalyzer);
$("#clear-btn").addEventListener("click", () => {
  sourceEl.value = "";
  lineMarks = new Map();
  renderGutter();
  updateCursor();
  sourceEl.focus();
});

let samples = [];
$("#sample-select").addEventListener("change", (e) => {
  const sample = samples.find((s) => s.id === e.target.value);
  if (sample) {
    sourceEl.value = sample.code;
    lineMarks = new Map();
    renderGutter();
    updateCursor();
  }
});

async function loadSamples() {
  try {
    samples = await api("/api/samples");
  } catch (err) {
    toast(err.message);
    return;
  }
  const select = $("#sample-select");
  select.replaceChildren(...samples.map((s) => h("option", { value: s.id }, s.title)));
  if (samples.length) {
    sourceEl.value = samples[0].code;
    renderGutter();
    analyze();
  }
}

/* ===================================================================
   Playground tab
   =================================================================== */
const DEMO = [
  "insert counter int variable",
  "enter main",
  "insert x int variable",
  "insert x float variable",
  "insert buf char array 16",
  "enter inner",
  "insert x char variable",
  "lookup x",
  "lookup counter",
  "lookup missing",
  "exit",
  "lookup x",
  "exit",
  "lookup x",
  "delete counter",
  "lookup counter",
];

let history = [];

async function runPlayground() {
  try {
    const state = await api("/api/playground", { commands: history });
    renderPlayground(state);
  } catch (err) {
    toast(err.message);
  }
}

function renderPlayground(state) {
  const list = $("#history");
  if (!state.results.length) {
    list.replaceChildren(h("li", { class: "empty" }, "No commands yet."));
  } else {
    list.replaceChildren(...state.results.map((r) =>
      h("li", { class: r.ok ? "ok" : "fail" }, h("code", null, r.command), h("span", { class: "msg" }, r.message))));
    list.scrollTop = list.scrollHeight;
  }
  const active = state.active_scopes;
  const chips = [];
  active.forEach((name, i) => {
    if (i) chips.push(h("span", { class: "muted" }, "›"));
    chips.push(h("span", { class: `chip ${i === active.length - 1 ? "cur" : ""}` }, name));
  });
  $("#scope-stack").replaceChildren(...chips);
  // show the innermost scopes first so the effect of the last command is visible
  renderScopes($("#pg-tables"), state.scopes, true, "Cmd");
  renderLog($("#pg-log"), state);
}

$("#cmd-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#cmd-input");
  const text = input.value.trim();
  if (!text) return;
  history.push(text);
  input.value = "";
  runPlayground();
});
$("#demo-btn").addEventListener("click", () => { history = DEMO.slice(); runPlayground(); });
$("#undo-btn").addEventListener("click", () => { history.pop(); runPlayground(); });
$("#reset-btn").addEventListener("click", () => { history = []; runPlayground(); });

/* ===================================================================
   Boot
   =================================================================== */
renderGutter();
loadSamples();
runPlayground();
