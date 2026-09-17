# ShopPilot AI

**Auditable multi-tenant sales and support agent for online shops.**

ShopPilot goes beyond an FAQ chatbot: it uses tools to search a tenant-scoped catalog, check live inventory, retrieve store policies, calculate shipping, prepare draft orders and hand complex conversations to a human. Order confirmation is enforced by deterministic application code, so the LLM cannot complete a write action on its own.

> Portfolio project by **Dương Thị Ngân** · Student ID **2A202602808**

![ShopPilot AI dashboard](docs/images/dashboard.png)

## What makes it different

- **Multi-tenant SaaS foundation:** one agent engine, isolated catalog, policies and conversations per shop.
- **Real tool loop:** Groq chooses tools; Python validates and executes them; observations return to the model.
- **Grounded answers:** prices, stock and policy evidence come from business data instead of model memory.
- **Safe write workflow:** draft order → explicit customer confirmation → final inventory check → stock update.
- **Human handoff:** escalates complaints, exceptions and low-confidence cases with conversation context.
- **Auditable traces:** records every tool, arguments, result, latency and outcome.
- **Resilient fallback:** core flows continue when the LLM provider is unavailable.
- **Evaluation-first:** automated tests plus a separate scenario suite for routing and safety behavior.

## Demo workspaces

| Shop | Vertical | Domain-specific attributes |
|---|---|---|
| Mint Fashion | Fashion | Size, color, material, fit |
| Lumi Beauty | Cosmetics | Skin type, ingredients, volume |
| Nova Tech | Electronics | Connectivity, power, warranty |

The same agent engine serves all three workspaces without mixing their data.

## Architecture

```mermaid
flowchart LR
    C[Customer] --> W[Web console]
    W --> A[FastAPI]
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
- SQLite for the portfolio MVP
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
.\.venv313\Scripts\python.exe -m pytest -q
.\.venv313\Scripts\python.exe scripts\evaluate.py
.\.venv313\Scripts\python.exe scripts\evaluate.py --online
```

Current deterministic baseline:

- **11 automated tests passed**
- **16/16 evaluation scenarios passed**
- **16/16 Groq online scenarios passed** after introducing hybrid routing
- Coverage includes tenant isolation, product grounding, stock guard, explicit confirmation, prompt injection refusal and human handoff.

The 100% scenario result describes only the committed evaluation set; it is not a claim of production accuracy.

## Core API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/shops` | List tenant workspaces |
| `GET` | `/api/shops/{slug}/products` | Read tenant-scoped catalog |
| `POST` | `/api/shops/{slug}/chat` | Run the sales agent |
| `POST` | `/api/shops/{slug}/products/import` | Import catalog CSV |
| `GET` | `/api/conversations/{id}/trace` | Inspect messages, tools and actions |
| `GET` | `/api/shops/{slug}/metrics` | Read operational demo metrics |

## Repository map

```text
app/
├── agent.py          # Groq tool loop, state and fallback
├── tools.py          # Business tools and confirmation workflow
├── repository.py     # Tenant-scoped data access
├── database.py       # SQLite schema and transactions
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

## Current limitations

This repository is a portfolio MVP. Catalog, shipping rules and order fulfillment are simulated. A production version would add PostgreSQL, authentication and RBAC, encrypted customer data, rate limiting, background jobs, real commerce/transport adapters, online evaluation with human labels and production monitoring.

## Learn the project

- [Learning guide](docs/LEARNING_GUIDE.md): concepts and suggested code-reading order.
- [Interview guide](docs/INTERVIEW_GUIDE.md): 90-second pitch, technical questions and honest limitations.

## License

MIT © 2026 Dương Thị Ngân
