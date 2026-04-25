#!/usr/bin/env bash
# smoke-post-rotation.sh — validate every service-bearing secret against its live provider
#
# Run this AFTER any secret rotation or deploy that touches KV values.
# Exits non-zero if any required probe fails so CI can gate on it.
#
# Probes (12):
#   db, fernet, anthropic, openai, voyage, resend, stripe, twilio,
#   elevenlabs, elevenlabs_agents, google_oidc, ms365 (informational)
#
# Why this exists: a prior secrets-to-KV migration silently left stale values
# for platform-twilio-auth-token and platform-elevenlabs-api-key. Those
# failures only surfaced when real traffic hit (a customer couldn't place a
# call). This script closes that gap by calling each provider's auth endpoint
# and failing loudly.
#
# Usage:
#   ./scripts/smoke-post-rotation.sh              # run on the BITSM host
#   CONTAINER=bitsm-gunicorn-1 ./scripts/...      # override container name
#   STRICT_MS365=1 ./scripts/...                  # fail on MS365 probe (requires concrete tenant_id)

set -euo pipefail

CONTAINER="${CONTAINER:-}"
STRICT_MS365="${STRICT_MS365:-0}"
COMPOSE_FILE="${COMPOSE_FILE:-/opt/bitsm/docker-compose.yml}"

# ---------- Preflight ----------

# Auto-detect the BITSM app container so this works whether the service is
# named "helpdesk" or "gunicorn" in docker-compose.yml.
if [[ -z "${CONTAINER}" ]]; then
    if [[ -f "${COMPOSE_FILE}" ]]; then
        for svc in helpdesk gunicorn app; do
            cid=$(docker compose -f "${COMPOSE_FILE}" ps -q "${svc}" 2>/dev/null | head -1)
            if [[ -n "${cid}" ]]; then
                CONTAINER=$(docker inspect --format '{{.Name}}' "${cid}" 2>/dev/null | sed 's|^/||')
                [[ -n "${CONTAINER}" ]] && break
            fi
        done
    fi
    if [[ -z "${CONTAINER}" ]]; then
        CONTAINER=$(docker ps --format '{{.Names}}' | grep -E '^bitsm-(helpdesk|gunicorn|app)-[0-9]+$' | head -1)
    fi
fi

if [[ -z "${CONTAINER}" ]] || ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "FAIL: could not resolve BITSM app container (tried: \$CONTAINER env, compose services helpdesk/gunicorn/app, name pattern)" >&2
    exit 2
fi

HEALTH_URL="${HEALTH_URL:-http://localhost:5060/api/webhooks/health}"
for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf "${HEALTH_URL}" >/dev/null; then
        HEALTH_OK=1
        break
    fi
    sleep 2
done

if [[ "${HEALTH_OK:-0}" != "1" ]]; then
    echo "FAIL: ${HEALTH_URL} did not return 200 within 20s" >&2
    exit 2
fi

echo "[smoke] container=${CONTAINER} health=ok — running 12 provider probes"
echo "---"

# ---------- Provider smoke (run inside container so we use the live env) ----------

docker exec -e STRICT_MS365="${STRICT_MS365}" "${CONTAINER}" python << 'PY'
import os, sys, requests

FAILURES = []

def probe(name, fn, informational=False):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"EXC {type(e).__name__}: {str(e)[:200]}"
    tag = "PASS" if ok else ("INFO" if informational else "FAIL")
    print(f"[{tag}] {name:20s} — {detail}")
    if not ok and not informational:
        FAILURES.append(name)

def db_probe():
    import psycopg2
    conn = psycopg2.connect(host='postgres', user='helpdesk_app',
                            password=os.environ['HELPDESK_PG_PASSWORD'], dbname='helpdesk')
    cur = conn.cursor(); cur.execute("SELECT 1")
    return cur.fetchone()[0] == 1, "SELECT 1 ok"

def fernet_probe():
    from cryptography.fernet import Fernet
    f = Fernet(os.environ['FERNET_KEY'].encode())
    return f.decrypt(f.encrypt(b"smoke")) == b"smoke", "encrypt+decrypt roundtrip ok"

def anthropic_probe():
    k = os.environ['ANTHROPIC_API_KEY']
    r = requests.post('https://api.anthropic.com/v1/messages',
        headers={'x-api-key': k, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
        json={'model': 'claude-haiku-4-5-20251001', 'max_tokens': 5,
              'messages': [{'role': 'user', 'content': 'hi'}]}, timeout=15)
    return r.status_code == 200, f"status={r.status_code} model={r.json().get('model') if r.ok else r.text[:120]}"

def openai_probe():
    k = os.environ['OPENAI_API_KEY']
    r = requests.get('https://api.openai.com/v1/models',
                     headers={'Authorization': f'Bearer {k}'}, timeout=15)
    return r.status_code == 200, f"status={r.status_code} models={len(r.json().get('data', [])) if r.ok else r.text[:120]}"

def voyage_probe():
    k = os.environ['VOYAGE_API_KEY']
    r = requests.post('https://api.voyageai.com/v1/embeddings',
        headers={'Authorization': f'Bearer {k}', 'content-type': 'application/json'},
        json={'model': 'voyage-3', 'input': ['ping']}, timeout=15)
    ok = r.status_code == 200
    return ok, f"status={r.status_code} dims={len(r.json()['data'][0]['embedding']) if ok else r.text[:120]}"

def resend_probe():
    k = os.environ['RESEND_API_KEY']
    r = requests.get('https://api.resend.com/domains',
                     headers={'Authorization': f'Bearer {k}'}, timeout=15)
    return r.status_code == 200, f"status={r.status_code}"

def stripe_probe():
    k = os.environ['STRIPE_SECRET_KEY']
    r = requests.get('https://api.stripe.com/v1/balance', auth=(k, ''), timeout=15)
    return r.status_code == 200, f"status={r.status_code} livemode={r.json().get('livemode') if r.ok else r.text[:120]}"

def twilio_probe():
    sid = os.environ['PLATFORM_TWILIO_ACCOUNT_SID']
    tok = os.environ['PLATFORM_TWILIO_AUTH_TOKEN']
    r = requests.get(f'https://api.twilio.com/2010-04-01/Accounts/{sid}.json',
                     auth=(sid, tok), timeout=15)
    return r.status_code == 200, f"status={r.status_code} name={r.json().get('friendly_name') if r.ok else r.text[:120]}"

def el_probe():
    k = os.environ['PLATFORM_ELEVENLABS_API_KEY']
    r = requests.get('https://api.elevenlabs.io/v1/user',
                     headers={'xi-api-key': k}, timeout=15)
    return r.status_code == 200, f"status={r.status_code} tier={r.json().get('subscription', {}).get('tier') if r.ok else r.text[:120]}"

def el_agents_probe():
    """Every phone_agents.el_agent_id must resolve under the current EL key."""
    import psycopg2
    k = os.environ['PLATFORM_ELEVENLABS_API_KEY']
    conn = psycopg2.connect(host='postgres', user='helpdesk_app',
                            password=os.environ['HELPDESK_PG_PASSWORD'], dbname='helpdesk')
    cur = conn.cursor()
    cur.execute("SELECT id, el_agent_id FROM helpdesk.phone_agents WHERE el_agent_id IS NOT NULL")
    rows = cur.fetchall()
    if not rows:
        return True, "no el_agent_id rows to verify"
    ok_count = sum(
        1 for _, aid in rows
        if requests.get(f'https://api.elevenlabs.io/v1/convai/agents/{aid}',
                        headers={'xi-api-key': k}, timeout=10).ok
    )
    return ok_count == len(rows), f"{ok_count}/{len(rows)} agents resolve"

def google_oidc_probe():
    cid = os.environ.get('GOOGLE_CLIENT_ID', '')
    r = requests.get('https://accounts.google.com/.well-known/openid-configuration', timeout=10)
    return (r.status_code == 200 and cid.endswith('.apps.googleusercontent.com'),
            f"discovery={r.status_code} client_id_format={'ok' if cid.endswith('.apps.googleusercontent.com') else 'BAD'}")

def ms365_probe():
    """Informational — BITSM uses delegated (user-redirect) OAuth, not client_credentials.
    This probe only works when AZURE_TENANT_ID is a concrete tenant, not 'common'."""
    cid = os.environ['AZURE_CLIENT_ID']
    sec = os.environ['AZURE_CLIENT_SECRET']
    tid = os.environ['AZURE_TENANT_ID']
    if tid == 'common':
        return True, "skipped — AZURE_TENANT_ID=common (BITSM uses delegated flow; rotation verified via login UI)"
    r = requests.post(f'https://login.microsoftonline.com/{tid}/oauth2/v2.0/token',
        data={'client_id': cid, 'client_secret': sec,
              'scope': 'https://graph.microsoft.com/.default',
              'grant_type': 'client_credentials'}, timeout=15)
    return r.status_code == 200, f"status={r.status_code} token_type={r.json().get('token_type') if r.ok else r.text[:160]}"

strict_ms365 = os.environ.get('STRICT_MS365') == '1'

probe('db',                db_probe)
probe('fernet',            fernet_probe)
probe('anthropic',         anthropic_probe)
probe('openai',            openai_probe)
probe('voyage',            voyage_probe)
probe('resend',            resend_probe)
probe('stripe',            stripe_probe)
probe('twilio',            twilio_probe)
probe('elevenlabs',        el_probe)
probe('elevenlabs_agents', el_agents_probe)
probe('google_oidc',       google_oidc_probe)
probe('ms365',             ms365_probe, informational=not strict_ms365)

print("---")
if FAILURES:
    print(f"SMOKE FAILED — {len(FAILURES)} probe(s): {', '.join(FAILURES)}")
    sys.exit(1)
print("SMOKE PASSED — all required probes green")
PY
