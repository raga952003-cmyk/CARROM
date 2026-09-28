# Tournament flow audit and rollout

This review covers the application changes completed on 27–28 September 2026.
Migrations 023 through 027 were applied to the live Supabase SQL Editor and
checked for service-role-only execution. The older auth, draw, scoring and
payment safeguards were also audited in the live database. Application code
still needs deployment before the new live flows can be used.

## Decisions implemented

| Flow | Behaviour |
| --- | --- |
| Create tournament | New events start in draft. The standard senior preset is best of three games, each to 25 points or eight boards. The 21-point/six-board variant is a separate preset. Federation presets fix their scoring options together; custom scoring stays available. |
| Registration | New and existing players cannot be added after the registration closing date in India time. The server enforces this for admin actions too. Only eligible registrations feed the draw. |
| Fee collection | A submitted GPay receipt stays pending until an organizer sees the credit in the receiving account. Approval settles the fee and approves the entry in one database transaction. A previously rejected receipt can be corrected after the credit is confirmed; a fresh explanation is required and the rejection remains in the audit history. Desk cash, UPI and bank-transfer collection records the amount, method, reference, actor and audit trail atomically. A captured Razorpay payment whose registration update fails is reported for retry and reconciliation. |
| Receipt checks | Exact file hashes and normalized UPI references block obvious reuse. Image-only uploads also get a 64-bit visual similarity check. When configured, Google Cloud Vision reads receipt text and compares the visible reference and amount with the claim. Flags guide review; they never prove that money arrived or approve an entry automatically. |
| Draw and fixtures | The organizer must close registration and resolve pending entries before generating fixtures. The database locks the entry list and tournament draw settings while replacing fixtures; later roster changes are refused. A one-entrant category or missing player/team blocks the whole draw instead of silently omitting an entry. Group fixtures retain their group; a cross-group league match is rejected. Late matches on a published schedule need a valid date, time and board, pass collision and rest checks, and notify both players. Once knockout qualifiers are seated, league fixtures and results for that category are locked so the bracket cannot silently disagree with the table. Early forced promotion is refused. Match deletion follows the result and publication safeguards. |
| Scoring | Federation-style detailed board entry credits the winner from the opponent's remaining coins, handles a covered queen, the senior 21-point queen cutoff and the 12-point board cap. The target or board limit ends a game, and best of three games decides a match. Before qualifier seating, corrections replay the result and dependent standings. |
| Points table | League match points sort first, then net score difference, board difference and head-to-head in the configured order. Board wins remain visible but are not silently substituted for net score difference. Knockout advancement uses match winners rather than league position. |

The scoring presets follow the [Indian Carrom Federation Laws of Carrom](https://www.indiancarrom.co.in/laws-of-carrom/) and its [21-point/six-board variant](https://www.indiancarrom.co.in/new-version-rules/). Associations can use different house rules, so a tournament organizer can choose the custom model before play begins.

## Scenarios exercised locally

The HTTP workflow suite runs **191 distinct scenarios**. Its core matrix has
five event phases (draft, registration open, registration closed, in progress,
completed) × six identities (owner, manager, scorer, outside admin, player,
anonymous) × six writes (rename, change rest period, change status, publish
directly, add an ineligible match, publish an empty draw) = **180** cases.
Eleven additional cases cover group assignment, cross-group attempts, late
entrants, published match conflicts, rest, out-of-range boards and dates, and
player notices. The suite checks that rejected writes have no side effects.

Other local suites cover exact and re-encoded receipt reuse, different typed
references, OCR mismatch and outage, payment replay, owner access, draw
generation, walkovers, result reopening, qualifier promotion, standings and
scoring. The simulation suite also runs **900 complete tournaments**. These are
local tests with an in-memory database, not transactions in the user's live
Supabase project.

## Representative failure paths and remaining limits

| Situation | Application response | Remaining consideration |
| --- | --- | --- |
| A player or admin tries to add an entrant after the registration closing date | Registration is refused, including import and new-player flows. | The organizer still closes the lifecycle state before drawing fixtures. |
| An organizer tries to draw while registration is open | No fixtures are written; the screen directs them to close registration first. | An early close is an explicit organizer decision. |
| A registration or tournament setting changes while fixtures are being built | The database refuses the stale draw; the organizer reloads the entry list and retries. | Concurrent edits may require a retry after the other write completes. |
| A drawn event receives a late registration, roster change, or reopened entry | The database refuses the roster edit, even if an old client or API tries it. | Exceptional changes to a played event need a reviewed correction or walkover process. |
| A late match overlaps another board or denies a player the configured rest period | The published edit or addition is refused. | A changed venue or real-world delay still needs an organizer to reschedule. |
| A league result is corrected after qualifiers have been placed in the knockout | The edit is refused so the bracket and points table cannot silently diverge. | A future audited reseeding workflow would be needed for exceptional corrections. |
| The eighth board leaves a federation game tied | Scoring requests a deciding board; later unused boards remain unplayed. | The event should publish its tie-break policy before play. |
| A senior game winner starts a board above 21 points and covers the queen | The board awards no queen bonus. | The scorer still has to record the queen and board events accurately. |
| Two entrants have the same league match points | Net score difference decides first, then later configured tie breakers. | The table only uses confirmed results. |
| Two people upload the identical receipt file or claim the same UPI reference | The later submission is refused. An organizer can correct the earlier rejected proof after confirming the credit and recording a new reason. | One reference cannot be reused for a different entry, even after a rejection. |
| A receipt is re-encoded with a different typed transaction ID | A close visual fingerprint warns the reviewer; text reading may show a reference mismatch. | Cropping, overlays, or a very similar app template can evade or falsely trigger the visual warning. |
| OCR is unavailable or reads a different amount | The proof stays pending and the organizer sees the flag or unavailable state. | A matching screenshot still does not prove money reached the account. |
| Razorpay captures money but the registration write fails | The callback reports a retryable failure and the ledger can reconcile the entry. | Test-mode credentials do not process live payments. |
| A payment is reviewed twice or two channels try to settle one entry | Transactional database functions and unique payment safeguards prevent a second paid ledger row. The live database has a global unique normalized UPI/bank/GPay reference index, and the duplicate-group audit found zero existing conflicts. | Verify any ambiguous real bank transfer against the receiving account. |

Visual similarity checks happen before the new proof is inserted, so two
near-simultaneous altered uploads can miss each other. The receiving-bank check
is the final defense. The similarity search also scans prior fingerprints and
should be measured again when the event has a large receipt archive.

## Existing live tournament requiring reconciliation

The live **September Month Carrom Tournament** is already in progress with
197 matches, 170 confirmed results, and 20 distinct match-side IDs, but only
one approved registration. This mismatch predates the roster safeguards in
this change. The existing match results were not altered. Do not regenerate
this draw: it contains confirmed results. The organiser needs to reconcile
the historical entrant list against the actual match participants and payment
records in a separate audited process before treating registration counts as
complete for this event. The new draw and registration guards protect future
events and freeze this played event's roster until that review.

## Deployment and remaining verification

1. Deploy the matching backend and frontend code. Migrations
   `023_atomic_manual_entry_payment.sql`,
   `024_payment_proof_image_analysis.sql`,
   `025_payment_proof_review_access.sql`,
   `026_payment_proof_reconsideration.sql`, and
   `027_registration_draw_atomicity.sql` were applied in order to Supabase.
   The local backend's live health check reported `ok` with no pending
   migrations. Check the deployed application's health endpoint afterward;
   these paths fail closed if a required database function is unavailable.
2. Configure `PAYMENT_PROOF_VISION_API_KEY` on the backend if automatic text
   reading is desired. The user authorized sending uploaded receipt images to
   `vision.googleapis.com`. No receipt is transmitted by the local tests.
3. With real admin and player accounts, test one full registration, GPay upload,
   bank-credit check, approval, draw, board entry and standings update. Check
   Razorpay webhooks separately if that payment method is enabled.

Receipt OCR is machine text reading, not a custom trained CNN or bank
verification. Image similarity can miss cropped or heavily edited copies and
can flag unrelated receipts that use the same app layout. Near-simultaneous
uploads may be evaluated before either image is stored. The similarity query
currently scans stored fingerprints, so it should be benchmarked if receipt
volume becomes large. An organizer must compare the receiving account's
transaction history before approval. The local browser was not authenticated
as a real admin or player during this review. The live migration checks prove
installation and access grants; they do not exercise a real payment.
