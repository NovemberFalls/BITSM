# BITSM Communications Platform — Complete Reference

> Voice AI agents, IVR routing, SMS, WhatsApp, and the full Twilio + ElevenLabs integration.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Voice System](#voice-system)
   - [Call Flow](#call-flow)
   - [IVR Routing](#ivr-routing)
   - [ElevenLabs ConvAI Agents](#elevenlabs-convai-agents)
   - [Webhook Tools](#webhook-tools)
   - [Call Transfer](#call-transfer)
   - [Post-Call Processing](#post-call-processing)
3. [Messaging System](#messaging-system)
   - [SMS](#sms)
   - [WhatsApp](#whatsapp)
   - [WhatsApp Templates](#whatsapp-templates)
   - [Inbound Message Flow](#inbound-message-flow)
4. [Credential Management](#credential-management)
   - [BYOK vs Platform Mode](#byok-vs-platform-mode)
   - [Credential Fallback Chain](#credential-fallback-chain)
   - [Fernet Encryption](#fernet-encryption)
5. [Multi-Agent System](#multi-agent-system)
   - [Agent Lifecycle](#agent-lifecycle)
   - [Deploy Pipeline](#deploy-pipeline)
   - [Per-Agent Configuration](#per-agent-configuration)
6. [Multi-Language Support](#multi-language-support)
   - [IVR Languages](#ivr-languages)
   - [Polly Voice Map](#polly-voice-map)
   - [Messaging Languages](#messaging-languages)
7. [Security](#security)
   - [HMAC Webhook Authentication](#hmac-webhook-authentication)
   - [Twilio Signature Validation](#twilio-signature-validation)
   - [Billing Gate](#billing-gate)
8. [Cost Tracking](#cost-tracking)
9. [Customer Onboarding Guide](#customer-onboarding-guide)
   - [Voice Setup](#voice-setup)
   - [SMS Setup](#sms-setup)
   - [WhatsApp Setup](#whatsapp-setup)
10. [Database Schema](#database-schema)
11. [API Reference](#api-reference)
12. [Environment Variables](#environment-variables)
13. [Key Files](#key-files)
14. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
Customer
   |
   |-- Phone Call ──> Twilio ──> IVR Greeting (TwiML)
   |                              |
   |                              ├── Press 1 ──> ElevenLabs ConvAI Agent (EN)
   |                              ├── Press 2 ──> ElevenLabs ConvAI Agent (ES)
   |                              └── Press N ──> ElevenLabs ConvAI Agent (N)
   |                                                  |
   |                                                  ├── search_knowledge_base (webhook)
   |                                                  ├── identify_caller (webhook)
   |                                                  ├── create_ticket (webhook)
   |                                                  ├── attempt_transfer (webhook)
   |                                                  ├── collect_email (webhook)
   |                                                  ├── get_category_fields (webhook)
   |                                                  ├── set_custom_field (webhook)
   |                                                  └── end_call (system)
   |
   |-- SMS ──────> Twilio ──> Inbound Webhook ──> Conversation + Auto-reply
   |
   |-- WhatsApp ──> Twilio ──> Inbound Webhook ──> Conversation + Auto-reply
   |
   └── All channels can auto-create tickets
```

**Stack:**
- **Twilio** — Phone numbers, IVR (TwiML), SMS/WhatsApp (Messages API), call routing
- **ElevenLabs** — Conversational AI agents (TTS/STT/LLM), voice personas
- **Amazon Polly** — IVR greeting voices (via Twilio `<Say voice="Polly.*">`)
- **BITSM Backend** — Webhook tools, credential management, session tracking, cost tracking

---

## Voice System

### Call Flow

```
1. Caller dials tenant's Twilio phone number
   ↓
2. Twilio fires GET /api/phone/ivr/<tenant_id>
   ↓
3. BITSM returns TwiML:
   - "This call may be recorded..." (Polly.Joanna)
   - Per-agent greeting in each language (Polly voice per language)
   - <Gather numDigits="1"> to collect digit selection
   - On timeout: redirects back to IVR greeting (loop)
   ↓
4. Caller presses digit → POST /api/phone/ivr/<tenant_id>/route
   ↓
5. BITSM looks up phone_agents by ivr_digit
   ↓
6. Billing gate check (reject if over cap or free tier)
   ↓
7. BITSM proxies to ElevenLabs: POST /v1/convai/twilio/register-call
   Body: { agent_id, from_number, to_number }
   ↓
8. ElevenLabs returns TwiML to connect the call to the AI agent
   ↓
9. Agent converses with caller, calling webhook tools as needed
   ↓
10. Call ends → ElevenLabs fires POST /api/phone/webhook/<tenant_id>/call_ended
    - Saves transcript (JSONB), duration, costs, summary
    - Links to ticket if one was created during the call
```

### IVR Routing

The IVR greeting is **auto-composed** from active phone agents. Each agent contributes a `<Say>` block in its configured language, using the appropriate Polly voice.

**TwiML structure:**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">This call may be recorded for quality assurance
    purposes. Thank you for calling {tenant_name}.</Say>
  <Pause length="1"/>
  <Gather numDigits="1" action=".../ivr/{tenant_id}/route" method="POST" timeout="10">
    <Say voice="Polly.Joanna">Press 1 for support in English.</Say>
    <Pause length="1"/>
    <Say voice="Polly.Lupe">Oprima 2 para soporte en espanol.</Say>
    <Pause length="1"/>
  </Gather>
  <Redirect method="POST">.../ivr/{tenant_id}</Redirect>
</Response>
```

**Key behaviors:**
- If no agents are configured, falls back to a generic English prompt
- Custom IVR greetings can be set per-agent (overrides the default template)
- Gather timeout (10s) redirects back to the greeting (loops until caller presses a digit)
- Twilio signature validation on all IVR endpoints

### ElevenLabs ConvAI Agents

Each phone agent maps to one ElevenLabs Conversational AI agent. The EL agent is configured with:

| Setting | Source | Default |
|---------|--------|---------|
| LLM Model | `phone_agents.llm_model` | `claude-haiku-4-5@20251001` |
| Temperature | `phone_agents.temperature` | `0.4` |
| TTS Voice | `phone_agents.voice_id` | `mcuuWJIofmzgKEGk3EMA` |
| TTS Speed | `phone_agents.tts_speed` | `1.15` |
| Turn Timeout | `phone_agents.turn_timeout` | `10.0` seconds |
| Audio Format | `phone_agents.audio_format` | `ulaw_8000` (telephony) |
| System Prompt | `phone_agents.system_prompt` | Platform default (see below) |
| First Message | `phone_agents.greeting_message` | Auto-generated per language |
| Language | `phone_agents.language` | `en` |

**Default system prompt highlights (Atlas - English):**
- Warm, relaxed personality — "like a knowledgeable friend on the team"
- Speaks in short, natural sentences
- Never identifies as AI — deflects with "I'm part of the {tenant} support team"
- NATO phonetic alphabet for case numbers, read twice, confirm
- Pacing rules: brief filler before tool results, short sentences, pause between ideas
- Name confirmation protocol: repeat back, confirm before calling identify_caller
- Graceful email fallback after 2 failed name attempts
- Create ticket for every call — no exceptions
- Warm transfer language: "connecting you with a colleague"

### Webhook Tools

8 tools are registered with each ElevenLabs agent. All are POST webhooks authenticated via `?t=<HMAC_TOKEN>`.

| Tool | Purpose | Key Params |
|------|---------|------------|
| `search_knowledge_base` | Search the tenant's KB via RAG | `query`, `conversation_id` |
| `identify_caller` | Look up caller by name/email/phone | `name`, `email`, `conversation_id` |
| `create_ticket` | Create a support ticket from the call | `subject`, `description`, `priority`, `caller_email`, `requester_user_id`, `problem_category_id`, `custom_fields` |
| `attempt_transfer` | Transfer to a human (on-call number) | `reason`, `conversation_id`, `ticket_id` |
| `collect_email` | Record caller's email after failed transfer | `email`, `conversation_id` |
| `get_category_fields` | Match issue to category, get required fields | `issue_description`, `category_name` |
| `set_custom_field` | Set a custom field value on a ticket | `ticket_id`, `field_key`, `value` |
| `end_call` | System tool — hang up the call | (none) |

**Tool execution details:**

- **identify_caller**: Checks phone number first (exact E.164 match), then email, then fuzzy name match via `pg_trgm`. Returns `found`, `created`, or `multiple_matches`.
- **create_ticket**: Generates a `ticket_number_spoken` field (NATO phonetic: "T as in Tango, K as in Kilo, T as in Tango, 0, 1, 5, 5, 8"). Supports custom fields and category linkage.
- **get_category_fields**: Uses `pg_trgm` similarity matching to find the best category for the caller's issue description. Returns the category and its required custom fields (including ancestor fields via CTE).
- **attempt_transfer**: Makes an outbound Twilio call to the agent's `oncall_number`. Polls status every 2 seconds for up to 30 seconds. Returns `connected`, `no_answer`, or `failed`.
- **collect_email**: Called after a failed transfer. Stores the email on the session and creates/updates the caller's user record.

### Call Transfer

Transfer flow:
1. Agent tells caller to hold
2. Agent calls `attempt_transfer` tool
3. BITSM makes outbound Twilio call to `oncall_number`
4. Polls call status every 2s for up to 30s
5. If answered → returns `connected`, agent ends conversation
6. If no answer → returns `no_answer`, agent apologizes, calls `collect_email`
7. Transfer attempts logged in `phone_transfer_attempts` table

### Post-Call Processing

When ElevenLabs fires the `call_ended` webhook:
1. Transcript saved to `phone_sessions.transcript` (JSONB array)
2. Duration calculated from Twilio call metadata
3. ElevenLabs costs stored: `el_cost_credits` (divide by 10,000 for USD), `el_llm_input_tokens`, `el_llm_output_tokens`
4. Twilio call cost fetched async: `twilio_cost_cents`
5. Session status updated: `active` → `resolved` / `transferred` / `email_collected` / `abandoned`
6. Summary generated from EL analysis

---

## Messaging System

### SMS

Standard Twilio SMS via the Messages API. Uses the same phone number as voice.

**Outbound flow:**
1. Agent composes message in the Messaging tab
2. POST /api/messaging/conversations/{id}/messages
3. Message saved to DB (status: `queued`)
4. Background thread sends via `client.messages.create()`
5. Status callback URL set for delivery tracking
6. Twilio fires status updates: queued → sent → delivered

**Inbound flow:**
1. Customer sends SMS to Twilio number
2. Twilio fires POST /api/messaging/webhook/{tenant_id}/inbound
3. BITSM parses From, Body, MediaUrl
4. Finds or creates conversation (keyed by tenant + channel + phone)
5. Saves message (status: `received`)
6. If auto-reply enabled → sends auto-reply
7. If auto-create-ticket enabled → creates ticket linked to conversation

### WhatsApp

Uses the same Twilio Messages API with `whatsapp:` prefix on phone numbers.

**Key differences from SMS:**
- Numbers formatted as `whatsapp:+15551234567`
- **24-hour session window**: After a customer sends a message, you have 24 hours to send free-form replies. After that, only pre-approved template messages are allowed.
- **Separate phone number**: WhatsApp can use a different number than voice/SMS (configured in `whatsapp_phone_number`)
- **Approval required**: WhatsApp Business numbers require Meta approval (1-4 weeks)

**WhatsApp status progression:**
- `not_configured` → number not set up
- `sandbox` → Twilio WhatsApp Sandbox (testing only)
- `pending` → submitted to Meta for approval
- `approved` → production-ready

### WhatsApp Templates

Templates are required for messages sent outside the 24-hour session window.

**Template lifecycle:**
1. Create template in BITSM (status: `draft`)
2. Submit to Twilio/Meta for approval (status: `pending`)
3. Meta reviews and approves/rejects (status: `approved` or `rejected`)
4. Approved templates can be used to re-engage customers after 24h

**Template fields:**
- `name` — unique identifier (e.g., `welcome_message`)
- `language` — template language code
- `body` — message text with `{{1}}`, `{{2}}` placeholders
- `category` — `utility`, `marketing`, or `authentication`
- `variables` — placeholder definitions (JSONB)

### Inbound Message Flow

```
Twilio ──POST──> /api/messaging/webhook/{tenant_id}/inbound?t={HMAC}
                    |
                    ├── Parse From number → determine channel (SMS vs WhatsApp)
                    ├── Check channel is enabled for tenant
                    ├── Find or create conversation
                    ├── Save inbound message
                    ├── Update conversation timestamps (last_message_at, last_inbound_at)
                    ├── Auto-reply if enabled
                    └── Auto-create ticket if enabled

Response: Empty TwiML <Response></Response>
(Replies are sent via the API, not inline in the webhook response)
```

---

## Credential Management

### BYOK vs Platform Mode

Tenants choose how to authenticate with Twilio and ElevenLabs:

| Mode | How it works | Who pays |
|------|-------------|----------|
| **Platform** | BITSM provides shared Twilio + ElevenLabs accounts | BITSM (billed back to tenant) |
| **BYOK** (Bring Your Own Key) | Tenant enters their own API keys | Tenant pays providers directly |

**Stored in:** `phone_configs.credentials_mode` (`'platform'` or `'byok'`)

### Credential Fallback Chain

```python
get_effective_credentials(tenant_id):
    1. If mode == 'byok' AND credentials_encrypted exists → decrypt and return
    2. If PLATFORM_TWILIO_ACCOUNT_SID set → use platform credentials
    3. If DEV_TWILIO_ACCOUNT_SID set → use dev credentials (local testing)
    4. Return dict with None values (never raises)
```

This chain means:
- Production (Azure): Platform env vars are set → all platform-mode tenants share them
- Local dev: Dev env vars in `.env` → works without platform keys
- BYOK tenant: Always uses their own keys regardless of environment

### Fernet Encryption

Credentials are stored encrypted in `phone_configs.credentials_encrypted` using Python's `cryptography.fernet.Fernet`.

**Encrypted blob structure:**
```json
{
  "twilio_account_sid": "AC...",
  "twilio_auth_token": "abc123...",
  "twilio_phone_number": "+14155552671",
  "elevenlabs_api_key": "sk-..."
}
```

**Key:** `FERNET_KEY` environment variable.

**API safety:** Credentials are never returned to the frontend. The GET config endpoint returns only boolean flags (`twilio_auth_token_set`, `elevenlabs_api_key_set`).

**Merge strategy:** On save, existing credentials are decrypted, only explicitly-provided keys are overwritten, then re-encrypted. This allows partial updates (e.g., updating just the auth token without re-entering the account SID).

---

## Multi-Agent System

### Agent Lifecycle

```
Create Agent → Configure (name, voice, language, prompt)
    ↓
Deploy Agent → Creates ElevenLabs ConvAI agent via API
    ↓
Activate Agent → Links Twilio number, sets IVR digit, marks active
    ↓
Live — Agent handles calls for its assigned IVR digit
    ↓
Deactivate/Delete → Deprovisions from ElevenLabs, unlinks sessions
```

### Deploy Pipeline

The `DeployStepper` UI shows three phases:

1. **Configure** — Agent has a name and voice ID set
2. **Deploy** — ElevenLabs agent created (PATCH if exists, POST if new; 404 fallback for deleted agents)
3. **Activate** — Twilio number linked to ElevenLabs, agent marked active

**Deploy action (`deploy_agent`):**
- Reads all agent settings (voice, prompt, LLM params, tools)
- Constructs ElevenLabs ConvAI payload
- If agent already has `el_agent_id` → PATCH update
- If not → POST create
- Registers post-call webhook automatically
- Updates `phone_agents.el_agent_id` and `is_deployed = TRUE`

**Activate action (`activate_agent`):**
- Links the Twilio phone number to the ElevenLabs agent
- Sets `is_active = TRUE` and `is_number_linked = TRUE`

### Per-Agent Configuration

Each agent can override all settings independently:

| Setting | Description | Per-Agent? |
|---------|-------------|------------|
| Name | Display name (e.g., "Atlas", "Astra") | Yes |
| Language | Language code (en, es, fr, de, pt) | Yes |
| Voice ID | ElevenLabs voice | Yes |
| System Prompt | Custom or platform default | Yes |
| Greeting Message | First message on call connect | Yes |
| IVR Greeting | Custom IVR greeting text | Yes |
| IVR Digit | Digit that routes to this agent | Yes |
| On-Call Number | Human transfer target | Yes |
| LLM Model | Claude model for the agent | Yes |
| Temperature | LLM temperature (0.0-1.0) | Yes |
| Turn Timeout | Seconds to wait for caller response | Yes |
| Audio Format | ulaw_8000, pcm_16000, pcm_44100 | Yes |
| TTS Speed | Text-to-speech speed multiplier | Yes |
| Tools Enabled | Which webhook tools the agent can use | Yes |

**Tools toggle:** Each agent has a `tools_enabled` array. Available tools:
- `search_kb` — KB Search
- `create_ticket` — Ticket Creation
- `identify_caller` — Caller Identification
- `attempt_transfer` — Human Transfer
- `collect_email` — Email Collection

(`get_category_fields` and `set_custom_field` are always enabled when `create_ticket` is enabled.)

---

## Multi-Language Support

### IVR Languages

The IVR system supports N languages simultaneously. Each phone agent has a language, and the IVR greeting auto-composes `<Say>` blocks from all active agents.

**Supported languages:** English, Spanish, French, German, Portuguese (extensible)

### Polly Voice Map

| Language | Polly Voice | IVR Default |
|----------|-------------|-------------|
| English (en) | `Polly.Joanna` | "Press {digit} for support in English." |
| Spanish (es) | `Polly.Lupe` | "Oprima {digit} para soporte en espanol." |
| French (fr) | `Polly.Lea` | "Appuyez sur {digit} pour le support en francais." |
| German (de) | `Polly.Vicki` | "Drucken Sie {digit} fur Unterstutzung auf Deutsch." |
| Portuguese (pt) | `Polly.Camila` | "Pressione {digit} para suporte em portugues." |

### Messaging Languages

- `messaging_default_language` on `phone_configs` — default for new conversations
- Per-conversation `language` field
- WhatsApp templates support per-language variants (same name, different language)
- Auto-reply uses the tenant's default language

---

## Security

### HMAC Webhook Authentication

All webhook tool URLs include a `?t=<TOKEN>` parameter. The token is a 32-character HMAC-SHA256 hash.

**Phone webhooks:**
```python
secret = (SECRET_KEY + str(tenant_id)).encode()
token = hmac.new(secret, f"phone-tool-{tenant_id}".encode(), sha256).hexdigest()[:32]
```

**Messaging webhooks:**
```python
secret = (SECRET_KEY + str(tenant_id)).encode()
token = hmac.new(secret, f"messaging-{tenant_id}".encode(), sha256).hexdigest()[:32]
```

Phone and messaging use **separate token namespaces** — a phone token cannot authenticate a messaging webhook and vice versa.

### Twilio Signature Validation

IVR endpoints validate the `X-Twilio-Signature` header using the tenant's Twilio auth token:

```python
from twilio.request_validator import RequestValidator
validator = RequestValidator(auth_token)
# Reconstruct URL using APP_URL (Docker internal URL differs from public URL)
url = Config.APP_URL + parsed.path + query
validator.validate(url, request.form.to_dict(), signature)
```

**Important:** The validation URL must use `APP_URL` (the public URL), not `request.url` (which reflects the Docker internal hostname).

### Billing Gate

Before routing a call to ElevenLabs, the system checks the tenant's billing status:

```python
from services.billing_service import check_ai_gate, ApiCapError
check_ai_gate(tenant_id)  # raises ApiCapError if over cap or free tier
```

Free tier tenants get 0 phone agents. Paid tier tenants get unlimited agents.

**Permission:** All admin endpoints require `@require_permission("phone.manage")` or `@require_permission("messaging.manage")`.

---

## Cost Tracking

### Voice Costs

| Source | Field | Unit |
|--------|-------|------|
| ElevenLabs | `phone_sessions.el_cost_credits` | Credits (divide by 10,000 for USD) |
| ElevenLabs | `phone_sessions.el_llm_input_tokens` | Tokens |
| ElevenLabs | `phone_sessions.el_llm_output_tokens` | Tokens |
| Twilio | `phone_sessions.twilio_cost_cents` | Cents |

EL costs come from the post-call webhook. Twilio costs are fetched async via `client.calls(sid).fetch().price`.

### Messaging Costs

| Source | Field | Unit |
|--------|-------|------|
| Twilio | `messages.cost_cents` | Cents (from status callback `Price` field) |
| Twilio | `messages.segments` | SMS segment count |

Messaging costs arrive via the Twilio status callback webhook.

### Billing Integration

Voice costs are tracked in `api_usage_monthly` via `billing_service.record_usage()` (fire-and-forget daemon thread). Per-tier caps:

| Plan | Monthly Cap |
|------|-------------|
| Free | $0.00 (phone disabled) |
| Trial | $15.00 |
| Starter | $15.00 |
| Pro | $30.00 |
| Business | $45.00 |
| Enterprise | Unlimited |

---

## Customer Onboarding Guide

### Voice Setup

**Platform mode (easiest):**
1. Navigate to Admin > Communications > Voice Agents
2. Click "Enable Platform Phone" — auto-provisions:
   - Purchases a Twilio phone number
   - Creates an ElevenLabs ConvAI agent
   - Links the number to the agent
3. Done — calls are live

**BYOK mode:**
1. Navigate to Admin > Communications > Voice Agents
2. Switch credential mode to "BYOK"
3. Enter Twilio Account SID, Auth Token, Phone Number
4. Enter ElevenLabs API Key
5. Click "Save Credentials"
6. Create a new agent (name + language)
7. Configure voice, greeting, system prompt, on-call number
8. Click "Deploy Agent" — creates the ElevenLabs agent
9. Click "Activate Agent" — links the number
10. Copy the IVR Greeting webhook URL
11. In Twilio Console: set the phone number's voice webhook to the IVR URL

### SMS Setup

1. Navigate to Admin > Communications > Messaging tab
2. Enable "SMS" toggle
3. Click "Save Settings"
4. Copy the "Inbound Messages" webhook URL
5. In Twilio Console: set the phone number's messaging webhook to this URL
6. (Optional) Enable auto-reply and/or auto-create-ticket
7. Inbound SMS messages will appear in the Conversations panel

### WhatsApp Setup

1. **Prerequisite**: Register your Twilio number as a WhatsApp Business sender
   - Go to Twilio Console > Messaging > Senders > WhatsApp Senders
   - Follow Twilio's WhatsApp onboarding wizard
   - Submit for Meta approval (1-4 weeks for production)
   - For immediate testing: use the Twilio WhatsApp Sandbox

2. Navigate to Admin > Communications > Messaging tab
3. Enable "WhatsApp" toggle
4. Set WhatsApp status:
   - `Sandbox` — for testing with Twilio Sandbox
   - `Pending` — submitted to Meta, awaiting approval
   - `Approved` — production ready
5. (Optional) Enter a separate WhatsApp phone number if different from voice
6. Click "Save Settings"
7. Copy the "Inbound Messages" webhook URL
8. In Twilio Console: set the WhatsApp sender's webhook to this URL
9. Create WhatsApp templates for messages outside the 24-hour window
10. Templates must also be submitted for Meta approval via Twilio Console

---

## Database Schema

### phone_configs (tenant-level settings)
```
id, tenant_id (UNIQUE), is_active, credentials_encrypted, credentials_mode,
assigned_phone_number, platform_twilio_number_sid, elevenlabs_phone_number_id,
llm_model, temperature, turn_timeout, audio_format,
sms_enabled, whatsapp_enabled, whatsapp_phone_number, whatsapp_status,
messaging_auto_reply, messaging_auto_reply_msg,
messaging_auto_create_ticket, messaging_default_language,
created_at, updated_at
```

### phone_agents (N per tenant)
```
id, tenant_id, slug (UNIQUE per tenant), name, language,
el_agent_id, voice_id, greeting_message, ivr_greeting, system_prompt,
llm_model, temperature, turn_timeout, audio_format, tts_speed,
ivr_digit, oncall_number, is_active, is_deployed, is_number_linked,
tools_enabled (TEXT[]), sort_order, created_at, updated_at
```

### phone_sessions (one per call)
```
id, tenant_id, twilio_call_sid, elevenlabs_conversation_id,
caller_phone, caller_email, phone_agent_id, ticket_id,
status (ivr/routing/active/resolved/transferred/email_collected/abandoned),
transfer_attempted, transfer_succeeded, transcript (JSONB), summary,
duration_seconds, el_cost_credits, el_llm_input_tokens, el_llm_output_tokens,
twilio_cost_cents, started_at, ended_at, created_at
```

### phone_transfer_attempts
```
id, session_id, oncall_number, outbound_call_sid,
status (pending/answered/timeout/failed), created_at
```

### messaging_conversations
```
id, tenant_id, channel (sms/whatsapp), contact_phone, contact_name,
contact_email, user_id, language, ticket_id, status (active/resolved/archived),
last_message_at, last_inbound_at, message_count, created_at, updated_at
UNIQUE(tenant_id, channel, contact_phone)
```

### messages
```
id, conversation_id, tenant_id, direction (inbound/outbound),
channel (sms/whatsapp), body, media_url, twilio_message_sid,
status (queued/sent/delivered/read/failed/received),
error_code, error_message, segments, cost_cents,
language, template_name, sender_user_id, created_at
```

### messaging_templates
```
id, tenant_id, name, language, body, category (utility/marketing/authentication),
status (draft/pending/approved/rejected), twilio_template_sid,
variables (JSONB), created_at, updated_at
UNIQUE(tenant_id, name, language)
```

---

## API Reference

### Voice — Config & Agents

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| GET | `/api/phone/config` | `phone.manage` | Get tenant phone config (masked creds) |
| PUT | `/api/phone/config` | `phone.manage` | Save credentials + settings |
| GET | `/api/phone/config/defaults` | `phone.manage` | Get default AI/audio values |
| GET | `/api/phone/webhooks` | `phone.manage` | Get all webhook URLs |
| POST | `/api/phone/enable` | `phone.manage` | One-click platform provisioning |
| GET | `/api/phone/agents` | `phone.manage` | List all agents |
| POST | `/api/phone/agents` | `phone.manage` | Create new agent |
| GET | `/api/phone/agents/{id}` | `phone.manage` | Get agent details |
| PUT | `/api/phone/agents/{id}` | `phone.manage` | Update agent settings |
| DELETE | `/api/phone/agents/{id}` | `phone.manage` | Delete agent (deprovisions EL) |
| POST | `/api/phone/agents/{id}/deploy` | `phone.manage` | Deploy to ElevenLabs |
| POST | `/api/phone/agents/{id}/activate` | `phone.manage` | Link number + activate |
| POST | `/api/phone/agents/{id}/reset` | `phone.manage` | Reset to defaults |
| GET | `/api/phone/agents/default-prompt` | `phone.manage` | Preview platform prompt |
| GET | `/api/phone/sessions` | `phone.manage` | Call log (filterable) |
| GET | `/api/phone/sessions/{id}` | `phone.manage` | Session detail + transcript |

### Voice — Webhooks (Token Auth)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/phone/tool/{tid}/search_kb?t=` | HMAC | Search KB during call |
| POST | `/api/phone/tool/{tid}/identify_caller?t=` | HMAC | Look up caller |
| POST | `/api/phone/tool/{tid}/create_ticket?t=` | HMAC | Create ticket |
| POST | `/api/phone/tool/{tid}/attempt_transfer?t=` | HMAC | Transfer to human |
| POST | `/api/phone/tool/{tid}/collect_email?t=` | HMAC | Collect email |
| POST | `/api/phone/tool/{tid}/get_category_fields?t=` | HMAC | Match category |
| POST | `/api/phone/tool/{tid}/set_custom_field?t=` | HMAC | Set custom field |
| POST | `/api/phone/webhook/{tid}/call_ended?t=` | HMAC | Post-call webhook |
| GET/POST | `/api/phone/ivr/{tid}` | Twilio Sig | IVR greeting |
| POST | `/api/phone/ivr/{tid}/route` | Twilio Sig | Digit routing |

### Messaging — Config & Conversations

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| GET | `/api/messaging/config` | `messaging.manage` | Get messaging config |
| PUT | `/api/messaging/config` | `messaging.manage` | Save messaging settings |
| GET | `/api/messaging/webhooks` | `messaging.manage` | Get Twilio webhook URLs |
| GET | `/api/messaging/stats` | `messaging.manage` | 30-day messaging stats |
| GET | `/api/messaging/conversations` | `messaging.manage` | List conversations |
| GET | `/api/messaging/conversations/{id}` | `messaging.manage` | Get conversation |
| PUT | `/api/messaging/conversations/{id}` | `messaging.manage` | Update (status, ticket) |
| GET | `/api/messaging/conversations/{id}/messages` | `messaging.manage` | List messages |
| POST | `/api/messaging/conversations/{id}/messages` | `messaging.manage` | Send message |
| GET | `/api/messaging/templates` | `messaging.manage` | List templates |
| POST | `/api/messaging/templates` | `messaging.manage` | Create template |
| GET | `/api/messaging/templates/{id}` | `messaging.manage` | Get template |
| PUT | `/api/messaging/templates/{id}` | `messaging.manage` | Update template |
| DELETE | `/api/messaging/templates/{id}` | `messaging.manage` | Delete template |

### Messaging — Webhooks (Token Auth)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/messaging/webhook/{tid}/inbound?t=` | HMAC | Inbound SMS/WhatsApp |
| POST | `/api/messaging/webhook/{tid}/status?t=` | HMAC | Delivery status updates |

---

## Environment Variables

```bash
# Platform credentials (shared pool for platform-mode tenants)
PLATFORM_ELEVENLABS_API_KEY=...
PLATFORM_TWILIO_ACCOUNT_SID=AC...
PLATFORM_TWILIO_AUTH_TOKEN=...

# Dev credentials (local testing fallback)
DEV_TWILIO_ACCOUNT_SID=AC...
DEV_TWILIO_AUTH_TOKEN=...
DEV_TWILIO_PHONE_NUMBER=+14155552671
DEV_ELEVENLABS_API_KEY=sk-...
DEV_ONCALL_NUMBER=+15551234567

# Required for credential encryption
FERNET_KEY=...

# Required for HMAC webhook tokens
SECRET_KEY=...

# Required for Twilio signature validation URL reconstruction
APP_URL=https://bitsm.io
```

---

## Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `services/phone_service.py` | ~2,200 | Core voice system: credentials, agents, sessions, EL/Twilio API, tools, prompts |
| `services/messaging_service.py` | ~500 | SMS/WhatsApp: config, conversations, send/receive, templates, cost tracking |
| `routes/phone.py` | ~920 | Voice API: 9 agent endpoints, IVR, 8 tool webhooks, session tracking |
| `routes/messaging.py` | ~250 | Messaging API: config, conversations, messages, templates, Twilio webhooks |
| `webapp/src/components/admin/PhoneSettings.tsx` | ~1,400 | Communications UI: Voice Agents, Messaging, Call Logs tabs |
| `webapp/src/types/index.ts` | — | TypeScript interfaces: PhoneConfig/Agent/Session, Messaging* types |
| `webapp/src/api/client.ts` | — | API client: phone + messaging methods |
| `config.py` | — | Environment variables for Twilio/EL credentials |
| `services/billing_service.py` | — | Cost tracking and billing cap enforcement |
| `migrations/024_phone_helpdesk.sql` | — | Initial phone tables |
| `migrations/045_phone_config_expansion.sql` | — | AI params + permissions |
| `migrations/047_phone_agents.sql` | — | Multi-agent support |
| `migrations/063_messaging.sql` | — | SMS/WhatsApp tables + messaging permission |

---

## Troubleshooting

### "ElevenLabs API key not configured"
- Check `phone_configs.credentials_mode` — if `platform`, ensure `PLATFORM_ELEVENLABS_API_KEY` is set in `.env`
- If `byok`, ensure the tenant has saved their EL API key

### IVR greeting plays but digit press does nothing
- Check that at least one `phone_agent` is active and deployed for this tenant
- Verify the agent's `ivr_digit` matches the digit being pressed
- Check Twilio logs for the `POST .../ivr/{tenant_id}/route` request

### Calls route to ElevenLabs but get "service unavailable"
- The agent may have been deleted from the ElevenLabs workspace — redeploy
- Check billing gate: tenant may be over their monthly cap
- Verify `el_agent_id` in `phone_agents` matches an existing EL agent

### Twilio signature validation fails
- `APP_URL` must match the public URL Twilio uses to call the webhook
- Docker reverse proxy must forward `X-Twilio-Signature` header
- Auth token must match the Twilio account that owns the phone number

### WhatsApp messages fail with "24h session expired"
- Send a template message to re-engage the customer
- Templates must be approved by Meta before use
- Check `messaging_conversations.last_inbound_at` — must be within 24 hours

### SMS/WhatsApp inbound messages not appearing
- Verify the Twilio webhook URL matches `/api/messaging/webhook/{tenant_id}/inbound?t=...`
- Check that SMS/WhatsApp is enabled in the messaging config
- Verify the HMAC token hasn't changed (regenerates if `SECRET_KEY` changes)

### Cost not showing for calls/messages
- EL costs: arrive via post-call webhook — check `call_ended` webhook is registered
- Twilio call costs: fetched async — may take 1-2 minutes after call ends
- Twilio message costs: arrive via status callback — check status webhook URL is set
