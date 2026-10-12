# ShopPilot AI production runbook

## Required release configuration

Set these values in the deployment secret store, never in Git:

- `DATABASE_URL`, `TOKEN_ENCRYPTION_KEY`, `GROQ_API_KEY`.
- `PUBLIC_BASE_URL`, `APP_ENV=production`, `AUTH_REQUIRED=true`.
- `SEED_DEMO_DATA=false`; production startup does not add or rewrite demonstration shops.
- Meta App ID, App Secret and webhook verify token when Messenger is enabled.
- `EMAIL_PROVIDER=resend`, `EMAIL_FROM`, `RESEND_API_KEY`, then `EMAIL_VERIFICATION_REQUIRED=true` when account email is enabled.
- `SHIPPING_QUOTE_URL` and/or `COMMERCE_ORDER_WEBHOOK_URL` plus `INTEGRATION_WEBHOOK_SECRET` when external fulfillment is enabled.
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO` and `STRIPE_PRICE_BUSINESS` when paid plans are enabled. Register `/api/webhooks/stripe` for Checkout and subscription events.

Provider credentials are external activation inputs. The server refuses mandatory email verification when no email provider is configured and never returns a secret through its status APIs.

Production startup refuses missing persistent PostgreSQL, disabled account protection,
an HTTP/non-origin public URL, a missing/short encryption key, disabled rate limits,
or mandatory email verification without a sender. Error messages name only configuration
keys, never their values. Existing deployment secrets are not changed by the code.

The production container disables raw Uvicorn access logging and uses sanitized JSON
route logs instead; OAuth codes, reset links and webhook verify tokens must not be
written as raw URLs. A custom server command must also use `--no-access-log`.

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
5. When billing is enabled, complete a Stripe test-mode checkout, open Customer Portal and confirm the signed webhook updates the workspace plan.

CI runs the complete tests on SQLite and PostgreSQL 16, scans runtime dependencies
with pip-audit and builds/runs the Docker image through the browser suite. Browser
tests create disposable local users/products, not real customers or Facebook posts.

## Infrastructure gate before public launch

The current Render Free deployment is a pilot, not an always-on customer service.
Free web instances sleep after 15 minutes without traffic and Free PostgreSQL expires
after 30 days. See https://render.com/docs/free. Keep the provided Render HTTPS
hostname if a custom domain is not needed; purchasing a domain is not required.

Before inviting actual customers:

1. Select an always-on server and a non-expiring PostgreSQL plan. This requires the
   owner's approval for any paid subscription; do not upgrade automatically.
2. Enable daily database backups, retention and an alert for failed backups. Restore
   a backup into a separate non-production database and verify tenants, messages,
   orders and encrypted Page connections. A backup is not proven until a restore is tested.
3. Configure Resend and a verified sender, exercise reset/verification delivery, then
   enable email verification before opening unrestricted signup. A temporary pilot
   can set `REGISTRATION_ENABLED=false` and keep existing accounts working.
4. Complete Meta review/Advanced Access where required; test a genuine inbound and
   outbound message for an authorized Page. A green configuration badge is not a
   delivery receipt. Keep tokens and customer screenshots out of public evidence.
5. Exercise provider timeout/retry, a restart and the expected simultaneous-user load
   in a staging environment. This release's tests are not a load/SLA guarantee.
6. Do not enable paid plans until a real Stripe test-mode checkout, cancellation,
   portal plan change and duplicate/out-of-order webhook delivery have been exercised
   with the configured prices. Mocked provider tests do not prove payment activation.
   Test repeated Checkout clicks as well; UI prevention alone does not eliminate
   independently created parallel Checkout sessions.

## Platform operator console

`/admin` and every `/api/admin/*` route require an explicit platform administrator
grant. Owning a shop, public signup or sending a role field does not provide this
permission. The console lists account/shop metadata and aggregate usage, not
customer message bodies, password hashes or provider credentials. It can suspend
non-admin accounts (revoking all their sessions), restore access, grant a 1–30 day
Pro/Business trial, and requeue supported failed jobs. Each mutation requires a
reason and writes an audit event in the same transaction. Admin accounts cannot
be suspended through this UI.

Bootstrap only a verified, existing active operator through trusted database access:

```powershell
python -m scripts.grant_platform_admin --email operator@example.com --reason "Verified platform owner" --grant-platform-admin --database-url-stdin
```

Supply the DSN through standard input or the operator environment, never through
CLI arguments, Git, screenshots or logs. Keep grants restricted to trusted platform
operators; no public grant endpoint exists. Test the console with the disposable
`scripts.seed_e2e_admin` fixture only on an offline local development test database.

## AI usage and fallback

Free is limited to 500 new assistant/copilot turns per UTC calendar month, Pro to
10,000 and Business to 50,000. One turn can include several model tool-loop calls;
this is not a token or dollar billing meter. The database reserves a turn atomically
across processes. Retries of a stored Messenger reply reuse it without reserving a
second turn. Deterministic confirmation/cancellation of an existing draft remains
available after quota exhaustion, and human replies do not use this budget.

Exhaustion pauses AI for that channel conversation and hands off to staff. At a new
month or after a valid plan upgrade, staff can re-enable AI in the inbox. Canceled,
unpaid or unknown plans use Free limits, except for a valid operator trial. An
unexpired operator trial can upgrade but never downgrade a paid plan. Trials end
automatically at their stored UTC expiry.

Free permits 1 member, Pro 5 and Business 20. Members and unexpired pending invites
both reserve seats; replacing the same pending invitation does not reserve twice.
Shop-scoped transaction locks protect concurrent invitations, acceptance and
last-owner changes. On downgrade, existing members are not silently removed, but
new invitations/acceptance are blocked when the new limit is reached.

Signed subscription events fetch Stripe's current resource rather than trusting a
potentially stale event snapshot. Receipt IDs deduplicate delivery, refresh revisions
fence concurrent HTTP snapshots, and subscription bindings prevent an older
subscription from replacing a newer one. Provider failures return a retryable error;
no successful receipt is stored until the update commits. See
https://docs.stripe.com/webhooks and https://docs.stripe.com/api/subscriptions/retrieve.

Model generation has a 30-second total turn budget and a grounded fallback. Jobs
have a 90-second processing timeout and shutdown waits at most 10 seconds before
cancelling work; the durable running job is recoverable on a subsequent worker tick.
Readiness returns 503 for failed database access or a dead worker, and reports
`degraded=true` for dead-letter jobs without forcing endless service restarts.

## Queue recovery

Messenger and confirmed-order deliveries are persisted in `jobs`. A worker retries failures with backoff. A job that exhausts its attempts moves to `failed` and remains visible in health/metrics. Diagnose the provider/credential first, then use the platform console's audited retry; do not edit customer messages or order payloads to hide the failure.

Running jobs older than five minutes are checked every minute and returned to
`queued`. Polling failures do not permanently kill the worker. Provider event IDs
and order IDs deduplicate jobs. A generated channel reply is persisted before
delivery, so delivery retries do not repeat the AI turn or order action.

Delivery is **at least once**, not exactly once: if Meta accepted a message but the
response was lost, resending may duplicate the customer-visible reply. A crash
before persisting the generated reply can also regenerate a model turn. Order
confirmation is separately protected by an atomic draft claim, conditional stock
updates and a transaction containing the commerce outbox. External commerce
receivers must deduplicate by `order_id`.

## Account recovery

Verification and password-reset tokens are random, hashed at rest, single-use and time-limited. Password reset revokes every existing session. The request endpoint always returns the same response for known and unknown addresses to prevent account enumeration.

If email delivery is unavailable, keep `EMAIL_VERIFICATION_REQUIRED=false` until the provider is restored; do not enable a mandatory flow without a configured sender.

Password reset and verification requests return `503` when account email is not
configured, for every email address. They must not display a "sent" message for
these failures. Once a provider is configured, `202` means the request was accepted,
not that delivery is confirmed. Provider failures are logged without credentials;
the response remains identical for known and unknown accounts, even when delivery
fails. `/api/auth/me` exposes the non-secret
`password_reset_available` capability. Registration with required verification
preserves the new account when delivery fails and supports resending verification.

For an owner whose identity and shop ownership have been verified through the
operator's trusted management channel, an operator with production database access
can issue a 15-minute, single-use reset link without email:

```powershell
python -m scripts.recover_account --email owner@example.com --expected-shop shop-slug --base-url https://your-production-host --reason "Verified owner support request" --issue-reset-link
```

Supply `DATABASE_URL` in the operator environment (or through standard input with
`--database-url-stdin`), never as a command-line argument or a committed file.
The command checks the existing active account's Owner membership. It does not
create accounts, alter roles or reveal the old password. The reset link is secret;
keep it out of logs and screenshots. Completing it uses the normal password-reset
endpoint and revokes previous sessions. Record the operator's audit event from
stderr in the incident record.

## Connector contract

Shipping and commerce payloads are canonical JSON. Verify `X-ShopPilot-Signature` as `sha256=<HMAC-SHA256(raw_body, INTEGRATION_WEBHOOK_SECRET)>` before accepting a request. Return a 2xx response only after the downstream system durably accepts the event.

Shipping quote responses must include non-negative integer `fee`; `eta` and `provider` are optional. Confirmed orders are sent only after ShopPilot's deterministic confirmation guard checks inventory and commits the order.

## Incident checklist

- Elevated 5xx: correlate JSON logs by `X-Request-ID`, inspect provider errors and database health.
- Growing queue: inspect `shoppilot_jobs{status="failed"}` and provider credentials/rate limits.
- Suspected token leak: revoke at the provider, rotate the deployment secret and reconnect affected Pages.
- Suspicious login traffic: inspect 429 rate, retain database-backed quotas and rotate sessions after a credential incident.
- Bad AI response: label the conversation `incorrect` or `unsafe`, preserve trace evidence and add the case to held-out evaluation before changing routing or prompts.
