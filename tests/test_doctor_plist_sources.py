"""Tests for doctor's reverse plist-drift direction (audit 2026-09-01, finding B5).

The forward check iterates SOURCE plists, so an installed job with no source is
invisible to it by construction. Five Order Samurai jobs were in exactly that state
— running daily on one machine, reproducible from nothing — while doctor printed
"32 source plist(s), no drift vs installed".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from execution.doctor import _factory_plist_drift_checks  # noqa: E402

_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string>
    <string>{program}</string>
  </array>
</dict></plist>
"""


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "Governance" / "automation" / "launchd").mkdir(parents=True)
    (root / "Governance" / "bin").mkdir(parents=True)
    return root


def _installed(tmp_path: Path) -> Path:
    d = tmp_path / "LaunchAgents"
    d.mkdir(exist_ok=True)
    return d


def _write(directory: Path, label: str, program: str) -> Path:
    path = directory / f"{label}.plist"
    path.write_text(_PLIST.format(label=label, program=program), encoding="utf-8")
    return path


def _policy(tmp_path: Path, labels: dict[str, str]) -> Path:
    path = tmp_path / "launchd_source_policy.json"
    path.write_text(json.dumps({"acknowledged_unsourced": {"labels": labels}}),
                    encoding="utf-8")
    return path


def _rows(results: list[dict], label: str) -> list[dict]:
    return [r for r in results if r["label"] == label]


def test_an_installed_plist_with_no_source_warns(tmp_path):
    """The B5 case in miniature."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    _write(installed, "com.agentica.orphan", f"{root}/Governance/bin/orphan.py")

    rows = _rows(_factory_plist_drift_checks(root, installed, _policy(tmp_path, {})),
                 "factory.plist-unsourced")
    assert [r["status"] for r in rows] == ["WARN"]
    assert "com.agentica.orphan" in rows[0]["detail"]


def test_a_sourced_plist_reports_ok_with_the_installed_count(tmp_path):
    root, installed = _root(tmp_path), _installed(tmp_path)
    src_dir = root / "Governance" / "automation" / "launchd"
    _write(src_dir, "com.agentica.sourced", f"{root}/Governance/bin/sourced.py")
    _write(installed, "com.agentica.sourced", f"{root}/Governance/bin/sourced.py")

    rows = _rows(_factory_plist_drift_checks(root, installed, _policy(tmp_path, {})),
                 "factory.plist-unsourced")
    assert [r["status"] for r in rows] == ["OK"]
    assert "1 installed plist(s) run repo code, all sourced" in rows[0]["detail"]


def test_an_acknowledged_label_is_counted_rather_than_warned_or_hidden(tmp_path):
    """The owner triaged 18 non-Order-Samurai jobs out of this milestone. Dropping
    them silently would rebuild the blind spot the check exists to close, so they are
    named in the OK row instead."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    _write(installed, "com.agentica.other-lane", f"{root}/Knowledge/build.py")
    policy = _policy(tmp_path, {"com.agentica.other-lane": "owned elsewhere"})

    results = _factory_plist_drift_checks(root, installed, policy)
    assert [r["status"] for r in _rows(results, "factory.plist-unsourced")] == ["OK"]
    ack = _rows(results, "factory.plist-acknowledged")
    assert [r["status"] for r in ack] == ["OK"]
    assert "com.agentica.other-lane" in ack[0]["detail"]


def test_an_unacknowledged_plist_warns_even_when_others_are_acknowledged(tmp_path):
    """The regression guard: a newly hand-installed job must surface immediately."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    _write(installed, "com.agentica.other-lane", f"{root}/Knowledge/build.py")
    _write(installed, "com.agentica.brand-new", f"{root}/Governance/bin/new.py")
    policy = _policy(tmp_path, {"com.agentica.other-lane": "owned elsewhere"})

    rows = _rows(_factory_plist_drift_checks(root, installed, policy),
                 "factory.plist-unsourced")
    assert [r["status"] for r in rows] == ["WARN"]
    assert "com.agentica.brand-new" in rows[0]["detail"]


def test_a_plist_that_does_not_run_repo_code_is_out_of_scope(tmp_path):
    """doctor governs jobs that run THIS repo, not every com.agentica.* on the box."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    _write(installed, "com.agentica.elsewhere", "/opt/other/tool.py")

    rows = _rows(_factory_plist_drift_checks(root, installed, _policy(tmp_path, {})),
                 "factory.plist-unsourced")
    assert [r["status"] for r in rows] == ["OK"]
    assert "0 installed plist(s) run repo code" in rows[0]["detail"]


def test_an_unparseable_plist_is_reported_not_treated_as_out_of_scope(tmp_path):
    """A plist that will not parse even with comments stripped is genuinely opaque.
    Silently skipping it would drop a live job out of the audit for a reason unrelated
    to what it runs."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    (installed / "com.agentica.broken.plist").write_text(
        '<?xml version="1.0"?>\n<plist version="1.0"><dict><key>unclosed</plist>',
        encoding="utf-8")

    rows = _rows(_factory_plist_drift_checks(root, installed, _policy(tmp_path, {})),
                 "factory.plist-unsourced")
    assert any(r["status"] == "WARN" and "could not be parsed" in r["detail"] for r in rows)
    assert any("com.agentica.broken" in r["detail"] for r in rows)


def test_a_missing_policy_file_warns_and_still_reports_every_unsourced_plist(tmp_path):
    """Losing the exemption list must fail loud, not quietly exempt everything."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    _write(installed, "com.agentica.orphan", f"{root}/Governance/bin/orphan.py")

    rows = _rows(_factory_plist_drift_checks(root, installed, tmp_path / "absent.json"),
                 "factory.plist-unsourced")
    assert all(r["status"] == "WARN" for r in rows)
    assert any("cannot read the acknowledged-unsourced policy" in r["detail"] for r in rows)
    assert any("com.agentica.orphan" in r["detail"] for r in rows)


def test_the_forward_drift_direction_still_fires(tmp_path):
    """The reverse direction is added alongside, not instead of."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    src_dir = root / "Governance" / "automation" / "launchd"
    _write(src_dir, "com.agentica.sourced", f"{root}/Governance/bin/a.py")
    _write(installed, "com.agentica.sourced", f"{root}/Governance/bin/DRIFTED.py")

    rows = _rows(_factory_plist_drift_checks(root, installed, _policy(tmp_path, {})),
                 "factory.plist-drift")
    assert [r["status"] for r in rows] == ["WARN"]
    assert "source != installed" in rows[0]["detail"]


# ── review findings, 2026-09-02 ─────────────────────────────────────────────────

def test_a_checkout_that_audits_nothing_does_not_report_ok(tmp_path):
    """Installed plists name the DEPLOYMENT path, so any other checkout matches none of
    them. "0 installed plist(s) … all sourced" is indistinguishable from "audited 55,
    all clean" — the same shape as the finding this check exists to close."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    _write(installed, "com.agentica.elsewhere", "/Users/someone/Deployment/Governance/bin/x.py")

    rows = _rows(_factory_plist_drift_checks(root, installed, _policy(tmp_path, {})),
                 "factory.plist-unsourced")
    assert [r["status"] for r in rows] == ["WARN"]
    assert "not applicable from this checkout" in rows[0]["detail"]


def test_an_empty_launch_agents_dir_is_not_reported_as_not_applicable(tmp_path):
    """A host with no installed jobs at all (a standalone export, Linux CI) has nothing
    to audit and nothing to warn about — distinct from a checkout that missed them."""
    root, installed = _root(tmp_path), _installed(tmp_path)

    rows = _rows(_factory_plist_drift_checks(root, installed, _policy(tmp_path, {})),
                 "factory.plist-unsourced")
    assert [r["status"] for r in rows] == ["OK"]
    assert "0 installed plist(s) run repo code" in rows[0]["detail"]


def test_the_acknowledged_set_is_reported_even_when_something_else_is_unsourced(tmp_path):
    """Folding the count into the OK row hid the 18 exemptions in exactly the run where
    an operator is actually reading the output."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    _write(installed, "com.agentica.other-lane", f"{root}/Knowledge/build.py")
    _write(installed, "com.agentica.brand-new", f"{root}/Governance/bin/new.py")
    policy = _policy(tmp_path, {"com.agentica.other-lane": "owned elsewhere"})

    results = _factory_plist_drift_checks(root, installed, policy)
    assert any(r["label"] == "factory.plist-unsourced" and r["status"] == "WARN"
               for r in results)
    ack = _rows(results, "factory.plist-acknowledged")
    assert [r["status"] for r in ack] == ["OK"]
    assert "com.agentica.other-lane" in ack[0]["detail"]


def test_an_acknowledged_label_that_is_no_longer_installed_warns(tmp_path):
    """A standing exemption for a job that does not exist pre-approves whatever next
    claims that label."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    _write(installed, "com.agentica.present", f"{root}/Governance/bin/a.py")
    _write(root / "Governance" / "automation" / "launchd", "com.agentica.present",
           f"{root}/Governance/bin/a.py")
    policy = _policy(tmp_path, {"com.agentica.long-gone": "owned elsewhere"})

    ack = _rows(_factory_plist_drift_checks(root, installed, policy),
                "factory.plist-acknowledged")
    assert any(r["status"] == "WARN" and "com.agentica.long-gone" in r["detail"]
               for r in ack)


def test_a_plist_reached_through_bash_dash_c_is_in_scope(tmp_path):
    """Two live jobs (conductor-prep, sensei-cycle) reach their script through
    `bash -c '… exec "$HOME/<repo>/…"'`. A prefix test on whole argv elements excluded
    them from the audit entirely."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    path = installed / "com.agentica.wrapped.plist"
    path.write_text(_PLIST.format(label="com.agentica.wrapped",
                                  program=f"exec /bin/bash {root}/Governance/bin/x.sh"),
                    encoding="utf-8")

    rows = _rows(_factory_plist_drift_checks(root, installed, _policy(tmp_path, {})),
                 "factory.plist-unsourced")
    assert [r["status"] for r in rows] == ["WARN"]
    assert "com.agentica.wrapped" in rows[0]["detail"]


def test_a_plist_whose_only_defect_is_a_double_dash_comment_is_still_audited(tmp_path):
    """`--` inside an XML comment is illegal XML that plutil -lint accepts. If that took
    a plist out of the audit, anyone could make a hand-installed job invisible to the
    very check that exists to find one, with a two-character edit."""
    root, installed = _root(tmp_path), _installed(tmp_path)
    body = _PLIST.format(label="com.agentica.commented",
                         program=f"{root}/Governance/bin/x.py")
    path = installed / "com.agentica.commented.plist"
    path.write_text(body.replace("<plist version=\"1.0\">",
                                 "<!-- runs with --write-state -->\n<plist version=\"1.0\">"),
                    encoding="utf-8")

    results = _factory_plist_drift_checks(root, installed, _policy(tmp_path, {}))
    rows = _rows(results, "factory.plist-unsourced")
    assert not any("could not be parsed" in r["detail"] for r in rows)
    assert any(r["status"] == "WARN" and "com.agentica.commented" in r["detail"]
               for r in rows)
