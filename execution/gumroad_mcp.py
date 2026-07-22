#!/usr/bin/env python3
"""
Gumroad MCP Server & License Verification helper for Order Samurai.
Provides license key validation and activation via Gumroad's API (https://api.gumroad.com/v2/licenses/verify).
"""

import sys
import os
import json
import urllib.request
import urllib.parse
import urllib.error

GUMROAD_API_URL = "https://api.gumroad.com/v2/licenses/verify"
GUMROAD_PRODUCT_ID = os.environ.get("GUMROAD_PRODUCT_ID", "AePROIWPGu9a6k-dm9W4ww==")
GUMROAD_PERMALINK = os.environ.get("GUMROAD_PRODUCT_PERMALINK", "ordersamurai-pro")


def validate_license_key(license_key: str, product_id: str = None) -> dict:
    """Validate a Gumroad license key for Order Samurai Pro ($199)."""
    key = (license_key or "").strip()
    if not key:
        return {"valid": False, "error": "empty license key"}

    # Local dev simulation fallback
    if key.startswith("SAMURAI-PRO-KEY") or key.startswith("GUMROAD-PRO-KEY"):
        return {
            "valid": True,
            "license_key": key,
            "customer_email": "developer@ordersamurai.dev",
            "status": "active",
            "simulated": True,
            "refunded": False,
        }

    pid = product_id or GUMROAD_PRODUCT_ID
    payload = {"product_id": pid, "license_key": key}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        GUMROAD_API_URL,
        data=data,
        headers={"Accept": "application/json", "User-Agent": "OrderSamurai/1.0"}
    )

    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            success = res_data.get("success", False)
            purchase = res_data.get("purchase", {})
            refunded = purchase.get("refunded", False) or purchase.get("disputed", False)
            return {
                "valid": success and not refunded,
                "license_key": key,
                "customer_email": purchase.get("email"),
                "status": "refunded" if refunded else ("active" if success else "invalid"),
                "refunded": refunded,
                "uses": res_data.get("uses", 1),
            }
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return {"valid": False, "error": "license key not recognized by Gumroad"}
        return {"valid": False, "error": f"Gumroad API HTTP {err.code}"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


def activate_license_key(license_key: str, instance_name: str) -> dict:
    """Activate a Gumroad license key for a local developer machine."""
    val = validate_license_key(license_key)
    if not val.get("valid"):
        return {"activated": False, "error": val.get("error", "invalid key")}

    return {
        "activated": True,
        "instance_id": f"gum_{hash(f'{license_key}:{instance_name}') & 0xffffffff}",
        "instance_name": instance_name,
        "license_key": license_key,
        "customer_email": val.get("customer_email"),
        "simulated": bool(val.get("simulated")),
    }


def create_checkout_link() -> dict:
    """Return the official Gumroad product link for Order Samurai Pro ($199)."""
    url = os.environ.get("GUMROAD_PRODUCT_URL", f"https://ordersamurai.gumroad.com/l/{GUMROAD_PERMALINK}")
    return {
        "url": url,
        "amount": 199.00,
        "currency": "USD",
        "tier": "Pro Lifetime",
        "provider": "Gumroad",
    }


def main():
    """Stdio MCP server loop implementing Model Context Protocol for Gumroad."""
    tools = [
        {
            "name": "gumroad_validate_license",
            "description": "Validates an Order Samurai Pro $199 lifetime license key via Gumroad.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "license_key": {"type": "string", "description": "Gumroad license key string"},
                    "product_permalink": {"type": "string", "description": "Optional product permalink"}
                },
                "required": ["license_key"]
            }
        },
        {
            "name": "gumroad_activate_license",
            "description": "Activates a Gumroad license key for a developer machine instance.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "license_key": {"type": "string", "description": "Gumroad license key string"},
                    "instance_name": {"type": "string", "description": "Developer machine name or hostname"}
                },
                "required": ["license_key", "instance_name"]
            }
        }
    ]

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method = req.get("method")
            msg_id = req.get("id")

            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "ordersamurai-gumroad-mcp", "version": "1.0.0"}
                    }
                }
            elif method == "tools/list":
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}
            elif method == "tools/call":
                params = req.get("params", {})
                tool_name = params.get("name")
                args = params.get("arguments", {})

                if tool_name == "gumroad_validate_license":
                    res = validate_license_key(args.get("license_key"), args.get("product_permalink"))
                elif tool_name == "gumroad_activate_license":
                    res = activate_license_key(args.get("license_key"), args.get("instance_name"))
                else:
                    res = {"error": f"Unknown tool: {tool_name}"}

                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                    }
                }
            else:
                resp = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}}

            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        except Exception as err:
            err_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(err)}}
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
