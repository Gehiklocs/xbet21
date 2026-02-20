import asyncio
import time
import logging
import os
import sys
import random
import subprocess
from typing import Tuple, Dict, Optional
from django.conf import settings
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from ..models import OneWinAccount

logger = logging.getLogger(__name__)


class ServerOneWinLogin:
    """1Win login service with robust proxy handling"""

    def __init__(self, account: OneWinAccount):
        self.account = account
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.use_proxy = True  # Can be disabled if proxy fails
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]

    def login(self) -> Tuple[bool, str, Dict]:
        """Main login method with proxy fallback"""
        try:
            print(f"\n🎯 1WIN LOGIN: {self.account.email or self.account.username}")
            print(f"🔗 Proxy: {self.account.proxy.ip if self.account.proxy else 'No proxy'}")

            # Run async
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._async_login_with_fallback())
            finally:
                loop.close()

        except Exception as e:
            error_msg = f"Login error: {str(e)}"
            print(f"❌ {error_msg}")
            logger.error(error_msg, exc_info=True)
            return False, error_msg, {}

    async def _async_login_with_fallback(self) -> Tuple[bool, str, Dict]:
        """Try multiple login strategies"""
        strategies = [
            self._try_login_with_proxy,
            self._try_login_without_proxy,
            self._try_login_with_simple_browser,
        ]

        for i, strategy in enumerate(strategies):
            print(f"\n🔄 Attempt {i + 1}/{len(strategies)}: {strategy.__name__}")
            try:
                success, message, data = await strategy()
                if success:
                    return True, message, data
            except Exception as e:
                print(f"⚠️ Strategy failed: {str(e)[:100]}...")
                await self._cleanup()
                continue

        return False, "All login strategies failed", {}

    async def _try_login_with_proxy(self) -> Tuple[bool, str, Dict]:
        """Try login with proxy"""
        try:
            print("🚀 Starting Playwright with proxy...")
            self.playwright = await async_playwright().start()

            # Test proxy connectivity first
            proxy_works = await self._test_proxy_connectivity()
            if not proxy_works:
                print("⚠️ Proxy test failed, skipping proxy strategy")
                return False, "Proxy not working", {}

            # Launch with proxy
            success = await self._launch_browser_with_proxy_fixed()
            if not success:
                return False, "Failed to launch browser with proxy", {}

            # Navigate
            navigate_success = await self._navigate_with_proxy()
            if not navigate_success:
                return False, "Failed to navigate with proxy", {}

            # Login
            login_success = await self._perform_js_login()
            if not login_success:
                return False, "Login failed", {}

            # Get session data
            cookies = await self.context.cookies()
            session_data = {
                'cookies': cookies,
                'url': self.page.url,
                'title': await self.page.title(),
                'timestamp': time.time(),
                'login_method': 'proxy',
            }

            return True, "Login successful with proxy", session_data

        except Exception as e:
            print(f"❌ Proxy strategy error: {str(e)}")
            return False, f"Proxy error: {str(e)}", {}

    async def _try_login_without_proxy(self) -> Tuple[bool, str, Dict]:
        """Try login without proxy"""
        try:
            print("🚀 Starting Playwright without proxy...")
            self.playwright = await async_playwright().start()

            # Launch without proxy
            success = await self._launch_browser_without_proxy()
            if not success:
                return False, "Failed to launch browser", {}

            # Navigate
            navigate_success = await self._navigate_without_proxy()
            if not navigate_success:
                return False, "Failed to navigate", {}

            # Login
            login_success = await self._perform_js_login()
            if not login_success:
                return False, "Login failed", {}

            # Get session data
            cookies = await self.context.cookies()
            session_data = {
                'cookies': cookies,
                'url': self.page.url,
                'title': await self.page.title(),
                'timestamp': time.time(),
                'login_method': 'no_proxy',
            }

            return True, "Login successful without proxy", session_data

        except Exception as e:
            print(f"❌ No-proxy strategy error: {str(e)}")
            return False, f"No-proxy error: {str(e)}", {}

    async def _try_login_with_simple_browser(self) -> Tuple[bool, str, Dict]:
        """Simple browser login with step-by-step clicks"""
        try:
            print("🚀 Starting browser (visible mode for debugging)...")
            self.playwright = await async_playwright().start()

            # Launch with visible browser for debugging
            self.browser = await self.playwright.chromium.launch(
                headless=False,  # Visible for debugging
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--window-size=1400,900',
                    '--start-maximized'
                ]
            )

            self.context = await self.browser.new_context(
                viewport={'width': 1400, 'height': 900},
                user_agent=random.choice(self.user_agents),
            )

            self.page = await self.context.new_page()
            self.page.set_default_timeout(30000)

            # Create screenshot directory
            screenshot_dir = "/tmp/1win_screenshots"
            os.makedirs(screenshot_dir, exist_ok=True)

            print("🌐 Step 1: Navigating to 1Win...")
            try:
                await self.page.goto('https://1wuswz.com/', timeout=30000, wait_until='networkidle')
                print("✅ Connected to 1Win")
                await self.page.screenshot(path=f"{screenshot_dir}/01_loaded.png")
            except Exception as e:
                print(f"❌ Navigation failed: {str(e)}")
                return False, "Navigation failed", {}

            # Wait a moment
            await self.page.wait_for_timeout(2000)

            print("🔍 Step 2: Looking for login button by text...")

            # List of possible login texts
            login_texts = [
                "Login", "Sign In", "Вход", "Войти", "Log in", "LOGIN", "SIGN IN"
            ]

            login_clicked = False
            login_screenshot_taken = False

            for login_text in login_texts:
                try:
                    print(f"   Looking for: '{login_text}'")

                    # Find all elements with this text
                    elements = await self.page.query_selector_all(f"text='{login_text}'")

                    if elements:
                        print(f"   Found {len(elements)} elements with text '{login_text}'")

                        for i, element in enumerate(elements):
                            try:
                                # Check if visible
                                is_visible = await element.is_visible()
                                print(f"   Element {i + 1}: visible={is_visible}")

                                if is_visible:
                                    # Get element info
                                    tag = await element.evaluate("el => el.tagName")
                                    print(f"   Tag: {tag}")

                                    # Take screenshot before click
                                    if not login_screenshot_taken:
                                        await self.page.screenshot(path=f"{screenshot_dir}/02_before_login_click.png")
                                        login_screenshot_taken = True

                                    # Click the login button
                                    print(f"🖱️ Clicking '{login_text}' button...")
                                    await element.click()

                                    login_clicked = True
                                    print(f"✅ Clicked login button with text: '{login_text}'")

                                    # Wait for page to respond
                                    await self.page.wait_for_timeout(3000)
                                    await self.page.screenshot(path=f"{screenshot_dir}/03_after_login_click.png")

                                    break

                            except Exception as e:
                                print(f"   Element {i + 1} error: {str(e)[:100]}")

                    if login_clicked:
                        break

                except Exception as e:
                    print(f"   Search for '{login_text}' failed: {str(e)[:100]}")

            if not login_clicked:
                print("❌ Could not find/click login button")
                await self.page.screenshot(path=f"{screenshot_dir}/error_no_login_button.png")
                return False, "Login button not found", {}

            print("🔍 Step 3: Looking for email login option...")

            # After clicking login, there might be multiple options (email, phone, etc.)
            # Look for email button or tab
            email_options = [
                "Email", "E-mail", "Электронная почта", "Почта", "По email",
                "text='Email'", "text='E-mail'", "text='email'",
                "button:has-text('Email')", "button:has-text('E-mail')",
                "[data-type='email']", ".email-tab", "#email-tab"
            ]

            email_button_clicked = False

            for option in email_options:
                try:
                    print(f"   Looking for email option: {option}")

                    if option.startswith("text="):
                        elements = await self.page.query_selector_all(option)
                    else:
                        elements = await self.page.query_selector_all(option)

                    if elements:
                        for i, element in enumerate(elements):
                            try:
                                if await element.is_visible():
                                    print(f"   Found email option {i + 1}")

                                    # Take screenshot before clicking email
                                    await self.page.screenshot(path=f"{screenshot_dir}/04_before_email_option.png")

                                    # Click email option
                                    print("🖱️ Clicking email login option...")
                                    await element.click()

                                    email_button_clicked = True
                                    print("✅ Clicked email option")

                                    # Wait for form to appear
                                    await self.page.wait_for_timeout(2000)
                                    await self.page.screenshot(path=f"{screenshot_dir}/05_after_email_option.png")

                                    break

                            except Exception as e:
                                print(f"   Email option {i + 1} error: {str(e)[:100]}")

                    if email_button_clicked:
                        break

                except Exception as e:
                    print(f"   Email option search error: {str(e)[:100]}")

            # If no specific email button found, assume we're already on email form
            if not email_button_clicked:
                print("⚠️ No specific email button found, assuming email form is already shown")
                await self.page.screenshot(path=f"{screenshot_dir}/05_email_form_already.png")

            print("🔍 Step 4: Looking for email input field...")

            # Wait a bit for form to load
            await self.page.wait_for_timeout(2000)

            # Take screenshot of current form
            await self.page.screenshot(path=f"{screenshot_dir}/06_current_form.png")

            # Try different email field selectors
            email_field = None
            email_selectors = [
                '#v-16-0-2',
                'input[name="email"]',
                'input[type="email"]',
                'input[placeholder*="email"]',
                'input[placeholder*="Email"]',
                'input[placeholder*="E-mail"]',
                'input[placeholder*="почта"]',
                'input[placeholder*="Почта"]',
                'input#email',
                '.email-input',
                '[data-testid="email-input"]',
                'input[autocomplete="email"]',
                'input[autocomplete="username"]',
            ]

            for selector in email_selectors:
                try:
                    print(f"   Trying selector: {selector}")
                    element = await self.page.wait_for_selector(selector, timeout=2000, state='visible')
                    if element:
                        email_field = element
                        print(f"✅ Found email field with: {selector}")
                        break
                except:
                    continue

            if not email_field:
                print("❌ Email field not found with selectors")

                # Try to find ANY input that might be email
                print("   Looking for any text input...")
                all_inputs = await self.page.query_selector_all('input[type="text"], input[type="email"]')
                print(f"   Found {len(all_inputs)} text/email inputs")

                for i, inp in enumerate(all_inputs):
                    try:
                        if await inp.is_visible():
                            placeholder = await inp.get_attribute('placeholder') or ''
                            name = await inp.get_attribute('name') or ''
                            id_attr = await inp.get_attribute('id') or ''

                            print(f"   Input {i + 1}: placeholder='{placeholder}', name='{name}', id='{id_attr}'")

                            if 'email' in placeholder.lower() or 'email' in name.lower() or 'mail' in placeholder.lower():
                                email_field = inp
                                print(f"✅ Found likely email field by placeholder/name")
                                break
                    except:
                        continue

            if not email_field:
                print("❌ Could not find email field at all")
                await self.page.screenshot(path=f"{screenshot_dir}/error_no_email_field.png")
                return False, "Email field not found", {}

            print("✏️ Step 5: Filling email field...")

            # Clear and fill email
            await email_field.click()
            await self.page.wait_for_timeout(500)
            await email_field.fill('')  # Clear first
            await self.page.wait_for_timeout(300)

            email_to_fill = self.account.email or self.account.username
            print(f"   Filling: {email_to_fill}")

            # Type email character by character (more human-like)
            await email_field.type(email_to_fill, delay=50)

            await self.page.screenshot(path=f"{screenshot_dir}/07_email_filled.png")
            print("✅ Email filled")

            print("🔍 Step 6: Looking for password field...")

            # Find password field
            password_field = None
            password_selectors = [
                '#v-16-0-3',
                'input[name="password"]',
                'input[type="password"]',
                'input[placeholder*="password"]',
                'input[placeholder*="Password"]',
                'input[placeholder*="пароль"]',
                'input[placeholder*="Пароль"]',
                'input#password',
                '.password-input',
                '[data-testid="password-input"]',
                'input[autocomplete="current-password"]',
            ]

            for selector in password_selectors:
                try:
                    print(f"   Trying selector: {selector}")
                    element = await self.page.wait_for_selector(selector, timeout=2000, state='visible')
                    if element:
                        password_field = element
                        print(f"✅ Found password field with: {selector}")
                        break
                except:
                    continue

            if not password_field:
                print("❌ Password field not found with selectors")

                # Look for any password input
                print("   Looking for any password input...")
                all_password_inputs = await self.page.query_selector_all('input[type="password"]')
                print(f"   Found {len(all_password_inputs)} password inputs")

                if all_password_inputs:
                    for i, inp in enumerate(all_password_inputs):
                        if await inp.is_visible():
                            password_field = inp
                            print(f"✅ Using password input {i + 1}")
                            break

            if not password_field:
                print("❌ Could not find password field")
                await self.page.screenshot(path=f"{screenshot_dir}/error_no_password_field.png")
                return False, "Password field not found", {}

            print("✏️ Step 7: Filling password...")

            # Clear and fill password
            await password_field.click()
            await self.page.wait_for_timeout(500)
            await password_field.fill('')  # Clear first
            await self.page.wait_for_timeout(300)

            # Type password (shorter delay for password)
            await password_field.type(self.account.password, delay=30)

            await self.page.screenshot(path=f"{screenshot_dir}/08_password_filled.png")
            print("✅ Password filled")

            print("🔍 Step 8: Looking for login/submit button...")

            # Find submit button
            submit_button = None
            submit_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Log in")',
                'button:has-text("Login")',
                'button:has-text("Sign In")',
                'button:has-text("Войти")',
                'button:has-text("Вход")',
                '.submit-button',
                '.login-button',
                '.form_root-PwGD3.form_form-wbqa0 button',
                'form button',
            ]

            for selector in submit_selectors:
                try:
                    print(f"   Trying selector: {selector}")
                    element = await self.page.wait_for_selector(selector, timeout=2000, state='visible')
                    if element:
                        submit_button = element
                        print(f"✅ Found submit button with: {selector}")

                        # Get button text
                        button_text = await element.inner_text()
                        print(f"   Button text: '{button_text}'")
                        break
                except:
                    continue

            if not submit_button:
                print("⚠️ No submit button found, looking for any button in form...")

                # Look for any button that might be submit
                all_buttons = await self.page.query_selector_all('form button, .form button')
                print(f"   Found {len(all_buttons)} buttons in forms")

                for i, btn in enumerate(all_buttons):
                    if await btn.is_visible():
                        btn_text = await btn.inner_text()
                        print(f"   Button {i + 1}: '{btn_text}'")

                        if any(text in btn_text.lower() for text in ['log', 'sign', 'войти', 'вход']):
                            submit_button = btn
                            print(f"✅ Using button with text: '{btn_text}'")
                            break

            print("🖱️ Step 9: Clicking login/submit button...")

            if submit_button:
                # Take screenshot before submit
                await self.page.screenshot(path=f"{screenshot_dir}/09_before_submit.png")

                # Click submit button
                await submit_button.click()
                print("✅ Clicked submit button")
            else:
                print("⚠️ No submit button found, pressing Enter...")
                await self.page.keyboard.press('Enter')

            # Wait for login to process
            print("⏳ Step 10: Waiting for login response...")
            await self.page.wait_for_timeout(5000)
            await self.page.screenshot(path=f"{screenshot_dir}/10_after_submit.png")

            print("🔍 Step 11: Checking login result...")

            # Get current URL and page content
            current_url = await self.page.evaluate("() => window.location.href")
            print(f"📊 Current URL: {current_url}")

            page_title = await self.page.title()
            print(f"📊 Page title: {page_title}")

            page_text = await self.page.evaluate("() => document.body.innerText")
            print(f"📄 Page text sample (first 300 chars):\n{page_text[:300]}")

            # Check for success indicators
            success_keywords = [
                'welcome', 'dashboard', 'balance', 'баланс', 'logout', 'выйти',
                'my account', 'аккаунт', 'profile', 'профиль', 'deposit', 'депозит',
                'withdraw', 'вывод', 'personal area', 'личный кабинет'
            ]

            for keyword in success_keywords:
                if keyword.lower() in page_text.lower():
                    print(f"✅ Found success keyword: {keyword}")

                    # Get cookies and session data
                    cookies = await self.context.cookies()

                    session_data = {
                        'cookies': cookies,
                        'url': current_url,
                        'title': page_title,
                        'timestamp': time.time(),
                        'login_method': 'simple_step_by_step',
                    }

                    print("🎉 LOGIN SUCCESSFUL!")
                    return True, "Login successful", session_data

            # Check for error indicators
            error_keywords = [
                'неверный', 'incorrect', 'error', 'ошибка', 'invalid',
                'неправильный', 'wrong', 'failed', 'failure', 'try again',
                'неверный логин или пароль', 'invalid login or password'
            ]

            for keyword in error_keywords:
                if keyword.lower() in page_text.lower():
                    print(f"❌ Found error keyword: {keyword}")
                    return False, f"Login error: {keyword}", {}

            # Check if we're still on login page
            url_lower = current_url.lower()
            if any(login_url in url_lower for login_url in ['/login', '/signin', '/auth', '/вход', '/войти']):
                print("❌ Still on login page URL")
                return False, "Still on login page", {}

            # If not on login page and no errors, assume success
            print("✅ Not on login page, assuming login successful")

            cookies = await self.context.cookies()
            session_data = {
                'cookies': cookies,
                'url': current_url,
                'title': page_title,
                'timestamp': time.time(),
                'login_method': 'simple_step_by_step',
            }

            return True, "Login successful (assumed)", session_data

        except Exception as e:
            print(f"❌ Simple browser login error: {str(e)}")
            import traceback
            print(f"📋 Error details:\n{traceback.format_exc()}")
            return False, f"Error: {str(e)}", {}

    async def _test_proxy_connectivity(self) -> bool:
        """Test if proxy is actually working"""
        if not self.account.proxy:
            return False

        try:
            print(f"🔍 Testing proxy: {self.account.proxy.ip}:{self.account.proxy.port}")

            # Use curl to test proxy
            proxy_url = f"{self.account.proxy.type}://{self.account.proxy.ip}:{self.account.proxy.port}"

            # Test with curl (system command)
            test_command = [
                'curl', '-s', '-x', proxy_url,
                '--connect-timeout', '10',
                '--max-time', '15',
                'https://httpbin.org/ip'
            ]

            # Add auth if needed
            if self.account.proxy.username and self.account.proxy.password:
                test_command.extend([
                    '--proxy-user', f"{self.account.proxy.username}:{self.account.proxy.password}"
                ])

            result = subprocess.run(
                test_command,
                capture_output=True,
                text=True,
                timeout=20
            )

            if result.returncode == 0 and 'origin' in result.stdout:
                print("✅ Proxy is working (curl test)")
                return True
            else:
                print(f"❌ Proxy test failed: {result.stderr[:100]}")
                return False

        except Exception as e:
            print(f"⚠️ Proxy test error: {str(e)}")
            return False

    async def _launch_browser_with_proxy_fixed(self) -> bool:
        """Launch browser with fixed proxy handling"""
        try:
            print("🌐 Configuring browser with proxy...")

            # Build launch options
            launch_options = {
                'headless': True,
                'args': [
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--window-size=1920,1080',
                    '--disable-blink-features=AutomationControlled',
                ]
            }

            # Handle different proxy types
            if self.account.proxy and self.account.proxy.ip and self.account.proxy.port:
                proxy = self.account.proxy
                proxy_type = proxy.type.lower()

                print(f"🔧 Proxy type: {proxy_type}")

                # Format proxy string correctly
                if proxy_type == 'socks5':
                    # Try different SOCKS5 formats
                    if proxy.username and proxy.password:
                        # Format 1: With auth in URL
                        proxy_str = f"socks5://{proxy.username}:{proxy.password}@{proxy.ip}:{proxy.port}"
                        # Format 2: Separate auth (alternative)
                        proxy_str_alt = f"socks5://{proxy.ip}:{proxy.port}"
                    else:
                        proxy_str = f"socks5://{proxy.ip}:{proxy.port}"
                        proxy_str_alt = f"socks5://{proxy.ip}:{proxy.port}"

                    print(f"🔗 SOCKS5 proxy: {proxy_str}")

                    # Try multiple SOCKS5 approaches
                    socks_args = [
                        f'--proxy-server={proxy_str}',
                        f'--host-resolver-rules="MAP * 0.0.0.0 , EXCLUDE 127.0.0.1"',
                    ]

                else:  # HTTP/HTTPS
                    if proxy.username and proxy.password:
                        proxy_str = f"http://{proxy.username}:{proxy.password}@{proxy.ip}:{proxy.port}"
                    else:
                        proxy_str = f"http://{proxy.ip}:{proxy.port}"

                    print(f"🔗 HTTP proxy: {proxy_str}")
                    socks_args = [f'--proxy-server={proxy_str}']

                # Add proxy arguments
                launch_options['args'].extend(socks_args)

                # Add proxy bypass
                launch_options['args'].append('--proxy-bypass-list=<-loopback>')

            print("🚀 Launching browser...")
            self.browser = await self.playwright.chromium.launch(**launch_options)

            # Create context
            context_options = {
                'viewport': {'width': 1920, 'height': 1080},
                'user_agent': random.choice(self.user_agents),
                'ignore_https_errors': True,
            }

            # Add proxy to context if needed
            if self.account.proxy and self.account.proxy.ip and self.account.proxy.port:
                proxy = self.account.proxy
                context_options['proxy'] = {
                    'server': f'{proxy.type}://{proxy.ip}:{proxy.port}',
                }
                if proxy.username and proxy.password:
                    context_options['proxy']['username'] = proxy.username
                    context_options['proxy']['password'] = proxy.password

            self.context = await self.browser.new_context(**context_options)
            self.page = await self.context.new_page()

            # Set timeouts
            self.page.set_default_timeout(30000)
            self.page.set_default_navigation_timeout(30000)

            # Add stealth
            await self.page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)

            print("✅ Browser launched")
            return True

        except Exception as e:
            print(f"❌ Browser launch failed: {str(e)}")
            return False

    async def _launch_browser_without_proxy(self) -> bool:
        """Launch browser without proxy"""
        try:
            print("🌐 Launching browser without proxy...")

            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--window-size=1920,1080',
                ]
            )

            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent=random.choice(self.user_agents),
                ignore_https_errors=True,
            )

            self.page = await self.context.new_page()
            self.page.set_default_timeout(30000)

            print("✅ Browser launched (no proxy)")
            return True

        except Exception as e:
            print(f"❌ Browser launch failed: {str(e)}")
            return False

    async def _navigate_with_proxy(self) -> bool:
        """Navigate with proxy handling"""
        domains = [
            "https://1wuswz.com/",
            "https://1win.com/",
            "https://1win.pro/",
            "https://1win.io/",
        ]

        for domain in domains:
            try:
                print(f"🌐 Trying {domain} with proxy...")
                response = await self.page.goto(
                    domain,
                    wait_until='domcontentloaded',
                    timeout=30000
                )

                if response and response.status in [200, 301, 302]:
                    print(f"✅ Connected: {domain}")
                    return True

            except Exception as e:
                error_msg = str(e)
                print(f"❌ Failed: {error_msg[:100]}...")

                # Check for specific proxy errors
                if "ERR_NO_SUPPORTED_PROXIES" in error_msg:
                    print("⚠️ Proxy not supported, trying next...")
                    continue
                elif "ERR_SOCKS_CONNECTION_FAILED" in error_msg:
                    print("⚠️ SOCKS connection failed, trying next...")
                    continue
                elif "ERR_PROXY_CONNECTION_FAILED" in error_msg:
                    print("⚠️ Proxy connection failed, trying next...")
                    continue

        return False

    async def _navigate_without_proxy(self) -> bool:
        """Navigate without proxy"""
        domains = [
            "https://1wuswz.com/",
            "https://1win.com/",
            "https://1win.pro/",
        ]

        for domain in domains:
            try:
                print(f"🌐 Trying {domain} without proxy...")
                response = await self.page.goto(
                    domain,
                    wait_until='domcontentloaded',
                    timeout=30000
                )

                if response and response.status in [200, 301, 302]:
                    print(f"✅ Connected: {domain}")
                    return True

            except Exception as e:
                print(f"❌ Failed: {str(e)[:100]}...")
                continue

        return False

    async def _perform_js_login(self) -> bool:
        """Perform login using JavaScript injection"""
        try:
            print("💉 Injecting login JavaScript...")

            # Wait for page to load
            await self.page.wait_for_timeout(2000)

            # Execute login script
            login_script = f"""
            (function() {{
                console.log('[1WIN LOGIN] Starting...');

                // Credentials
                const email = "{self.account.email or self.account.username}";
                const password = "{self.account.password}";

                // Helper to wait
                const wait = (ms) => new Promise(r => setTimeout(r, ms));

                async function login() {{
                    try {{
                        console.log('[1WIN] Step 1: Finding elements...');

                        // Find email field (v-16-0-2 or similar)
                        let emailField = document.getElementById('v-16-0-2') || 
                                       document.querySelector('input[type="email"]') ||
                                       document.querySelector('input[name="email"]') ||
                                       document.querySelector('input[placeholder*="email"]');

                        if (!emailField) {{
                            // Try all text inputs
                            const inputs = document.querySelectorAll('input[type="text"]');
                            for (let input of inputs) {{
                                if ((input.placeholder || '').toLowerCase().includes('email')) {{
                                    emailField = input;
                                    break;
                                }}
                            }}
                        }}

                        if (!emailField) {{
                            console.error('[1WIN] Email field not found');
                            return false;
                        }}

                        console.log('[1WIN] Step 2: Filling email...');
                        emailField.value = '';
                        emailField.focus();
                        await wait(100);
                        emailField.value = email;

                        // Trigger events
                        ['input', 'change', 'blur'].forEach(ev => {{
                            emailField.dispatchEvent(new Event(ev, {{bubbles: true}}));
                        }});

                        // Find password field (v-16-0-3 or similar)
                        let passwordField = document.getElementById('v-16-0-3') || 
                                          document.querySelector('input[type="password"]') ||
                                          document.querySelector('input[name="password"]');

                        if (!passwordField) {{
                            console.error('[1WIN] Password field not found');
                            return false;
                        }}

                        console.log('[1WIN] Step 3: Filling password...');
                        passwordField.value = '';
                        passwordField.focus();
                        await wait(100);
                        passwordField.value = password;

                        // Trigger events
                        ['input', 'change'].forEach(ev => {{
                            passwordField.dispatchEvent(new Event(ev, {{bubbles: true}}));
                        }});

                        console.log('[1WIN] Step 4: Finding submit...');

                        // Look for form with class
                        let form = document.querySelector('.form_root-PwGD3.form_form-wbqa0');
                        if (!form) {{
                            form = emailField.closest('form') || passwordField.closest('form');
                        }}

                        // Find submit button
                        let submitBtn = null;
                        if (form) {{
                            submitBtn = form.querySelector('button[type="submit"], input[type="submit"]');
                        }}

                        if (!submitBtn) {{
                            // Look for button with text
                            const buttons = document.querySelectorAll('button');
                            for (let btn of buttons) {{
                                const text = btn.innerText.toLowerCase();
                                if (text.includes('log in') || text.includes('login') || 
                                    text.includes('sign in') || text.includes('войти')) {{
                                    submitBtn = btn;
                                    break;
                                }}
                            }}
                        }}

                        if (submitBtn) {{
                            console.log('[1WIN] Step 5: Clicking submit...');
                            submitBtn.click();
                        }} else {{
                            console.log('[1WIN] Step 5: Pressing Enter...');
                            const enterEvent = new KeyboardEvent('keydown', {{
                                key: 'Enter',
                                code: 'Enter',
                                keyCode: 13,
                                bubbles: true
                            }});
                            passwordField.dispatchEvent(enterEvent);
                        }}

                        console.log('[1WIN] Step 6: Waiting...');
                        await wait(3000);

                        // Check if login worked
                        const currentUrl = window.location.href.toLowerCase();
                        const isLoginPage = currentUrl.includes('/login') || 
                                          currentUrl.includes('/signin') || 
                                          currentUrl.includes('/auth');

                        if (!isLoginPage) {{
                            console.log('[1WIN] SUCCESS: Not on login page');
                            return true;
                        }}

                        const bodyText = document.body.innerText.toLowerCase();
                        if (bodyText.includes('welcome') || bodyText.includes('dashboard')) {{
                            console.log('[1WIN] SUCCESS: Welcome/Dashboard found');
                            return true;
                        }}

                        console.log('[1WIN] FAILED: Still on login page');
                        return false;

                    }} catch (error) {{
                        console.error('[1WIN] ERROR:', error);
                        return false;
                    }}
                }}

                return login();
            }})();
            """

            result = await self.page.evaluate(login_script)

            if result:
                print("✅ JS login successful")
            else:
                print("❌ JS login failed")

            return result

        except Exception as e:
            print(f"❌ JS login error: {str(e)}")
            return False

    async def _perform_simple_login(self) -> bool:
        """Simple login attempt"""
        try:
            print("🔑 Trying simple login...")

            # Look for login button
            try:
                login_buttons = [
                    "text=Login",
                    "text=Sign In",
                    "text=Вход",
                ]

                for selector in login_buttons:
                    try:
                        await self.page.click(selector, timeout=3000)
                        print(f"✅ Clicked: {selector}")
                        await self.page.wait_for_timeout(2000)
                        break
                    except:
                        continue
            except:
                pass

            # Try to fill form
            try:
                # Fill email
                await self.page.fill('#v-16-0-2, input[type="email"], input[name="email"]',
                                     self.account.email or self.account.username)
                print("✅ Filled email")

                # Fill password
                await self.page.fill('#v-16-0-3, input[type="password"], input[name="password"]',
                                     self.account.password)
                print("✅ Filled password")

                # Submit
                await self.page.press('input[type="password"], input[name="password"]', 'Enter')
                print("✅ Submitted")

                await self.page.wait_for_timeout(5000)

                # Check result
                current_url = self.page.url
                if 'login' not in current_url and 'signin' not in current_url:
                    print("✅ Not on login page - success!")
                    return True
                else:
                    print("❌ Still on login page")
                    return False

            except Exception as e:
                print(f"❌ Simple login error: {str(e)}")
                return False

        except Exception as e:
            print(f"❌ Simple login failed: {str(e)}")
            return False

    async def _cleanup(self):
        """Clean up resources"""
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            print("✅ Cleanup complete")
        except Exception as e:
            print(f"⚠️ Cleanup error: {str(e)}")