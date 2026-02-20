# accounts/services/webDriver/selenium_undetectable.py

import asyncio
import random
import time
import os
from typing import Optional, Dict, Tuple
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
import undetected_chromedriver as uc

from .human_behavior import HumanBehavior
from .fingerprint_manager import FingerprintManager


class SeleniumUndetectableBrowser:
    """Creates undetectable browser instances using undetected-chromedriver"""

    def __init__(self):
        self.driver = None
        self.fingerprint = None
        self.actions = None

    async def launch(self, headless: bool = False, proxy: Dict = None, user_data_dir: str = None) -> Tuple[any, Dict]:
        """Launch undetectable browser with minimal but effective options."""

        self.fingerprint = FingerprintManager.get_random_fingerprint()

        options = uc.ChromeOptions()

        # Only essential arguments – undetected-chromedriver handles the rest
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument(
            f'--window-size={self.fingerprint["viewport"]["width"]},{self.fingerprint["viewport"]["height"]}')
        options.add_argument(f'--user-agent={self.fingerprint["user_agent"]}')
        options.add_argument(f'--lang={self.fingerprint["languages"][0]}')

        if proxy and proxy.get('server'):
            options.add_argument(f'--proxy-server={proxy["server"]}')

        if user_data_dir:
            options.add_argument(f'--user-data-dir={user_data_dir}')

        # Do NOT add experimental options – undetected-chromedriver adds them internally

        try:
            self.driver = uc.Chrome(
                options=options,
                headless=headless,
                version_main=144,
            )

            self.actions = ActionChains(self.driver)

            # Additional stealth via CDP
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    window.chrome = { runtime: {} };
                '''
            })

            await HumanBehavior.random_delay(1, 3)
            return self.driver, self.fingerprint

        except Exception as e:
            print(f"❌ Failed to launch undetected Chrome: {e}")
            import traceback
            traceback.print_exc()
            raise

    async def navigate_with_human(self, url: str, max_retries: int = 3):
        """Navigate like a human using Selenium"""

        for attempt in range(max_retries):
            try:
                # Random delay before navigation
                await HumanBehavior.random_delay(1, 4)

                print(f"🌐 Navigating to {url} (attempt {attempt + 1}/{max_retries})")

                # Navigate
                self.driver.get(url)

                # Wait for page to load
                await self._wait_for_page_load()

                # Handle potential Cloudflare
                cloudflare_handled = await self._handle_cloudflare()

                if cloudflare_handled:
                    # Wait like a human after successful navigation
                    await HumanBehavior.human_wait_for_page_load_selenium(self.driver)
                    return True
                else:
                    if attempt < max_retries - 1:
                        wait_time = random.uniform(5, 10) * (attempt + 1)
                        print(f"⏳ Cloudflare issue. Waiting {wait_time:.1f}s...")
                        await asyncio.sleep(wait_time)

            except Exception as e:
                print(f"⚠️ Navigation error (attempt {attempt + 1}): {e}")

                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + random.uniform(1, 5)
                    print(f"⏳ Waiting {wait_time:.1f}s before retry...")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"❌ Navigation failed after {max_retries} attempts")
                    return False

        return False

    async def _wait_for_page_load(self, timeout: int = 30):
        """Wait for page to load completely"""
        try:
            # Wait for document ready state
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )

            # Wait for no network activity for 2 seconds
            await asyncio.sleep(2)

        except Exception as e:
            print(f"⚠️ Page load wait error: {e}")

    async def _handle_cloudflare(self, max_wait: int = 30) -> bool:
        """Handle Cloudflare challenges"""
        try:
            # Check for Cloudflare
            page_source = self.driver.page_source
            current_url = self.driver.current_url

            cloudflare_patterns = [
                'cf-browser-verification',
                'cloudflare',
                'cf-challenge',
                'cf-ray',
                'checking your browser',
                '请稍候',
                'attention required',
                'your browser is being checked'
            ]

            is_cloudflare = any(pattern in page_source.lower() for pattern in cloudflare_patterns)

            if is_cloudflare or 'cf' in current_url.lower():
                print("☁️ Cloudflare detected, waiting for verification...")

                start_time = time.time()

                while (time.time() - start_time) < max_wait:
                    await asyncio.sleep(2)

                    # Check if we've passed Cloudflare
                    current_source = self.driver.page_source
                    current_url = self.driver.current_url

                    if ('1wuswz.com' in current_url and
                            'cf' not in current_url.lower() and
                            not any(pattern in current_source.lower() for pattern in cloudflare_patterns)):
                        elapsed = time.time() - start_time
                        print(f"✅ Cloudflare passed after {elapsed:.1f} seconds")

                        await HumanBehavior.random_delay(2, 5)
                        return True

                    # Add human-like behavior while waiting
                    if int(time.time() - start_time) % 5 == 0:
                        try:
                            # Random mouse movement
                            await self._random_mouse_movement()
                        except:
                            pass

                print("⚠️ Cloudflare timeout")
                return False

            return True

        except Exception as e:
            print(f"⚠️ Cloudflare handling error: {e}")
            return False

    async def _random_mouse_movement(self):
        """Generate random mouse movement"""
        try:
            viewport_width = self.driver.execute_script("return window.innerWidth")
            viewport_height = self.driver.execute_script("return window.innerHeight")

            x = random.randint(100, viewport_width - 100)
            y = random.randint(100, viewport_height - 100)

            self.actions.move_by_offset(x, y).perform()
            self.actions.reset_actions()

            await asyncio.sleep(random.uniform(0.1, 0.3))

        except:
            pass

    async def close(self):
        """Close the browser"""
        await HumanBehavior.random_delay(0.5, 1.5)
        if self.driver:
            self.driver.quit()

    async def get_cookies(self) -> list:
        """Get all cookies"""
        return self.driver.get_cookies()

    async def add_cookies(self, cookies: list):
        """Add cookies to browser"""
        for cookie in cookies:
            try:
                self.driver.add_cookie(cookie)
            except:
                pass

    async def execute_script(self, script: str, *args):
        """Execute JavaScript"""
        return self.driver.execute_script(script, *args)

    async def find_element(self, by: str, value: str):
        """Find element with wait"""
        return WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((by, value))
        )

    async def human_click(self, by: str, value: str):
        """Human-like click on element"""
        element = await self.find_element(by, value)

        # Move to element with random offset
        self.actions.move_to_element_with_offset(
            element,
            random.randint(1, 10),
            random.randint(1, 10)
        ).perform()

        await HumanBehavior.random_delay(0.1, 0.3)

        # Click
        self.actions.click().perform()

        await HumanBehavior.random_delay(0.2, 0.5)

    async def human_type(self, by: str, value: str, text: str):
        """Human-like typing"""
        element = await self.find_element(by, value)

        # Click on element first
        await self.human_click(by, value)

        # Clear field
        element.clear()
        await HumanBehavior.random_delay(0.2, 0.4)

        # Type character by character
        for char in text:
            element.send_keys(char)
            await asyncio.sleep(random.uniform(0.05, 0.15))

        await HumanBehavior.random_delay(0.3, 0.7)