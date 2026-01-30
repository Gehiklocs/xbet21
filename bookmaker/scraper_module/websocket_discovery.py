import asyncio
from playwright.async_api import async_playwright, WebSocket

async def discover_websockets(url: str):
    """
    Navigates to a URL and attempts to discover WebSocket endpoints.
    """
    print(f"🚀 Starting WebSocket discovery for: {url}")
    discovered_ws_urls = set()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()

        def on_websocket(ws: WebSocket):
            print(f"🌐 WebSocket opened: {ws.url}")
            discovered_ws_urls.add(ws.url)

        page.on("websocket", on_websocket)

        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            print("✅ Page loaded. Waiting for potential WebSocket activity...")
            # Give some time for dynamic content and WebSockets to establish
            await asyncio.sleep(10) 
        except Exception as e:
            print(f"❌ Error navigating to {url}: {e}")
        finally:
            await browser.close()

    if discovered_ws_urls:
        print("\n--- Discovered WebSocket URLs ---")
        for ws_url in discovered_ws_urls:
            print(f"- {ws_url}")
    else:
        print("\n--- No WebSocket URLs discovered ---")
    
    return list(discovered_ws_urls)

if __name__ == "__main__":
    # Replace with the actual URL you want to investigate
    target_url = "https://gstake.net/line/201" 
    asyncio.run(discover_websockets(target_url))
