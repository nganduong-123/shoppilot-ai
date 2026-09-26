# Messenger pilot through Make

This optional bridge provides a fast pilot while direct Meta Developer access is pending. A shop owner never shares a Facebook password with ShopPilot. They authorize Make on Facebook's own screen and select the Page they manage.

## Environment

```env
PUBLIC_BASE_URL=https://your-public-domain.example
MAKE_BRIDGE_SECRET=a-long-random-value
MAKE_MESSENGER_OUTBOUND_WEBHOOK_URL=https://hook.make.com/your-outbound-hook
```

Restart ShopPilot and call `GET /api/integrations/make/status`. The response exposes the inbound URL and readiness flags, but never returns either secret.

## Scenario 1: customer message to ShopPilot

1. Add **Facebook Messenger > Watch Messages**.
2. Add **HTTP > Make a request**, use `POST` and the ShopPilot `inbound_url`.
3. Add header `X-ShopPilot-Bridge-Key` with `MAKE_BRIDGE_SECRET`.
4. Send this JSON:

```json
{
  "event_id": "Messenger message ID",
  "sender_id": "Messenger sender PSID",
  "page_id": "Facebook Page ID",
  "customer_name": "Optional display name",
  "message": "Incoming message text"
}
```

5. Add a filter where `replied` equals `true`.
6. Add **Facebook Messenger > Send a Message** and map `recipient_id` and `reply` from the HTTP response.

ShopPilot deduplicates the event, stores the thread in Unified Inbox, runs the grounded sales agent and returns the reply to the scenario.

## Scenario 2: human reply from Unified Inbox

1. Create a Make **Custom webhook** and copy its URL into `MAKE_MESSENGER_OUTBOUND_WEBHOOK_URL`.
2. Add **Facebook Messenger > Send a Message** after the webhook.
3. Map webhook `recipient_id` to the recipient and `message` to the body.

When a staff member takes over, ShopPilot sends their reviewed reply through this scenario. Keep the custom webhook URL secret.

## Production path

The Make bridge is suitable for a pilot and portfolio demo. A commercial multi-shop release should add first-party Meta OAuth or an approved messaging provider, encrypted per-shop credentials, rate limits, durable retries and delivery monitoring.
