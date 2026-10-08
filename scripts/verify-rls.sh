#!/usr/bin/env bash
# Verify RLS + column privileges against the LOCAL Supabase stack.
# Run after every migration change: ./scripts/verify-rls.sh
set -euo pipefail

URL="${SUPABASE_URL:-http://127.0.0.1:54321}"
ANON="${SUPABASE_ANON_KEY:?export SUPABASE_ANON_KEY (from supabase status)}"

PASS=0; FAIL=0
check() { # check <desc> <expected> <actual>
  if [ "$2" = "$3" ]; then PASS=$((PASS+1)); echo "  ok: $1"
  else FAIL=$((FAIL+1)); echo "  FAIL: $1 (expected [$2] got [$3])"; fi
}

signup() { curl -s -X POST "$URL/auth/v1/signup" -H "apikey: $ANON" -H "Content-Type: application/json" -d '{}'; }
jsonval() { python3 -c "import sys,json;d=json.load(sys.stdin);print(eval(\"d$1\"))"; }

A=$(signup); B=$(signup)
TA=$(echo "$A" | jsonval "['access_token']"); UA=$(echo "$A" | jsonval "['user']['id']")
TB=$(echo "$B" | jsonval "['access_token']")

rest() { # rest <method> <path> <token> [json-body]
  local m=$1 p=$2 t=$3 b=${4:-}
  if [ -n "$b" ]; then
    curl -s -X "$m" "$URL/rest/v1/$p" -H "apikey: $ANON" -H "Authorization: Bearer $t" \
      -H "Content-Type: application/json" -H "Prefer: return=representation" -d "$b"
  else
    curl -s -X "$m" "$URL/rest/v1/$p" -H "apikey: $ANON" -H "Authorization: Bearer $t"
  fi
}
code_of() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('code','') if isinstance(d,dict) else 'OK')"; }
len_of() { python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d) if isinstance(d,list) else 'ERR')"; }

BODY_OWN='{"user_id":"'$UA'","name":"A"}'
BODY_FORGE='{"user_id":"'$UA'","name":"evil"}'
BODY_PREMIUM_INSERT='{"user_id":"'$UA'","is_premium":true}'

echo "== owner isolation =="
check "A inserts own profile" "OK" "$(rest POST profiles "$TA" "$BODY_OWN" | code_of)"
check "B sees no foreign rows" "0" "$(rest GET "profiles?select=user_id" "$TB" | len_of)"
check "B cannot forge A's user_id" "42501" "$(rest POST profiles "$TB" "$BODY_FORGE" | code_of)"
check "A reads own row" "1" "$(rest GET "profiles?select=user_id" "$TA" | len_of)"

echo "== column privileges (monetization) =="
check "A cannot set own is_premium" "42501" "$(rest PATCH "profiles?user_id=eq.$UA" "$TA" '{"is_premium":true}' | code_of)"
check "A cannot insert with is_premium" "42501" "$(rest POST profiles "$TB" "$BODY_PREMIUM_INSERT" | code_of)"
check "A can still update allowed cols" "OK" "$(rest PATCH "profiles?user_id=eq.$UA" "$TA" '{"name":"A2"}' | code_of)"

echo "== column privileges (reading provenance) =="
# profile_revision is what tells a stored reading which chart it describes.
# A client that could pin it could make a stale reading look current, which
# is the one claim the column exists to prevent.
check "A cannot set own profile_revision" "42501" "$(rest PATCH "profiles?user_id=eq.$UA" "$TA" '{"profile_revision":99}' | code_of)"
# And the trigger, not the grant, is what decides the value: editing the
# birth data has to move it even though nothing in the payload mentions it.
REV_BEFORE="$(rest GET "profiles?user_id=eq.$UA&select=profile_revision" "$TA" | sed -n 's/.*"profile_revision":\([0-9]*\).*/\1/p')"
rest PATCH "profiles?user_id=eq.$UA" "$TA" '{"birth_time":"09:15:00"}' >/dev/null
REV_AFTER="$(rest GET "profiles?user_id=eq.$UA&select=profile_revision" "$TA" | sed -n 's/.*"profile_revision":\([0-9]*\).*/\1/p')"
check "editing birth data bumps the revision" "yes" "$([ "$REV_AFTER" -gt "$REV_BEFORE" ] && echo yes || echo "no ($REV_BEFORE -> $REV_AFTER)")"
rest PATCH "profiles?user_id=eq.$UA" "$TA" '{"name":"A3"}' >/dev/null
REV_RENAMED="$(rest GET "profiles?user_id=eq.$UA&select=profile_revision" "$TA" | sed -n 's/.*"profile_revision":\([0-9]*\).*/\1/p')"
check "renaming does not bump it" "$REV_AFTER" "$REV_RENAMED"

echo "== room tables reject client writes =="
# A client that could insert a membership row could add itself to someone
# else''"'"'s room; one that could update astro_rooms could flip a revoked room
# back to ready. Neither grant exists, and this is what notices if one
# appears.
check "authenticated cannot create a room" "42501" "$(rest POST astro_rooms "$TB" '{"owner_user_id":"'$UA'","topic":"friendship"}' | code_of)"
check "authenticated cannot add itself as a member" "42501" "$(rest POST astro_room_members "$TB" '{"room_id":"00000000-0000-4000-8000-000000000000","user_id":"'$UA'","role":"invitee"}' | code_of)"

echo "== shared content =="
check "authenticated reads tarot_cards" "78" "$(rest GET "tarot_cards?select=id" "$TB" | len_of)"
check "authenticated reads sign_interpretations" "72" "$(rest GET "sign_interpretations?select=sign" "$TB" | len_of)"
check "authenticated cannot write tarot_cards" "42501" "$(rest PATCH "tarot_cards?id=eq.1" "$TB" '{"name_en":"x"}' | code_of)"

echo "== service-role-only tables =="
for tbl in prompt_templates ai_content_cache iap_events; do
  check "authenticated denied on $tbl" "42501" "$(rest GET "$tbl?select=*" "$TB" | code_of)"
done

echo "== backend-owned tables (read only through the API) =="
# natal_reports and synastry_results hold PAID content. Their premium gate
# lives in the API (reports_router._gate_content), so a direct select grant
# here is a way around it — a lapsed subscriber could read the full report
# straight from PostgREST. The other three carry no grant because no client
# reads them. Any of these turning green again means someone re-granted.
# The four room tables join the same group. A room holds two accounts'
# material and its premium_content is paid text, so the two-sided consent
# rule can only be enforced where the invitation token is — in the API.
# astro_room_invitations additionally holds the token hashes.
for tbl in natal_reports synastry_results partner_profiles tarot_draws tarot_qa_log \
           astro_rooms astro_room_members astro_room_invitations astro_room_results; do
  check "authenticated denied on $tbl" "42501" "$(rest GET "$tbl?select=*" "$TB" | code_of)"
done

echo
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
