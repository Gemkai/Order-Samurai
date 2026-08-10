#!/usr/bin/env python3
"""review_pending_patch.py — human review CLI for propose-only remediation patches.

The reflex engine's propose-only lane (REFLEX_AUTO_APPLY=false, the default) saves every
patch that passed the maker-checker audit + pytest gate to state/pending_remediation_*.patch
instead of git-applying it. reflex-engine.ts's _enqueuePendingPatchHitl already routes each
one onto hitl_queue.json too (source 'reflex_patch') and hitl_alerts.py surfaces both (the
queue item and this file's own glob, de-duplicated against each other); this CLI is the
human half of the loop:

  python3 bin/review_pending_patch.py --list           # pending patches, oldest first
  python3 bin/review_pending_patch.py --show NAME      # print the diff
  python3 bin/review_pending_patch.py --apply NAME     # git apply from the repo root, then
                                                       # archive to state/patch_archive/applied/
  python3 bin/review_pending_patch.py --reject NAME    # archive to state/patch_archive/rejected/

Apply and reject both resolve the matching hitl_queue.json 'reflex_patch' item (if one
exists) through the same Python writer reflex-engine.ts uses to create it
(agentica_core.bushido_engine) — --apply marks it done, --reject marks it rejected — so an
applied/rejected patch stops reporting as a pending approval forever. That resolution is
best-effort and never fails the apply/reject itself: the file-level outcome (patch applied
or archived) is this tool's real product.

Apply stays MANUAL — nothing schedules or auto-invokes this tool; it runs only when the
human runs it, and it never commits (review + commit remain yours). A failed git apply
leaves the patch in place for another attempt or a manual rebase. Exit codes: 0 success,
1 failure (missing patch / apply error / not a git repo), 2 usage.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 guard

_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(Path(__file__).resolve().parents[1])))
STATE_DIR = _ROOT / "state"
PATCH_GLOB = "pending_remediation_*.patch"        # must match reflex-engine.ts patchIdSlug naming
ARCHIVE_APPLIED = STATE_DIR / "patch_archive" / "applied"
ARCHIVE_REJECTED = STATE_DIR / "patch_archive" / "rejected"
# Patches are generated against the AgenticaOS superproject (the reflex worktree's repo),
# so git apply must run from there, not from Order Samurai/.
REPO_ROOT = Path(os.environ.get("AGENTICA_REPO_ROOT", str(_ROOT.parents[1])))
# Parent of Order Samurai/ — sys.path prefix to import agentica_core, mirroring how
# reflex-engine.ts's HITL_ENQUEUE_PY resolves the same package to WRITE the queue item
# this file needs to resolve.
GOVERNANCE_ROOT = _ROOT.parent
GIT_TIMEOUT = 60           # every external call gets a timeout (Release It! rule)
# Matches the filename token, not "patch=<rest of the string>": the patch's absolute
# path embeds ".../Order Samurai/state/..." — a directory name WITH a space — so a
# whitespace-delimited capture off "patch=" would truncate mid-path. patchIdSlug
# (reflex-engine.ts) replaces every non [A-Za-z0-9_-] char before ".patch", so the
# filename itself is always contiguous and space-free regardless of where it lives.
_PATCH_FILENAME_RE = re.compile(r"(pending_remediation_[A-Za-z0-9_-]+\.patch)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_str(p: Path) -> str:
    try:
        days = (_now() - datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)).days
    except OSError:
        return "?"
    return f"{days}d" if days < 14 else f"{days // 7}wk"


def _pending() -> list[Path]:
    return sorted(STATE_DIR.glob(PATCH_GLOB), key=lambda p: p.stat().st_mtime)


def _resolve(name: str) -> Path:
    """Map a user-supplied name to a pending patch, refusing anything outside state/.

    Only the basename is honored (no traversal), and it must match the pending naming
    scheme — this tool must never be talked into cat-ing or moving an arbitrary file.
    """
    base = Path(name).name
    if not (base.startswith("pending_remediation_") and base.endswith(".patch")):
        raise SystemExit(f"review_pending_patch: {name!r} is not a pending_remediation_*.patch name")
    p = STATE_DIR / base
    if not p.is_file():
        raise SystemExit(f"review_pending_patch: no pending patch {base} in {STATE_DIR}")
    return p


def _archive(patch: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / patch.name
    if dest.exists():
        # Same reflex id proposed again after an earlier review — keep both, timestamped.
        dest = target_dir / f"{patch.stem}.{_now().strftime('%Y%m%dT%H%M%SZ')}{patch.suffix}"
    patch.replace(dest)
    return dest


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True, timeout=GIT_TIMEOUT,
    )


def _find_reflex_patch_queue_id(patch_name: str) -> str | None:
    """The hitl_queue.json item id _enqueuePendingPatchHitl created for this patch, if any.

    Read-only scan of state/hitl_queue.json for a `status: 'pending'`, `source:
    'reflex_patch'` item whose free-text context embeds `patch=<path>` ending in this
    patch's basename. Returns None on a missing/unreadable/malformed queue, or when no
    item matches — every case means "nothing to resolve", not an error worth failing
    apply/reject over (the patch file itself is this tool's real product).
    """
    try:
        data = json.loads((STATE_DIR / "hitl_queue.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "pending" or item.get("source") != "reflex_patch":
            continue
        m = _PATCH_FILENAME_RE.search(item.get("context") or "")
        if m and m.group(1) == patch_name:
            return item.get("id")
    return None


def _resolve_queue_item(queue_id: str | None, outcome: str) -> None:
    """Close the loop on the matching hitl_queue.json item — an applied/rejected patch
    must stop reporting as a pending approval indefinitely. Uses the same writer
    (agentica_core.bushido_engine) reflex-engine.ts uses to create the item, not a
    second hand-rolled queue mutator. Never raises: a delivery/import problem here must
    not undo or fail an apply/reject whose file-level effect already landed.
    """
    if queue_id is None:
        return
    try:
        if str(GOVERNANCE_ROOT) not in sys.path:
            sys.path.insert(0, str(GOVERNANCE_ROOT))
        from agentica_core.bushido_engine import mark_complete, review_hitl
        if outcome == "apply":
            mark_complete(queue_id, _ROOT, failed=False)
        else:
            review_hitl(queue_id, _ROOT, "reject", reason="rejected via review_pending_patch.py")
    except Exception as exc:  # noqa: BLE001 — see docstring: must never fail the caller
        print(f"review_pending_patch: could not resolve hitl_queue item {queue_id}: {exc}",
              file=sys.stderr)


def do_list() -> int:
    patches = _pending()
    if not patches:
        print(f"no pending patches in {STATE_DIR}")
        return 0
    for p in patches:
        print(f"{p.name} · waiting {_age_str(p)} · {p.stat().st_size} bytes")
    return 0


def do_show(name: str) -> int:
    print(_resolve(name).read_text(encoding="utf-8"), end="")
    return 0


def do_apply(name: str) -> int:
    patch = _resolve(name)
    # Looked up before the patch moves, so a match is possible either way this ends.
    queue_id = _find_reflex_patch_queue_id(patch.name)
    # Anti-pattern #1: confirm the target is a git repo before piping git at it.
    probe = _git("rev-parse", "--git-dir")
    if probe.returncode != 0:
        print(f"review_pending_patch: {REPO_ROOT} is not a git repo: "
              f"{probe.stderr.strip()[:200]}", file=sys.stderr)
        return 1
    out = _git("apply", "--verbose", str(patch))
    if out.returncode != 0:
        # git apply is all-or-nothing without --reject: a failure leaves the tree
        # untouched and the patch in place for a retry or manual rebase.
        print(f"review_pending_patch: git apply FAILED (exit {out.returncode}) — patch left in place:\n"
              f"{out.stderr.strip()[:500]}", file=sys.stderr)
        return 1
    dest = _archive(patch, ARCHIVE_APPLIED)
    _resolve_queue_item(queue_id, "apply")
    print(f"applied {patch.name} to {REPO_ROOT}")
    print(f"archived → {dest}")
    print("review the working tree and commit yourself (git diff / git commit) — this tool never commits.")
    return 0


def do_reject(name: str) -> int:
    patch = _resolve(name)
    queue_id = _find_reflex_patch_queue_id(patch.name)
    dest = _archive(patch, ARCHIVE_REJECTED)
    _resolve_queue_item(queue_id, "reject")
    print(f"rejected {patch.name} (repo untouched)")
    print(f"archived → {dest}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Review propose-only remediation patches (manual, human-invoked).")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="list pending patches, oldest first")
    mode.add_argument("--show", metavar="NAME", help="print one patch's diff")
    mode.add_argument("--apply", metavar="NAME", help="git apply from the repo root, then archive")
    mode.add_argument("--reject", metavar="NAME", help="archive without applying")
    args = ap.parse_args()
    if args.list:
        return do_list()
    if args.show:
        return do_show(args.show)
    if args.apply:
        return do_apply(args.apply)
    return do_reject(args.reject)


if __name__ == "__main__":
    sys.exit(main())
