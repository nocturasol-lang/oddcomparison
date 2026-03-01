#!/usr/bin/env python3.12
"""Discover Laystars API endpoints by monitoring network traffic"""

import asyncio
import json
from playwright.async_api import async_playwright

async def discover_endpoints():
    print("🔍 Laystars API Discovery Tool")
    print("=" * 60)
    print("1. A browser window will open")
    print("2. Log in manually if needed")
    print("3. Wait for live events to load")
    print("4. API calls will be displayed here")
    print("=" * 60)
    
    async with async_playwright() as p:
        # Launch browser with network monitoring
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        # Track unique API endpoints
        api_calls = set()
        
        # Listen to all network requests
        page.on("request", lambda request: print(f"📡 Request: {request.method} {request.url}"))
        
        page.on("response", lambda response: asyncio.create_task(
            handle_response(response, api_calls)
        ))
        
        print("\n🌐 Opening Laystars...")
        await page.goto("https://www.laystars888.com/xch/")
        
        # Wait for manual login
        input("\n🔑 Press Enter after you've logged in and see live events...")
        
        # Wait a bit to capture more requests
        print("📡 Capturing API calls for 10 seconds...")
        await asyncio.sleep(10)
        
        # Show unique API endpoints found
        print("\n" + "=" * 60)
        print("📊 UNIQUE API ENDPOINTS FOUND:")
        print("=" * 60)
        for url in sorted(api_calls):
            if any(x in url for x in ['api', 'exchange', 'v1', 'v2', 'data', 'odds', 'event']):
                print(f"✅ {url}")
        
        await browser.close()

async def handle_response(response, api_calls):
    """Process each response"""
    url = response.url
    status = response.status
    
    # Only care about successful JSON responses
    if status == 200 and 'application/json' in response.headers.get('content-type', ''):
        api_calls.add(url)
        try:
            data = await response.json()
            print(f"\n📨 Success: {status} {url}")
            print(f"   Response preview: {json.dumps(data)[:200]}")
        except:
            pass

if __name__ == "__main__":
    asyncio.run(discover_endpoints())