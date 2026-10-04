# Tournament poster verification — 4 October 2026

Implemented real PNG exports, encoded QR links to a public poster page, awaited publishing with errors and busy states, shared admin/player/public rendering, accurate scoring descriptions, complete date/venue details, portrait/square/A4 sizes, optional public organiser contact, eligibility and confirmed sponsor text. Saves use the existing poster_config JSON column; no migration is required.

## Verified

- Production build and TypeScript checks.
- Real admin sign-in and poster save to the live Supabase project, using the existing free, no-prize completed event E2E Admin2 Full Match Test 2026-10-04.
- Reopened the editor and confirmed the saved blue theme, highlight and server save timestamp.
- Generated and inspected actual PNG output: portrait 1080×1350, square 1080×1080, A4 2480×3508.
- Decoded each PNG QR with an independent decoder and checked the exact tournament URL.
- Loaded the public poster without signing in and confirmed the saved layout and closed-registration state.
- Offline HTTP tests check owner-only editing, rejection of players/other organisers, persistence of optional fields, server-owned timestamp, null rejection, and protection of completed tournament settings.

## Deployment and limits

QR correction: the default QR destination is the user-confirmed public website https://carrom-umber-six.vercel.app/, including when generated locally. Organisers can set a validated public HTTPS base address per poster. Local/private addresses are rejected. The code now renders at 132px in the base layout with a four-module white border and sharp black modules. Draft links remain unavailable publicly until the tournament is opened. Registration links retain the tournament selection through sign-in and open the entry form for eligible players; completing a new player signup or real payment was not repeated during this banner test. AI copy generation was not called during verification. Downloaded files cannot change after sharing: the editor warns when tournament facts have changed, so download a new copy.

To verify an exported file:

    node frontend/scripts/check-poster-export.mjs <PNG path> <expected tournament URL>

To run the poster permission test:

    backend/.venv/Scripts/python.exe backend/tests/offline/test_poster.py

## QR correction verified

After the QR complaint, actual portrait, square and A4 exports were decoded again. All point to the public tournament URL on carrom-umber-six.vercel.app, and that public page was opened without sign-in. URL validation tests and the poster permission/persistence tests pass.
