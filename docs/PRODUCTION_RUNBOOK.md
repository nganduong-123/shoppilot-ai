# ShopPilot AI production runbook

## Required release configuration

Set these values in the deployment secret store, never in Git:

- `DATABASE_URL`, `TOKEN_ENCRYPTION_KEY`, `GROQ_API_KEY`.
- `PUBLIC_BASE_URL`, `APP_ENV=production`, `AUTH_REQUIRED=true`.
- Meta App ID, App Secret and webhook verify token when Messenger is enabled.
- `EMAIL_PROVIDER=resend`, `EMAIL_FROM`, `RESEND_API_KEY`, then `EMAIL_VERIFICATION_REQUIRED=true` when account email is enabled.
- `SHIPPING_QUOTE_URL` and/or `COMMERCE_ORDER_WEBHOOK_URL` plus `INTEGRATION_WEBHOOK_SECRET` when external fulfillment is enabled.

Provider credentials are external activation inputs. The server refuses mandatory email verification when no email provider is configured and never returns a secret through its status APIs.

## Release gate

```powershell
.\.venv313\Scripts\python.exe -m pytest -q
.\.venv313\Scripts\python.exe scripts\evaluate.py
node scripts\e2e_browser.mjs
```

The release is ready only when unit/integration tests, evaluation and browser E2E all pass, `git diff --check` is clean and tracked-source secret scanning returns no provider key.

After deploy, verify:

1. `/api/health/live` returns `status=ok`.
2. `/api/health/ready` returns `ready=true`, PostgreSQL storage and no growing failed-job count.
3. `/metrics` is collected by the monitoring system.
4. Register/login, widget chat, human takeover and one provider webhook are exercised in the target environment.

## Queue recovery

Messenger and confirmed-order deliveries are persisted in `jobs`. A worker retries failures with backoff. A job that exhausts its attempts moves to `failed` and remains visible in health/metrics. Diagnose the provider/credential first, then create a fresh delivery event or requeue the job through an approved operations procedure; do not edit customer messages or order payloads to hide the failure.

Running jobs older than five minutes are automatically returned to `queued` on worker startup. Provider event IDs and order IDs are dedupe keys, so a retry cannot create two replies or two fulfillment events.

## Account recovery

Verification and password-reset tokens are random, hashed at rest, single-use and time-limited. Password reset revokes every existing session. The request endpoint always returns the same response for known and unknown addresses to prevent account enumeration.

If email delivery is unavailable, keep `EMAIL_VERIFICATION_REQUIRED=false` until the provider is restored; do not enable a mandatory flow without a configured sender.

## Connector contract

Shipping and commerce payloads are canonical JSON. Verify `X-ShopPilot-Signature` as `sha256=<HMAC-SHA256(raw_body, INTEGRATION_WEBHOOK_SECRET)>` before accepting a request. Return a 2xx response only after the downstream system durably accepts the event.

Shipping quote responses must include non-negative integer `fee`; `eta` and `provider` are optional. Confirmed orders are sent only after ShopPilot's deterministic confirmation guard checks inventory and commits the order.

## Incident checklist

- Elevated 5xx: correlate JSON logs by `X-Request-ID`, inspect provider errors and database health.
- Growing queue: inspect `shoppilot_jobs{status="failed"}` and provider credentials/rate limits.
- Suspected token leak: revoke at the provider, rotate the deployment secret and reconnect affected Pages.
- Suspicious login traffic: inspect 429 rate, retain database-backed quotas and rotate sessions after a credential incident.
- Bad AI response: label the conversation `incorrect` or `unsafe`, preserve trace evidence and add the case to held-out evaluation before changing routing or prompts.
