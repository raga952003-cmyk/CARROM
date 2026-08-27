# Test harnesses

These run against a **live API and a real database**. They are not unit tests:
they sign up throwaway admin accounts, create tournaments, players, fixtures and
boards, assert on what the server actually stored, and delete it all again.

That is deliberate — most of the bugs this project has hit lived in the seams
between FastAPI, Pydantic, PostgREST and the RPCs, where a unit test with a
mocked database would have passed happily.

## Running them

Start the API, then:

```bash
python tests/run_all.py
```

The exit code is the number of failing suites, so CI can gate a deploy on it.
Point it elsewhere with `CARROM_API=https://staging.example.com`.

Run one suite directly:

```bash
python tests/test_boardscoring.py
```

Always run from `backend/`, not from `tests/` — the harnesses import
`app.database` to verify rows directly.

## Do not point these at a live tournament

They write real rows while they run. Use a database you are willing to have
churned. Cleanup is best-effort: a suite killed partway through leaves its
tournaments behind, named with a random six-character run tag.

## Conventions

- **`ok(label, cond, detail)`** — one assertion. `detail` prints only on
  failure. It must print for `0`, `""` and `None` too: a board score of `0`
  suppressed by truthiness is exactly the case you need to see, and that
  once hid a failing request behind what looked like a wrong score.
- **`RUN`** — a six-character hex tag mixed into every name and email, so
  concurrent runs and leftovers stay distinguishable.
- **`cleanup()`** in a `finally` — deletes the tournaments and auth users the
  run created.
- **`_session.request`** — routes a call through a retry that
  re-authenticates once on a 401. Long suites do occasionally get a token
  refused mid-run; without this the refusal leaves a board at `0-0` and
  surfaces as a scoring bug that is not there.

`e2e.py`, `e2e2.py`, `e2e_doubles.py` and `test_access.py` predate `_session`
and call `requests` directly, so they have no 401 retry. If one of them fails
with a `401`, re-run it before investigating.

## Migrations

Several suites assert on columns added by migrations. When a migration is not
applied those assertions **skip** rather than fail, and `run_all.py` prints a
warning first. A green run with pending migrations is not full coverage —
check the warning line.

`/api/health` reports `pending_migrations`; a deploy should gate on
`status == "ok"`.
