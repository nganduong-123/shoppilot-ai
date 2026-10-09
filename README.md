# ShopPilot AI

**Production-ready, auditable multi-tenant sales and support platform for online shops.**

**Live demo:** https://shoppilot-ai-bt8u.onrender.com/<br>
The deployed demo uses PostgreSQL, secure shop accounts and the Groq-hosted
`openai/gpt-oss-120b` model. Owners create an account on the login screen, manage
their own tenant-scoped catalog and connect a Facebook Page from Integrations.

ShopPilot goes beyond an FAQ chatbot: it uses tools to search a tenant-scoped catalog, check live inventory, retrieve store policies, calculate shipping, prepare draft orders and hand complex conversations to a human. Order confirmation is enforced by deterministic application code, so the LLM cannot complete a write action on its own.

> Portfolio project by **Dương Thị Ngân** · Student ID **2A202602808**

![ShopPilot AI omnichannel inbox](docs/images/omnichannel-inbox.png)

## What makes it different

- **Multi-tenant SaaS foundation:** one agent engine, isolated catalog, policies and conversations per shop.
- **Real tool loop:** Groq chooses tools; Python validates and executes them; observations return to the model.
- **Grounded answers:** prices, stock and policy evidence come from business data instead of model memory.
- **Safe write workflow:** draft order → explicit customer confirmation → final inventory check → stock update.
- **Human handoff:** escalates complaints, exceptions and low-confidence cases with conversation context.
- **Unified inbox:** Website and Messenger conversations share one queue, with an AI/human takeover switch.
- **Revenue-rescue queue:** detects buying signals, flags unanswered handoffs, tracks a five-minute SLA and lets staff take or resolve each conversation.
- **Human reply copilot:** summarizes the thread and drafts a grounded reply for staff review; it never sends a customer message automatically.
- **Meta review readiness:** public privacy/terms pages plus a signed user-data deletion callback and status receipt.
- **Secure shop accounts:** scrypt password hashing, HttpOnly sessions and owner/manager/agent tenant boundaries.
- **Team workspaces:** expiring email invitations, role management and last-owner protection for every tenant.
- **Order operations:** staff track confirmed orders through processing, shipping and delivery with carrier codes.
- **SaaS billing:** Stripe Checkout, Customer Portal and signed subscription webhooks, with a safe Free fallback until Stripe is configured.
- **Account lifecycle:** optional email verification and one-time password reset links through Resend.
- **Shared abuse protection:** PostgreSQL-backed limits protect authentication and public chat across instances.
- **Durable delivery:** webhook jobs survive restarts, retry with backoff and expose a dead-letter state.
- **Production observability:** JSON request logs, request IDs, liveness/readiness probes and Prometheus metrics.
- **Human evaluation:** reviewers label conversations as helpful, incorrect or unsafe; metrics aggregate the results.
- **Commerce connectors:** signed webhooks connect shipping quotes and confirmed orders to real providers.
- **Meta OAuth onboarding:** owners authorize on Meta, select a Page and store only an encrypted Page token; ShopPilot never receives a Facebook password.
- **Optional Make bridge:** connects a Messenger Page while direct Meta App access is pending.
- **Embeddable web widget:** add a sales assistant to an existing store with one script tag.
- **Auditable traces:** records every tool, arguments, result, latency and outcome.
- **Resilient fallback:** core flows continue when the LLM provider is unavailable.
- **Evaluation-first:** automated tests plus a separate scenario suite for routing and safety behavior.

![ShopPilot AI integration readiness](docs/images/integrations.png)

## Demo workspaces

| Shop | Vertical | Domain-specific attributes |
|---|---|---|
| MisterBox Men | Men's fashion | Size, color, material, fit |
| Lumi Beauty | Cosmetics | Skin type, ingredients, volume |
| Nova Tech | Electronics | Connectivity, power, warranty |

The same agent engine serves all three workspaces without mixing their data.

## Architecture

```mermaid
flowchart LR
    C[Customer] --> CH[Website / Messenger]
    CH --> IN[Unified inbox]
    IN --> A[FastAPI]
    A --> R[Tenant resolver]
    R --> G[Agent loop]
    G <--> L[Groq LLM]
    G --> T[Validated tools]
    T --> D[(Catalog / Inventory / Orders)]
    T --> P[Policy retrieval]
    T --> H[Human handoff]
    T --> X[(Audit trace)]
    G --> Q{Confirmation?}
    Q -->|Explicit yes| D
```

Detailed design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Tech stack

- Python 3.13, FastAPI and Pydantic
- Groq OpenAI-compatible Chat Completions API
- SQLite locally; PostgreSQL persistence on the Render deployment
- Scrypt authentication, role-based tenant access and encrypted Meta tokens
- Resend account verification/password recovery and single-use hashed tokens
- PostgreSQL durable jobs, shared rate limits and human feedback labels
- Responsive HTML/CSS/JavaScript console
- Pytest, scenario evaluation and GitHub Actions
- Docker and Docker Compose

## Run locally

```powershell
Copy-Item .env.example .env
# Add GROQ_API_KEY to .env
./run.ps1
```

Open:

- App: <http://127.0.0.1:8000>
- OpenAPI: <http://127.0.0.1:8000/docs>

The application still works in deterministic fallback mode if `GROQ_API_KEY` is absent.

For production account recovery, set `EMAIL_PROVIDER=resend`, `EMAIL_FROM`,
`RESEND_API_KEY` and then enable `EMAIL_VERIFICATION_REQUIRED=true`. Shipping and
order systems connect through the signed `SHIPPING_QUOTE_URL` and
`COMMERCE_ORDER_WEBHOOK_URL` bridges documented in the production runbook.

For durable server data, set `DATABASE_URL` to a PostgreSQL connection string. It
takes precedence over `DATABASE_PATH`; local development and tests continue to use
SQLite without extra setup.

To enable account protection, first create the owner at `/login`, then set
`AUTH_REQUIRED=true`. For Facebook self-service onboarding, add
`{PUBLIC_BASE_URL}/api/integrations/meta/callback` to the Meta app's valid OAuth
redirect URIs and configure `TOKEN_ENCRYPTION_KEY`.

### Manual setup

```powershell
py -3.13 -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install -r requirements.txt
.\.venv313\Scripts\python.exe -m uvicorn app.main:app --reload
```

### Docker

```bash
docker compose up --build
```

## Test and evaluate

```powershell
.\.venv313\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv313\Scripts\ruff.exe check app tests scripts
.\.venv313\Scripts\python.exe -m compileall -q app scripts
.\.venv313\Scripts\python.exe -m pytest -q
.\.venv313\Scripts\python.exe scripts\evaluate.py
.\.venv313\Scripts\python.exe scripts\evaluate.py --online
node scripts\e2e_browser.mjs  # requires the app running on port 8000
```

Current deterministic baseline:

- **65 automated tests passed**
- **13/13 browser E2E checks passed** across AI reply, priority inbox, human copilot, takeover, resolution, integrations, team access, billing and order operations.
- **16/16 evaluation scenarios passed**
- **16/16 Groq online scenarios passed** after introducing hybrid routing
- Coverage includes tenant isolation, product grounding, stock guard, explicit confirmation, prompt injection refusal and human handoff.

The 100% scenario result describes only the committed evaluation set; it is not a claim of production accuracy.

## Core API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/shops` | List tenant workspaces |
| `PATCH` | `/api/shops/{slug}` | Update a managed shop's profile, policy and agent voice |
| `GET` | `/api/shops/{slug}/products` | Read tenant-scoped catalog |
| `GET/PATCH` | `/api/shops/{slug}/orders`, `/orders/{id}/fulfillment` | Operate order fulfillment and tracking |
| `GET/POST/PATCH/DELETE` | `/api/shops/{slug}/team/*` | Invite members and manage tenant roles |
| `GET/POST` | `/api/shops/{slug}/billing/*` | Read plans, start Stripe Checkout and open Customer Portal |
| `POST` | `/api/webhooks/stripe` | Verify and apply Stripe subscription events |
| `POST` | `/api/shops/{slug}/chat` | Run the sales agent |
| `POST` | `/api/channels/web/{slug}/messages` | Receive a website-widget message |
| `GET/POST` | `/api/webhooks/meta` | Verify and receive Messenger webhooks |
| `POST` | `/api/bridges/make/messenger/{slug}` | Receive a Messenger message from Make |
| `GET` | `/api/integrations/make/status` | Read Make bridge readiness and its inbound URL |
| `GET` | `/api/shops/{slug}/inbox/conversations` | List unified inbox conversations |
| `GET` | `/api/shops/{slug}/inbox/conversations/{id}/messages` | Read a channel thread |
| `POST` | `/api/shops/{slug}/inbox/conversations/{id}/messages` | Reply as a human agent |
| `POST` | `/api/shops/{slug}/inbox/conversations/{id}/bot` | Switch between AI and human handling |
| `POST` | `/api/shops/{slug}/inbox/conversations/{id}/actions` | Take over, resolve or reopen an inbox conversation |
| `POST` | `/api/shops/{slug}/inbox/conversations/{id}/read` | Mark inbound messages as read |
| `POST` | `/api/shops/{slug}/inbox/conversations/{id}/assist` | Draft a grounded reply for human review without sending it |
| `GET` | `/api/integrations/meta/status` | Read review URLs and configuration readiness without exposing secrets |
| `POST` | `/api/auth/register`, `/api/auth/login`, `/api/auth/logout` | Manage ShopPilot accounts and HttpOnly sessions |
| `POST` | `/api/auth/verify-email/*`, `/api/auth/password-reset/*` | Verify account email and reset a forgotten password |
| `GET` | `/api/shops/{slug}/integrations/meta/connect` | Start Meta OAuth without collecting a Facebook password |
| `GET` | `/api/integrations/meta/callback` | Exchange Meta authorization and load manageable Pages |
| `POST` | `/api/shops/{slug}/integrations/meta/complete` | Encrypt the selected Page token and subscribe its webhook |
| `POST` | `/api/meta/data-deletion` | Verify Meta's signed deletion request and remove user data |
| `GET` | `/api/data-deletion/status/{code}` | Check an anonymous deletion receipt |
| `POST` | `/api/shops/{slug}/products/import` | Import catalog CSV |
| `GET` | `/api/conversations/{id}/trace` | Inspect messages, tools and actions |
| `GET` | `/api/shops/{slug}/metrics` | Read operational demo metrics |
| `POST` | `/api/conversations/{id}/feedback` | Store a human quality/safety label |
| `GET` | `/api/health/live`, `/api/health/ready`, `/metrics` | Operability and Prometheus endpoints |

## Repository map

```text
app/
├── agent.py          # Groq tool loop, state and fallback
├── copilot.py        # Human-assist summaries and reply drafts
├── inbox.py          # Idempotent channel-to-agent orchestration
├── channels/         # Website and Meta Messenger adapters
├── tools.py          # Business tools and confirmation workflow
├── repository.py     # Tenant-scoped data access
├── database.py       # SQLite/PostgreSQL schema and transactions
├── jobs.py           # Durable webhook/order worker with retry
├── billing.py        # Stripe Checkout, portal and signed webhook handling
├── integrations.py   # Signed shipping and commerce bridges
├── email_service.py  # Verification and password recovery delivery
├── observability.py  # Request IDs, security headers, logs and metrics
├── main.py           # FastAPI endpoints
└── static/           # Chat, catalog and observability UI
evals/                # Scenario-based agent evaluation
scripts/evaluate.py   # Reproducible evaluation runner
tests/                # Unit and integration tests
docs/                 # Architecture and interview learning material
```

## Safety boundaries

- The LLM never receives a final-order confirmation tool.
- Draft creation does not decrement stock.
- Product access is scoped by shop before tool execution.
- Prompt injection attempts cannot change prices or expose another tenant.
- The agent escalates complaints and exceptions instead of inventing an answer.

## External activation boundaries

The application code and deployment path are complete for a production release. A new installation must still supply credentials for the services it chooses to use: Groq, Resend, Meta and optional shipping/commerce webhooks. Meta Advanced Access is an external review performed by Meta, not a code change. Until a provider URL is configured, the included catalog and shipping rules remain a safe deterministic fallback rather than pretending an external order was fulfilled.

See [docs/PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md) for release, monitoring, recovery and connector activation steps.

## Embed the website widget

```html
<script
  src="https://YOUR-SHOPPILOT-DOMAIN/static/widget.js"
  data-shop="mint-fashion"
  data-color="#18b99f">
</script>
```

For a local preview, open <http://127.0.0.1:8000/static/widget.html?shop=mint-fashion>.

Messenger setup: [docs/META_SETUP.md](docs/META_SETUP.md).

Fast Messenger bridge through Make: [docs/MAKE_MESSENGER_SETUP.md](docs/MAKE_MESSENGER_SETUP.md).

Public review pages are available at `/privacy`, `/terms` and `/data-deletion`.

## Learn the project

- [Learning guide](docs/LEARNING_GUIDE.md): concepts and suggested code-reading order.
- [Interview guide](docs/INTERVIEW_GUIDE.md): 90-second pitch, technical questions and honest limitations.
- [Production runbook](docs/PRODUCTION_RUNBOOK.md): required configuration, release checks and incident recovery.

## License

MIT © 2026 Dương Thị Ngân
