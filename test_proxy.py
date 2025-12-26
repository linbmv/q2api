#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import httpx
import asyncio
import sys
import io

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PROXY_URL = "socks5://LinHome:wo20Fang1314520@home.linfj.com:61230"

async def test_proxy():
    print("Testing SOCKS5 proxy connection...")
    print(f"Proxy: {PROXY_URL}")

    try:
        transport = httpx.AsyncHTTPTransport(proxy=PROXY_URL)
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
            print("\nTesting connection to httpbin.org...")
            response = await client.get("https://httpbin.org/ip")
            print(f"✅ SUCCESS! Status: {response.status_code}")
            print(f"Response: {response.text}")

            print("\nTesting connection to Amazon Q endpoint...")
            response2 = await client.get("https://q.us-east-1.api.aws")
            print(f"✅ Amazon Q accessible! Status: {response2.status_code}")

    except Exception as e:
        print(f"❌ FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test_proxy())
