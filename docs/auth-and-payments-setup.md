# Authentication and payment setup

Apply database migrations in order: `017_secure_data_api.sql`, `018_duplicate_charge_ledger.sql`, `019_atomic_draw_and_schedule.sql`, `020_gpay_payment_proofs.sql`, `021_atomic_match_delete.sql`, then `022_auto_approve_settled_registrations.sql`, after `016` and before deploying the updated API. Migration 017 limits direct Data API reads to safe rows and revokes browser writes, including `TRUNCATE`. Migration 018 adds a ledger status for captured duplicate charges awaiting a refund and preserves those records against deletion. Migration 019 creates service-role-only, atomic draw replacement and schedule update RPCs. Fixture generation and scheduling will fail until 019 is installed. Migration 020 creates the private `payment-proofs` Storage bucket, the proof ledger, and the atomic review RPC; GPay proof upload and review will fail until it is installed. Migration 021 creates atomic match deletion, a global unique index for normalized UPI/bank references, and the atomic transition to the next game after a score. Match deletion and official best-of-three scoring require it. Migration 022 makes a verified payment or organiser waiver approve its registration in the same database update and reconciles older paid entries left pending.

Before applying 021, inspect duplicate existing UPI or bank references in the Supabase SQL editor. Reconcile real duplicate payments and refunds rather than dropping this constraint:

```sql
SELECT regexp_replace(upper(notes->>'reference'), '[^A-Z0-9]', '', 'g') AS reference,
       count(*) AS uses, array_agg(id) AS payment_ids
FROM public.payments
WHERE method IN ('upi', 'bank_transfer', 'gpay_upi')
  AND status IN ('paid', 'refunded', 'refund_due')
  AND notes->>'reference' IS NOT NULL
GROUP BY 1 HAVING count(*) > 1;
```

In Supabase Auth, enable **Confirm email**, set the application URL and allowed redirect URLs to the deployed frontend origin, and configure SMTP so confirmation and recovery messages can be delivered. Public signup creates player accounts only. Create organizer accounts through a trusted admin process that sets `app_metadata.role = admin`; user metadata is never a source of organizer role.

Use a recovery email template that sends the one-time token hash to the frontend route:

```html
<a href="{{ .RedirectTo }}#/reset-password?token_hash={{ .TokenHash }}">Reset password</a>
```

The API sends `.RedirectTo` as the frontend root. A Supabase default recovery link containing `access_token` is intentionally rejected by the reset endpoint. The token hash is verified as a recovery OTP before any password update.

Configure the Razorpay webhook and secret as described by the existing deployment guide. Reconcile `payments.status = 'refund_due'` with the Razorpay dashboard and issue refunds there. Organizer desk payments require a method and receipt reference; waivers require a reason. Both are audited. Verify a real payment and refund in the provider sandbox before accepting production registrations.

For a GPay/UPI option, set each tournament's GPay number (a 10-digit Indian phone) or UPI ID. The player submits the exact transaction reference and a JPG, PNG, WebP, or PDF receipt (5 MiB maximum). The backend checks file type and SHA-256, uploads to the private bucket with its service key, then inserts a `payment_proofs` row with a snapshot of the payee account. A repeated reference or identical file is rejected globally, including after a prior rejection; the normalized reference is also checked against desk-recorded payments. If insertion fails, the newly uploaded object is removed. The organiser views the receipt through a short-lived signed URL, checks the actual credit and amount in their bank or UPI account, then approves or rejects it. The `review_payment_proof` RPC records the decision, audit entry, paid ledger row, and registration payment status in one transaction. A screenshot alone does not establish payment.
