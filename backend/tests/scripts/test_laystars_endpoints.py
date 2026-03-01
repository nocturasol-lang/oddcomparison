#!/usr/bin/env python3.12
"""Test common Laystars API endpoint patterns"""

import asyncio
import aiohttp
from config import LAYSTARS_COOKIES

async def test_endpoint(session, base_url, path):
    url = f"{base_url}{path}"
    try:
        async with session.get(url, timeout=5) as resp:
            status = resp.status
            if status == 200:
                try:
                    data = await resp.json()
                    print(f"✅ {url:<60} {status} - Valid JSON")
                    return True, data
                except:
                    print(f"⚠️ {url:<60} {status} - Not JSON")
                    return False, None
            else:
                print(f"❌ {url:<60} {status}")
                return False, None
    except Exception as e:
        print(f"❌ {url:<60} Error: {str(e)[:30]}")
        return False, None

async def main():
    print("🔍 Testing Common Laystars API Patterns")
    print("=" * 80)
    
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": LAYSTARS_COOKIES
    }
    
    base_urls = [
        "https://www.laystars888.com",
        "https://api.laystars888.com",
        "https://ws.laystars888.com",
        "https://exchange.laystars888.com",
        "https://www.laystars888.com/exchange-service",
        "https://www.laystars888.com/api",
        "https://www.laystars888.com/v1",
        "https://www.laystars888.com/v2",
    ]
    
    paths = [
        "/inplay",
        "/sports/football/live",
        "/events/live",
        "/soccer/live",
        "/live/events",
        "/exchange/events/live",
        "/api/sports/1/events",
        "/v1/events?state=live",
        "/data/odds/live",
        "/feed/events",
        "/sport/football/inplay",
        "/inplay/list",
        "/left-menu/eventId",
    ]
    
    async with aiohttp.ClientSession(headers=headers) as session:
        working_endpoints = []
        
        for base in base_urls:
            print(f"\n📡 Testing base: {base}")
            for path in paths:
                success, data = await test_endpoint(session, base, path)
                if success and data:
                    working_endpoints.append(f"{base}{path}")
                    # Try to extract event IDs
                    if isinstance(data, dict):
                        print(f"   Keys: {list(data.keys())}")
                    elif isinstance(data, list):
                        print(f"   List length: {len(data)}")
        
        print("\n" + "=" * 80)
        print("✅ WORKING ENDPOINTS:")
        for url in working_endpoints:
            print(f"   {url}")

if __name__ == "__main__":
    asyncio.run(main())