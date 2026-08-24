/**
 * MODULE 3 · Node Health — is this node well, and what is it holding?
 *
 * The most useful panel to open first, and the one that shows the PATTERN most
 * plainly. A module is three things and nothing else:
 *
 *   1. it registers itself in the nav (`register({ id, title, render })`);
 *   2. it fetches ONE operator-scoped endpoint (`/admin/<name>`);
 *   3. it renders read-mostly.
 *
 * The shell does not change to admit it. That is the whole extension story, and
 * the four seams at the bottom of `boot.js` are the next four panels waiting for
 * their endpoint.
 *
 * Read-only: there is no action here. A "flush the IIIF cache" button would be
 * gated and confirmed like the archive in Storage, and it is not needed yet — a
 * console that grows actions before somebody asks grows the wrong ones.
 */
import { api, escapeHtml, register, say } from "../console.js";

/** The four states, and what each one means to somebody looking at it. Colour is
 *  carried by a class so the palette stays in `console.css` (one theme, one
 *  place — a module that wrote a hex value would be a second palette). */
const STATE = {
  ok: { tag: "good", say: "answered" },
  degraded: { tag: "warn", say: "answered, but not well" },
  unreachable: { tag: "bad", say: "did not answer" },
  "not configured": { tag: "muted", say: "nobody asked for it here" },
};

async function render(root) {
  const report = await api.get("/admin/health");
  root.innerHTML = "";

  const verdict = STATE[report.verdict] ?? STATE.degraded;
  const head = document.createElement("div");
  head.className = "head";
  head.innerHTML = `<h1>Node Health
      <span class="tag ${verdict.tag}">${escapeHtml(report.verdict)}</span></h1>
    <p class="muted">
      Every probe ran under a wall clock of <b>${report.deadline_s}s</b> —
      «unreachable» means it did not answer in that time, which is a fact and not
      a guess. A service that is <em>not configured</em> is not a failure.
    </p>`;
  root.appendChild(head);

  const table = document.createElement("table");
  table.className = "wide";
  table.innerHTML = `<thead><tr>
      <th>service</th><th>state</th><th class="num">latency</th>
      <th>where</th><th>what it said</th>
    </tr></thead><tbody></tbody>`;
  const body = table.querySelector("tbody");

  for (const check of report.checks) {
    const meta = STATE[check.state] ?? STATE.degraded;
    const row = document.createElement("tr");
    row.className = check.state === "unreachable" ? "warn-row" : "";
    row.innerHTML = `
      <td><b>${escapeHtml(check.name)}</b></td>
      <td><span class="tag ${meta.tag}">${escapeHtml(check.state)}</span></td>
      <td class="num">${check.latency_ms == null
        ? "—" : escapeHtml(check.latency_ms) + " ms"}</td>
      <td class="mono small">${escapeHtml(check.target ?? "—")}</td>
      <td>${escapeHtml(check.detail)}</td>`;
    body.appendChild(row);
    const facts = factsRow(check);
    if (facts) body.appendChild(facts);
  }
  root.appendChild(table);
  root.appendChild(versions(report.versions));
}

/** What a probe learned, when it learned anything worth a line: a bucket's size,
 *  the number of signing keys a realm published. Never a secret — the endpoint
 *  does not send one. */
function factsRow(check) {
  const facts = check.facts || {};
  const keys = Object.keys(facts);
  if (!keys.length) return null;
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = 5;
  cell.className = "muted small";
  cell.innerHTML = keys.map((key) => {
    let value = facts[key];
    if (key === "bytes") value = human(value);
    if (key === "truncated" && value) {
      // The cap is SAID. A count that stopped at the cap and did not say so
      // would be a wrong number wearing a right one's clothes.
      return `<b>counted the first ${escapeHtml(facts.count_cap)} objects only</b>`;
    }
    if (key === "truncated" || key === "count_cap") return "";
    return `${escapeHtml(key)}: <b>${escapeHtml(String(value))}</b>`;
  }).filter(Boolean).join(" · ");
  row.appendChild(cell);
  return row;
}

function human(bytes) {
  const n = Number(bytes) || 0;
  const units = ["B", "kB", "MB", "GB", "TB"];
  let i = 0;
  let value = n;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value < 10 && i ? value.toFixed(1) : Math.round(value)} ${units[i]}`;
}

/** The versions, in one place — the anteroom of the drift panel. Showing them
 *  here is not the drift check: that one compares this node's numbers with what
 *  the consumers speak, and it needs the consumers. This is the half that is
 *  already knowable. */
function versions(versions) {
  const box = document.createElement("section");
  box.className = "card";
  const rows = Object.entries(versions || {})
    .map(([key, value]) => `<tr><td>${escapeHtml(key.replace(/_/g, " "))}</td>
      <td class="mono">${escapeHtml(value ?? "—")}</td></tr>`).join("");
  box.innerHTML = `<h2>Versions in view</h2>
    <p class="muted">What this node speaks. A future <em>drift</em> panel compares
      these with what its consumers speak (EMStudio, EMtools, the chatbot) —
      these are the ones already knowable from here.</p>
    <table>${rows}</table>`;
  return box;
}

register({ id: "health", title: "Node Health", render });
