# Inbound Email → Ticket Setup Guide

Emails sent to `{tenant-slug}@bitsm.io` automatically create support tickets.
Customer replies (which include `[TKT-XXXXX]` in the subject) are appended as comments.
Atlas auto-engages on every new ticket exactly as it does for web-created tickets.

---

## Architecture

```
Customer emails acme@bitsm.io
        ↓
Cloudflare Email Routing (MX on bitsm.io)
        ↓
helpdesk-email-worker (Cloudflare Worker, postal-mime)
        ↓
POST /api/webhooks/inbound-email  (X-Webhook-Secret header)
        ↓
Flask: find/create user → create ticket → SLA → Atlas pipeline
        ↓
Confirmation email sent to customer (Reply-To: acme@bitsm.io)
        ↓
Customer replies → thread detected → comment added
```

---

## One-time Setup

### 1. Cloudflare Email Routing

1. Go to **Cloudflare Dashboard → bitsm.io → Email → Email Routing**
2. Click **Get started** (or skip wizard if already on routes page)
3. Cloudflare will prompt you to add MX records — click **Add records automatically**
4. Go to **Routes** tab
5. Scroll to **Catch-all address** → Edit
   - Action: **Send to a Worker**
   - Worker: `helpdesk-email-worker`
6. Save

### 2. Server .env

Already done. The following vars are set in `/opt/bitsm/.env` on the Azure VM:
```
INBOUND_EMAIL_SECRET=A6lhDnj0iCbode4mjMt4geSg8rjPamS_Mz-vGixU-BE
INBOUND_EMAIL_DOMAIN=bitsm.io
```

### 3. Deploy the Cloudflare Worker

Run this once from your development machine:

```bash
cd C:/Code/Personal/bitsm/email-worker
npm install
npx wrangler login          # opens browser — log in with your Cloudflare account
npx wrangler secret put WEBHOOK_SECRET
# When prompted, paste: A6lhDnj0iCbode4mjMt4geSg8rjPamS_Mz-vGixU-BE
npx wrangler deploy
```

Verify it deployed:
```bash
npx wrangler deployments list
```

---

## How It Works Per Tenant

Each tenant's inbound address is derived from their slug:
```
{tenant-slug}@bitsm.io
```

Visible in **Admin → Notifications → Settings → Inbound Email** with a Copy button.

### New ticket flow
1. Email arrives at `acme@bitsm.io`
2. Worker parses From, Subject, body text
3. Flask looks up tenant by slug `acme`
4. Sender is found or created as an `end_user` in that tenant
5. Ticket created with `source = 'email'`, priority `p3`
6. SLA applied, Atlas pipeline queued, `ticket_created` notification sent
7. Confirmation email goes back to sender with `Reply-To: acme@bitsm.io`

### Reply / thread flow
1. Customer replies to the confirmation email
2. Subject still contains `[TKT-00001]` — thread detection fires
3. Comment added to existing ticket instead of creating a new one
4. `requester_reply` notification sent to assigned agent

### Anti-loop protection
Emails from known ticketing systems (`*@noreply.*`, `*@freshdesk.com`, etc.) are
silently dropped before any ticket is created.

---

## Re-deploying the Worker

After any changes to `email-worker/src/index.js`:
```bash
cd C:/Code/Personal/bitsm/email-worker
npx wrangler deploy
```

The secret does not need to be re-set unless rotated.

---

## Rotating the Webhook Secret

1. Generate a new secret: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
2. Update server: `echo 'INBOUND_EMAIL_SECRET=<new>' >> /opt/bitsm/.env`
   then rebuild: `cd /opt/bitsm && docker compose up -d --build`
3. Update Worker: `npx wrangler secret put WEBHOOK_SECRET` (enter new value)

Both must be updated — if they don't match, the endpoint returns 401 and emails are dropped.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Emails not arriving | Cloudflare MX records added? Email Routing enabled? Catch-all rule set? |
| Worker deploys but tickets not created | `INBOUND_EMAIL_SECRET` on server matches `WEBHOOK_SECRET` in Worker? |
| `tenant not found` in logs | Tenant slug matches the To: address prefix exactly? |
| Replies creating new tickets | Subject line must contain `[TKT-XXXXX]` — check outbound email template |
| Blocked sender | From address matches a blocklist pattern in Notification Settings |

Check Worker logs:
```bash
npx wrangler tail helpdesk-email-worker
```

Check Flask logs:
```bash
ssh -i ~/.ssh/your-vm-key.pem azureuser@<YOUR_VM_IP>
sudo -u deploy bash -c 'cd /opt/bitsm && docker compose logs helpdesk --tail=50'
```
