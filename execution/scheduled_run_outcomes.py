"""Observe whether scheduled launchd work produced its promised outcome.

This family is a spectator for its first week. Once it has run clean for one
week, promote it to a doctor gate; the measurements themselves already use
FAIL for broken outcome contracts so that promotion needs no semantic change.
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import plistlib
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from xml.parsers.expat import ExpatError
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.claude_runtime_target import governance_root  # noqa: E402


_STAMP = re.compile(
    r"(?P<stamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)"
)
_PLIST_KEYS = {
    "KeepAlive", "Label", "Program", "ProgramArguments", "RunAtLoad",
    "StandardErrorPath", "StandardOutPath", "StartCalendarInterval",
    "StartInterval", "WorkingDirectory",
}
# Ordinary clock skew between the process that wrote a log stamp and this one. Past that,
# a future stamp is a wrong clock or a mis-parsed zone, not a run — grade nothing on it.
_CLOCK_SKEW_HOURS = 5 / 60


def _row(status: str, label: str, detail: str) -> dict:
    return {"status": status, "label": f"scheduled-run-outcomes.{label}", "detail": detail}


def _load_registry(path: Path) -> list[dict]:
    """Call the operator registry's canonical loader without changing sys.path.

    The loader ships beside this pack's agentica_core/ -- Governance/tools/ in the
    repo, tools/ in the public export (allow-listed by bin/extract_public.py) --
    so it is found through governance_root(), never through repo_root: the export
    has no Governance/ directory, and resolving through repo_root there read a
    path outside the distribution, so every exported scheduled-run-outcomes test
    saw "operator registry unreadable" (20 failures, 2026-09-06).
    """
    module_path = governance_root() / "tools" / "operator_registry_check.py"
    spec = importlib.util.spec_from_file_location("operator_registry_check", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_registry(path)


def _read_plist(path: Path, *, strict: bool) -> dict:
    raw = path.read_bytes()
    if not strict:
        raw = re.sub(br"<!--.*?-->", b"", raw, flags=re.DOTALL)
    data = plistlib.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("plist root is not a dictionary")
    return data


def _path(value: str, repo_root: Path) -> Path:
    expanded = Path(value).expanduser()
    return expanded if expanded.is_absolute() else repo_root / expanded


def _newest_match(value: str | None, repo_root: Path) -> Path | None:
    if not value:
        return None
    matches = [Path(item) for item in glob.glob(str(_path(value, repo_root)), recursive=True)]
    files = [item for item in matches if item.is_file()]
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


def _age_hours(path: Path | None, now: datetime) -> float | None:
    if path is None:
        return None
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return max(0.0, (now.astimezone(timezone.utc) - modified).total_seconds() / 3600)


def _launchctl() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/launchctl", "list"], capture_output=True, text=True,
        timeout=15, check=False,
    )


def _launchctl_statuses(proc: subprocess.CompletedProcess) -> dict[str, int]:
    statuses: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 3 or not fields[2].startswith("com.agentica."):
            continue
        try:
            statuses[fields[2]] = int(fields[1])
        except ValueError:
            continue
    return statuses


def _calendar_hours(plist: dict) -> set[int]:
    intervals = plist.get("StartCalendarInterval", [])
    if isinstance(intervals, dict):
        intervals = [intervals]
    return {
        item["Hour"] for item in intervals
        if isinstance(item, dict) and isinstance(item.get("Hour"), int)
    }


def _last_run_first_time(path: Path, run_span_hours: float, local_zone=None) -> datetime | None:
    """LOCAL time of the first timestamped line of the newest run in the log.

    The fire time is the run's FIRST stamp, not its newest — a job that starts at 02:00
    and logs until 05:40 is on time, not 3h late. The newest run is every stamp within
    `run_span_hours` of the newest one (a job cannot outrun its own cadence without
    overlapping itself). Hours are only comparable to a plist's local
    `StartCalendarInterval` after conversion, so convert before reading `.hour`.
    """
    found: list[datetime] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _STAMP.match(line.lstrip())
        if not match:
            continue
        raw = match.group("stamp").replace("Z", "+00:00")
        if re.search(r"[+-]\d{4}$", raw):
            raw = raw[:-5] + raw[-5:-2] + ":" + raw[-2:]
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if stamp.tzinfo is None:
            # A zone-less stamp in a launchd log is LOCAL time; reading it as UTC fakes
            # drift of exactly the host's offset. Only Z/offset stamps are converted.
            stamp = stamp.replace(tzinfo=local_zone) if local_zone else stamp.astimezone()
        found.append(stamp)
    if not found:
        return None
    newest = max(found)
    window = timedelta(hours=min(max(run_span_hours, 0.5), 6.0))
    first = min(s for s in found if newest - s <= window)
    return first.astimezone(local_zone)   # None = the host's local zone, as launchd sees it


def _hour_gap(actual: int, planned: set[int]) -> int:
    return min(min((actual - hour) % 24, (hour - actual) % 24) for hour in planned)


def _outcome_checks(
    automations: list[dict], runtime_root: Path, now: datetime,
    statuses: dict[str, int] | None, installed: set[str], scheduled: set[str],
) -> list[dict]:
    rows: list[dict] = []
    for job in automations:
        job_id = job.get("id", "")
        if not job_id.startswith("com.agentica.") or (job_id not in scheduled
                                                       and "cadence_hours" not in job):
            continue
        cadence = job.get("cadence_hours")
        problems = []
        if not isinstance(cadence, (int, float)) or isinstance(cadence, bool) or cadence <= 0:
            problems.append("cadence_hours must be a positive number")
        if not isinstance(job.get("may_be_empty", False), bool):
            problems.append("may_be_empty must be boolean")
        if not isinstance(job.get("log"), str) or not job.get("log"):
            problems.append("log must be a non-empty path")
        artifact_value = job.get("artifact")
        if artifact_value is not None and not isinstance(artifact_value, str):
            problems.append("artifact must be a path string or null")
        if artifact_value is None and not job.get("artifact_reason"):
            problems.append("artifact:null requires artifact_reason")
        if problems:
            rows.append(_row("WARN", "unmeasured",
                             f"{job_id}: invalid outcome contract: {', '.join(problems)}"))
            continue
        artifact = _newest_match(artifact_value, runtime_root)
        log = _newest_match(job.get("log"), runtime_root)
        artifact_age = _age_hours(artifact, now)
        log_age = _age_hours(log, now)
        if not job.get("artifact"):
            reason = job.get("artifact_reason") or "registry gives no reason"
            rows.append(_row("WARN", "unmeasured", f"{job_id}: no artifact declared ({reason})"))
        else:
            stale = artifact_age is None or artifact_age > 2 * float(cadence)
            ran_empty = (log_age is not None and log_age <= cadence and stale
                         and not job.get("may_be_empty", False))
            if ran_empty:
                rows.append(_row(
                    "FAIL", "ran-empty",
                    f"{job_id}: log is fresh ({log_age:.1f}h) but artifact is not",
                ))
            elif stale:
                age = "missing" if artifact_age is None else f"{artifact_age:.1f}h old"
                rows.append(_row(
                    "FAIL", "stale-artifact",
                    f"{job_id}: artifact is {age}; limit {2 * cadence:g}h",
                ))
        if statuses is not None and job_id in installed:
            if job_id not in statuses:
                rows.append(_row("WARN", "never-started",
                                 f"{job_id}: installed but not loaded in launchctl"))
            elif statuses[job_id] != 0 and (log_age is None or log_age > cadence):
                age = "missing" if log_age is None else f"{log_age:.1f}h old"
                rows.append(_row(
                    "WARN", "never-started",
                    f"{job_id}: launchctl status {statuses[job_id]}, log {age}",
                ))
    return rows


def _first_fire_checks(
    automations: list[dict], runtime_root: Path, installed_plists: dict[str, Path],
    now: datetime, local_zone=None,
) -> list[dict]:
    rows: list[dict] = []
    for job in automations:
        job_id = job.get("id", "")
        log_value = job.get("log")
        plist_path = installed_plists.get(job_id)
        if not log_value or plist_path is None:
            continue
        try:
            hours = _calendar_hours(_read_plist(plist_path, strict=False))
        except (OSError, ValueError, plistlib.InvalidFileException, ExpatError):
            continue
        if not hours:
            continue
        log = _newest_match(log_value, runtime_root)
        cadence = job.get("cadence_hours")
        span = float(cadence) if isinstance(cadence, (int, float)) else 24.0
        first = _last_run_first_time(log, span, local_zone) if log else None
        if first is None:
            rows.append(_row(
                "WARN", "unmeasured",
                f"{job_id}: log has no parseable first-fire timestamp",
            ))
            continue
        # Validity window for grading the fire hour, NOT a freshness SLA — that is
        # `_outcome_checks`' stale-artifact contract, and duplicating it here would report
        # one fact twice. The stamp is ~cadence old just before every fire, so a bare
        # `span` bound WARNs on healthy sub-day calendar jobs; two cadences is the same
        # one-missed-cycle slack artifacts get, floored at `_last_run_first_time`'s own 6h
        # run-span cap.
        stale_after = max(2.0 * span, 6.0)
        age = (now.astimezone(first.tzinfo) - first).total_seconds() / 3600.0
        if age < -_CLOCK_SKEW_HOURS:
            rows.append(_row("WARN", "unmeasured",
                             f"{job_id}: newest anchored log stamp is {-age:.1f}h in the "
                             f"future; skew tolerance {_CLOCK_SKEW_HOURS * 60:g}min"))
        elif age > stale_after:
            rows.append(_row("WARN", "unmeasured",
                             f"{job_id}: newest anchored log stamp is {age:.1f}h old; "
                             f"limit {stale_after:g}h"))
        elif _hour_gap(first.hour, hours) >= 1:
            rows.append(_row(
                "FAIL", "tz-drift",
                f"{job_id}: planned hour {sorted(hours)}, observed {first.hour:02d}:xx",
            ))
    return rows


def _plist_checks(source_dir: Path, installed_dir: Path) -> tuple[list[dict], dict[str, Path], set[str]]:
    rows: list[dict] = []
    sources = {path.stem: path for path in source_dir.glob("com.agentica.*.plist")}
    installed = {path.stem: path for path in installed_dir.glob("com.agentica.*.plist")}

    for label in sorted(sources.keys() - installed.keys()):
        rows.append(_row("WARN", "plist-drift", f"{label}: source plist is not installed"))
    # Deliberately one direction: "installed with no committed source" belongs to doctor's
    # `factory.plist-drift`, which honours the triaged exemptions in
    # config/launchd_source_policy.json. One authoritative representation of "unsourced".
    for label, path in sorted(installed.items()):
        try:
            _read_plist(path, strict=True)
        except (OSError, ValueError, plistlib.InvalidFileException, ExpatError) as exc:
            rows.append(_row(
                "WARN", "strict-plist",
                f"{label}: strict plist parse failed: {exc}",
            ))
    for label in sorted(sources.keys() & installed.keys()):
        try:
            source = _read_plist(sources[label], strict=False)
            live = _read_plist(installed[label], strict=False)
        except (OSError, ValueError, plistlib.InvalidFileException, ExpatError) as exc:
            rows.append(_row(
                "WARN", "plist-drift",
                f"{label}: plist comparison failed: {exc}",
            ))
            continue
        changed = sorted(key for key in _PLIST_KEYS if source.get(key) != live.get(key))
        if changed:
            rows.append(_row(
                "WARN", "plist-drift",
                f"{label}: installed values differ: {', '.join(changed)}",
            ))
    scheduled: set[str] = set()
    for label, path in installed.items():
        try:
            data = _read_plist(path, strict=False)
        except (OSError, ValueError, plistlib.InvalidFileException, ExpatError):
            continue
        if "StartCalendarInterval" in data or "StartInterval" in data:
            scheduled.add(label)
    return rows, installed, scheduled


def _scheduler_checks(paths: tuple[Path, ...], installed_dir: Path) -> list[dict]:
    manifest = next((path for path in paths if path.exists()), None)
    if manifest is None:
        return []
    try:
        tasks = json.loads(manifest.read_text(encoding="utf-8")).get("tasks", [])
    except (OSError, ValueError, AttributeError) as exc:
        return [_row(
            "WARN", "unmeasured",
            f"{manifest}: scheduled task registry unreadable: {exc}",
        )]
    declared: set[str] = set()
    rows: list[dict] = []
    for task in tasks:
        if (not isinstance(task, dict)
                or not str(task.get("launchd_id", "")).startswith("com.agentica.")):
            continue
        label = task["launchd_id"]
        declared.add(label)
        if task.get("plist_file") != f"{label}.plist":
            rows.append(_row(
                "WARN", "scheduler-drift",
                f"{label}: plist_file does not match launchd_id",
            ))
    installed = {path.stem for path in installed_dir.glob("com.agentica.*.plist")}
    for label in sorted(declared - installed):
        rows.append(_row("WARN", "scheduler-drift", f"{label}: app scheduler task is not installed"))
    # Deliberately one direction: the app scheduler's manifest is a SUBSET of the launchd
    # fleet, so "installed but absent from the manifest" is the normal state, not drift.
    return rows


def _timezone_check(
    now: datetime, localtime_path: Path, process_offset: timedelta | None,
    zoneinfo: Callable[[str], ZoneInfo],
) -> list[dict]:
    try:
        target = localtime_path.resolve(strict=True)
        parts = target.parts
        marker = parts.index("zoneinfo")
        zone_name = "/".join(parts[marker + 1:])
        host_offset = now.astimezone(zoneinfo(zone_name)).utcoffset()
        seen_offset = process_offset if process_offset is not None else now.astimezone().utcoffset()
    except (OSError, ValueError, KeyError) as exc:
        return [_row("WARN", "unmeasured", f"host timezone could not be read: {exc}")]
    if seen_offset != host_offset:
        return [_row(
            "WARN", "process-tz-drift",
            f"process offset {seen_offset}, {zone_name} offset {host_offset}",
        )]
    return []


def run_checks(
    *, repo_root: Path | None = None, registry_path: Path | None = None,
    source_dir: Path | None = None, installed_dir: Path | None = None,
    scheduled_tasks_paths: tuple[Path, ...] | None = None,
    launchctl_runner: Callable[[], subprocess.CompletedProcess] | None = None,
    now: datetime | None = None, localtime_path: Path = Path("/etc/localtime"),
    process_offset: timedelta | None = None,
    zoneinfo: Callable[[str], ZoneInfo] = ZoneInfo,
    local_zone=None, runtime_root: Path | None = None,
) -> list[dict]:
    """Return scheduled-run findings; all machine inputs can be replaced in tests.

    `local_zone` is the zone launchd's StartCalendarInterval hours are expressed in —
    None means the host's local zone; tests pin it so log stamps and plist hours are
    compared in one zone regardless of where the suite runs."""
    repo_root = repo_root or Path(__file__).resolve().parents[3]
    runtime_root = runtime_root or Path(os.environ.get(
        "AGENTICA_MAIN_CHECKOUT", str(Path.home() / "AgenticaOS"),
    ))
    # Governance-level inputs resolve by layout marker, not by hop from repo_root
    # (see _load_registry): repo_root keeps its one remaining job, anchoring the
    # registry's relative artifact/log paths.
    governance = governance_root()
    registry_path = registry_path or governance / "config" / "operator_registry.json"
    source_dir = source_dir or governance / "automation" / "launchd"
    installed_dir = installed_dir or Path.home() / "Library" / "LaunchAgents"
    if scheduled_tasks_paths is None:
        scheduled_tasks_paths = (
            Path.home() / ".claude" / "data" / "scheduled_tasks.json",
            Path.home() / ".claude" / "scheduled_tasks.json",
        )
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    try:
        automations = _load_registry(registry_path)
    except (OSError, ValueError, ImportError, AttributeError) as exc:
        automations = []
        rows.append(_row("WARN", "unmeasured", f"operator registry unreadable: {exc}"))

    statuses: dict[str, int] | None = None
    try:
        proc = (launchctl_runner or _launchctl)()
        if proc.returncode == 0:
            statuses = _launchctl_statuses(proc)
        else:
            rows.append(_row("WARN", "unmeasured", f"launchctl list exited {proc.returncode}"))
    except (OSError, subprocess.SubprocessError) as exc:
        rows.append(_row("WARN", "unmeasured", f"launchctl unavailable: {exc}"))

    plist_rows, installed_plists, scheduled = _plist_checks(source_dir, installed_dir)
    rows.extend(plist_rows)
    rows.extend(_outcome_checks(automations, runtime_root, now, statuses,
                               set(installed_plists), scheduled))
    rows.extend(_first_fire_checks(automations, runtime_root, installed_plists, now, local_zone))
    rows.extend(_scheduler_checks(scheduled_tasks_paths, installed_dir))
    rows.extend(_timezone_check(now, localtime_path, process_offset, zoneinfo))

    registered = {job.get("id") for job in automations}
    installed = {path.stem for path in installed_dir.glob("com.agentica.*.plist")}
    for label in sorted(installed - registered):
        rows.append(_row(
            "WARN", "undeclared",
            f"{label}: installed but absent from operator registry",
        ))
    measured = sum(1 for job in automations
                   if str(job.get("id", "")) in scheduled
                   and isinstance(job.get("cadence_hours"), (int, float)))
    if not measured:
        # A registry with no outcome contracts measures nothing; reporting "healthy" over
        # it would be the silence-read-as-health this family exists to end.
        rows.append(_row("WARN", "unmeasured",
                         f"no registry entry declares cadence_hours — {len(installed)} installed "
                         f"job(s), 0 outcome contracts; nothing above was measured"))
    elif not rows:
        rows.append(_row("OK", "healthy", f"{measured} job(s) measured, all outcomes on contract"))
    return rows
