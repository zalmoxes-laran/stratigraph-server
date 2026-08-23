/**
 * THE SHELL. Everything specific to a panel lives in a module; this file only
 * knows how to (a) hold a token, (b) call the node, (c) show one module at a
 * time.
 *
 * Adding a module — the whole extension story, on purpose:
 *
 *     import { register } from "./console.js";
 *     register({ id: "corpus", title: "DTC corpus",
 *                async render(root, api) { … } });
 *
 * A module gets the panel element and an `api` that already carries the token
 * and the base path. It renders; it does not route, style the chrome, or reach
 * for the token. That is the seam the next five panels use (node health, corpus,
 * IIIF, Catalog, datamodel drift) without this file changing.
 */

const MODULES = [];
let active = null;

/** Register a module. Order of registration is the order in the nav. */
export function register(module) {
  MODULES.push(module);
  return module;
}

// ── the node's API, with the token attached once ────────────────────────────

/** Where the API is, derived from where this page is served from.
 *
 *  Everything from `/admin` onwards is cut, so all three of the URLs a browser
 *  actually ends up on resolve to the same API: `/admin`, `/admin/` and
 *  `/admin/index.html` — and behind a reverse proxy under a prefix (`/em/admin`)
 *  it becomes `/em/v1`, which is what the dev stack serves.
 *
 *  Measured, and it is why this is not a one-line strip of a trailing slash: at
 *  `/em/admin/index.html` the first version of this produced
 *  `/em/admin/index.html/v1` and every call 404'd — with nothing on screen but
 *  "Loading…". */
const BASE = window.location.pathname.replace(/\/admin(\/.*)?$/, "") + "/v1";

let token = "";

export const api = {
  base: BASE,
  get hasToken() {
    return Boolean(token);
  },
  async get(path) {
    return request("GET", path);
  },
  async post(path, body) {
    return request("POST", path, body);
  },
  async put(path, body) {
    // The member route is a PUT — "state the role, idempotently" — so the shell
    // owns the verb. A module reaching for `fetch` to get one would need the
    // token, and the token is the one thing modules must not hold.
    return request("PUT", path, body);
  },
  async del(path) {
    return request("DELETE", path);
  },
};

async function request(method, path, body) {
  const headers = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const answer = await fetch(BASE + path, {
    method, headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await answer.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { detail: text };
  }
  if (!answer.ok) {
    // The node's own sentence, surfaced as-is. Every refusal in em-server says
    // what is missing and who can grant it; replacing that with "Error 403"
    // would throw away the only useful part.
    const error = new Error(payload?.detail || `${answer.status} ${method} ${path}`);
    error.status = answer.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

// ── chrome ─────────────────────────────────────────────────────────────────

const nav = document.getElementById("nav");
const panel = document.getElementById("panel");
const whoEl = document.getElementById("who");
const dialog = document.getElementById("ask-token");

export function html(strings, ...values) {
  // Small on purpose: escaping every interpolation is the one thing a console
  // that prints ORCIDs and room names out of a database must not get wrong.
  return strings.reduce((out, chunk, i) => {
    const value = values[i - 1];
    return out + escapeHtml(value === undefined || value === null ? "" : String(value)) + chunk;
  });
}

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function say(message, kind = "info") {
  const note = document.createElement("p");
  note.className = `note ${kind}`;
  note.textContent = message;
  panel.prepend(note);
  return note;
}

/** A deliberate action asks first, and asks with the NAME in it. */
export function confirmNamed(what, name) {
  return window.confirm(`${what}\n\n${name}\n\nThis is somebody's workspace. Continue?`);
}

function drawNav() {
  nav.innerHTML = "";
  for (const module of MODULES) {
    const button = document.createElement("button");
    button.className = "nav-item" + (active === module ? " on" : "");
    button.textContent = module.title;
    button.addEventListener("click", () => show(module));
    nav.appendChild(button);
  }
}

export async function show(module) {
  active = module;
  drawNav();
  panel.innerHTML = `<p class="muted">Loading ${escapeHtml(module.title)}…</p>`;
  try {
    await module.render(panel, api);
  } catch (error) {
    panel.innerHTML = "";
    say(error.message, "bad");
  }
  panel.focus();
}

// ── the session ────────────────────────────────────────────────────────────

async function whoami() {
  try {
    return await api.get("/admin/whoami");
  } catch (error) {
    if (error.status === 401) return null;      // no token, or not a valid one
    // ANYTHING else is said out loud rather than thrown into a module boundary
    // where nothing shows it. A 404 here means the API is not where this page
    // thinks it is, and "Loading…" for ever is the worst way to learn that.
    panel.innerHTML = "";
    say(`Cannot reach this node's API at ${BASE} — ${error.message}`, "bad");
    return undefined;                           // …and boot() stops, quietly
  }
}

async function boot() {
  let me = await whoami();
  if (me === undefined) return;                 // the API itself did not answer
  if (!me) {
    // Ask, once. `authenticator` is the node's, and this console has no login of
    // its own on purpose: a second way to authenticate would be a second thing
    // to get wrong (and the realm already has one).
    dialog.showModal();
    await new Promise((resolve) => dialog.addEventListener("close", resolve, { once: true }));
    token = document.getElementById("token-input").value.trim();
    me = token ? await whoami() : null;
  }
  if (!me) {
    whoEl.textContent = "not signed in";
    panel.innerHTML = "";
    say("This console needs a token from the node's realm. Reload to try again.",
        "bad");
    return;
  }
  whoEl.textContent = me.operator
    ? `operator${me.orcid ? " · " + me.orcid : " · dev mode"}`
    : (me.orcid || "signed in");
  if (!me.operator) {
    panel.innerHTML = "";
    // The refusal a person can act on: the capability is not something they can
    // give themselves, so the page says who can.
    say(`You are signed in${me.orcid ? " as " + me.orcid : ""} but you are not an `
        + `operator of this node. Capability: ${me.capability}. Owning a room does `
        + `not grant it — ask whoever runs this node.`, "bad");
    return;
  }
  drawNav();
  if (MODULES.length) await show(MODULES[0]);
}

document.getElementById("reload").addEventListener("click", () => {
  if (active) show(active);
});

/**
 * Start. Called by `boot.js` AFTER the modules have registered themselves.
 *
 * This file deliberately does not import them, and the reason is a bug this
 * console had for exactly one measurement: the shell imported the modules with a
 * top-level `await import(...)`, each module imports `register` from the shell,
 * and a circular import where one side is blocked on a top-level await **never
 * resolves**. The page loaded, said "Loading…", and printed no error — nothing
 * ran at all. Now the dependency points one way (module → shell) and a third file
 * does the ordering.
 */
export { boot };
