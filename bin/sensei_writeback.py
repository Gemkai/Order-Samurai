#!/usr/bin/env python3
"""Write-back for the sensei live ADW (2026-07-14).

Reads the workflow's JSON result (file arg or stdin) and, ONLY when SENSEI_ARM=1,
performs the three writes: POST verdicts to the ReflexEngine, append backlog entries
(atomic read-modify-write — never naive-append a JSON array), append ledger rows (JSONL).
Without SENSEI_ARM it prints exactly what it WOULD do and exits 0.

Safety invariants (from the grill + local pre-review):
- Human gate preserved: backlog entries are approved:false; nothing auto-promotes.
- POST first, then the ledger records the ACTUAL result (verdict_posted vs
  verdict_post_failed) — the ledger never claims a post that didn't happen.
- Backlog write is atomic (temp + os.replace); a crash can't truncate the JSON.
- Any required armed sink failure exits non-zero so the scheduler cannot report
  a partial write-back as a successful cycle.
"""
import contextlib, json, os, sys, datetime, tempfile, pathlib, time, urllib.request

# A2: warn-only schema validation at the ledger-append ingestion point. Imported,
# never re-implemented (Anti-Pattern #2) — schema_guard owns the draft-07 loading
# and the schema_violations.jsonl sink for every caller.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentica_core.schema_guard import (  # noqa: E402
    check_warn_only,
    violations as schema_violations,
)

UTC = datetime.timezone.utc
OSR = pathlib.Path(os.environ.get("ORDER_SAMURAI_ROOT",
                                  str(pathlib.Path.home() / "AgenticaOS/Governance/Order Samurai")))
API = os.environ.get("REFLEX_API", "http://localhost:3001/api/reflex/verdicts")
ARMED = os.environ.get("SENSEI_ARM") == "1"
LEDGER = OSR / "state/SENSEI_LEDGER.jsonl"
BACKLOG = OSR / "state/PROPOSED_BACKLOG.json"
BACKLOG_LOCK = BACKLOG.with_name(BACKLOG.name + ".lock")
LOCK_WAIT_S = float(os.environ.get("SENSEI_LOCK_WAIT_S", "10"))
LOCK_STALE_S = float(os.environ.get("SENSEI_LOCK_STALE_S", "300"))
TERMINAL_BACKLOG_STATUSES = frozenset(
    {"resolved", "closed", "completed", "rejected", "promoted"}
)

data = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(sys.stdin)
posts = data.get("post_verdicts", []) or []
backlog = data.get("backlog_entries", []) or []
rows = data.get("ledger_rows", []) or []
now = datetime.datetime.now(UTC).isoformat()


def do_post():
    body = [{**p, "ts": now} for p in posts]
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:  # explicit timeout (Release It! rule)
        return r.status


def append_ledger(final_rows):
    """Append rows to SENSEI_LEDGER.jsonl, validating each WARN-ONLY first (A2).

    Deliberately not fail-fast, unlike aggregate.validate_payload: these rows are
    agent output, and rejecting a historically-valid shape would silently stall
    remediation. A violation is recorded to state/schema_violations.jsonl and the
    row still lands. A3 flips this to reject after 7 clean days.
    """
    violations = 0
    with open(LEDGER, "a") as f:
        for r in final_rows:
            row = {"ts": now, **r}
            if check_warn_only(row, "sensei_ledger_row", OSR / "state",
                               context={"sink": "SENSEI_LEDGER.jsonl",
                                        "cycle_id": row.get("cycle_id")}):
                violations += 1
            f.write(json.dumps(row) + "\n")
    return violations


@contextlib.contextmanager
def backlog_lock():
    """Serialize the backlog read-modify-write across concurrent sensei runs.

    os.replace makes the *write* atomic, but read-compute-id-write is not: two runs
    that read the same snapshot both derive the same next SENSEI-<n> and the second
    os.replace discards the first run's items -- silent loss, no error. This wraps
    the whole cycle so only one run holds it at a time.

    os.mkdir is an atomic create-if-absent, which is why it is used here in place of
    flock: lock semantics vary by filesystem, while mkdir's exclusivity does not.

    A holder that dies mid-write would otherwise deadlock the mechanism forever, so a
    lock older than LOCK_STALE_S is reclaimed. Waiting past LOCK_WAIT_S raises rather
    than proceeding unlocked -- proceeding is the bug this exists to prevent.
    """
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            os.mkdir(BACKLOG_LOCK)
            break
        except FileExistsError:
            try:
                age = time.time() - os.stat(BACKLOG_LOCK).st_mtime
            except FileNotFoundError:
                continue            # holder released between our mkdir and our stat
            if age > LOCK_STALE_S:
                with contextlib.suppress(OSError):
                    os.rmdir(BACKLOG_LOCK)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"backlog lock {BACKLOG_LOCK.name} held {age:.1f}s by another sensei "
                    f"run; gave up after {LOCK_WAIT_S}s rather than risk losing entries")
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.rmdir(BACKLOG_LOCK)


def append_backlog():
    """Locked wrapper -- the read-modify-write below must never interleave."""
    with backlog_lock():
        return _append_backlog_locked()


def _append_backlog_locked():
    # PROPOSED_BACKLOG.json on this system is an OBJECT {generated_at, note, items:[...]},
    # but was historically a bare list. Preserve whichever structure exists — writing a bare
    # list back over the object would drop generated_at/note and break the generator/readers.
    # NOTE: this file is machine-generated (replenish/ronin regenerate it), so a direct append
    # can be clobbered by the next regeneration. Direct append matches the skill's Step 7 spec,
    # but the durable escalation path is the metric-intake flow — treat this as best-effort.
    doc = None
    if BACKLOG.exists():
        try:
            doc = json.load(open(BACKLOG))
        except Exception:
            doc = None
    if isinstance(doc, dict):
        items = doc.get("items")
        if not isinstance(items, list):
            items = []
            doc["items"] = items
        container = doc
    elif isinstance(doc, list):
        items = doc
        container = doc
    else:
        items = []
        container = items
    existing = [str(i.get("id", "")) for i in items]
    n = max([int(i.split("-")[-1]) for i in existing
             if i.startswith("SENSEI-") and i.split("-")[-1].isdigit()] + [0])
    unresolved_reflexes = {
        str(i.get("reflex_id"))
        for i in items
        if i.get("source") == "sensei"
        and i.get("reflex_id")
        and str(i.get("status", "")).strip().lower() not in TERMINAL_BACKLOG_STATUSES
    }
    added = []
    for b in backlog:
        reflex_id = str(b.get("reflex_id", "")).strip()
        is_sensei_reflex = b.get("source") == "sensei" and bool(reflex_id)
        if is_sensei_reflex and reflex_id in unresolved_reflexes:
            continue
        n += 1
        items.append({"id": f"SENSEI-{n}", **b})   # approved:false comes from the entry
        added.append(f"SENSEI-{n}")
        if is_sensei_reflex:
            unresolved_reflexes.add(reflex_id)
    fd, tmp = tempfile.mkstemp(dir=str(BACKLOG.parent), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(container, f, indent=2)   # write the WHOLE container (object or list)
    os.replace(tmp, BACKLOG)   # atomic
    return added


if not ARMED:
    print(f"[sensei-writeback] DRY (SENSEI_ARM unset) — WOULD POST {len(posts)} verdict(s), "
          f"append {len(backlog)} backlog entr(y/ies), {len(rows)} ledger row(s). Nothing written.")
    for p in posts:
        print("  POST   :", json.dumps({**p, "ts": now}))
    for b in backlog:
        print("  BACKLOG:", json.dumps(b), "(approved:false)")
    # A2 verify runs the dry path ("one full sensei dry-run produces zero
    # violations"), so validate here too — but report only. Dry writes nothing,
    # including to the violations sink.
    bad = 0
    for r in rows:
        for msg in schema_violations({"ts": now, **r}, "sensei_ledger_row"):
            bad += 1
            print(f"  VIOLATION: sensei_ledger_row {msg}")
    print(f"  schema : {len(rows)} ledger row(s) checked, {bad} violation(s) (warn-only)")
    sys.exit(0)

# An armed cycle always produces SOMETHING (a real run emits ledger rows even
# when there is nothing to post or escalate). A fully empty payload is exactly
# what sensei_cycle_live.sh substitutes ({}) when the claude call fails or
# returns unextractable output — exiting 0 here reported a total write-back
# failure as a successful cycle. Fail loudly instead; the dry path above stays
# exit 0 (informational).
if not (posts or backlog or rows):
    print("[sensei-writeback] ARMED but payload is empty (no verdicts, backlog "
          "entries, or ledger rows) — treating as upstream cycle failure.")
    sys.exit(1)

print("[sensei-writeback] ARMED — writing.")
writeback_ok = True

# 1) POST first, capture truth
post_ok = True
if posts:
    try:
        status = do_post()
        post_ok = 200 <= status < 300
        if not post_ok:
            writeback_ok = False
        print(f"  post   : HTTP {status} for {len(posts)} verdict(s)")
    except Exception as e:
        post_ok = False
        writeback_ok = False
        print(f"  post   : FAILED — {e}")

# 2) ledger reflects the ACTUAL post result
final_rows = []
for r in rows:
    r2 = dict(r)
    if r2.get("action_taken") == "verdict_posted" and not post_ok:
        r2["action_taken"] = "verdict_post_failed"
    final_rows.append(r2)
try:
    bad = append_ledger(final_rows)
    note = f" ({bad} schema_violation(s) logged — warn-only, rows still written)" if bad else ""
    print(f"  ledger : appended {len(final_rows)} row(s){note}")
except Exception as e:
    writeback_ok = False
    print(f"  ledger : FAILED — {e}")

# 3) backlog (independent of POST; human-gated approved:false)
if backlog:
    try:
        print(f"  backlog: appended {append_backlog()} (approved:false — bin/ronin promote still required)")
    except Exception as e:
        writeback_ok = False
        print(f"  backlog: FAILED — {e}")

# An armed write-back is a transaction with independently durable sinks. The
# ledger records a failed POST truthfully, but that does not make the failed POST
# itself successful; launchd/callers still need a non-zero signal for any required
# sink that did not land.
sys.exit(0 if writeback_ok else 1)
