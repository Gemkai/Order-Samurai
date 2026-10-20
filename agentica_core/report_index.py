"""index.json for the dashboard's Reports tab. Standard library only, so `samurai audit`
can rebuild it on a bare python3 (report dependencies may live in ~/.samurai/venv)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


def rebuild_index(directory: Path) -> list[dict]:
    """Index ALL report .md present on disk (not just ones written this run), so a
    latest-only refresh never clobbers the historical list the UI reads. Full audits
    (audit__<UTC stamp>.md) and weekly reports (<week>__<platform>.md) share one list,
    newest first by `generated` (the audit's stamp, else the file's mtime)."""
    _HDR_BYTES = 4096  # "Records this week:" always appears in the first ~1 KB of the header
    index: list[dict] = []
    for f in Path(directory).glob("*.md"):
        audit = re.match(r"audit__(\d{8}T\d{6}Z)\.md$", f.name)
        if audit:
            stamp = datetime.strptime(audit.group(1), "%Y%m%dT%H%M%SZ")
            index.append({"file": f.name, "kind": "audit", "title": "Full Audit",
                          "generated": stamp.strftime("%Y-%m-%dT%H:%M:%SZ")})
            continue
        m = re.match(r"(.+?)__(.+)\.md$", f.name)
        if not m:
            continue
        try:
            # Read only the header slice — avoids full-file I/O for large historical reports.
            with f.open(encoding="utf-8", errors="ignore") as fh:
                head = fh.read(_HDR_BYTES)
        except OSError:
            head = ""
        rec = re.search(r"Records this week:\s*\*\*(\d+)\*\*", head)
        mtime = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)
        index.append({"file": f.name, "kind": "weekly", "title": f"Weekly Report — {m.group(2)}",
                      "week": m.group(1), "platform": m.group(2),
                      "records": int(rec.group(1)) if rec else 0,
                      "generated": mtime.strftime("%Y-%m-%dT%H:%M:%SZ")})
    # Same second: an audit (written after the weekly rewrite it ran) first, then week order.
    index.sort(key=lambda x: (x["generated"], x["kind"] == "audit", x.get("week", ""), x.get("platform", "")),
               reverse=True)
    (Path(directory) / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index
