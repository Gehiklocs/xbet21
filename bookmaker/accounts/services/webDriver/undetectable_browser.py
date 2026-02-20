# accounts/services/webDriver/undetectable_browser.py

import asyncio
import random
from typing import Optional, Dict, Tuple

# Added async_playwright, removed the unused Playwright base class import
from playwright.async_api import async_playwright, Page

# Removed the incompatible puppeteer_extra_plugin_stealth import entirely

from .human_behavior import HumanBehavior
from .fingerprint_manager import FingerprintManager


class UndetectableBrowser:
    """Creates undetectable browser instances using playwright-extra and stealth plugin."""

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.fingerprint = None

    async def launch(
            self,
            headless: bool = False,
            proxy: Optional[Dict] = None,
            user_data_dir: Optional[str] = None,
    ) -> Tuple[Page, Dict]:
        """Launch a browser with stealth and human‑like fingerprint."""
        self.fingerprint = FingerprintManager.get_random_fingerprint()

        # Browser arguments
        args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-features=IsolateOrigins,site-per-process',
            '--no-sandbox',
            f'--timezone={self.fingerprint["timezone"]}',
            f'--lang={self.fingerprint["languages"][0]}',
        ]
        if user_data_dir:
            args.append(f'--user-data-dir={user_data_dir}')

        # Use standard async_playwright
        self.playwright = await async_playwright().start()

        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=args,
            proxy=proxy,
        )

        self.context = await self.browser.new_context(
            viewport=self.fingerprint['viewport'],
            user_agent=self.fingerprint['user_agent'],
            locale=self.fingerprint['languages'][0],
            timezone_id=self.fingerprint['timezone'],
            ignore_https_errors=True,
            proxy=proxy,
        )

        self.page = await self.context.new_page()

        # Apply our custom stealth (no extra plugins needed)
        await self._apply_stealth()

        await HumanBehavior.random_delay(1, 3)
        return self.page, self.fingerprint

    async def _apply_stealth(self):
        """Apply stealth techniques using standard Playwright."""
        await self.page.add_init_script("""
            // Hide webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            // Add chrome object
            window.chrome = { runtime: {} };
            // Set languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            // Add plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
        """)

    async def _apply_extra_stealth(self):
        """Complement the stealth plugin with some custom overrides."""
        await self.page.add_init_script("""
            // Override languages again to be safe
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            // Ensure webdriver is hidden
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

    async def navigate_with_human(self, url: str, max_retries: int = 3) -> bool:
        """Navigate to URL with human‑like delays and Cloudflare handling."""
        for attempt in range(max_retries):
            try:
                await HumanBehavior.random_delay(1, 4)
                response = await self.page.goto(
                    url,
                    wait_until='domcontentloaded',
                    timeout=60000
                )

                if response and response.status >= 400:
                    print(f"⚠️ HTTP {response.status}")
                    if response.status == 429:
                        await asyncio.sleep(random.uniform(15, 30))
                        continue

                if await self._handle_cloudflare():
                    await HumanBehavior.human_wait_for_page_load(self.page)
                    return True

                # Cloudflare not fully handled – retry
                if attempt < max_retries - 1:
                    wait = random.uniform(5, 15) * (attempt + 1)
                    print(f"⏳ Cloudflare issue, retrying in {wait:.1f}s...")
                    await asyncio.sleep(wait)
                else:
                    print("❌ Cloudflare could not be bypassed.")
                    return False

            except Exception as e:
                print(f"⚠️ Navigation error (attempt {attempt+1}): {e}")
                if attempt == max_retries - 1:
                    return False
                await asyncio.sleep(random.uniform(5, 15))

        return False

    async def _handle_cloudflare(self, max_wait: int = 30) -> bool:
        """Wait for Cloudflare challenge to pass."""
        start = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start) < max_wait:
            content = await self.page.content()
            url = self.page.url

            # Cloudflare patterns
            if any(p in content.lower() for p in [
                'cf-browser-verification', 'cloudflare', 'checking your browser'
            ]) or 'cf' in url.lower():
                await asyncio.sleep(2)
                # Human‑like micro‑movements while waiting
                if random.random() > 0.7:
                    vp = self.page.viewport_size
                    if vp:
                        x = random.randint(100, vp['width'] - 100)
                        y = random.randint(100, vp['height'] - 100)
                        await HumanBehavior.human_mouse_movement(self.page, x, y)
                continue
            else:
                # No Cloudflare detected or passed
                elapsed = asyncio.get_event_loop().time() - start
                if elapsed > 2:  # Only log if we actually waited
                    print(f"✅ Cloudflare passed after {elapsed:.1f}s")
                await HumanBehavior.random_delay(2, 5)
                return True
        return False

    async def close(self):
        """Clean up resources."""
        await HumanBehavior.random_delay(0.5, 1.5)
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()