/**
 * MY ROOMS — sign in, see your rooms, make one, hand one to a tool.
 *
 * The whole point is the last verb. Everything before it (a list, a create box)
 * exists so that "open in EMStudio" can be a click instead of three fields and a
 * pasted token.
 *
 * **It imports the node console's sign-in module rather than copying it**
 * (`../admin/auth.js`, relative so the `/em` prefix a proxy adds comes along).
 * One PKCE implementation for both faces of this server: two would drift, and the
 * one that drifted would be the one nobody tested.
 *
 * **What this page never holds:** a token on disk. The access token lives in a
 * module-scoped variable for this tab, refreshed at 80% of its lifetime, and the
 * links this page produces carry no credential at all — that is the handoff
 * contract, not a convention (`app/handoff.py`).
 */

import * as oidc from "../admin/auth.js";

/** The API, derived from where this page is served — the same reasoning (and the
 *  same measured trap) as the console's: at `/em/rooms/index.html` a naive strip
 *  produces `/em/rooms/index.html/v1` and every call 404s behind a "Loading…". */
const BASE = window.location.pathname.replace(/\/rooms(\/.*)?$/, "") + "/v1";

let token = "";
let refreshToken = "";
let refreshTimer = 0;
let authConfig = null;
let me = null;

const $ = (id) => document.getElementById(id);

// ── the node, with the token attached once ──────────────────────────────────

async function request(method, path, body) {
  const answer = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      Accept: "application/json",
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const text = await answer.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = null; }
  if (!answer.ok) {
    const error = new Error((payload && payload.detail) || `HTTP ${answer.status}`);
    error.status = answer.status;
    throw error;
  }
  return payload;
}

// ── session ─────────────────────────────────────────────────────────────────

function scheduleRefresh(seconds) {
  window.clearTimeout(refreshTimer);
  if (!refreshToken || !seconds) return;
  const wait = Math.max(15, Math.floor(seconds * 0.8)) * 1000;
  refreshTimer = window.setTimeout(async () => {
    const result = await oidc.refresh(authConfig, refreshToken);
    if (!result.ok) {
      note($("gate-note"), `Your session could not be refreshed (${result.error}).`,
           true);
      return;
    }
    token = result.token;
    refreshToken = result.refresh_token || refreshToken;
    scheduleRefresh(result.expires_in);
  }, wait);
}

function adoptSession(result) {
  token = result.token;
  refreshToken = result.refresh_token || "";
  scheduleRefresh(result.expires_in);
}

function note(element, text, bad = false) {
  if (!element) return;
  element.textContent = text || "";
  element.classList.toggle("warn", Boolean(bad));
}

// ── boot ────────────────────────────────────────────────────────────────────

async function boot() {
  authConfig = await oidc.loadConfig(BASE).catch(() => null);

  if (authConfig && oidc.returningFromIdp()) {
    const result = await oidc.completeSignIn(authConfig);
    if (result.ok) adoptSession(result);
    else note($("gate-note"), `Sign-in did not complete: ${result.error}`, true);
  }

  // A room listing needs only that you are SOMEBODY — no capability, unlike the
  // node console. So the question is simply whether the listing answers.
  let rooms = null;
  try {
    rooms = await request("GET", "/rooms");
  } catch (error) {
    if (error.status === 401 || error.status === 403) {
      showGate();
      return;
    }
    note($("gate-note"),
         `Cannot reach this node's API at ${BASE} — ${error.message}`, true);
    showGate();
    return;
  }
  await enter(rooms);
}

function showGate() {
  $("gate").classList.remove("hidden");
  $("workspace").classList.add("hidden");
  if (!authConfig) {
    note($("gate-note"),
         "This node has no OIDC configuration, so there is nobody to sign in as. "
         + "It is running open (dev mode) — reload if the listing is empty.", true);
  } else if (!authConfig.enforcing) {
    note($("gate-note"),
         "This node is in dev mode: every identity is an owner of every room.");
  }
}

async function enter(rooms) {
  $("gate").classList.add("hidden");
  $("workspace").classList.remove("hidden");
  try {
    me = await request("GET", "/whoami").catch(() => null);
  } catch { me = null; }
  $("who").textContent = (me && (me.orcid || me.name)) || "";
  $("btn-signout").classList.toggle("hidden", !authConfig || !token);
  render(rooms);
}

// ── the list ────────────────────────────────────────────────────────────────

function render(rooms) {
  const host = $("rooms");
  host.innerHTML = "";
  const live = (rooms || []).filter((r) => !r.archived_at);
  if (!live.length) {
    const empty = document.createElement("p");
    empty.className = "note";
    empty.textContent = "No rooms yet. Make one above — you will be its owner.";
    host.appendChild(empty);
    return;
  }
  for (const room of live) host.appendChild(card(room));
}

function card(room) {
  const box = document.createElement("div");
  box.className = "room";

  const head = document.createElement("div");
  head.className = "room-head";
  const title = document.createElement("span");
  title.className = "room-title";
  title.textContent = room.title || room.room_id;
  const id = document.createElement("span");
  id.className = "room-id";
  id.textContent = room.room_id;
  const role = document.createElement("span");
  role.className = "role";
  role.textContent = room.your_role || "—";
  head.append(title, id, role);
  box.appendChild(head);

  if ((room.missing_refs || []).length) {
    // REPORTED, never hidden: a workspace whose container was moved still
    // exists, and the honest answer is the name of what is missing.
    const missing = document.createElement("p");
    missing.className = "note warn";
    missing.textContent =
      `container not in the store: ${room.missing_refs.join(", ")}`;
    box.appendChild(missing);
  }

  const actions = document.createElement("div");
  actions.className = "room-actions";
  const said = document.createElement("span");
  said.className = "note";

  for (const [tool, label] of [["emstudio", "Open in EMStudio"],
                               ["chatbot", "Open in field assistant"]]) {
    const button = document.createElement("button");
    button.textContent = label;
    button.addEventListener("click", () => void openIn(room, tool, box, said));
    actions.appendChild(button);
  }
  const copy = document.createElement("button");
  copy.className = "ghost";
  copy.textContent = "Copy link";
  copy.addEventListener("click", () => void copyLink(room, said));
  actions.append(copy, said);
  box.appendChild(actions);
  return box;
}

// ── the handoff ─────────────────────────────────────────────────────────────

async function handoff(room) {
  // The SERVER builds the link, not this page. It knows its own public address
  // (configuration, never a request header — a link built from `Host` is a link
  // an attacker can aim), and one builder means the four consumers are measured
  // against one grammar.
  return await request("GET", `/rooms/${encodeURIComponent(room.room_id)}/open`);
}

async function openIn(room, tool, box, said) {
  let targets;
  try {
    targets = await handoff(room);
  } catch (error) {
    note(said, error.message, true);
    return;
  }
  showLink(box, targets);
  // Try the registered handler; the page it falls back to is the server's own
  // `/open`, which says what happened instead of doing nothing silently.
  let left = false;
  const onBlur = () => { left = true; };
  window.addEventListener("blur", onBlur, { once: true });
  window.location.href = targets.scheme;
  window.setTimeout(() => {
    window.removeEventListener("blur", onBlur);
    if (left) return;
    note(said,
         `Nothing opened — no handler for ${targets.scheme.split(":")[0]}:// on `
         + `this machine. Copy the link and open it inside the tool.`, true);
  }, 1800);
}

async function copyLink(room, said) {
  let targets;
  try {
    targets = await handoff(room);
  } catch (error) {
    note(said, error.message, true);
    return;
  }
  try {
    await navigator.clipboard.writeText(targets.web);
    note(said, "Link copied — it carries no token.");
  } catch {
    note(said, targets.web);
  }
}

function showLink(box, targets) {
  let row = box.querySelector(".link-row");
  if (!row) {
    row = document.createElement("div");
    row.className = "link-row";
    box.appendChild(row);
  }
  row.innerHTML = "";
  const code = document.createElement("code");
  code.textContent = targets.scheme;
  row.appendChild(code);
}

// ── create ──────────────────────────────────────────────────────────────────

async function createRoom() {
  const input = $("new-room-title");
  const title = (input.value || "").trim();
  if (!title) {
    note($("create-note"), "A room needs a name.", true);
    return;
  }
  // The id is derived from the name so a person never types two things that must
  // agree. Collisions are the SERVER's to refuse, and its sentence is shown.
  const room_id = title.toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "").slice(0, 60)
    || `room-${Date.now().toString(36)}`;
  try {
    await request("POST", "/rooms", { room_id, title });
  } catch (error) {
    note($("create-note"), error.message, true);
    return;
  }
  input.value = "";
  note($("create-note"), `Created — you are its owner.`);
  render(await request("GET", "/rooms"));
}

// ── wiring ──────────────────────────────────────────────────────────────────

$("btn-signin").addEventListener("click", async () => {
  if (!authConfig) {
    note($("gate-note"), "This node has no OIDC configuration.", true);
    return;
  }
  await oidc.signIn(authConfig);
});
$("btn-signout").addEventListener("click", () => {
  const url = authConfig ? oidc.signOutUrl(authConfig) : "";
  token = ""; refreshToken = ""; window.clearTimeout(refreshTimer);
  if (url) window.location.href = url;
  else window.location.reload();
});
$("btn-create").addEventListener("click", () => void createRoom());
$("new-room-title").addEventListener("keydown", (event) => {
  if (event.key === "Enter") void createRoom();
});

await boot();
