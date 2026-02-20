import time
from typing import Tuple, Dict
from django.conf import settings
from .models import OneWinAccount
from .services.playwright_service import PlaywrightService
import logging

logger = logging.getLogger(__name__)


class OneWinLoginService:
    """Service for logging into 1Win accounts using Playwright"""

    def __init__(self, account: OneWinAccount):
        self.account = account
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.logs = []

    def login_to_1win(self) -> Tuple[bool, str, Dict]:
        """
        Login to 1Win account using Playwright

        Returns:
            Tuple of (success, message, session_data)
        """
        try:
            print("\n" + "=" * 60)
            print(f"🔐 LOGIN TO 1WIN: {self.account.username}")
            print("=" * 60)

            self._add_log("Starting 1Win login process with Playwright")

            # Run async Playwright in sync context
            result = PlaywrightService.run_sync(self._async_login_to_1win())
            return result

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            print(f"❌ {error_msg}")
            self._add_log(f"ERROR: {error_msg}")
            return False, error_msg, {}

    async def _async_login_to_1win(self) -> Tuple[bool, str, Dict]:
        """Async implementation of login"""
        try:
            # Create Playwright browser
            headless = not getattr(settings, 'DEBUG', False)

            (self.browser, self.context, self.page,
             self.playwright) = await PlaywrightService.create_browser_with_proxy(
                proxy=self.account.proxy,
                headless=headless,
                incognito=True
            )

            print("✅ Browser opened")
            if self.account.proxy:
                print(f"✅ Using proxy: {self.account.proxy.ip}:{self.account.proxy.port}")

            # Navigate to 1Win
            success, message = await self._navigate_to_1win()
            if not success:
                return False, message, {}

            # Perform login
            success, message = await self._perform_login()

            if success:
                # Get session data
                session_data = await self._get_session_data()

                print("=" * 60)
                print("✅ LOGIN SUCCESSFUL!")
                print(f"   Account: {self.account.username}")
                print(f"   Session: {len(session_data.get('cookies', []))} cookies")
                print("=" * 60 + "\n")

                return True, "Login successful", session_data
            else:
                print("=" * 60)
                print("❌ LOGIN FAILED")
                print(f"   Error: {message}")
                print("=" * 60 + "\n")

                return False, message, {}

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            print(f"❌ {error_msg}")
            self._add_log(f"ERROR: {error_msg}")
            return False, error_msg, {}

        finally:
            await self._close_browser()

    async def _navigate_to_1win(self) -> Tuple[bool, str]:
        """Navigate to 1Win website"""
        try:
            print("🌐 Navigating to 1Win...")
            self._add_log("Navigating to https://1wuswz.com/")

            # Add stealth to avoid detection
            await self._add_stealth()

            # Navigate with retry
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await self.page.goto("https://1wuswz.com/",
                                         wait_until="networkidle",
                                         timeout=30000)

                    # Wait for page to be interactive
                    await self.page.wait_for_load_state("networkidle", timeout=10000)

                    # Check if page loaded successfully
                    page_title = await self.page.title()
                    print(f"✅ Page loaded: {page_title}")
                    self._add_log(f"Page loaded: {page_title}")

                    # Take screenshot
                    await PlaywrightService.take_screenshot(
                        self.page,
                        f"1win_nav_{self.account.username}.png"
                    )

                    return True, "Navigation successful"

                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"⚠️ Navigation attempt {attempt + 1} failed, retrying...")
                        await asyncio.sleep(2)
                    else:
                        raise e

        except Exception as e:
            error_msg = f"Navigation error: {str(e)}"
            print(f"❌ {error_msg}")
            # Take screenshot of error
            await PlaywrightService.take_screenshot(
                self.page,
                f"1win_nav_error_{self.account.username}.png"
            )
            return False, error_msg

    async def _add_stealth(self):
        """Add stealth features to avoid bot detection"""
        # Overwrite navigator.webdriver
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // Overwrite plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // Overwrite languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
        """)

    async def _perform_login(self) -> Tuple[bool, str]:
        """Perform login with account credentials"""
        try:
            print("🔍 Looking for login elements...")

            # Find login button
            login_button = await self._find_login_button()
            if not login_button:
                return False, "Login button not found"

            print("✅ Login button found")
            await login_button.click()
            await self.page.wait_for_timeout(2000)

            # Find login form
            username_field, password_field = await self._find_login_fields()
            if not username_field or not password_field:
                return False, "Login form fields not found"

            print("✅ Login form found")

            # Fill credentials
            print("⌨️ Filling credentials...")
            await username_field.fill(self.account.username)
            await password_field.fill(self.account.password)

            # Take screenshot
            await PlaywrightService.take_screenshot(
                self.page,
                f"1win_form_{self.account.username}.png"
            )

            # Submit form
            print("🚀 Submitting login...")
            submit_success = await self._submit_login_form()
            if not submit_success:
                return False, "Submit button not found"

            # Wait for login to process
            await self.page.wait_for_timeout(3000)

            # Check login success
            success = await self._check_login_success()
            if success:
                return True, "Login successful"
            else:
                # Check for error messages
                error_text = await self._get_error_message()
                if error_text:
                    return False, f"Login failed: {error_text}"
                return False, "Login failed - check credentials"

        except Exception as e:
            error_msg = f"Login error: {str(e)}"
            # Take screenshot of error
            await PlaywrightService.take_screenshot(
                self.page,
                f"1win_login_error_{self.account.username}.png"
            )
            return False, error_msg

    async def _find_login_button(self):
        """Find login button on the page"""
        selectors = [
            "a:has-text('Login')",
            "a:has-text('Sign In')",
            "button:has-text('Login')",
            "button:has-text('Sign In')",
            "#login",
            "#signin",
            "text=Вход",
            "text=Войти",
            ".login-button",
            ".signin-button",
            "[data-testid='login-button']",
        ]

        for selector in selectors:
            try:
                element = self.page.locator(selector).first
                if await element.is_visible(timeout=5000):
                    return element
            except:
                continue

        # Try to find by common patterns
        try:
            all_buttons = await self.page.query_selector_all("a, button")
            for button in all_buttons:
                text = await button.inner_text()
                if any(word in text.lower() for word in ['login', 'sign in', 'вход', 'войти']):
                    return button
        except:
            pass

        return None

    async def _find_login_fields(self):
        """Find username and password fields"""
        username_field = None
        password_field = None

        # Try common field names and types
        username_selectors = [
            "input[name='username']",
            "input[name='login']",
            "input[name='user']",
            "input[name='email']",
            "input[type='text']",
            "input[type='email']",
            "#username",
            "#login",
            "#email",
        ]

        password_selectors = [
            "input[name='password']",
            "input[name='pass']",
            "input[name='pwd']",
            "input[type='password']",
            "#password",
        ]

        for selector in username_selectors:
            try:
                field = self.page.locator(selector).first
                if await field.is_visible(timeout=5000):
                    username_field = field
                    break
            except:
                continue

        for selector in password_selectors:
            try:
                field = self.page.locator(selector).first
                if await field.is_visible(timeout=5000):
                    password_field = field
                    break
            except:
                continue

        return username_field, password_field

    async def _submit_login_form(self):
        """Submit the login form"""
        selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Login')",
            "button:has-text('Sign In')",
            "button:has-text('Войти')",
            "button:has-text('Вход')",
            ".submit-button",
        ]

        for selector in selectors:
            try:
                element = self.page.locator(selector).first
                if await element.is_visible(timeout=5000):
                    await element.click()
                    return True
            except:
                continue

        # Try pressing Enter
        try:
            await self.page.keyboard.press("Enter")
            return True
        except:
            return False

    async def _check_login_success(self):
        """Check if login was successful"""
        try:
            # Wait for page to update
            await self.page.wait_for_timeout(2000)

            # Check for success indicators in page content
            content = await self.page.content()
            content_lower = content.lower()

            success_words = ['welcome', 'dashboard', 'balance', 'баланс', 'logout', 'выйти', 'my account', 'аккаунт']
            for word in success_words:
                if word in content_lower:
                    return True

            # Check current URL
            current_url = self.page.url.lower()
            if 'login' not in current_url and 'signin' not in current_url and 'auth' not in current_url:
                return True

            return False

        except:
            return False

    async def _get_error_message(self):
        """Get error message if login failed"""
        try:
            # Look for common error message selectors
            error_selectors = [
                ".error",
                ".error-message",
                ".alert-danger",
                ".alert-error",
                "[role='alert']",
                "text=неверный",
                "text=incorrect",
                "text=error",
                "text=ошибка",
            ]

            for selector in error_selectors:
                try:
                    element = self.page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        return await element.inner_text()
                except:
                    continue
        except:
            pass

        return None

    async def _get_session_data(self) -> Dict:
        """Get session data from the browser"""
        cookies = await self.context.cookies()
        storage_state = await self.context.storage_state()

        return {
            'cookies': cookies,
            'storage_state': storage_state,
            'current_url': self.page.url,
            'title': await self.page.title(),
            'timestamp': time.time(),
        }

    def _add_log(self, message: str):
        """Add log message"""
        timestamp = time.strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.logs.append(log_entry)
        print(f"📝 {log_entry}")

    async def _close_browser(self):
        """Close Playwright browser"""
        await PlaywrightService.close_browser(self.browser, self.context, self.playwright)
        print("✅ Browser closed")