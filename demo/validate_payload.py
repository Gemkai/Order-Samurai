#!/usr/bin/env python3
"""Validate demo/wid_payload.json for internal consistency.

Checks:
  a) needs_attention.count matches len(needs_attention.items)
  b) by_platform Total_Cost figures sum to <= the global Total_Cost, and no
     single platform's Total_Cost exceeds the global figure
  c) no metric leaf tagged is_percent:true has an unresolved "—" val
  d) each tier_mix share group's percentages sum to <= 100.5
  e) by-platform Estimated_Cost_Savings sum reconciles within 5% of the
     top-level (global) figure
  f) no value in the payload matches a secret-looking pattern
     (sk-, AKIA, ghp_, -----BEGIN)

Exit 0 with a confirmation line when all checks pass. Exit 1 with a
list of violations otherwise.

Usage: python3 demo/validate_payload.py demo/wid_payload.json
"""
import json
import math
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_PATH = REPO_ROOT / "dashboard-ui" / "public" / "wid_payload.json"
if not PAYLOAD_PATH.exists():
    PAYLOAD_PATH = REPO_ROOT / "demo" / "wid_payload.json"

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]"),
    re.compile(r"AKIA[0-9A-Z]"),
    re.compile(r"ghp_[A-Za-z0-9]"),
    re.compile(r"-----BEGIN"),
]

DASH = "—"


def find_metric_leaves(node, name=None, out=None):
    """Recursively collect (name, leaf_dict) for every metric leaf.

    A metric leaf is a dict carrying both 'val' and 'is_percent' keys.
    """
    if out is None:
        out = []
    if isinstance(node, dict):
        if "val" in node and "is_percent" in node:
            out.append((name, node))
        else:
            for k, v in node.items():
                find_metric_leaves(v, k, out)
    elif isinstance(node, list):
        for item in node:
            find_metric_leaves(item, name, out)
    return out


def to_float(val):
    try:
        value = float(str(val).replace(",", ""))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def sum_named_metric(subtree, metric_name):
    """Sum every leaf named metric_name found anywhere under subtree."""
    total = 0.0
    found = False
    for name, leaf in find_metric_leaves(subtree):
        if name == metric_name:
            v = to_float(leaf.get("val"))
            if v is not None:
                total += v
                found = True
    return total if found else None


def check_needs_attention(payload, violations):
    na = payload.get("needs_attention", {})
    count = na.get("count")
    items = na.get("items", [])
    if count != len(items):
        violations.append(
            f"needs_attention.count ({count}) != len(items) ({len(items)})"
        )


def check_total_cost(payload, violations):
    global_total = sum_named_metric(payload.get("pillars", {}), "Total_Cost")
    by_platform = payload.get("by_platform", {})
    if global_total is None:
        violations.append("no global Total_Cost metric found under pillars")
        return
    platform_sum = 0.0
    for platform, subtree in by_platform.items():
        p_total = sum_named_metric(subtree, "Total_Cost")
        if p_total is None:
            continue
        platform_sum += p_total
        if p_total > global_total:
            violations.append(
                f"by_platform.{platform} Total_Cost ({p_total}) exceeds "
                f"global Total_Cost ({global_total})"
            )
    if platform_sum > global_total:
        violations.append(
            f"sum of by_platform Total_Cost ({platform_sum}) exceeds "
            f"global Total_Cost ({global_total})"
        )


def check_percent_dash(payload, violations):
    """No metric leaf may carry an unresolved placeholder.

    Originally this only inspected is_percent leaves, which let 62 non-percent
    placeholders through -- they rendered as a bare em dash on the live demo
    ("the vault holds — curated articles at a health score of —/231").
    """
    for name, leaf in find_metric_leaves(payload):
        if leaf.get("val") == DASH:
            kind = "percent" if leaf.get("is_percent") else "metric"
            violations.append(f"{kind} leaf '{name}' has unresolved val '{DASH}'")


def check_summary_prose(payload, violations):
    """The generated narrative is public-facing copy and must read cleanly.

    The demo cards were reconciled before the prose was, so summaries still
    contained '—%' and implausible magnitudes while every leaf validated.
    """
    for pillar, text in (payload.get("summaries") or {}).items():
        if not isinstance(text, str):
            continue
        # An em dash is legitimate punctuation ("keep it steady -- hold the score"),
        # so flag only the shapes that mean "a value failed to render": adjacent to
        # a unit or digit ("—%", "—/231"), or sitting in a value slot after a
        # quantity-introducing verb ("the vault holds — curated articles").
        placeholder = re.search(rf"{DASH}\s*[%/]|[\d/]\s*{DASH}|{DASH}\s*\d", text) or re.search(
            rf"\b(holds|carried|spent|ran|at|of|now at|is)\s+{DASH}", text
        )
        if placeholder:
            violations.append(
                f"summaries.{pillar} has an unrendered value near "
                f"'{placeholder.group(0).strip()}'"
            )
        # Active-leak framing undercuts the product's core claim on the default view.
        if re.search(r"there are [1-9]\d* leaked", text):
            violations.append(
                f"summaries.{pillar} advertises active credential leaks; "
                "demonstrate interception instead"
            )
        # Catch runaway synthetic magnitudes (e.g. '$468473.9656', '134727.2 tasks').
        for raw in re.findall(r"\$?(\d[\d,]*\.?\d*)", text):
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            if value > 100_000:
                violations.append(
                    f"summaries.{pillar} quotes an implausible figure '{raw}'"
                )
                break
        # Unrounded currency reads as machine output, not a product surface.
        if re.search(r"\$\d+\.\d{3,}", text):
            violations.append(
                f"summaries.{pillar} quotes currency to >2 decimal places"
            )


def check_session_counts_agree(payload, violations):
    """The header renders record_counts; the summaries narrate a session count.

    They were 2834 vs 182 -- the header still carried the pre-synthetic snapshot,
    so the first line of the demo contradicted the pillar copy underneath it.
    """
    counts = payload.get("record_counts") or {}
    if not counts:
        return
    total = sum(v for v in counts.values() if isinstance(v, (int, float)))
    window_records = (payload.get("window") or {}).get("records")
    if window_records is not None and total > window_records:
        violations.append(
            f"record_counts sum to {total} but window.records is {window_records}"
        )
    bow = (payload.get("summaries") or {}).get("bow", "")
    match = re.search(r"across ([\d,]+) work sessions", bow)
    if match:
        narrated = float(match.group(1).replace(",", ""))
        if window_records is not None and abs(narrated - window_records) > 1:
            violations.append(
                f"summaries.bow narrates {narrated:.0f} sessions but "
                f"window.records is {window_records}"
            )


def check_headline_metrics(payload, violations):
    """Each pillar's headline metric must exist, or its hero renders empty.

    The bundle looks these keys up by name; arts pointed at Human_Hours_Saved
    while the payload only carried Craft_Improvements, so the biggest number on
    the Arts card rendered as an em dash.
    """
    headlines = {
        "bow": "Estimated_Agent_Time_Saved",
        "sword": "Kill_Chains_Disrupted",
        "brush": "Estimated_Cost_Savings",
        "arts": "Craft_Improvements",
    }
    for pillar_key, metric in headlines.items():
        pillar = (payload.get("pillars") or {}).get(pillar_key)
        if not isinstance(pillar, dict):
            continue
        found = any(
            isinstance(group, dict) and metric in group for group in pillar.values()
        )
        if not found:
            violations.append(
                f"pillars.{pillar_key} is missing headline metric '{metric}'"
            )


def check_remediation_efficacy(payload, violations):
    """Require the render contract without inventing successful repairs."""
    eff = payload.get("remediation_efficacy")
    # Legacy payloads can omit the panel entirely; a present object must be complete.
    if eff is None:
        return
    if not isinstance(eff, dict):
        violations.append("remediation_efficacy must be an object")
        return
    for key in ("applied", "improved", "regressed", "flat"):
        value = eff.get(key)
        if type(value) is not int or value < 0:
            violations.append(f"remediation_efficacy.{key} must be a nonnegative integer")
    for key in ("attempted", "completed"):
        if key in eff and (type(eff[key]) is not int or eff[key] < 0):
            violations.append(f"remediation_efficacy.{key} must be a nonnegative integer")
    if not isinstance(eff.get("events"), list):
        violations.append("remediation_efficacy.events must be a list")
    if not isinstance(eff.get("by_skill"), dict):
        violations.append("remediation_efficacy.by_skill must be an object")
    if not isinstance(eff.get("note"), str) or not eff["note"].strip():
        violations.append("remediation_efficacy.note must be a nonempty string")
    if "success_rate" not in eff:
        violations.append("remediation_efficacy.success_rate is required (null when not evaluated)")
    elif eff["success_rate"] is not None:
        rate = to_float(eff["success_rate"])
        if rate is None or not 0 <= rate <= 100:
            violations.append("remediation_efficacy.success_rate must be null or a percentage")


def check_tier_mix(payload, violations):
    tier_mix = payload.get("tier_mix", {})
    for group, data in tier_mix.items():
        slices = data.get("slices", "")
        pcts = [float(m) for m in re.findall(r":(\d+(?:\.\d+)?)%", slices)]
        total = sum(pcts)
        if total > 100.5:
            violations.append(
                f"tier_mix.{group} slices sum to {total} (> 100.5): '{slices}'"
            )


def check_savings(payload, violations):
    global_savings = sum_named_metric(
        payload.get("pillars", {}), "Estimated_Cost_Savings"
    )
    by_platform = payload.get("by_platform", {})
    if global_savings is None:
        return
    platform_sum = 0.0
    for platform, subtree in by_platform.items():
        p_val = sum_named_metric(subtree, "Estimated_Cost_Savings")
        if p_val is not None:
            platform_sum += p_val
    if global_savings == 0:
        if platform_sum != 0:
            violations.append(
                "Estimated_Cost_Savings: global figure is 0 but "
                f"by-platform sum is {platform_sum}"
            )
        return
    diff_pct = abs(platform_sum - global_savings) / abs(global_savings) * 100
    if diff_pct > 5:
        violations.append(
            f"by-platform Estimated_Cost_Savings sum ({platform_sum}) diverges "
            f"{diff_pct:.1f}% from the top-level figure ({global_savings}), "
            "exceeds the 5% tolerance"
        )


def check_secrets(payload, violations):
    raw = json.dumps(payload)
    for pattern in SECRET_PATTERNS:
        if pattern.search(raw):
            violations.append(
                f"payload contains a secret-looking pattern: {pattern.pattern}"
            )


def metric_value(payload, pillar, name):
    for key, leaf in find_metric_leaves((payload.get("pillars") or {}).get(pillar, {})):
        if key == name:
            return to_float(leaf.get("val"))
    return None


def check_rollups(payload, violations):
    """Validate every window/lifetime/platform/tier score, not just the hero."""
    def visit(node, path):
        if not isinstance(node, dict):
            return
        if "rollup" not in node:
            for key, child in node.items():
                visit(child, f"{path}.{key}")
            return
        rollup = node["rollup"]
        if not isinstance(rollup, dict):
            violations.append(f"{path}.rollup must be an object")
            return
        passing, graded = rollup.get("passing"), rollup.get("graded")
        if any(type(v) is not int or v < 0 for v in (passing, graded)):
            violations.append(f"{path}: rollup counts must be nonnegative integers")
        elif passing > graded:
            violations.append(f"{path}: passing exceeds graded")
        flags = node.get("flags") or []
        grades = {flag.get("grade") for flag in flags}
        expected = "CRITICAL" if "F" in grades else "HIGH" if "D" in grades else "PASS"
        if rollup.get("worst") != expected:
            violations.append(f"{path}: worst status disagrees with flagged grades")
        count, total = node.get("graded_count"), node.get("total_gradeable")
        if count is not None and total is not None:
            if any(type(v) is not int or v < 0 for v in (count, total)) or count > total or count != graded:
                violations.append(f"{path}: inconsistent grading coverage counts")
            elif total and node.get("coverage_pct") is not None:
                coverage = to_float(node["coverage_pct"])
                if coverage is None or abs(coverage - 100 * count / total) > 0.11:
                    violations.append(f"{path}: coverage percentage disagrees with counts")
    for field in ("category_scores", "category_scores_lifetime", "by_platform_scores", "by_tier_scores"):
        visit(payload.get(field) or {}, field)


def check_referenced_values(payload, violations):
    for item in (payload.get("needs_attention") or {}).get("items", []):
        expected = metric_value(payload, item.get("pillar"), item.get("metric"))
        if expected is None or to_float(item.get("val")) != expected:
            violations.append(f"attention item {item.get('metric')} disagrees with metric value")
    for reflex in payload.get("reflexes") or []:
        parts = str(reflex.get("id", "")).split(":")
        if len(parts) != 3 or parts[0] != "metric":
            continue
        expected = metric_value(payload, parts[1], parts[2])
        match = re.search(r"\bis at ([\d,]+(?:\.\d+)?)", reflex.get("message", ""))
        if expected is None or not match or to_float(match.group(1)) != expected:
            violations.append(f"reflex {reflex.get('id')} disagrees with metric value")


def check_density(payload, violations):
    tokens = metric_value(payload, "brush", "Token_Spend")
    tasks = (payload.get("window") or {}).get("successful_sessions")
    density = metric_value(payload, "brush", "Token_Execution_Density")
    if tasks is None:
        return  # Legacy snapshots do not expose the producer's denominator.
    if type(tasks) is not int or tasks <= 0:
        violations.append("window.successful_sessions must be a positive integer")
    elif tokens is not None and density is not None:
        if abs(density - tokens / tasks) > 0.11:
            violations.append("Token_Execution_Density disagrees with tokens per successful session")


def check_narrative_values(payload, violations):
    # Check the numeric slots used by the synthetic summaries. Rounded token
    # millions allow one decimal place; these are demo facts, not telemetry.
    slots = (
        ("bow", "Complexity_Weighted_Throughput", r"([\d,]+) complexity-weighted tasks", 1, 0.5),
        ("brush", "Total_Cost", r"spent \$([\d,.]+) in total", 1, 0.01),
        ("brush", "Token_Spend", r"using ([\d.]+)M tokens", 1_000_000, 50_000),
        ("arts", "Craft_Improvements", r"logged ([\d,]+) craft improvements", 1, 0),
        ("arts", "Doc_Parity_Issues", r"[Dd]oc parity issues[^.]*?now at ([\d,]+)", 1, 0),
    )
    for pillar, name, pattern, scale, tolerance in slots:
        match = re.search(pattern, (payload.get("summaries") or {}).get(pillar, ""))
        if not match:
            continue
        expected = metric_value(payload, pillar, name)
        actual = to_float(match.group(1))
        if expected is None or actual is None or abs(actual * scale - expected) > tolerance:
            violations.append(f"summaries.{pillar}: {name} disagrees with metric value")


def validate(payload):
    violations = []
    check_needs_attention(payload, violations)
    check_total_cost(payload, violations)
    check_percent_dash(payload, violations)
    check_summary_prose(payload, violations)
    check_headline_metrics(payload, violations)
    check_session_counts_agree(payload, violations)
    check_remediation_efficacy(payload, violations)
    check_tier_mix(payload, violations)
    check_savings(payload, violations)
    check_secrets(payload, violations)
    check_rollups(payload, violations)
    check_referenced_values(payload, violations)
    check_density(payload, violations)
    check_narrative_values(payload, violations)
    return violations


def main():
    if len(sys.argv) > 2:
        print("usage: validate_payload.py [path-to-wid_payload.json]", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1]) if len(sys.argv) == 2 else PAYLOAD_PATH
    violations = validate_payload(path)
    if violations:
        print(f"FAIL: {len(violations)} violation(s) found:")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    print("OK: demo payload passes all consistency checks")
    sys.exit(0)


def validate_payload(path):
    """Keep the shipped file-based interface while checking the full payload."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return validate(json.load(f))
    except (OSError, ValueError) as exc:
        return [f"Failed to read payload: {exc}"]


if __name__ == "__main__":
    main()
