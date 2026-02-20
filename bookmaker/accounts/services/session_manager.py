import asyncio
import datetime
import os
import random
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Tuple, Dict, Optional

from datetime import datetime

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from playwright.async_api import async_playwright, BrowserContext, Page
import aiohttp  # Add this import
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Tuple, Dict

# Import your custom modules
from .webDriver import HumanBehavior, FingerprintManager

# Try to import stealth, fallback gracefully if not available
try:
    from playwright_stealth import stealth_async

    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False


    async def stealth_async(page):
        print("⚠️ playwright-stealth not installed – skipping stealth")

from ..models import OneWinSession, CardTransaction, Profile


class OneWinSessionManager:
    """
    Manages persistent Chromium sessions for 1Win accounts using ONLY Playwright.
    Full state persistence (cookies, storage, scroll, forms) via database JSONField.
    """

    def __init__(self):
        # Active in‑memory sessions: key = f"session_{session.id}"
        self.active_sessions = {}

    # ----------------------------------------------------------------------
    # PROXY CONFIGURATION
    # ----------------------------------------------------------------------
    def _clear_profile_lock(self, profile_path: str):
        """Forcefully remove Chromium lock files that prevent re-launching."""
        lock_files = [
            'SingletonLock',
            'SingletonCookie',
            'SingletonSocket'
        ]
        for lock_file in lock_files:
            full_path = os.path.join(profile_path, lock_file)
            if os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    print(f"🔓 Released profile lock: {lock_file}")
                except Exception as e:
                    print(f"⚠️ Could not remove {lock_file}: {e}")


    async def _get_proxy_config(self, session_id: int) -> Optional[Dict]:
        """Build Playwright proxy dict from the account's proxy."""

        def get_config():
            try:
                s = OneWinSession.objects.select_related('account__proxy').get(id=session_id)
                proxy = s.account.proxy
                if not proxy or not proxy.is_active:
                    return None

                ip = proxy.ip.strip()
                port = proxy.port
                username = proxy.username.strip() if proxy.username else None
                password = proxy.password.strip() if proxy.password else None
                proxy_type = proxy.type.lower()

                config = {'bypass': 'localhost,127.0.0.1'}

                if proxy_type in ('http', 'https'):
                    config['server'] = f"{proxy_type}://{ip}:{port}"
                    if username and password:
                        config['username'] = username
                        config['password'] = password
                elif proxy_type == 'socks5':
                    config['server'] = f"socks5://{ip}:{port}"
                else:
                    return None

                return config
            except Exception as e:
                print(f"Error in proxy config: {e}")
                return None

        return await sync_to_async(get_config)()

    # ----------------------------------------------------------------------
    # BROWSER LIVENESS & CLEANUP
    # ----------------------------------------------------------------------
    async def _is_browser_alive(self, session_data: Dict) -> bool:
        """Check if the Playwright page is still responsive."""
        try:
            page = session_data.get('page')
            if not page or page.is_closed():
                return False
            await page.evaluate("1", timeout=2000)
            return True
        except Exception:
            return False

    async def _cleanup_session_data(self, session_key: str):
        if session_key not in self.active_sessions:
            return
        data = self.active_sessions[session_key]

        try:
            # Close browser first
            if 'browser' in data:
                await data['browser'].close()

            # Stop playwright instance second
            if 'playwright' in data:
                await data['playwright'].stop()

            print(f"🧹 Cleanly closed Playwright for: {session_key}")
        except Exception as e:
            print(f"⚠️ Cleanup warning: {e}")
        finally:
            if session_key in self.active_sessions:
                del self.active_sessions[session_key]
    # ----------------------------------------------------------------------
    # STATE PERSISTENCE (database JSONField)
    # ----------------------------------------------------------------------
    async def _save_session_state(self, session: OneWinSession, page: Page) -> None:
        """Capture full browser state and save to session.session_state."""
        try:
            context = page.context
            cookies = await context.cookies()
            storage = await page.evaluate("""() => ({
                localStorage: Object.entries(localStorage || {}),
                sessionStorage: Object.entries(sessionStorage || {})
            })""")
            scroll = await page.evaluate("() => ({x: window.scrollX, y: window.scrollY})")
            form_data = await page.evaluate("""() => {
                const data = {};
                document.querySelectorAll('input, textarea, select').forEach(el => {
                    if (el.name || el.id) {
                        const key = el.name || el.id;
                        data[key] = el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.value;
                    }
                });
                return data;
            }""")

            state = {
                'url': page.url,
                'cookies': cookies,
                'localStorage': storage['localStorage'],
                'sessionStorage': storage['sessionStorage'],
                'scrollX': scroll['x'],
                'scrollY': scroll['y'],
                'formData': form_data,
                'timestamp': time.time()
            }

            session.session_state = state
            session.last_used = timezone.now()
            await sync_to_async(session.save)()
            print(f"💾 Saved FULL state to DB for session {session.id}")
        except Exception as e:
            print(f"⚠️ Failed to save state: {e}")

    async def _restore_session_state(self, session: OneWinSession, context: BrowserContext, page: Page) -> bool:
        """Restore previously saved state safely into Playwright."""
        if not session.session_state:
            print("ℹ️ No saved state to restore")
            return False

        try:
            state = session.session_state
            print(f"🔄 Attempting to restore state from {time.ctime(state.get('timestamp', 0))}")

            # 1. RESTORE COOKIES (With safe domain visit first)
            if state.get('cookies'):
                try:
                    # Navigate to a lightweight page on the domain first to establish origin
                    print("🌐 Establishing domain origin for cookies...")
                    await page.goto('https://1wuswz.com/favicon.ico', wait_until='commit', timeout=30000)

                    # Sanitize cookies (Playwright rejects strict Selenium keys like 'sameParty')
                    valid_keys = {'name', 'value', 'url', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite'}
                    sanitized_cookies = []
                    for c in state['cookies']:
                        clean_cookie = {k: v for k, v in c.items() if k in valid_keys}
                        sanitized_cookies.append(clean_cookie)

                    await context.add_cookies(sanitized_cookies)
                    print(f"🍪 Restored {len(sanitized_cookies)} cookies")
                except Exception as e:
                    print(f"⚠️ Failed to restore cookies: {e}")

            # 2. RESTORE URL WITH HUMAN-LIKE NAVIGATION
            if state.get('url'):
                url = state['url']
                print(f"🌐 Navigating to saved URL: {url}")
                await asyncio.sleep(random.uniform(1, 3))

                try:
                    await page.goto(
                        url,
                        wait_until='domcontentloaded',
                        timeout=60000,
                        referer='https://www.google.com/' if random.random() > 0.5 else None
                    )
                    await asyncio.sleep(random.uniform(2, 4))
                    await self._handle_cloudflare(page)
                except Exception as e:
                    print(f"⚠️ Navigation failed: {e}")

            # 3. RESTORE STORAGE
            if state.get('localStorage') or state.get('sessionStorage'):
                try:
                    await page.evaluate("""(s) => {
                        if (s.localStorage && s.localStorage.length) {
                            localStorage.clear();
                            s.localStorage.forEach(([k, v]) => localStorage.setItem(k, v));
                        }
                        if (s.sessionStorage && s.sessionStorage.length) {
                            sessionStorage.clear();
                            s.sessionStorage.forEach(([k, v]) => sessionStorage.setItem(k, v));
                        }
                    }""", {
                                            'localStorage': state.get('localStorage', []),
                                            'sessionStorage': state.get('sessionStorage', [])
                                        })
                    print("💾 Restored storage data")
                except Exception as e:
                    print(f"⚠️ Failed to restore storage: {e}")

            # 4. RESTORE FORM DATA
            if state.get('formData'):
                try:
                    await page.evaluate("""(fd) => {
                        for (let [k, v] of Object.entries(fd)) {
                            let el = document.querySelector(`[name="${k}"], #${k}`);
                            if (el) {
                                if (el.type === 'checkbox' || el.type === 'radio') el.checked = v;
                                else el.value = v;
                                el.dispatchEvent(new Event('input', {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                            }
                        }
                    }""", state['formData'])
                    print("📝 Restored form data")
                except Exception as e:
                    pass

            # 5. RESTORE SCROLL POSITION
            if 'scrollX' in state and 'scrollY' in state:
                try:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    await page.evaluate("""(scrollPos) => {
                        window.scrollTo({top: scrollPos.y, left: scrollPos.x, behavior: 'smooth'});
                    }""", {'x': state['scrollX'], 'y': state['scrollY']})
                    print(f"📜 Restored scroll position")
                except Exception as e:
                    pass

            # Update last_used timestamp
            session.last_used = timezone.now()
            await sync_to_async(session.save)(update_fields=['last_used'])
            print(f"✅ Successfully restored state for session {session.id}")
            return True

        except Exception as e:
            print(f"❌ Failed to restore state: {e}")
            return False

    # ----------------------------------------------------------------------
    # CLOUDFLARE HANDLING (Unified)
    # ----------------------------------------------------------------------
    async def _handle_cloudflare(self, page: Page, max_wait: int = 30) -> bool:
        """Robustly handle Cloudflare challenges with human-like interactions."""
        try:
            content = await page.content()
            url = page.url

            # Explicitly catch the 1015 IP ban
            if "Error 1015" in await page.title() or "Rate limited" in content:
                print("🚫 IP Rate Limited (1015). Must wait or rotate proxy.")
                return False

            patterns = ['cf-browser-verification', 'cloudflare', 'checking your browser', '请稍候']
            is_cf = any(p in content.lower() for p in patterns) or 'cf' in url.lower()

            if is_cf:
                print("☁️ Cloudflare detected, waiting for verification...")
                start_time = asyncio.get_event_loop().time()

                while (asyncio.get_event_loop().time() - start_time) < max_wait:
                    await asyncio.sleep(2)
                    current_content = await page.content()
                    current_url = page.url

                    if '1wuswz.com' in current_url and 'cf' not in current_url.lower() and not any(
                            p in current_content.lower() for p in patterns):
                        elapsed = asyncio.get_event_loop().time() - start_time
                        print(f"✅ Cloudflare passed after {elapsed:.1f} seconds")
                        await asyncio.sleep(random.uniform(2, 4))
                        return True

                    # Human-like behavior while waiting
                    try:
                        vp = page.viewport_size
                        if vp and random.random() > 0.6:
                            x = random.randint(100, vp['width'] - 100)
                            y = random.randint(100, vp['height'] - 100)
                            await page.mouse.move(x, y, steps=random.randint(5, 15))

                            if random.random() > 0.8:
                                await page.evaluate(f"window.scrollBy(0, {random.randint(50, 150)})")
                    except:
                        pass

                print("⚠️ Cloudflare timeout")
                return False

            return True

        except Exception as e:
            print(f"⚠️ Error handling Cloudflare: {e}")
            return False

    # ----------------------------------------------------------------------
    # TAB INSPECTOR
    # ----------------------------------------------------------------------
    async def _print_all_pages(self, context: BrowserContext, session_id: int):
        """Log all open tabs and their titles/URLs."""
        try:
            pages = context.pages
            print(f"\n📑 Session {session_id} – {len(pages)} open tab(s):")
            for i, page in enumerate(pages, 1):
                try:
                    url = page.url
                    title = await page.title()
                    print(f"   Tab {i}: {title} – {url}")
                except:
                    print(f"   Tab {i}: <error retrieving page info>")
        except Exception as e:
            pass

    # ----------------------------------------------------------------------
    # MANUAL SESSION (HEADLESS = FALSE)
    # ----------------------------------------------------------------------
    async def open_session_browser(self, session: OneWinSession) -> Tuple[bool, str, Dict]:
        """Open a visible Chromium window with stealth and retry logic."""
        try:
            username = await sync_to_async(lambda: session.account.username)()
            session_id = await sync_to_async(lambda: session.id)()
            account_id = await sync_to_async(lambda: session.account.id)()
            profile_path = await sync_to_async(session.get_or_create_profile_path)()

            self._clear_profile_lock(profile_path)
            session_key = f"session_{session_id}"

            if session.profile_path != profile_path:
                session.profile_path = profile_path
                await sync_to_async(session.save)(update_fields=['profile_path'])

            print(f"\n🖥️ Opening persistent Chromium for {username}")

            if session_key in self.active_sessions:
                if await self._is_browser_alive(self.active_sessions[session_key]):
                    print("⚠️ Session already active – reusing.")
                    if await sync_to_async(lambda: session.session_status)() != 'active':
                        await sync_to_async(session.mark_active)()
                    session.last_used = timezone.now()
                    await sync_to_async(session.save)(update_fields=['last_used'])
                    return True, "Browser already open", {'url': self.active_sessions[session_key]['page'].url}
                else:
                    await self._cleanup_session_data(session_key)

            proxy_config = None
            args = [
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-gpu',  # <--- Add this
                '--disable-software-rasterizer',  # <--- Add this
            ]

            playwright = await async_playwright().start()
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=False,
                proxy=proxy_config,
                ignore_https_errors=True,
                args=args,
                viewport={'width': random.choice([1366, 1440, 1536]), 'height': 768},
            )

            page = context.pages[0] if context.pages else await context.new_page()

            # Block unnecessary resources to avoid 1015 limits
            # await page.route("**/*.{png,jpg,jpeg,gif,webp,woff,woff2,svg}", lambda route: route.abort())

            await stealth_async(page)
            await self._restore_session_state(session, context, page)

            current_url = page.url
            if not current_url or current_url == 'about:blank' or '1wuswz.com' not in current_url:
                await page.goto('https://1wuswz.com/', wait_until='networkidle')

            session.last_used = timezone.now()
            await sync_to_async(session.save)(update_fields=['last_used'])

            browser_obj = context.browser
            if browser_obj:
                async def on_disconnect():
                    await self._save_session_state(session, page)

                browser_obj.on('disconnected', lambda: asyncio.create_task(on_disconnect()))

            self.active_sessions[session_key] = {
                'browser': context,
                'page': page,
                'playwright': playwright,
                'session_id': session_id,
            }

            await sync_to_async(session.mark_active)()
            return True, "Browser opened", {'session_id': session_key, 'url': page.url}

        except Exception as e:
            print(f"❌ open_session_browser failed: {e}")
            if 'playwright' in locals():
                try:
                    await playwright.stop()
                except:
                    pass
            return False, str(e), {}

    # ----------------------------------------------------------------------
    # AUTOMATED SESSION (HEADLESS = TRUE)
    # ----------------------------------------------------------------------
    async def _get_or_create_browser_session(self, session: OneWinSession) -> Optional[Dict]:
        """Get existing live session or launch a headless Playwright instance."""
        session_id = await sync_to_async(lambda: session.id)()
        session_key = f"session_{session_id}"

        if session_key in self.active_sessions:
            if await self._is_browser_alive(self.active_sessions[session_key]):
                session.last_used = timezone.now()
                await sync_to_async(session.save)(update_fields=['last_used'])
                return self.active_sessions[session_key]
            else:
                await self._cleanup_session_data(session_key)

        try:
            profile_path = await sync_to_async(lambda: session.profile_path)()
            if not profile_path or not os.path.exists(profile_path):
                profile_path = await sync_to_async(session.get_or_create_profile_path)()
                session.profile_path = profile_path
                await sync_to_async(session.save)(update_fields=['profile_path'])

            playwright = await async_playwright().start()
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=True,
                ignore_https_errors=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox'],
                viewport={'width': 1440, 'height': 900},
            )

            page = context.pages[0] if context.pages else await context.new_page()

            # Block heavy resources in headless to save bandwidth
            await page.route("**/*.{png,jpg,jpeg,gif,webp,woff,woff2,svg,css}", lambda route: route.abort())

            await stealth_async(page)
            print("🕵️ Launched headless Playwright browser")

            await self._restore_session_state(session, context, page)

            session.last_used = timezone.now()
            await sync_to_async(session.save)(update_fields=['last_used'])

            session_data = {
                'browser': context,
                'page': page,
                'playwright': playwright,
                'session_id': session_id,
            }
            self.active_sessions[session_key] = session_data
            return session_data

        except Exception as e:
            print(f"❌ Headless browser launch failed: {e}")
            return None

    # ----------------------------------------------------------------------
    # CLOSE SESSION
    # ----------------------------------------------------------------------
    async def close_session_browser(self, session: OneWinSession) -> bool:
        """Close the browser and save state."""
        session_id = await sync_to_async(lambda: session.id)()
        session_key = f"session_{session_id}"

        if session_key not in self.active_sessions:
            return False

        data = self.active_sessions[session_key]
        if await self._is_browser_alive(data):
            await self._save_session_state(session, data['page'])

        await self._cleanup_session_data(session_key)
        await sync_to_async(session.close_session)()
        return True

    # ----------------------------------------------------------------------
    # CHECK SESSION STATUS
    # ----------------------------------------------------------------------
    async def check_session_status(self, session: OneWinSession) -> Dict:
        """Quick check if session is logged in."""
        try:
            profile_path = await sync_to_async(lambda: session.profile_path)()
            if not profile_path:
                return {'is_logged_in': False, 'status': 'no_profile'}

            playwright = await async_playwright().start()
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=profile_path, headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await stealth_async(page)
            await page.goto('https://1wuswz.com/', wait_until='domcontentloaded', timeout=30000)

            is_logged_in = await self._is_logged_in(page)

            await context.close()
            await playwright.stop()

            if is_logged_in:
                await sync_to_async(session.mark_active)()
            else:
                await sync_to_async(session.mark_needs_login)()

            return {'is_logged_in': is_logged_in, 'status': await sync_to_async(lambda: session.session_status)()}

        except Exception as e:
            return {'is_logged_in': False, 'status': 'error', 'error': str(e)}

    @staticmethod
    async def _is_logged_in(page: Page) -> bool:
        """Heuristic: check for logout button, balance, etc."""
        try:
            content = await page.content()
            lower = content.lower()
            if any(w in lower for w in ['logout', 'выйти', 'balance', 'баланс', 'my account']): return True
            if '/login' in page.url.lower(): return False
            return True
        except:
            return False

    # ----------------------------------------------------------------------
    # DEPOSIT METHODS
    # ----------------------------------------------------------------------
    async def perform_deposit(
            self,
            session: OneWinSession,
            amount: Decimal,
            card_number: str,
            card_tx: CardTransaction,
            user_profile: Profile
    ) -> Tuple[bool, str, Dict]:
        browser_data = None
        try:
            account = await sync_to_async(lambda: session.account)()
            proxy = await sync_to_async(lambda: account.proxy)()

            try:
                user_amount = Decimal(card_tx.amount)
            except (InvalidOperation, TypeError):
                print("❌ Invalid Amount entered")
                return False, "Invalid Amount entered", {}

            # -------------------------------------------------------------
            # 💱 ASYNC CURRENCY CONVERSION TO EUR
            # -------------------------------------------------------------
            # Fallback to 'EUR' if your model doesn't have a currency field yet
            original_currency = getattr(card_tx, 'currency', 'EUR').upper()
            amount_in_eur = user_amount
            exchange_rate = Decimal('1.0')

            if original_currency != 'MDL':
                print(f"🔄 Fetching live exchange rate for {original_currency} to MDL...")
                try:
                    async with aiohttp.ClientSession() as http_session:
                        async with http_session.get(f"https://open.er-api.com/v6/latest/{original_currency}",
                                                    timeout=5) as response:
                            if response.status == 200:
                                data = await response.json()
                                if data.get("result") == "success":
                                    rate_to_eur = data['rates']['MDL']
                                    exchange_rate = Decimal(str(rate_to_eur))

                                    # Convert and round to 2 decimal places
                                    amount_in_mdl = (user_amount * exchange_rate).quantize(Decimal('0.01'),
                                                                                           rounding=ROUND_HALF_UP)
                                else:
                                    print(f"⚠️ API error fetching rate for {original_currency}")
                            else:
                                print(f"⚠️ HTTP {response.status} fetching exchange rate.")
                except Exception as e:
                    print(f"⚠️ Currency conversion network error: {e}")

            # -------------------------------------------------------------

            print("\n" + "═" * 60)
            print("📥 DEPOSIT EXECUTION DATA")
            print("═" * 60)
            print(f"🆔 TRANSACTION ID:  #{card_tx.id}")
            print(f"👤 SITE USER:       {user_profile.full_name} (@{user_profile.user.username})")
            print(f"💰 ORIGINAL AMOUNT: {user_amount} {original_currency}")
            if original_currency != 'MDL':
                print(f"💶 DEPOSIT IN MDL:  {amount_in_mdl} MDL (Rate: {exchange_rate})")
            print(f"💳 CARD USED:       {card_tx.card_number} {getattr(card_tx, 'card_type', '')}")
            print(f"🤖 1WIN TARGET:     {session.account.username} (ID: {session.account.id})")

            if session.account.proxy:
                print(f"🌐 VIA PROXY:       {session.account.proxy.ip}:{session.account.proxy.port}")
                print(f"🌍 PROXY COUNTRY:   {session.account.proxy.country}")

            print("═" * 60 + "\n")

            browser_data = await self._get_or_create_browser_session(session)
            if not browser_data:
                return False, "Failed to obtain browser session", {}

            page = browser_data['page']

            # Allow images to load for the screenshot
            try:
                await page.unroute("**/*.{png,jpg,jpeg,gif,webp,woff,woff2,svg,css}")
            except:
                pass

            await asyncio.sleep(random.uniform(2, 5))
            await page.goto('https://1wuswz.com/', wait_until='domcontentloaded')
            await self._handle_cloudflare(page)
            await page.wait_for_timeout(random.uniform(3000, 5000))

            # --- Extract and Print Data to Terminal ---
            print("\n🔍 EXTRACTING ACCOUNT DETAILS FROM PAGE...")
            try:
                view_name = await page.inner_text('.view_name-2qtZk', timeout=5000)
                view_id = await page.inner_text('.view_id-2qtZk', timeout=5000)

                print("----------------------------------------")
                print(f"👤 Account Name: {view_name.strip()}")
                print(f"🆔 Account ID:   {view_id.strip()}")
                print("----------------------------------------\n")
            except Exception as e:
                print(f"⚠️ Could not find account details on page: {e}")

            rounded_amount_in_mdl = (user_amount * exchange_rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP)

            profile_info = await self._extract_profile_info_with_retry(
                page=page,
                card_tx=card_tx,
                amount_in_mdl=rounded_amount_in_mdl
            )

            payment_link = profile_info.get('payment_link')

            if payment_link:
                return True, "Payment link generated successfully", {
                    'payment_link': payment_link,
                    'account_id': account.id,
                    'amount_in_eur': str(amount_in_eur)
                }
            else:
                filename = f"deposit_{account.id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                screenshot_path = os.path.join(settings.MEDIA_ROOT, 'screenshots', filename)
                os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                await page.screenshot(path=screenshot_path, full_page=True)

                return False, "Screenshot captured", {
                    'account_id': account.id,
                    'profile_name': profile_info.get('profileName'),
                    'profile_id': profile_info.get('profileId'),
                    'screenshot': os.path.join('screenshots', filename),
                    'amount_in_eur': str(amount_in_eur)  # Pass the converted amount back if needed!
                }

        except Exception as e:
            if browser_data: await self._cleanup_session_data(f"session_{session.id}")
            return False, str(e), {}

    async def _extract_profile_info_with_retry(
            self,
            page,
            card_tx,
            amount_in_mdl: Optional[float] = None,
            max_retries: int = 3
    ) -> Dict[str, str]:
        """
        Attempts to perform a deposit and returns the resulting paybase.top payment link.
        Takes a screenshot on timeout for debugging.

        Args:
            page: Playwright page object.
            card_tx: Transaction object with a 'card_type' attribute (Visa/Mastercard).
            amount_in_mdl: Deposit amount in MDL (must be a positive number).
            max_retries: Number of retry attempts.

        Returns:
            Dict with a single key 'payment_link' if successful, otherwise
            raises an exception after max retries.
        """
        if amount_in_mdl is None or amount_in_mdl <= 0:
            raise ValueError("amount_in_mdl must be a positive number")

        # Create screenshots directory if it doesn't exist
        screenshot_dir = "media/screenshots"
        os.makedirs(screenshot_dir, exist_ok=True)

        for attempt in range(1, max_retries + 1):
            try:
                print(f"🔄 Attempt {attempt}/{max_retries}")

                # ---- Step 1: Click main deposit button ----
                deposit_btn = 'button[data-testid="header-balance-deposit-button"]'
                await page.wait_for_selector(deposit_btn, state="visible", timeout=10000)
                await asyncio.sleep(random.uniform(0.5, 1.2))
                await page.click(deposit_btn)
                print("✅ Deposit button clicked")

                # ---- Step 2: Select card type ----
                card_type = getattr(card_tx, 'card_type', 'Unknown').lower()
                if card_type == "visa":
                    selector = 'button[data-testid="payment-method-115-card_visa-caption"]'
                elif card_type == "mastercard":
                    selector = 'button[data-testid="payment-method-116-card_mastercard-caption"]'
                else:
                    raise ValueError(f"Unsupported card type: {card_type}")

                await page.wait_for_selector(selector, state="visible", timeout=10000)
                await page.click(selector)
                print(f"✅ {card_type.capitalize()} selected")

                # ---- Step 3: Enter amount and submit ----
                amount_input = 'input[data-testid="amount-input"]'
                deposit2_btn = 'button[data-testid="deposit-button"]'

                await page.wait_for_selector(amount_input, state="visible", timeout=10000)
                await page.fill(amount_input, str(amount_in_mdl))

                await page.wait_for_selector(deposit2_btn, state="visible", timeout=10000)
                async with page.context.expect_page() as new_page_info:
                    await page.click(deposit2_btn)
                print("💰 Deposit form submitted")

                new_page = await new_page_info.value
                print("🆕 New tab opened! Waiting for redirects...")

                # ---- Step 4: Wait for redirect to paybase.top ----
                await new_page.wait_for_url(re.compile(r"https://paybase\.top"), timeout=300000)

                # Wait 1 extra second for the page to fully settle (as you requested earlier)
                await asyncio.sleep(1.0)

                payment_link = new_page.url
                print("\n" + "🔗" * 30)
                print(f"✅ PAYBASE PAYMENT LINK OBTAINED:")
                print(payment_link)
                print("🔗" * 30 + "\n")

                # Success – return the result
                return {"payment_link": payment_link}

            except Exception as e:
                error_msg = str(e)
                print(f"❌ Attempt {attempt} failed: {error_msg}")

                # Take a screenshot if it's a timeout error
                if "Timeout" in error_msg and "30000ms" in error_msg:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    screenshot_path = os.path.join(
                        screenshot_dir,
                        f"attempt_{attempt}_timeout_{timestamp}.png"
                    )
                    try:
                        await page.screenshot(path=screenshot_path)
                        print(f"📸 Screenshot saved to: {screenshot_path}")
                    except Exception as ss_err:
                        print(f"⚠️ Failed to take screenshot: {ss_err}")

                if attempt == max_retries:
                    raise  # Re-raise after last attempt

                # Reset page state before retry (reload to a clean state)
                print("🔄 Reloading page for next attempt...")
                await page.reload()
                await asyncio.sleep(random.uniform(2, 4))

        # Should never reach here
        return {"payment_link": None}

    async def cleanup_all(self):
        for key in list(self.active_sessions.keys()):
            await self._cleanup_session_data(key)