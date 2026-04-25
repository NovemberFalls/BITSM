# BITSM — AI-native IT helpdesk

> Multi-tenant support ticketing with an agentic RAG assistant, for IT teams.

[![License: BSL 1.1](https://img.shields.io/badge/License-BSL%201.1-blue.svg)](LICENSE)

---

> **License:** [BSL 1.1](LICENSE) — source-available, not open source.
> Self-hosting for your own team is free. Offering BITSM as a SaaS, hosted,
> managed, or white-label service requires a commercial license.
> Contact [leonard@boord-it.com](mailto:leonard@boord-it.com).

---

## Why I built this

Honestly? I got tired.

Tired of combing through poorly-filled-in tickets to produce reports that should
have been automatic. Tired of reminding teams that the priority was wrong, the
category was blank, the field never got populated. Tired of watching managers
burn cycles on data hygiene instead of running their teams. The usual fix — hire
someone to keep the basics in order — is a band-aid that doesn't scale and
doesn't change the underlying problem.

Helpdesk software has stayed roughly the same shape for fifteen years: a form, a
queue, a status, a thread. The grunt work — tagging, routing, categorizing,
escalating, summarizing, closing duplicates, drafting follow-ups — is the
low-hanging fruit AI is actually good at. That's the gap BITSM was built to
close.

So BITSM is **AI-native, highly agentic, and adaptable**:

- **AI-native** — Atlas (the AI engine) isn't a feature bolted on. Auto-engagement,
  routing, custom-field inference, knowledge-base search, and escalation are
  first-class behaviors, not optional toggles.
- **Highly agentic** — Atlas runs a tool-use loop: searches, fetches, evaluates,
  decides, acts. It writes back to tickets, hands off to humans when it should,
  and stays passive when a human is already on it.
- **Adaptable** — multi-tenant from day one. BYOK for every external provider.
  Every tenant configures its own modules, fields, categories, RBAC permissions,
  and workflows.

A major shout-out to **[Ed Donner](https://www.linkedin.com/in/eddonner/)** — his courses on agentic AI and LLM
engineering shaped how I think about tool-use loops, RAG retrieval, and the gap
between "AI demo" and "AI that actually works in production." A lot of what's in
Atlas exists because of patterns I learned from him.

---

## What it does

- **Multi-tenant helpdesk** — tickets, SLAs, comments, attachments, locations,
  and problem categories across fully isolated tenants, each with configurable
  roles and permissions.
- **RAG-powered AI assistant (Atlas)** — three-tier escalation: L1 (Claude Haiku 4.5)
  handles routine requests with a full knowledge-base tool-use loop; L2 (Claude Sonnet 4)
  handles escalations; L3 hands off to a human agent. Cost per resolution is predictable
  and auditable.
- **Agentic tool-use loop** — Atlas does not just retrieve documents. It calls tools:
  KB search, ticket lookup, custom-field writes, and live-agent transfer. It acts until
  the question is answered or it needs a human.
- **Multi-channel intake** — web portal, in-page chat widget, inbound email (Cloudflare
  Email Worker), SMS, WhatsApp, and AI voice agents (Twilio + ElevenLabs).
- **BYOK for every external provider** — tenants supply their own API keys for LLM,
  embeddings, voice, messaging, email, and billing. No vendor lock-in at the tenant
  layer.

---

## Architecture at a glance

| Layer | Tech |
|---|---|
| Backend | Flask 3.x + Gunicorn + Python 3.12 |
| Frontend | React 19 + TypeScript + Zustand + Vite |
| Database | PostgreSQL 16 + pgvector |
| Cache / queue | Redis 7 |
| AI | Claude Haiku 4.5 / Sonnet 4 + Voyage AI embeddings (BYOK) |

The backend is organized as Flask blueprints (auth, tickets, KB, AI, admin,
billing, phone, messaging, automations, sprints, and more). The frontend is a
single-page React app served from `static/app-dist/` and built by Vite. All
staff routes are tenant-slug-prefixed (`/<slug>/tickets`, `/<slug>/kb`, etc.).

---

## Prerequisites

- **Python 3.12**
- **Node 20+**
- **Docker and Docker Compose** (v2)
- A **PostgreSQL 16** instance with the `pgvector` extension installed (the
  provided `docker-compose.yml` provisions one automatically)
- **Redis 7** (the compose file provisions this too)
- API keys for the BYOK providers you plan to use (see `.env.example`)

---

## Quick start — development

```bash
# Clone
git clone https://github.com/NovemberFalls/BITSM.git bitsm
cd bitsm

# Copy and fill the environment file
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY and VOYAGE_API_KEY
# (or OPENAI_API_KEY as the embedding fallback).
# For dev mode without OAuth, set AUTH_ENABLED=false

# Start Postgres and Redis
docker compose up -d postgres redis

# Install backend dependencies
pip install -r requirements.txt

# Run database migrations
bash scripts/migrate.sh

# Build the frontend
cd webapp
npm ci
npm run build
cd ..

# Start the app
python -m flask --app app:create_app run --port 5070 --debug
```

Open `http://localhost:5070`. With `AUTH_ENABLED=false`, the app auto-provisions
a "Dev User" and "Dev Tenant" on first request — no OAuth credentials needed for
local development.

---

## Production deployment

See `.env.example` for the full list of environment variables and
`scripts/azure-setup.sh` for an example infrastructure setup script.

The repository includes `.github/workflows/deploy-prod.yml` as a reference
GitHub Actions workflow. It runs migrations and restarts the Docker Compose
stack on every push to `main`. Forks must adapt this workflow to their own
infrastructure — it references Azure-specific secrets and a specific VM host
that will not exist in your environment.

For a self-hosted production stack, the minimum components are:

- A Linux host running Docker Compose with the `bitsm` stack
- A reverse proxy (nginx, Caddy, or Cloudflare Tunnel) terminating TLS
- PostgreSQL 16 + pgvector and Redis 7 (containers or managed services)
- A process manager or systemd unit to ensure the stack restarts on reboot

---

## BYOK providers

BITSM supports bring-your-own-key for all seven external providers. Tenants
configure their keys through the Admin panel (Admin > Billing > API Keys).
Platform defaults fall back to environment variables when no tenant key is
present.

| Provider | Purpose | Key environment variable(s) |
|---|---|---|
| Anthropic | Claude Haiku + Sonnet (L1/L2 AI) | `ANTHROPIC_API_KEY` |
| OpenAI | Fallback LLM + embeddings | `OPENAI_API_KEY` |
| Voyage AI | Primary RAG embeddings | `VOYAGE_API_KEY` |
| Resend | Transactional email | `RESEND_API_KEY` |
| Twilio | SMS, WhatsApp, Voice | `PLATFORM_TWILIO_ACCOUNT_SID`, `PLATFORM_TWILIO_AUTH_TOKEN`, `PLATFORM_TWILIO_PHONE_NUMBER` |
| ElevenLabs | AI voice agents | `PLATFORM_ELEVENLABS_API_KEY` |
| Stripe | Billing and subscriptions | `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, price IDs |

---

## Further reading

- [Communications reference](docs/communications-reference.md) — Phone, SMS, WhatsApp, and
  inbound email setup and configuration
- [Migrations reference](docs/migrations-reference.md) — Full database schema evolution
  history (72+ applied migrations)
- [Knowledge pipeline architecture](docs/knowledge-pipeline-architecture.md) — How documents
  are ingested, chunked, and embedded for RAG retrieval
- [Inbound email setup](docs/inbound-email-setup.md) — Cloudflare Email Worker configuration
  for inbound ticket creation via email

---

## Tests

```bash
python -m pytest tests/ -v
```

The test suite covers core API routes, permission resolution, SLA logic, and
queue pipeline behavior. Tests require a running PostgreSQL instance; see
`.env.example` for the database connection variables the test runner expects.

---

## Contributing

This is a source-available project under BSL 1.1. We welcome bug reports and
pull requests, but please read [LICENSE](LICENSE) before contributing — your
contributions are subject to the same license terms. For feature discussions,
open a GitHub issue before starting work so the approach can be agreed on before
you invest time writing code.

---

## Security

Report vulnerabilities per [SECURITY.md](SECURITY.md).
Security contact: [leonard@boord-it.com](mailto:leonard@boord-it.com).

---

## License

Business Source License 1.1. See [LICENSE](LICENSE) for the full text.

Commercial licensing inquiries: [leonard@boord-it.com](mailto:leonard@boord-it.com).

Copyright Boord Information Technology Services, LLC.
