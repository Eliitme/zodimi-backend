// Auth + fetch wrapper for the console. Same-origin with the API, so no
// CORS setup is needed; Supabase auth is called directly (it allows any
// origin) to obtain the same JWT the iOS app uses.

const SESSION_KEY = "stellune-console-session";

export const state = {
  supabaseUrl: null,
  anonKey: null,
  accessToken: null,
  userId: null,
  onLog: () => {},
};

export async function bootstrap() {
  const config = await (await fetch("/v1/dev/config")).json();
  state.supabaseUrl = config.supabase_url;
  state.anonKey = config.supabase_anon_key;

  const saved = localStorage.getItem(SESSION_KEY);
  if (saved) {
    const session = JSON.parse(saved);
    state.accessToken = session.access_token;
    state.userId = session.user_id;
  }
}

export async function signInAsGuest() {
  const response = await fetch(`${state.supabaseUrl}/auth/v1/signup`, {
    method: "POST",
    headers: { apikey: state.anonKey, "Content-Type": "application/json" },
    body: "{}",
  });
  const data = await response.json();
  if (!data.access_token) throw new Error(data.msg || "anonymous sign-in failed");
  state.accessToken = data.access_token;
  state.userId = data.user.id;
  localStorage.setItem(
    SESSION_KEY,
    JSON.stringify({ access_token: data.access_token, user_id: data.user.id }),
  );
  state.onLog({ method: "POST", path: "/auth/v1/signup", status: 200, body: { user_id: state.userId } });
  return data.user.id;
}

export function signOut() {
  state.accessToken = null;
  state.userId = null;
  localStorage.removeItem(SESSION_KEY);
}

export function isSignedIn() {
  return Boolean(state.accessToken);
}

/** JSON request against the backend. Never throws on HTTP errors — the
 *  console needs to SHOW 403/429 responses, they are the interesting ones. */
export async function api(method, path, body) {
  const started = performance.now();
  const headers = { "Content-Type": "application/json", "Accept-Language": "vi" };
  if (state.accessToken) headers.Authorization = `Bearer ${state.accessToken}`;

  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let parsed = text;
  try {
    parsed = JSON.parse(text);
  } catch {
    /* keep raw text */
  }
  state.onLog({
    method,
    path,
    status: response.status,
    ms: Math.round(performance.now() - started),
    body: parsed,
  });
  return { ok: response.ok, status: response.status, data: parsed };
}

/** Consumes an SSE endpoint, rejoining multi-line events the way the iOS
 *  client does (the backend splits chunks containing newlines). */
export async function streamSSE(path, body, onEvent) {
  const started = performance.now();
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept-Language": "vi",
      Authorization: `Bearer ${state.accessToken}`,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    state.onLog({ method: "POST", path, status: response.status, body: text });
    return { ok: false, status: response.status };
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let events = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const payload = raw
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.replace(/^data: ?/, ""))
        .join("\n");
      if (payload === "[DONE]") continue;
      events += 1;
      onEvent(payload);
    }
  }

  state.onLog({
    method: "POST",
    path,
    status: response.status,
    ms: Math.round(performance.now() - started),
    body: `${events} SSE event(s)`,
  });
  return { ok: true, status: response.status };
}
