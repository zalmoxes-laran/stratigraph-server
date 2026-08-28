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

import * as oidc from "./auth.js";

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
//: the node's answer to "how does a browser sign in here" (`/v1/auth-config`)
let authConfig = null;
//: the refresh token and the timer that uses it. In memory, like the access
//: token: a refresh token in web storage is a credential that outlives the tab.
let refreshToken = "";
let refreshTimer = 0;

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

/** Keep the session alive without asking again.
 *
 *  Dev tokens last minutes, and a console that logged you out mid-panel would
 *  teach people to keep the paste box open. Refreshed at 80% of the lifetime —
 *  early enough that a slow realm does not turn into an expired token, late
 *  enough that it is not a poll. */
function scheduleRefresh(seconds) {
  window.clearTimeout(refreshTimer);
  if (!refreshToken || !seconds) return;
  const wait = Math.max(15, Math.floor(seconds * 0.8)) * 1000;
  refreshTimer = window.setTimeout(async () => {
    const result = await oidc.refresh(authConfig, refreshToken);
    if (!result.ok) {
      // Said, not swallowed: the next call would 401 and the page would look
      // broken for a reason that has nothing to do with the node.
      say(`Your session could not be refreshed (${result.error}). Sign in again.`,
          "bad");
      return;
    }
    token = result.token;
    refreshToken = result.refresh_token || refreshToken;
    scheduleRefresh(result.expires_in);
  }, wait);
}

/** Take the token from a completed sign-in. */
function adoptSession(result) {
  token = result.token;
  refreshToken = result.refresh_token || "";
  scheduleRefresh(result.expires_in);
}

async function boot() {
  authConfig = await oidc.loadConfig(BASE).catch(() => null);

  // Coming BACK from the IdP is the first thing to check: the page is loading
  // with `?code=…` on it and there is nothing else to do until that is spent.
  if (authConfig && oidc.returningFromIdp()) {
    const result = await oidc.completeSignIn(authConfig);
    if (result.ok) adoptSession(result);
    else say(`Sign-in did not complete: ${result.error}`, "bad");
  }

  let me = await whoami();
  if (me === undefined) return;                 // the API itself did not answer
  if (!me) {
    // ASK THE REALM, not the person. The console has no login of its own — it
    // sends you to the node's own IdP and comes back with the same kind of token
    // everybody else carries. The paste box stays underneath it, for a dev stack
    // and for the day the realm is the thing that is broken.
    showSignIn();
    return;
  }
  await enter(me);
}

/**
 * Signed in — now, may you administer this node?
 *
 * The two questions stay separate, and that separation is the whole point of
 * signing in this way. WHO you are is the realm's answer; WHETHER you are an
 * operator is `/v1/admin/whoami`'s, decided server-side from a realm role or an
 * ORCID allow-list. A console that took the second question on itself would be a
 * console you could talk out of it with a devtools console.
 */
async function enter(me) {
  whoEl.textContent = me.operator
    ? `operator${me.orcid ? " · " + me.orcid : " · dev mode"}`
    : (me.orcid || "signed in");
  if (!me.operator) {
    panel.innerHTML = "";
    // The refusal a person can act on: the capability is not something they can
    // give themselves, so the page says who can. Note what this is NOT: a
    // failure. The sign-in worked; this identity simply does not run the node.
    say(`You are signed in${me.orcid ? " as " + me.orcid : ""} but you are not an `
        + `operator of this node. Capability: ${me.capability}. Owning a room does `
        + `not grant it — ask whoever runs this node.`, "bad");
    if (authConfig?.end_session_endpoint) {
      const out = document.createElement("button");
      out.className = "ghost";
      out.textContent = "Sign out";
      out.addEventListener("click", () => {
        token = "";
        refreshToken = "";
        window.clearTimeout(refreshTimer);
        // …of the REALM too, or the next Sign in walks straight back in on the
        // IdP's cookie — which is not what somebody signing out meant.
        window.location.assign(oidc.signOutUrl(authConfig));
      });
      panel.appendChild(out);
    }
    return;
  }
  drawNav();
  if (MODULES.length) await show(MODULES[0]);
}

/** The signed-out screen: one button, and the fallback under it. */
function showSignIn() {
  whoEl.textContent = "not signed in";
  panel.innerHTML = "";
  const box = document.createElement("section");
  box.className = "card";
  const head = document.createElement("h2");
  head.textContent = "Sign in";
  box.appendChild(head);

  if (authConfig?.enforcing && authConfig.authorization_endpoint) {
    const line = document.createElement("p");
    line.className = "muted";
    line.textContent = `This node authenticates against ${authConfig.issuer}. `
      + `You will come back here signed in — the token stays in this tab and is `
      + `never stored.`;
    const button = document.createElement("button");
    button.textContent = "Sign in with the node's realm";
    button.addEventListener("click", () => void oidc.signIn(authConfig));
    box.append(line, button);
  } else {
    const line = document.createElement("p");
    line.className = "muted";
    // A "Sign in" that cannot work is worse than none: say which of the two
    // reasons it is, because they have different fixes.
    line.textContent = authConfig
      ? "This node enforces no authentication (dev-no-auth), so there is nothing "
        + "to sign in to — reload, or use a token below if the node is behind "
        + "something that does."
      : "This node did not answer /v1/auth-config, so this console cannot tell "
        + "where its realm is. Use a token below.";
    box.appendChild(line);
  }

  const advanced = document.createElement("details");
  advanced.className = "small";
  const summary = document.createElement("summary");
  summary.textContent = "Paste a token instead (dev, or when the realm is down)";
  const paste = document.createElement("button");
  paste.className = "ghost";
  paste.textContent = "Paste a bearer token…";
  paste.addEventListener("click", () => void askForToken());
  advanced.append(summary, paste);
  box.appendChild(advanced);
  panel.appendChild(box);
}

/** The old way, kept: one dialog, memory only, and a re-check of `whoami`. */
async function askForToken() {
  dialog.showModal();
  await new Promise((resolve) =>
    dialog.addEventListener("close", resolve, { once: true }));
  token = document.getElementById("token-input").value.trim();
  document.getElementById("token-input").value = "";
  if (!token) return;
  const me = await whoami();
  if (!me) {
    say("That token was not accepted by this node.", "bad");
    return;
  }
  await enter(me);
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
