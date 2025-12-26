#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script to verify which Amazon Q model IDs are actually supported.
Tests directly against Amazon Q backend, bypassing the VPS API layer.
"""
import asyncio
import httpx
import json
import os
import sys
import io
from typing import Dict, Any, Optional

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Test models to verify (comprehensive list)
TEST_MODELS = [
    # Short names
    "claude-sonnet-4.5",
    "claude-sonnet-4",
    "claude-haiku-4.5",
    "claude-opus-4.5",
    # Canonical names with dates
    "claude-sonnet-4-20250514",
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-5-20251101",
    # Thinking variants
    "claude-sonnet-4-5-20250929-thinking",
    "claude-opus-4-5-20251101-thinking",
    # Legacy 3.5
    "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-20240620",
]

# Amazon Q endpoint
Q_ENDPOINT = "https://q.us-east-1.api.aws/workspaces-streaming"

# VPS API endpoint to get access token
VPS_API = "https://a2q.150129.xyz"

# SOCKS5 Proxy configuration
PROXY_URL = "socks5://LinHome:wo20Fang1314520@home.linfj.com:61230"


async def get_access_token_from_vps() -> Optional[str]:
    """Get access token from VPS database via API."""
    print("Fetching access token from VPS database...")

    # Try to get console token from environment
    console_token = os.getenv("CONSOLE_TOKEN", "")

    headers = {"Content-Type": "application/json"}
    if console_token:
        headers["Authorization"] = f"Bearer {console_token}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get list of accounts
            response = await client.get(f"{VPS_API}/v2/accounts", headers=headers)

            if response.status_code == 200:
                data = response.json()
                # API returns {"accounts": [...], "count": ...}
                accounts = data.get("accounts", []) if isinstance(data, dict) else data

                if accounts and len(accounts) > 0:
                    # Use first enabled account
                    for account in accounts:
                        if account.get("enabled", False):
                            access_token = account.get("accessToken")
                            if access_token:
                                email = account.get("email", "unknown")
                                print(f"✅ Got access token for account: {email}")
                                return access_token

                    print("❌ No enabled accounts found")
                else:
                    print("❌ No accounts in database")
            else:
                print(f"❌ Failed to get accounts: HTTP {response.status_code}")
                print(f"   Response: {response.text[:200]}")

    except Exception as e:
        print(f"❌ Error fetching access token: {e}")

    return None


async def test_model(access_token: str, model_id: str) -> Dict[str, Any]:
    """Test if a model ID is supported by Amazon Q."""

    payload = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "Hello",
                    "userInputMessageContext": {
                        "envState": {
                            "operatingSystem": "macos",
                            "currentWorkingDirectory": "/"
                        }
                    },
                    "origin": "KIRO_CLI",
                    "modelId": model_id
                }
            },
            "chatTriggerType": "MANUAL"
        }
    }

    headers = {
        "authorization": f"Bearer {access_token}",
        "content-type": "application/json",
    }

    try:
        # Use SOCKS5 proxy for Amazon Q endpoint
        from httpx import AsyncHTTPTransport

        transport = AsyncHTTPTransport(proxy=PROXY_URL)
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
            response = await client.post(Q_ENDPOINT, json=payload, headers=headers)

            result = {
                "model": model_id,
                "status_code": response.status_code,
                "supported": response.status_code == 200,
            }

            if response.status_code != 200:
                try:
                    error_data = response.json()
                    result["error"] = error_data
                except:
                    result["error"] = response.text[:200]
            else:
                result["success"] = True

            return result

    except Exception as e:
        return {
            "model": model_id,
            "status_code": None,
            "supported": False,
            "error": str(e)
        }


async def main():
    """Run model ID tests."""

    print("=" * 70)
    print("Amazon Q Direct Model Testing")
    print("=" * 70)
    print()

    # Try to get access token from environment first
    access_token = os.getenv("Q_ACCESS_TOKEN")

    if not access_token:
        print("Q_ACCESS_TOKEN not in environment, trying to fetch from VPS...")
        access_token = await get_access_token_from_vps()

    if not access_token:
        print()
        print("=" * 70)
        print("ERROR: Cannot get access token")
        print("=" * 70)
        print()
        print("Options:")
        print("1. Set Q_ACCESS_TOKEN environment variable:")
        print("   export Q_ACCESS_TOKEN='your-token'")
        print()
        print("2. Set CONSOLE_TOKEN to access VPS database:")
        print("   export CONSOLE_TOKEN='your-console-token'")
        print()
        return

    print()
    print("Testing Amazon Q Model IDs (Direct Backend)")
    print("=" * 70)

    results = []
    for model_id in TEST_MODELS:
        print(f"\nTesting: {model_id:50} ", end="", flush=True)
        result = await test_model(access_token, model_id)
        results.append(result)

        if result["supported"]:
            print("✅ SUPPORTED")
        else:
            status = result.get("status_code", "ERR")
            print(f"❌ FAILED ({status})")

    print("\n" + "=" * 70)
    print("\nSUMMARY")
    print("-" * 70)

    supported = [r for r in results if r["supported"]]
    unsupported = [r for r in results if not r["supported"]]

    print(f"\n✅ Supported models ({len(supported)}):")
    for r in supported:
        print(f"   {r['model']}")

    print(f"\n❌ Unsupported models ({len(unsupported)}):")
    for r in unsupported:
        status = r.get("status_code", "ERR")
        print(f"   {r['model']} (HTTP {status})")

    # Save results to JSON
    output_file = "amazonq_direct_test_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nDetailed results saved to: {output_file}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
