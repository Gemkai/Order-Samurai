#!/usr/bin/env python3
"""Guards public claims across landing page, README, legal docs, and onboarding."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

def test_no_unsupported_dmg_claims():
    landing = (REPO_ROOT / "dashboard-ui" / "src" / "components" / "LandingPage.tsx").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    onboarding = (REPO_ROOT / "docs" / "ONBOARDING.md").read_text(encoding="utf-8") if (REPO_ROOT / "docs" / "ONBOARDING.md").exists() else ""

    for text, name in [(landing, "LandingPage.tsx"), (readme, "README.md"), (onboarding, "ONBOARDING.md")]:
        assert ".dmg" not in text.lower(), f"Found unsupported .dmg claim in {name}"


def test_no_fake_checkout_mode_or_card_inputs():
    landing = (REPO_ROOT / "dashboard-ui" / "src" / "components" / "LandingPage.tsx").read_text(encoding="utf-8")
    assert "stripe demo mode" not in landing.lower(), "Found fake stripe demo mode in LandingPage.tsx"
    assert "cardholder name" not in landing.lower(), "Found raw credit card input in LandingPage.tsx"
    assert "samurai-pro-key" not in landing.lower(), "Found fabricated license key in LandingPage.tsx"


CHECKOUT_URL = "https://jemakaib1.gumroad.com/l/sqwomh"


def test_no_stale_support_emails_or_dead_checkout_links():
    # Gumroad is the live storefront (2026-10-01). The Lemon Squeezy store returns 404,
    # so any link to it strands a buyer at checkout.
    docs_to_check = [
        REPO_ROOT / "dashboard-ui" / "src" / "components" / "LandingPage.tsx",
        REPO_ROOT / "dashboard-ui" / "src" / "App.tsx",
        REPO_ROOT / "dashboard-ui" / "src" / "components" / "PillarPage.tsx",
        REPO_ROOT / "README.md",
        REPO_ROOT / "TERMS.md",
        REPO_ROOT / "EULA.md",
        REPO_ROOT / "PRIVACY.md",
        REPO_ROOT / "SECURITY.md",
        REPO_ROOT / "docs" / "ONBOARDING.md",
        REPO_ROOT / "bin" / "lib_pro_gate.sh",
    ]

    for p in docs_to_check:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        forbidden_agentica = "".join(["support@", "agentica"])
        assert "lemonsqueezy.com" not in text, f"Found dead Lemon Squeezy checkout link in {p.name}"
        assert "lemon squeezy" not in text.lower(), f"Found stale Lemon Squeezy buyer instruction in {p.name}"
        assert forbidden_agentica not in text, f"Found stale agentica support email in {p.name}"


def test_buy_links_point_at_the_live_storefront():
    for p in [
        REPO_ROOT / "dashboard-ui" / "src" / "components" / "LandingPage.tsx",
        REPO_ROOT / "docs" / "ONBOARDING.md",
        REPO_ROOT / "bin" / "lib_pro_gate.sh",
    ]:
        assert CHECKOUT_URL in p.read_text(encoding="utf-8"), f"{p.name} has no link to {CHECKOUT_URL}"


def test_tracked_demo_payload_is_never_pro():
    # bin/agentica_emit.py refreshes this file from the live aggregate; a maintainer's dev
    # license must never ship as the public landing-page demo.
    import json

    demo = json.loads((REPO_ROOT / "dashboard-ui" / "public" / "wid_payload.json").read_text(encoding="utf-8"))
    assert (demo.get("license") or {}).get("tier", "free") != "pro"
