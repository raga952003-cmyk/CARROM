# Latency improvements — 4 October 2026

## Changes

- Tournament mutations and board scoring reload the affected event rather than every tournament and board in the database.
- Realtime events identify their tournament or match and use the same scoped reads. Inserts, deletions, unknown event ownership and reconnects still reconcile the full list.
- Concurrent reads are serialized; pending scopes merge so a late response cannot overwrite a newer saved score. Full reconciliation takes precedence.
- The realtime batching window is 50 ms instead of 250 ms. New events do not keep postponing the window.
- Removed the 1.4-second toss wait and three-second import auto-close timer. Imports with skipped rows remain visible for review.
- Removed the duplicate explicit dashboard refresh during login. Authentication state starts the initial load.

## Measurements and verification

Before editing, local HTTP reads against the real Supabase-backed server measured:

| Read | Elapsed | Response bytes |
| --- | ---: | ---: |
| All tournaments | 2,585 ms | 1,157,793 |
| Completed two-player test event | 317 ms | 17,732 |

These are individual diagnostic reads, not a latency guarantee or a measured total score-save duration. The scoped response was about 98.5% smaller; elapsed time varies with the network, event size and database.

Live browser verification used a new free draft, **E2E Latency Check 2026-10-04**, ID `70b6e0f2-16d5-4491-9d20-c4aeaf4976d8`. Saving its description succeeded and became visible. The server recorded PUT followed only by GETs for that event, including its realtime echo; no full-list GET occurred after that edit. The completed scoring test correctly rejected editing with 409 and was left unchanged.

Checks passed: TypeScript, production build, and refresh queue tests covering concurrent scope merging, serialized reads, independent resources, full refresh priority and recovery after failure.

Supabase writes, authentication, payment verification and outage retries still need actual network time. This change removes avoidable waiting; it does not promise zero latency.
