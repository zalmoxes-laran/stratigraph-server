/**
 * SIGNING IN — Authorization Code + PKCE against this node's own realm.
 *
 * The console used to ask for a pasted bearer token. Fine for a dev stack, wrong
 * for a service: whoever administers a node should sign in the way everybody
 * else does — **the same IdP, the same token, a different surface** — and a
 * console with a login of its own would be a second thing to keep correct.
 *
 * ## What this file is careful about
 *
 * * **No secret.** The browser authenticates as a PUBLIC client (`em-console`),
 *   and what replaces the secret is PKCE: a random verifier stays in this tab,
 *   only its SHA-256 goes to the IdP, and the code that comes back is worthless
 *   to anyone who does not hold the verifier. A confidential client whose secret
 *   ships inside a page is a confidential client in name only, which is why the
 *   node's own `em-server` client is NOT the one used here.
 * * **The token lives in memory.** Same rule the paste box always had. The
 *   VERIFIER, though, has to survive the redirect to the IdP and back — the page
 *   is unloaded in between — so it goes in `sessionStorage`, which dies with the
 *   tab, and it is deleted the moment the code is exchanged. A verifier left
 *   behind is a spare key on the doormat.
 * * **The capability does not move here.** Signing in says who you are; whether
 *   you may administer this node is still `/v1/admin/whoami`'s answer, decided
 *   server-side from a realm role or an ORCID allow-list. A console that decided
 *   that for itself would be a console you could talk out of it.
 * * **The redirect is COMPUTED, not configured.** The page is served at `/admin/`
 *   bare and at `/em/admin/` behind a proxy, so the redirect it sends is the URL
 *   it is actually on. The realm must list it; when it does not, the IdP says so
 *   at the last step and this file surfaces that sentence rather than a code.
 */

const VERIFIER_KEY = "em-console.pkce";
const RETURN_KEY = "em-console.return";

/** The page's own base, up to and including `/admin/` — which is the redirect
 *  the realm has to know. Same shape as the API base the shell derives. */
export function redirectUri() {
  const path = window.location.pathname.replace(/\/admin(\/.*)?$/, "/admin/");
  return `${window.location.origin}${path}`;
}

/** `?code=…` is on the URL, i.e. the IdP just sent the browser back here. */
export function returningFromIdp() {
  const query = new URLSearchParams(window.location.search);
  return query.has("code") || query.has("error");
}

/** How this node wants a browser to sign in. Public by construction. */
export async function loadConfig(base) {
  const answer = await fetch(`${base}/auth-config`, {
    headers: { Accept: "application/json" },
  });
  if (!answer.ok) return null;
  return await answer.json();
}

// ── PKCE ────────────────────────────────────────────────────────────────────

function randomVerifier() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64url(bytes);
}

async function challengeFor(verifier) {
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

function base64url(bytes) {
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Leave for the IdP. Does not return — the browser navigates away. */
export async function signIn(config) {
  const verifier = randomVerifier();
  const challenge = await challengeFor(verifier);
  const state = randomVerifier();
  sessionStorage.setItem(VERIFIER_KEY, JSON.stringify({ verifier, state }));
  // …and where we were, so a person who signed in from a panel comes back to it
  sessionStorage.setItem(RETURN_KEY, window.location.hash || "");
  const url = new URL(config.authorization_endpoint);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", config.client_id);
  url.searchParams.set("redirect_uri", redirectUri());
  url.searchParams.set("scope", config.scope || "openid");
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("state", state);
  window.location.assign(url.toString());
}

/**
 * Come back from the IdP: `?code=…` → tokens.
 *
 * Returns `{ok: true, token, expires_in, refresh_token}` or
 * `{ok: false, error}` with the IdP's own words. The query string is cleaned off
 * the URL either way — an authorization code in the address bar is a code in the
 * browser history, and it stays there long after it stops being useful.
 */
export async function completeSignIn(config) {
  const query = new URLSearchParams(window.location.search);
  const code = query.get("code");
  const error = query.get("error");
  const returned = query.get("state");
  const saved = readSaved();
  cleanUrl();
  if (error) {
    return { ok: false, error: query.get("error_description") || error };
  }
  if (!code) return { ok: false, error: "no authorization code came back" };
  if (!saved) {
    return { ok: false, error: "this tab has no sign-in in progress (the code "
                              + "came back to a page that did not start one)" };
  }
  if (saved.state && returned !== saved.state) {
    // The one check that makes the round trip mean anything: a code delivered
    // with somebody else's state is a code this tab did not ask for.
    return { ok: false, error: "the sign-in state did not match — refusing the "
                              + "code this tab did not ask for" };
  }
  return await exchange(config, {
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri(),
    code_verifier: saved.verifier,
  });
}

/** A refresh, before the access token expires. Same endpoint, no verifier. */
export async function refresh(config, refreshToken) {
  return await exchange(config, {
    grant_type: "refresh_token",
    refresh_token: refreshToken,
  });
}

async function exchange(config, fields) {
  const body = new URLSearchParams({ client_id: config.client_id, ...fields });
  let answer;
  try {
    answer = await fetch(config.token_endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
  } catch {
    // The realm is reachable from the BROWSER, not from the server, and the two
    // are different machines the moment there is a proxy. Saying which one
    // failed is the difference between a fixable problem and a mystery.
    return { ok: false, error: `cannot reach the realm at ${config.token_endpoint} `
                              + `from this browser` };
  }
  const payload = await answer.json().catch(() => null);
  if (!answer.ok) {
    return { ok: false,
             error: payload?.error_description || payload?.error
                    || `token endpoint answered ${answer.status}` };
  }
  return {
    ok: true,
    token: payload.access_token,
    refresh_token: payload.refresh_token || "",
    expires_in: Number(payload.expires_in || 0),
  };
}

function readSaved() {
  try {
    const raw = sessionStorage.getItem(VERIFIER_KEY);
    // consumed on read: a verifier that outlives its exchange is a spare key
    sessionStorage.removeItem(VERIFIER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/** Take `?code=…&state=…` off the address bar without reloading. */
function cleanUrl() {
  const hash = sessionStorage.getItem(RETURN_KEY) || "";
  sessionStorage.removeItem(RETURN_KEY);
  window.history.replaceState({}, "", window.location.pathname + hash);
}

/** Sign OUT of the realm as well as of this tab. Without the second half, the
 *  next "Sign in" walks straight back in on the IdP's cookie, which is not what
 *  a person pressing "sign out" on somebody else's machine meant. */
export function signOutUrl(config) {
  if (!config?.end_session_endpoint) return "";
  const url = new URL(config.end_session_endpoint);
  url.searchParams.set("client_id", config.client_id);
  url.searchParams.set("post_logout_redirect_uri", redirectUri());
  return url.toString();
}
