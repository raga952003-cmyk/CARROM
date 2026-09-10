# Razorpay: test → live

How to move this project's Razorpay integration from test keys to live keys, and
what has to be true before that switch is safe.

**Status as of 2026-09-09: the integration is built and tested; it is not
switched on.** Entry fees are collected end to end -- order creation, signature
verification, webhook, the payments ledger and the confirm-on-payment flow all
exist and are covered by `backend/tests/offline/test_payments.py` (89
assertions). What is missing on this machine is credentials and a migration:
`RAZORPAY_KEY_SECRET` is empty, `RAZORPAY_WEBHOOK_SECRET` is unset, and
`db/migrations/015_payments.sql` has not been applied.

`/api/health` says which of those are outstanding. With none of them done it
reports:

```json
"pending_migrations": ["015_payments"],
"payments": {"configured": false, "mode": "off"}
```

Part 1 is what the code already does and what you still have to switch on.
Part 2 is the cutover to live keys.

---

## The one thing to understand first

Razorpay has **no mode switch**. There is no `RAZORPAY_MODE=live` setting and no
flag in the SDK. The environment is carried by the key pair itself:

| Key prefix | What it touches |
|---|---|
| `rzp_test_…` | Test mode. Fake cards, no money moves. |
| `rzp_live_…` | Live mode. Real cards, real money, real refunds. |

"Going live" *is* replacing one pair with the other. Everything else in this doc
exists because that swap is silent — nothing in the code looks different
afterwards, and a mistake surfaces as a real customer being charged, or a real
payment accepted without verification.

Two consequences worth internalising:

- **Test and live are separate accounts with separate data.** Orders, payments,
  customers, webhook subscriptions and settlement history do not carry over. A
  webhook registered in test mode does not exist in live mode.
- **The two secrets are different.** A live `key_id` paired with a test
  `key_secret` fails authentication, which is the good outcome. The dangerous
  pairing is a live `key_id` in the browser with a backend still verifying
  signatures against the test secret.

---

## Part 1 — Switching it on in test mode

Do all of this against **test** keys first. Test mode is free, and every step
below is cheaper to get wrong there.

### 1.1 Apply the migration

`backend/db/migrations/015_payments.sql`, pasted into the Supabase SQL editor.
There is no direct Postgres connection configured in this project, so no script
applies it for you. It prints a `RAISE NOTICE` on success, and it is safe to
re-run.

It creates the `payments` ledger and adds `registrations.fee_paise`. Until it is
applied, `/api/health` reports `015_payments` pending, entries are still taken
(without a fee snapshot), and opening an order fails.

### 1.2 Set the test credentials

In `backend/.env`:

```
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=<the test secret>
RAZORPAY_WEBHOOK_SECRET=<generated when you create the test webhook, 1.3>
```

`RAZORPAY_KEY_ID` is already there. The other two are not.

The server treats a `key_id` with no `key_secret` as **not configured** — it
will not hand the browser a key it cannot later verify a payment against.
`/api/payments/config` reports `enabled: false` and the registration form falls
back to "Payable at venue", which is what the app did before this existed.

### 1.3 Create the test webhook

Dashboard → Settings → Webhooks, with the mode toggle on **Test**.

- URL: `https://<your host>/api/payments/webhook`
- Events: `payment.captured` and `payment.failed`
- Secret: generate one, and set it as `RAZORPAY_WEBHOOK_SECRET`

The endpoint must be reachable from the internet, so a local run needs a tunnel
(`ngrok`, `cloudflared`). Without the secret set the endpoint answers 503 to
everything — it refuses to accept an unverified delivery rather than trusting
the caller.

### 1.4 Confirm the wiring

`GET /api/health` should report:

```json
"pending_migrations": [],
"payments": {"provider": "razorpay", "configured": true, "mode": "test",
             "webhook": "configured"}
```

`mode` comes from the key prefix, which is the only thing that decides test or
live. If it says `live` on a machine you meant to keep in test, stop.

---

## What the code already does

Recorded here because these are the properties you would otherwise have to
re-derive before trusting a cutover. All are covered by
`backend/tests/offline/test_payments.py`.

**The amount is the server's.** `/api/payments/registrations/{id}/order` reads
the fee from the registration and never from the request body. The fee is
snapshotted onto the entry (`fee_paise`) when it is made, so an organiser
raising the fee mid-window does not change what an already-entered player owes.
Amounts are integer paise throughout; `rupees_to_paise` rounds rather than
truncating, because `int(19.99 * 100)` is 1998.

**Callbacks are verified.** `verify_payment_signature` HMACs
`order_id|payment_id` with the key secret and compares with
`hmac.compare_digest`. An unverified callback is refused and audited. The order
id is looked up against a row this server created, so a signature over an order
we never opened gets nowhere.

**A verified signature is not taken as proof of the amount.** The signature
covers two ids and nothing else, so `_settle_payment` fetches the payment from
Razorpay and refuses it unless the order matches and the amount is exactly what
was recorded. Without that, one order's payment can be presented against
another.

**The webhook is the fallback, and it is idempotent.** `payment.captured` and
`payment.failed` are handled; the signature is checked against the **raw request
bytes** with the webhook secret. `razorpay_payment_id` is UNIQUE, and settling
is a no-op once a payment is paid — so a redelivery, a callback and webhook for
the same payment, or a double-tapped Pay button all confirm one entry once, and
notify once.

**The entry is saved before checkout opens.** A player who abandons payment has
a real pending registration the organiser can see, chase or waive, instead of
nothing. Paying sets `payment_status='paid'` and `status='approved'` together —
that is the "pay, then it is confirmed" rule.

**The key_secret never reaches the browser.** The `key_id` is served from
`/api/payments/config` rather than built into the bundle, so test→live is a
server environment change with no frontend rebuild.

### One consequence to be deliberate about

Payment auto-approves the entry. The organiser's `/registrations/{id}/reject`
still works, but **rejecting a paid entry does not refund it** — nothing here
refunds automatically. A refund is a Dashboard action. If you would rather vet
entries before taking money, that is a different flow and this is not it.

### Exercise the failure paths before going live

Test mode is free; use it. Confirm you handle:

- A card that fails (Razorpay's test cards include failure cases).
- Closing the checkout modal — the entry should be waiting, unpaid, with a Pay
  button.
- A duplicate webhook for the same `payment_id`.
- A forged callback — must be rejected.
- A webhook arriving *before* the browser callback.

---

## Part 2 — The cutover

### 2.1 Complete KYC and get live mode activated

Live keys do not exist until Razorpay approves the account: business details,
PAN, bank account, and category-dependent documents. This takes **days, not
minutes**, and is the step that ruins deadlines. Start it early. Until
activation completes, live-mode key generation is unavailable in the Dashboard.

### 2.2 Generate the live key pair

Razorpay Dashboard → switch the mode toggle to **Live** → Account & Settings →
API Keys → Generate Key.

**The `key_secret` is shown exactly once.** Copy it straight into your secret
store. If you lose it you must regenerate, which invalidates the previous pair.

### 2.3 Put the live keys where the deployed server reads them

Set them in the hosting platform's secret configuration:

```
RAZORPAY_KEY_ID=rzp_live_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=<the secret shown once>
RAZORPAY_WEBHOOK_SECRET=<set when creating the live webhook, 2.4>
```

Do not put live keys in `backend/.env`. That file is gitignored here (verified),
but it is a developer-machine file — laptops get backed up, shared and stolen.
Live credentials belong in the platform secret store, injected at runtime.

Keep the test pair in local `.env`, so development never touches real money.
That is the point of the split: **local stays test forever, only deployed goes
live.**

### 2.4 Recreate the webhook in live mode

Webhooks do not carry across modes. In the Dashboard, with the toggle on
**Live**: Settings → Webhooks → Add New Webhook.

- URL: your production HTTPS endpoint. It must be publicly reachable — a
  localhost or tunnel URL that worked in test will not do.
- Events: the same set you subscribed to in test.
- Secret: generate one and set it as `RAZORPAY_WEBHOOK_SECRET`.

A live integration with no live webhook looks fine right up until a customer
closes their tab mid-payment — at which point that payment is invisible to you.

### 2.5 Check what else assumes test

```bash
grep -rniE "rzp_test|rzp_live" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.json" . | grep -v node_modules
```

Comments and docs will match. What must **not** match is any key literal in
committed code or in a `VITE_*` variable — keys belong in `.env` locally and in
the platform secret store in production, and nowhere else.

Also confirm:

- No `rzp_test_` string is baked into the built frontend bundle.
- `CORS_ORIGINS` includes the production origin — callbacks come from the real
  domain.
- `ENV` is not `development` in production, so CORS is actually enforced.

### 2.6 Verify with one real payment, then refund it

This is not optional and cannot be simulated. Test mode will not catch a wrong
live secret, a webhook pointed at the wrong URL, or a paise/rupee error.

1. Make a **small real payment** (₹1–₹5) with a real card, on the production
   site.
2. Confirm it appears in the Dashboard in **Live** mode.
3. Confirm the webhook fired and your payments table recorded it as verified.
4. Confirm the thing being paid for actually unlocked.
5. **Refund it** from the Dashboard, and confirm your side handles the refund.

If any step fails you have found it for ₹5, instead of hearing it from a
customer.

### 2.7 Watch the first real transactions

For the first day, check that Dashboard payment counts match your payments
table. A silent mismatch is almost always a webhook that is not being delivered,
or not being verified.

---

## Rollback

If something is wrong after cutover, **stop taking payments** — do not switch
back to test keys. Test keys in production give customers a checkout that cannot
charge them, which looks like a successful payment that never arrives.

1. Put the payment path behind a flag, or return a clear "payments temporarily
   unavailable" from the order endpoint.
2. Leave the webhook live, so in-flight payments are still recorded.
3. Fix, then re-verify with 2.6.

If a live secret is exposed, regenerate the pair in the Dashboard immediately.
The old pair stops working the moment you do, so deploy the new secret in the
same window.

---

## Checklist

Already true in the code — verify rather than build:

- [x] Orders created server-side; amount never trusted from the browser
- [x] Amounts in integer paise, rounded not truncated
- [x] Callback signature verified with `hmac.compare_digest`
- [x] Amount and order re-checked against Razorpay after the signature passes
- [x] Webhook signature-verified over the raw body, idempotent on `payment_id`
- [x] Payments persisted with `payment_id` and verification status
- [x] `key_secret` absent from the frontend bundle and from any `VITE_*` var

Yours to do, in order:

- [ ] `015_payments.sql` applied; `/api/health` shows nothing pending
- [ ] Test keys set, `/api/health` reports `mode: test`, `webhook: configured`
- [ ] Test webhook created and reaching the deployment
- [ ] Failure paths exercised in test mode (list above)
- [ ] Razorpay KYC approved, live mode active
- [ ] Live keys in the platform secret store, **not** in `.env`
- [ ] Live webhook created, its own `RAZORPAY_WEBHOOK_SECRET` set
- [ ] `CORS_ORIGINS` covers the production origin; `ENV` is not `development`
- [ ] `/api/health` on production reports `mode: live`
- [ ] One real payment made, recorded, and refunded
