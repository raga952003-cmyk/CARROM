"""
Run every offline suite, and measure how much of the application they reach.

    python tests/offline/run_offline.py            # everything
    python tests/offline/run_offline.py --fast     # skip the 900 scenarios
    python tests/offline/run_offline.py --no-cover # skip the coverage pass

None of this touches the network or a database, so it is safe to run at any
time -- including during an event. The live suites in backend/tests/*.py are
NOT run from here and never will be: they sign up real accounts and write real
rows into whatever database the API points at.

Coverage is statement coverage, measured with the standard library's `trace`
module so nothing has to be installed. Executable lines are taken from the AST,
which counts statements rather than bytecode offsets; treat the percentages as a
guide to what is unexercised, not as an exact figure. Only the HTTP-driven
suites are traced -- they are the ones that reach the routers, and tracing the
enumeration suites would multiply their runtime for no new information.
"""
import ast
import os
import sys
import threading
import time
import traceback
import trace as trace_mod

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
for path in (BACKEND, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

# A suite that raised rather than reporting. Distinct from a failure
# count so the summary can say which happened.
CRASHED = -1

FAST = "--fast" in sys.argv
NO_COVER = "--no-cover" in sys.argv

# (module, label, traced)
SUITES = [
    ("test_pure_logic", "unit          pure functions, exhaustive enumeration", False),
    ("test_official_transaction", "unit          official score atomic write", False),
    ("test_scenarios", "scenario      900 whole tournaments", False),
    ("test_integration", "integration   real app + in-memory database", True),
    ("test_system", "system        whole app over HTTP, black box", True),
    ("test_e2e_matchday", "end-to-end    draw -> schedule -> board queues -> scoring", True),
    ("test_official_carrom", "carrom        target-point games and best-of-three match", False),
    ("test_payments", "payments      entry fees, signatures, webhook replay", True),
    ("test_auth_client_isolation", "auth          isolated sessions and profile lookup", True),
    ("test_payment_proofs", "payment proof private upload and review access", True),
    ("test_acceptance", "acceptance    user stories per role", True),
    ("test_real_workflows", "workflows     191 role, phase, and fixture scenarios", True),
    # These five lived beside the others on disk but were never in this list,
    # so `run_offline.py` reported PASS across the board while they failed
    # unrun. test_health_probes had three assertions failing against the
    # health payload and one against the unprobeable list, and nothing said
    # so -- the suite has to be in the runner to be a check at all.
    ("test_health_probes", "health        migration probes and the health payload", True),
    ("test_lifecycle", "lifecycle     tournament state transitions", True),
    ("test_entry_list_freeze", "entries       final draw freezes registrations", True),
    ("test_reopen", "reopen        undoing a confirmed result", True),
    ("test_qualifiers", "qualifiers    who goes through, and into which slot", True),
    ("test_fixture_routes", "fixtures      draw, edit, remove, and who may", True),
    # Each case here failed against the code before its fix: 11 of 25 did,
    # which is the only evidence that a regression test is worth having.
    ("test_regressions", "regressions   audited defects, pinned so they cannot return", True),
]


def executable_lines(path):
    """Line numbers that carry a statement, per the AST."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except Exception:
        return set()
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and not isinstance(
                node, (ast.Import, ast.ImportFrom)):
            # A docstring is a statement but not logic worth counting.
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                continue
            lines.add(node.lineno)
    return lines


def app_files():
    out = []
    for root, _dirs, names in os.walk(os.path.join(BACKEND, "app")):
        if "__pycache__" in root:
            continue
        for name in sorted(names):
            if name.endswith(".py") and name != "__init__.py":
                out.append(os.path.join(root, name))
    return out


def main():
    results = []
    started = time.time()

    for module_name, label, traced in SUITES:
        if FAST and module_name == "test_scenarios":
            print("skipping %s (--fast)" % module_name)
            continue
        print("\n" + "#" * 78)
        print("# %s" % label)
        print("#" * 78)
        # A suite that raises must not take the run down with it.
        #
        # Every suite counts its own failures and returns a code; that is the
        # contract. But a suite can also raise -- an assert outside the check()
        # helper, an import error, a route returning 404 to a bare `assert
        # response.status_code == 200`. That exception used to propagate
        # straight out of main(), and the run DIED at that point: the suites
        # after it never executed and no summary was printed at all. Observed
        # with test_official_carrom asserting on a route that did not exist
        # yet -- unit, scenario, integration, system and end-to-end passed,
        # and then payments, acceptance, health, lifecycle, reopen,
        # qualifiers, fixtures and payment_proofs silently did not run. CI
        # went red saying nothing whatever about the other eight.
        #
        # Caught, counted as a failure, and the run carries on. A crash is
        # strictly worse news than a failed assertion, so it is reported at
        # least as loudly -- with its traceback, which is the thing you need.
        try:
            module = __import__(module_name)
            code = module.main()
        except BaseException as exc:                       # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            print()
            print("!! %s CRASHED -- it did not finish, so its checks "
                  "did not all run." % module_name)
            traceback.print_exc()
            code = CRASHED
        results.append((label, code))

    counts = {}
    if not NO_COVER:
        print("\n" + "#" * 78)
        print("# coverage pass (HTTP-driven suites, re-run under trace)")
        print("#" * 78)
        tracer = trace_mod.Trace(count=1, trace=0, ignoremods=("trace",))
        # Starlette's TestClient runs the ASGI application on its own thread,
        # and sys.settrace only ever applies to the thread that set it. Without
        # this the tracer watches the test thread doing nothing but waiting,
        # and reports that the entire application is unreachable.
        threading.settrace(tracer.globaltrace)
        try:
            for module_name, _label, is_traced in SUITES:
                if not is_traced:
                    continue
                module = sys.modules.get(module_name) or __import__(module_name)
                # Each suite accumulates into module-level RESULTS; clear it so
                # the second run does not double-count its own assertions.
                module.RESULTS.clear()
                tracer.runfunc(module.main)
        finally:
            threading.settrace(None)
        counts = tracer.results().counts

    if counts:
        hit_by_file = {}
        for (filename, lineno) in counts:
            hit_by_file.setdefault(os.path.abspath(filename), set()).add(lineno)

        print("\n" + "=" * 78)
        print("statement coverage of backend/app (HTTP-driven suites)")
        print("=" * 78)
        total_lines = total_hit = 0
        rows = []
        for path in app_files():
            lines = executable_lines(path)
            if not lines:
                continue
            hit = lines & hit_by_file.get(os.path.abspath(path), set())
            total_lines += len(lines)
            total_hit += len(hit)
            rows.append((len(hit) / float(len(lines)), path, len(hit), len(lines)))

        rows.sort()
        rel = lambda p: os.path.relpath(p, BACKEND).replace("\\", "/")
        print("\nleast covered first:")
        for pct, path, hit, count in rows:
            bar = "#" * int(pct * 20)
            print("  %5.1f%%  %-20s %4d/%-4d  %s" % (pct * 100, bar, hit, count,
                                                     rel(path)))
        if total_lines:
            print("\n  overall: %d of %d statements  (%.1f%%)"
                  % (total_hit, total_lines, 100.0 * total_hit / total_lines))
        print("\n  Uncovered code is not necessarily untested -- the unit and")
        print("  scenario suites reach the engines directly and are not traced")
        print("  here. Read this as a map of what the HTTP layer never touches.")

    print("\n" + "=" * 78)
    print("summary")
    print("=" * 78)
    failures = 0
    for label, code in results:
        if code == CRASHED:
            state = "CRASHED - did not finish"
        elif code == 0:
            state = "PASS"
        else:
            state = "%d INVARIANT(S) VIOLATED" % code
        print("  %-58s %s" % (label, state))
        # A crash counts as one failure rather than -1, which would otherwise
        # cancel out a real violation elsewhere and hand back a green exit.
        failures += 1 if code == CRASHED else code
    print("\n  elapsed: %.1fs" % (time.time() - started))
    return failures


if __name__ == "__main__":
    sys.exit(main())
