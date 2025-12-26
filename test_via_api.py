#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test model support via the public API endpoint.
No need to access the database directly.
"""
import requests
import json
import sys
import io

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Configuration
API_BASE_URL = "https://a2q.150129.xyz"
API_KEY = "sk-wo20Fang1314520"

# Models to test
TEST_MODELS = [
    "claude-sonnet-4.5",
    "claude-sonnet-4",
    "claude-haiku-4.5",
    "claude-opus-4.5",
    "claude-opus-4-5-20251101",
    "claude-opus-4-5-20251101-thinking",
]


def test_model(model_name):
    """Test a model by sending a request to the API."""

    url = f"{API_BASE_URL}/v1/messages"
    headers = {
        "Content-Type": "application/json",
    }

    # Add API key if configured
    if API_KEY and API_KEY != "your-api-key":
        headers["Authorization"] = f"Bearer {API_KEY}"

    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": "Hi"}
        ],
        "max_tokens": 10,
        "stream": False
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)

        result = {
            "model": model_name,
            "status_code": response.status_code,
            "success": response.status_code == 200
        }

        if response.status_code == 200:
            # Check response to see which model was actually used
            data = response.json()
            result["response_model"] = data.get("model", "unknown")
        else:
            result["error"] = response.text[:200]

        return result

    except Exception as e:
        return {
            "model": model_name,
            "status_code": None,
            "success": False,
            "error": str(e)
        }


def main():
    """Run model tests via API."""

    # Check configuration
    if API_BASE_URL == "http://your-vps-ip:8000":
        print("⚠️  Please edit the script and set API_BASE_URL to your VPS address")
        print("Example: API_BASE_URL = 'http://123.456.789.0:8000'")
        sys.exit(1)

    print("Testing Models via API")
    print("=" * 70)
    print(f"API Endpoint: {API_BASE_URL}")
    print("=" * 70)

    results = []

    for model_name in TEST_MODELS:
        print(f"\nTesting: {model_name:45} ", end="", flush=True)
        result = test_model(model_name)
        results.append(result)

        if result["success"]:
            response_model = result.get("response_model", "?")
            if response_model == model_name:
                print(f"✅ OK (exact match)")
            else:
                print(f"⚠️  OK (mapped to: {response_model})")
        else:
            status = result.get("status_code", "ERR")
            print(f"❌ FAILED ({status})")

    # Summary
    print("\n" + "=" * 70)
    print("\nSUMMARY")
    print("-" * 70)

    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print(f"\n✅ Successful: {len(successful)}")
    for r in successful:
        resp_model = r.get("response_model", "?")
        if resp_model != r["model"]:
            print(f"   {r['model']} → {resp_model}")
        else:
            print(f"   {r['model']}")

    print(f"\n❌ Failed: {len(failed)}")
    for r in failed:
        print(f"   {r['model']}")

    # Save results
    with open("api_test_results.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nDetailed results saved to: api_test_results.json")
    print("\n💡 Check your VPS logs to see the actual model mapping:")
    print("   docker logs <container_name> | grep 'Model mapping'")


if __name__ == "__main__":
    main()
