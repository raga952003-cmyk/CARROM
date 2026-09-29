# Authentication and payment setup

Apply the numbered database migrations in order through `028_receipt_payee_review_guard.sql` before deploying the matching API. Migration 017 restricts direct Data API access. Migration 018 preserves captured duplicate charges awaiting a refund. Migration 019 creates atomic draw and schedule RPCs. Migration 020 creates the private proof bucket and ledger. Migration 021 adds atomic match deletion, a global normalized UPI/bank reference constraint, and the next-game scoring transition. Migration 022 auto-approves a registration after verified settlement or waiver. Migration 023 settles a desk payment and entry atomically. Migration 024 stores image similarity and optional text-reading signals. Migration 025 restricts proof review to the event owner or approved manager. Migration 026 permits an audited correction of a rejected proof. Migration 027 freezes and checks the roster when the draw is made. Migration 028 blocks approval of a receipt whose readable payee is a different account and requires a detailed note for flagged receipts. `/api/health` reports a missing migration; verify it says `status: ok` with no pending migrations before taking payments.

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

Configure the Razorpay webhook and secret as described by the deployment guide. Use live Razorpay keys for real tournament fees. Test keys are blocked by default and always blocked when `ENV=production`; only an isolated development or test server can opt in with `ALLOW_TEST_RAZORPAY_IN_DEVELOPMENT=true`. The player sees Razorpay only when checkout and its webhook are ready. Reconcile `payments.status = 'refund_due'` with the Razorpay dashboard and issue refunds there. Organizer desk payments require a method and receipt reference; waivers require a reason. Both are audited. Verify a real payment and refund in the provider sandbox before accepting production registrations.

For a GPay/UPI option, set each tournament's receiving phone number (10 Indian digits) or UPI ID. The player chooses this method and submits the exact transaction reference and a JPG, PNG, or WebP receipt (5 MiB maximum). The backend rejects a repeated reference or identical file globally, including after a prior rejection; it also checks the normalized reference against desk-recorded payments and blocks a proof if the entry already has a captured payment. A perceptual image fingerprint flags visually similar screenshots for manual review, including when the file is re-encoded or the typed reference differs. The private proof retains the payee account shown at submission. If configured, `PAYMENT_PROOF_VISION_API_KEY` sends the image to Google Cloud Vision for text reading and compares readable transaction reference, amount and explicitly labelled receiving account. A readable different payee blocks approval; absent or masked payee text remains for manual bank verification. A flagged similar image or amount/reference mismatch requires a detailed review note. The organiser must independently see the matching credit in the receiving bank or UPI account before approval. The `review_payment_proof_v2` RPC writes the decision, audit, ledger payment and registration approval in one transaction. A screenshot or OCR result alone never establishes payment.
