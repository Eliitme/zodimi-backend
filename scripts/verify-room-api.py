"""The room endpoints end to end, against the LOCAL stack.

    uv run python scripts/verify-room-api.py

Needs Supabase local and `uvicorn` already running, which is why this is a
script rather than a pytest: the unit suite stubs PostgREST, and a stub is
exactly where an assumption about PostgREST goes unnoticed. `.is_()` on a
null column, `upsert(on_conflict=...)`, and the shape an RPC returns are all
things only the real thing can confirm.

Creates three throwaway anonymous users and leaves their rows behind; local
stacks are disposable, and `supabase db reset` clears them.
"""
import json, urllib.request

API = "http://127.0.0.1:8000"
SB = "http://127.0.0.1:54321"
ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0"

def call(url, method="GET", token=None, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items(): req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")

def guest():
    req = urllib.request.Request(f"{SB}/auth/v1/signup", data=b"{}", method="POST")
    req.add_header("apikey", ANON); req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        d = json.loads(r.read())
    return d["access_token"], d["user"]["id"]

def set_birth(token, uid, date, time_, unknown=False):
    body = {"user_id": uid, "birth_date": date, "birth_time": time_,
            "birth_time_unknown": unknown, "lat": 21.03, "lon": 105.85,
            "tz_of_birth": "Asia/Ho_Chi_Minh"}
    req = urllib.request.Request(f"{SB}/rest/v1/profiles", data=json.dumps(body).encode(), method="POST")
    for k, v in {"apikey": ANON, "Authorization": f"Bearer {token}",
                 "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req) as r: r.read()

ok = fail = 0
def check(label, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"  ok: {label}")
    else: fail += 1; print(f"  FAIL: {label} {extra}")

ta, ua = guest(); tb, ub = guest(); tc, uc = guest()
set_birth(ta, ua, "1988-01-02", "10:00:00")
set_birth(tb, ub, "1990-04-12", "08:30:00")
set_birth(tc, uc, "1995-06-06", None, unknown=True)

s, room = call(f"{API}/v1/rooms", "POST", ta, {"topic": "friendship", "client_request_id": "11111111-1111-4111-8111-111111111111"})
check("A creates a room", s == 201 and room["status"] == "pending", (s, room))
s, again = call(f"{API}/v1/rooms", "POST", ta, {"topic": "friendship", "client_request_id": "11111111-1111-4111-8111-111111111111"})
check("the same request id returns the same room", again["id"] == room["id"], (s, again))

rid = room["id"]
s, inv = call(f"{API}/v1/rooms/{rid}/invitations", "POST", ta, {})
check("A gets an invite link", s == 201 and "/m/" in inv["invite_url"], (s, inv))
token = inv["invite_url"].rsplit("/", 1)[-1]
check("the link carries no ids", rid not in inv["invite_url"] and ua not in inv["invite_url"])

s, prev = call(f"{API}/v1/invitations/{token}/preview", token=tb)
check("B previews before deciding", s == 200 and prev["status"] == "open", (s, prev))
check("the preview names nobody", ua not in json.dumps(prev) and rid not in json.dumps(prev), prev)

s, _ = call(f"{API}/v1/rooms/{rid}", token=tb)
check("B cannot see the room before joining", s == 404, s)

s, body = call(f"{API}/v1/invitations/{token}/accept", "POST", ta, {"consent": True})
check("A cannot accept their own invitation", s == 409, (s, body))

s, body = call(f"{API}/v1/invitations/{token}/accept", "POST", tb, {"consent": False})
check("a refusal is refused", s == 422, (s, body))

s, body = call(f"{API}/v1/invitations/{token}/accept", "POST", tb, {"consent": True})
check("B joins", s == 200 and body["status"] == "joined", (s, body))

s, body = call(f"{API}/v1/invitations/{token}/accept", "POST", tb, {"consent": True})
check("B retrying is not a second claim", s == 200, (s, body))

s, body = call(f"{API}/v1/invitations/{token}/accept", "POST", tc, {"consent": True})
check("a third person is turned away", s == 409, (s, body))

s, res = call(f"{API}/v1/rooms/{rid}/preview", "POST", ta, {}, {"Accept-Language": "vi"})
check("the reading computes", s == 200 and len(res["free_insights"]) == 3, (s, res))
check("three categories in order",
      [i["key"] for i in res["free_insights"]] == ["attraction", "communication", "differences"])
check("evidence is real aspects",
      any(i["evidence"] for i in res["free_insights"]), res["free_insights"])
check("no birth data in the reading",
      not any(x in json.dumps(res) for x in ("1988-01-02", "10:00:00", "105.85")), res)
check("premium stays locked", res["premium_report"]["status"] == "locked")

s, again = call(f"{API}/v1/rooms/{rid}/result", token=tb, headers={"Accept-Language": "vi"})
check("B reads the same reading", s == 200 and again["pair_revision"] == res["pair_revision"], (s, again))

s, _ = call(f"{API}/v1/rooms/{rid}/result", token=tc)
check("a stranger gets nothing", s == 404, s)

s, _ = call(f"{API}/v1/rooms/{rid}/members/me", "DELETE", tb)
check("B withdraws", s == 200, s)
s, _ = call(f"{API}/v1/rooms/{rid}/result", token=ta, headers={"Accept-Language": "vi"})
check("and A loses the reading too", s == 403, s)

print(f"\npassed: {ok}, failed: {fail}")
raise SystemExit(1 if fail else 0)
