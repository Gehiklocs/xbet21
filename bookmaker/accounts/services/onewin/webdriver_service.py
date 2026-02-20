import logging
import asyncio
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
import json
import os
import tempfile
import shutil

logger = logging.getLogger(__name__)


class PlaywrightService:
    """Service for creating and managing Playwright instances"""

    @staticmethod
    async def create_browser_with_proxy(proxy=None, headless=True, incognito=True):
        """
        Create Playwright browser with proxy configuration

        Args:
            proxy: Proxy model instance
            headless: Run in headless mode
            incognito: Run in incognito mode

        Returns:
            Tuple of (browser, context, page)
        """
        playwright = await async_playwright().start()

        try:
            launch_options = {
                'headless': headless,
                'args': [
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--window-size=1920,1080',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-notifications',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--disable-site-isolation-trials',
                ]
            }

            # Launch browser
            browser = await playwright.chromium.launch(**launch_options)

            # Configure proxy if provided
            proxy_options = {}
            if proxy and proxy.ip and proxy.port:
                proxy_options = {
                    'server': f'{proxy.type}://{proxy.ip}:{proxy.port}',
                }

                if proxy.username and proxy.password:
                    proxy_options['username'] = proxy.username
                    proxy_options['password'] = proxy.password

                logger.info(f"Using proxy: {proxy.ip}:{proxy.port}")

            # Create context with proxy
            context_options = {
                'viewport': {'width': 1920, 'height': 1080},
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'ignore_https_errors': True,
            }

            if proxy_options:
                context_options['proxy'] = proxy_options

            context = await browser.new_context(**context_options)

            # Create page
            page = await context.new_page()

            # Set timeouts
            page.set_default_timeout(30000)
            page.set_default_navigation_timeout(30000)

            logger.info("Playwright browser created successfully")

            return browser, context, page, playwright

        except Exception as e:
            logger.error(f"Failed to create Playwright browser: {str(e)}")
            if 'playwright' in locals():
                await playwright.stop()
            raise

    @staticmethod
    async def take_screenshot(page, filename, directory="/tmp/screenshots"):
        """Take screenshot and save it"""
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, filename)
            await page.screenshot(path=path, full_page=True)
            logger.info(f"Screenshot saved: {path}")
            return path
        except Exception as e:
            logger.error(f"Failed to take screenshot: {str(e)}")
            return None

    @staticmethod
    async def close_browser(browser, context, playwright):
        """Safely close Playwright browser"""
        try:
            if context:
                await context.close()
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()
            logger.info("Playwright browser closed successfully")
        except Exception as e:
            logger.error(f"Error closing Playwright browser: {str(e)}")

    @staticmethod
    def run_sync(coroutine):
        """
        Run async coroutine in sync context
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coroutine)
        finally:
            loop.close()