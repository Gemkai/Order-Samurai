"""
Unit tests for execution/lemonsqueezy_mcp.py
"""
import sys
import os
import pytest

# Add execution directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "execution")))

from lemonsqueezy_mcp import (
    validate_license_key,
    activate_license_key,
    create_checkout_link,
    get_order_details,
)

def test_validate_license_key_fails_closed_on_network_error():
    # No live network access in tests, so the real API call fails — this must
    # fail closed (never a key-prefix bypass to "valid": True).
    res = validate_license_key("SAMURAI-PRO-KEY-2026-7781-9921-X", instance_id="macbook_dev_1")
    assert res["valid"] is False
    assert "error" in res

def test_activate_license_key_fails_closed_on_network_error():
    res = activate_license_key("SAMURAI-PRO-KEY-2026-7781-9921-X", instance_name="dev-workstation")
    assert res["activated"] is False
    assert "error" in res

def test_create_checkout_link_simulated():
    res = create_checkout_link(variant_id="pro_199", customer_email="buyer@example.com")
    assert "url" in res
    assert res["amount"] == 199.00
    assert res["refund_policy"] == "14-Day 100% Money-Back Guarantee"

def test_get_order_details_simulated():
    res = get_order_details(order_id="ord_99812")
    assert res["order_id"] == "ord_99812"
    assert res["refund_eligible"] is True
    assert res["total_usd"] == 199.00
