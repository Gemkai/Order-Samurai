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


def test_no_stale_support_emails_or_personal_gumroad_links():
    docs_to_check = [
        REPO_ROOT / "dashboard-ui" / "src" / "components" / "LandingPage.tsx",
        REPO_ROOT / "dashboard-ui" / "src" / "App.tsx",
        REPO_ROOT / "dashboard-ui" / "src" / "components" / "PillarPage.tsx",
        REPO_ROOT / "README.md",
        REPO_ROOT / "TERMS.md",
        REPO_ROOT / "EULA.md",
        REPO_ROOT / "PRIVACY.md",
        REPO_ROOT / "SECURITY.md",
    ]

    for p in docs_to_check:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        forbidden_gumroad = "".join(["jemakai", "b1", ".gumroad.com"])
        forbidden_agentica = "".join(["support@", "agentica"])
        assert forbidden_gumroad not in text, f"Found stale personal gumroad link in {p.name}"
        assert forbidden_agentica not in text, f"Found stale agentica support email in {p.name}"
