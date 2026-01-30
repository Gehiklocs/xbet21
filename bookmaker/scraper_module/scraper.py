# scraper/scraper.py
import asyncio
import time
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import logging
import re
from tqdm import tqdm
from django.utils import timezone

from asgiref.sync import sync_to_async
from matches.models import Match, Bookmaker, Odds, Team, Market, Outcome

logger = logging.getLogger(__name__)


class XStakeScraper:
    def __init__(self):
        self.base_url = "https://gstake.net/line/201"
        self.playwright = None
        self.browser = None
        self.page = None
        print("✅ XStakeScraper initialized with Playwright (async)")

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - ensures proper cleanup"""
        await self.cleanup()

    async def setup_driver(self):
        """Setup Playwright browser and page with proper context management"""
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--window-size=1920,1080"
                ]
            )
            self.page = await self.browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                viewport={"width": 1920, "height": 1080}
            )
            
            # Log every request made by the page
            # self.page.on("request", lambda request: print(f"🌐 REQUEST: {request.method} {request.url}"))

            print("✅ Playwright browser & page setup successfully")
            return True
        except Exception as e:
            print(f"❌ Error setting up Playwright driver: {e}")
            await self.cleanup()
            return False

    async def cleanup(self):
        """Proper cleanup of all resources"""
        cleanup_errors = []

        try:
            if self.page:
                await self.page.close()
                self.page = None
                print("✅ Page closed")
        except Exception as e:
            cleanup_errors.append(f"Page close: {e}")

        try:
            if self.browser:
                await self.browser.close()
                self.browser = None
                print("✅ Browser closed")
        except Exception as e:
            cleanup_errors.append(f"Browser close: {e}")

        try:
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
                print("✅ Playwright stopped")
        except Exception as e:
            cleanup_errors.append(f"Playwright stop: {e}")

        if cleanup_errors:
            print(f"⚠️ Cleanup warnings: {', '.join(cleanup_errors)}")

    def parse_match_datetime(self, date_string, time_string):
        """Parse date and time strings to timezone-aware datetime object"""
        try:
            day, month = date_string.split('.')
            current_year = datetime.now().year
            hours, minutes = time_string.split(':')
            naive_datetime = datetime(current_year, int(month), int(day), int(hours), int(minutes))
            aware_datetime = timezone.make_aware(naive_datetime)
            return aware_datetime
        except Exception as e:
            return timezone.now()

    def parse_odds(self, odds_text):
        """Parse odds text to decimal numbers"""
        try:
            odds_value = float(odds_text.strip())
            return odds_value
        except (ValueError, TypeError) as e:
            return None

    async def extract_logo_url(self, element):
        """Extract logo URL from element's style attribute"""
        try:
            style = await element.get_attribute('style')
            if style and 'background-image' in style:
                pattern = r'url\("([^"]+)"\)'
                match = re.search(pattern, style)
                if match:
                    return match.group(1)
        except Exception as e:
            pass
        return None

    async def wait_for_selector_safe(self, selector, timeout=10000):
        """Wrapper for waiting for selector; returns element or None"""
        try:
            return await self.page.wait_for_selector(selector, timeout=timeout)
        except PlaywrightTimeoutError:
            return None

    async def query_all(self, selector):
        """Return list of element handles for selector (may be empty)"""
        try:
            elements = await self.page.query_selector_all(selector)
            return elements or []
        except Exception:
            return []

    async def monitor_main_list_persistent(self, status_check_callback=None, log_callback=None):
        """
        Persistently monitors the main match list page (gstake.net/line/201).
        Keeps the browser open and scrapes data every 5 seconds.
        
        Args:
            status_check_callback (callable): Async function that returns True if scraping should continue.
            log_callback (callable): Function to handle log messages.
        """
        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        url = "https://gstake.net/line/201"
        log(f"🟢 Opening persistent connection to Main List: {url}")
        
        try:
            if not self.page:
                await self.setup_driver()
                
            await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            log("   ✅ Page loaded. Waiting for dynamic content...")
            
            try:
                await self.page.wait_for_selector(".accordion.league-wrap", timeout=15000)
            except:
                log("   ⚠️ Timeout waiting for league selector, proceeding anyway...")

            heartbeat = 0

            while True:
                # Check if we should stop
                if status_check_callback:
                    should_continue = await status_check_callback()
                    if not should_continue:
                        log("🛑 Stop signal received. Exiting monitor loop.")
                        break

                # Extract ALL match data from the page using JS
                scraped_data = await self.page.evaluate("""() => {
                    const matches = [];
                    const leagues = document.querySelectorAll('.accordion.league-wrap, .league-wrap');
                    
                    leagues.forEach(league => {
                        let leagueName = "Unknown";
                        const titleEl = league.querySelector('.icon-title__text');
                        if (titleEl) leagueName = titleEl.innerText.trim();
                        
                        const events = league.querySelectorAll('.event');
                        
                        events.forEach(event => {
                            const match = {
                                league_name: leagueName,
                                match_url: null,
                                raw_date: "",
                                raw_time: "",
                                home_team: "",
                                away_team: "",
                                home_odds: null,
                                draw_odds: null,
                                away_odds: null,
                                markets: []
                            };
                            
                            const link = event.querySelector('a.event__link') || event.querySelector('a');
                            if (link) {
                                const href = link.getAttribute('href');
                                match.match_url = href.startsWith('http') ? href : 'https://gstake.net' + (href.startsWith('/') ? href : '/' + href);
                            }
                            
                            const timeEl = event.querySelector('.time__hours');
                            const dateEl = event.querySelector('.time__date');
                            if (timeEl) match.raw_time = timeEl.innerText.trim();
                            if (dateEl) match.raw_date = dateEl.innerText.trim();
                            
                            const opps = event.querySelectorAll('.opps__container .icon-title__text');
                            if (opps.length >= 2) {
                                match.home_team = opps[0].innerText.trim();
                                match.away_team = opps[1].innerText.trim();
                            } else {
                                const teams = event.querySelectorAll('.icon-title__text');
                                if (teams.length >= 2) {
                                    match.home_team = teams[0].innerText.trim();
                                    match.away_team = teams[1].innerText.trim();
                                }
                            }
                            
                            const oddsContainer = event.querySelector('.odds');
                            if (oddsContainer) {
                                const buttons = oddsContainer.querySelectorAll('.stake-button');
                                if (buttons.length >= 3) {
                                    const h = buttons[0].querySelector('.formated-odd');
                                    const d = buttons[1].querySelector('.formated-odd');
                                    const a = buttons[2].querySelector('.formated-odd');
                                    
                                    if (h) match.home_odds = h.innerText.trim();
                                    if (d) match.draw_odds = d.innerText.trim();
                                    if (a) match.away_odds = a.innerText.trim();
                                }
                            }
                            
                            if (match.home_team && match.away_team) {
                                matches.push(match);
                            }
                        });
                    });
                    return matches;
                }""")

                if scraped_data:
                    log(f"\n   📥 Extracted {len(scraped_data)} matches. Processing...")
                    
                    processed_data = []
                    for m in scraped_data:
                        # Log match details
                        log(f"      ⚽ {m['home_team']} vs {m['away_team']} ({m['league_name']})")
                        log(f"         🕒 {m['raw_time']} | Odds: 1: {m['home_odds']} | X: {m['draw_odds']} | 2: {m['away_odds']}")

                        try: m['home_odds'] = float(m['home_odds']) if m['home_odds'] else None
                        except: m['home_odds'] = None
                        try: m['draw_odds'] = float(m['draw_odds']) if m['draw_odds'] else None
                        except: m['draw_odds'] = None
                        try: m['away_odds'] = float(m['away_odds']) if m['away_odds'] else None
                        except: m['away_odds'] = None
                        
                        try:
                            if m['raw_date'] and m['raw_time']:
                                day, month = map(int, m['raw_date'].split('.'))
                                hour, minute = map(int, m['raw_time'].split(':'))
                                year = datetime.now().year
                                dt = datetime(year, month, day, hour, minute)
                                m['match_datetime'] = timezone.make_aware(dt)
                            else:
                                m['match_datetime'] = timezone.now()
                        except:
                            m['match_datetime'] = timezone.now()

                        processed_data.append(m)

                    await bulk_save_scraped_data(processed_data)
                    log(f"   💾 Saved {len(processed_data)} matches to DB.")

                else:
                    log("   ⚠️ No matches found on page.")

                heartbeat += 1
                if heartbeat >= 6:
                    log("   💓 Monitor active...")
                    heartbeat = 0

                await asyncio.sleep(5)

        except Exception as e:
            log(f"   ❌ Error monitoring main list: {e}")
            # Don't close page here, let the loop or caller handle it, or retry
            # await self.page.close() 

    async def scrape_match_detail_page(self, match_url):
        """
        Scrapes all odds from a specific match detail page.
        Returns a dictionary with all found markets and odds.
        """
        try:
            print(f"🔍 Visiting: {match_url}")
            await self.page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)  # Wait for dynamic content

            # Extract Match Info (Score, Time)
            score_text = ""
            try:
                score_el = await self.page.query_selector('.score') or await self.page.query_selector('.match-score')
                if score_el:
                    score_text = (await score_el.inner_text()).strip()
            except: pass

            print(f"   📊 Score: {score_text if score_text else 'Not started/No score'}")

            # Extract All Markets
            markets_data = {}
            
            # Look for market containers (often accordions or blocks)
            # Adjust selectors based on actual site structure
            market_containers = await self.page.query_selector_all('.accordion-stake')
            if not market_containers:
                market_containers = await self.page.query_selector_all('.market-group') # Fallback

            for container in market_containers:
                try:
                    # Get Market Name
                    title_el = await container.query_selector('.accordion__header') or await container.query_selector('.market-title')
                    if not title_el: continue
                    market_name = (await title_el.inner_text()).strip()
                    
                    if market_name not in markets_data:
                        markets_data[market_name] = []

                    # Get Outcomes
                    buttons = await container.query_selector_all('.stake-button')
                    for btn in buttons:
                        name_el = await btn.query_selector('.stake__title')
                        odd_el = await btn.query_selector('.formated-odd')
                        
                        if name_el and odd_el:
                            outcome_name = (await name_el.inner_text()).strip()
                            odd_val = (await odd_el.inner_text()).strip()
                            markets_data[market_name].append(f"{outcome_name}: {odd_val}")
                except Exception: continue

            return markets_data

        except Exception as e:
            print(f"   ❌ Error scraping detail page: {e}")
            return {}

    async def update_specific_matches(self, matches_queryset):
        """
        Task 2: Simplified update task.
        """
        try:
            print(f"🚀 Starting Detail Update for {len(matches_queryset)} matches...")
            
            matches_list = []
            async for m in matches_queryset:
                matches_list.append(m)

            for match in tqdm(matches_list, desc="Recalculating Derived Odds", unit="match"):
                await calculate_and_save_derived_odds(match)
            
            print("✅ Detail update complete (derived odds recalculated).")
            return True

        except Exception as e:
            print(f"💥 Detail update error: {e}")
            return False
        finally:
            pass

    async def update_live_matches(self, matches_queryset):
        """
        Task 4: Visits match pages to check for live scores and status updates.
        """
        try:
            print("🚀 Starting Live Match Monitoring...")
            if not await self.setup_driver():
                return False
            
            matches_list = []
            async for m in matches_queryset:
                matches_list.append(m)
            
            for match in matches_list:
                if not match.match_url:
                    continue
                    
                print(f"🔴 Checking Live: {match.home_team} vs {match.away_team}")
                await self.scrape_match_status_score(match)
                
            print("✅ Live monitoring complete.")
            return True
            
        except Exception as e:
            print(f"💥 Live monitoring error: {e}")
            return False
        finally:
            await self.cleanup()

    async def scrape_match_status_score(self, match):
        """
        Navigates to the match page and extracts score/status.
        Improved robustness for score parsing.
        """
        try:
            await self.page.goto(match.match_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3) # Wait for dynamic score updates
            
            home_score = None
            away_score = None
            status = None
            
            # 1. Try multiple selectors for score
            score_selectors = [
                '.score', '.match-score', '.event__score', 
                '.live-score', '.scoreboard', '[class*="score"]'
            ]
            
            score_text = ""
            for selector in score_selectors:
                try:
                    el = await self.page.query_selector(selector)
                    if el:
                        score_text = (await el.inner_text()).strip()
                        if score_text and (':' in score_text or '-' in score_text):
                            break
                except: continue

            # 2. Robust Score Parsing
            if score_text:
                # Remove any non-digit/separator chars
                clean_score = re.sub(r'[^\d:-]', '', score_text)
                parts = re.split(r'[-:]', clean_score)
                
                if len(parts) >= 2:
                    try:
                        h_s = int(parts[0].strip())
                        a_s = int(parts[1].strip())
                        # Sanity check: scores shouldn't be absurdly high (e.g. 2024)
                        if 0 <= h_s < 50 and 0 <= a_s < 50:
                            home_score = h_s
                            away_score = a_s
                    except ValueError: pass

            # 3. Check Status (Finished/Live)
            content_text = await self.page.content()
            content_lower = content_text.lower()
            
            finished_keywords = ["full time", "finished", "ft", "ended", "final result"]
            if any(k in content_lower for k in finished_keywords):
                status = Match.STATUS_FINISHED
            elif home_score is not None:
                status = Match.STATUS_LIVE
            
            # 4. Update DB safely
            if home_score is not None and away_score is not None:
                await update_match_score_status(match, home_score, away_score, status)
                print(f"   📝 Updated: {home_score} - {away_score} ({status or 'Live'})")
            elif status == Match.STATUS_FINISHED:
                await update_match_status(match, status)
                print(f"   🏁 Match Finished (Score not found/changed)")
                
        except Exception as e:
            print(f"   ⚠️ Failed to update score: {e}")

    # Deprecated method kept for compatibility
    async def scrape_matches(self, reuse_driver=False):
        return await self.scrape_main_list_only()

    async def scrape_main_list_only(self):
        """
        Task 1: Scrapes the main page to get leagues and match lists, including all detailed odds.
        Uses bulk processing for speed.
        """
        try:
            print("🚀 Starting Main List Scrape (Bulk Mode)...")
            if not await self.setup_driver():
                return False

            await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=60000)
            print("✅ Page loaded")
            
            await self.wait_for_selector_safe(".accordion.league-wrap", timeout=10000)

            league_containers = await self.query_all(".accordion.league-wrap")
            if not league_containers:
                league_containers = await self.query_all(".league-wrap")
            
            if not league_containers:
                print("❌ No league containers found.")
                return False

            scraped_data = []
            print("🔍 Parsing page content...")
            
            for league_container in league_containers:
                try:
                    league_name = "Unknown League"
                    try:
                        title_el = await league_container.query_selector('.icon-title__text')
                        if title_el:
                            league_name = (await title_el.inner_text()).strip() or league_name
                    except Exception: pass

                    match_containers = await league_container.query_selector_all('.event')
                    
                    for match_container in match_containers:
                        try:
                            match_data = {
                                'league_name': league_name,
                                'match_url': None,
                                'match_datetime': timezone.now(),
                                'home_team': None,
                                'away_team': None,
                                'home_logo': None,
                                'away_logo': None,
                                'home_odds': None,
                                'draw_odds': None,
                                'away_odds': None,
                                'markets': []
                            }

                            try:
                                link_el = await match_container.query_selector('a.event__link')
                                if not link_el: link_el = await match_container.query_selector('a')
                                if link_el:
                                    href = await link_el.get_attribute('href')
                                    if href:
                                        match_data['match_url'] = href if href.startswith('http') else f"https://gstake.net/{href.lstrip('/')}"
                            except Exception: pass

                            try:
                                time_el = await match_container.query_selector('.time__hours')
                                date_el = await match_container.query_selector('.time__date')
                                match_time = (await time_el.inner_text()).strip() if time_el else ""
                                match_date_str = (await date_el.inner_text()).strip() if date_el else ""
                                if match_time and match_date_str:
                                    match_data['match_datetime'] = self.parse_match_datetime(match_date_str, match_time)
                            except Exception: pass

                            try:
                                team_containers = await match_container.query_selector_all('.opps__container .icon-title')
                                if len(team_containers) >= 2:
                                    ht_el = await team_containers[0].query_selector('.icon-title__text')
                                    match_data['home_team'] = (await ht_el.inner_text()).strip() if ht_el else ""
                                    hl_el = await team_containers[0].query_selector('.icon-title__icon')
                                    if hl_el: match_data['home_logo'] = await self.extract_logo_url(hl_el)
                                    
                                    at_el = await team_containers[1].query_selector('.icon-title__text')
                                    match_data['away_team'] = (await at_el.inner_text()).strip() if at_el else ""
                                    al_el = await team_containers[1].query_selector('.icon-title__icon')
                                    if al_el: match_data['away_logo'] = await self.extract_logo_url(al_el)
                                else:
                                    team_elements = await match_container.query_selector_all('.icon-title__text')
                                    if len(team_elements) >= 3:
                                        match_data['home_team'] = (await team_elements[1].inner_text()).strip()
                                        match_data['away_team'] = (await team_elements[2].inner_text()).strip()
                            except Exception: continue

                            if not match_data['home_team'] or not match_data['away_team']: continue

                            try:
                                main_odds_container = await match_container.query_selector('.odds')
                                if main_odds_container:
                                    stake_buttons = await main_odds_container.query_selector_all('.stake-button')
                                    if len(stake_buttons) >= 3:
                                        h_text = await stake_buttons[0].query_selector('.formated-odd')
                                        d_text = await stake_buttons[1].query_selector('.formated-odd')
                                        a_text = await stake_buttons[2].query_selector('.formated-odd')
                                        
                                        match_data['home_odds'] = self.parse_odds((await h_text.inner_text()) if h_text else "")
                                        match_data['draw_odds'] = self.parse_odds((await d_text.inner_text()) if d_text else "")
                                        match_data['away_odds'] = self.parse_odds((await a_text.inner_text()) if a_text else "")
                            except Exception: pass

                            accordions = await match_container.query_selector_all('.accordion-stake')
                            for accordion in accordions:
                                header = await accordion.query_selector('.accordion__header')
                                if not header: continue
                                market_name = (await header.inner_text()).strip()
                                
                                market_data = {'name': market_name, 'outcomes': []}
                                
                                stake_buttons = await accordion.query_selector_all('.stake-button')
                                for button in stake_buttons:
                                    title_el = await button.query_selector('.stake__title')
                                    odd_el = await button.query_selector('.formated-odd')
                                    
                                    if title_el and odd_el:
                                        outcome_name = (await title_el.inner_text()).strip()
                                        odd_value_text = (await odd_el.inner_text()).strip()
                                        odd_value = self.parse_odds(odd_value_text)
                                        
                                        if odd_value:
                                            market_data['outcomes'].append({'name': outcome_name, 'odds': odd_value})
                                
                                if market_data['outcomes']:
                                    match_data['markets'].append(market_data)

                            scraped_data.append(match_data)
                            print(f"⚽ Found: {match_data['home_team']} vs {match_data['away_team']}")

                        except Exception as e:
                            print(f"⚠️ Error parsing match: {e}")
                            continue
                except Exception as e:
                    print(f"⚠️ Error parsing league: {e}")
                    continue
            
            print(f"💾 Saving {len(scraped_data)} matches to database...")
            await bulk_save_scraped_data(scraped_data)
            
            print(f"✅ Main list scrape complete.")
            return True
            
        except Exception as e:
            print(f"💥 Main list scrape error: {e}")
            return False
        finally:
            await self.cleanup()


# ------------------------
# DB wrappers (sync_to_async)
# ------------------------
@sync_to_async
def bulk_save_scraped_data(scraped_data):
    """
    Efficiently saves all scraped data using bulk operations and transactions.
    """
    if not scraped_data:
        return

    # 1. Get or Create Teams (Bulk-ish)
    team_cache = {}
    all_team_names = set()
    for m in scraped_data:
        all_team_names.add(m['home_team'])
        all_team_names.add(m['away_team'])
    
    existing_teams = Team.objects.filter(name__in=all_team_names)
    for t in existing_teams:
        team_cache[t.name] = t
        
    new_teams = []
    for name in all_team_names:
        if name not in team_cache:
            new_teams.append(Team(name=name))
    
    if new_teams:
        Team.objects.bulk_create(new_teams, ignore_conflicts=True)
        for t in Team.objects.filter(name__in=[nt.name for nt in new_teams]):
            team_cache[t.name] = t

    # 2. Get or Create Matches
    bookmaker, _ = Bookmaker.objects.get_or_create(name="XStake", defaults={'website': "https://xstake.bet"})
    
    for m_data in scraped_data:
        home_team = team_cache.get(m_data['home_team'])
        away_team = team_cache.get(m_data['away_team'])
        
        if not home_team or not away_team:
            continue

        match, created = Match.objects.get_or_create(
            home_team=home_team,
            away_team=away_team,
            match_date=m_data['match_datetime'],
            defaults={'league': m_data['league_name'], 'match_url': m_data['match_url']}
        )
        
        if m_data['match_url'] and match.match_url != m_data['match_url']:
            match.match_url = m_data['match_url']
            match.save()

        # 3. Update 1X2 Odds
        if all([m_data['home_odds'], m_data['draw_odds'], m_data['away_odds']]):
            Odds.objects.update_or_create(
                match=match,
                bookmaker=bookmaker,
                defaults={
                    'home_odds': m_data['home_odds'],
                    'draw_odds': m_data['draw_odds'],
                    'away_odds': m_data['away_odds']
                }
            )

        # 4. Update Markets & Outcomes
        for market_data in m_data['markets']:
            market, _ = Market.objects.get_or_create(match=match, name=market_data['name'])
            
            for outcome_data in market_data['outcomes']:
                Outcome.objects.update_or_create(
                    market=market,
                    name=outcome_data['name'],
                    defaults={'odds': outcome_data['odds']}
                )
        
        # 5. Calculate Derived Odds
        match.calculate_derived_odds()

@sync_to_async
def calculate_and_save_derived_odds(match_obj):
    match_obj.calculate_derived_odds()

@sync_to_async
def update_match_score_status(match, home_score, away_score, status=None):
    # Ensure scores are integers
    try:
        match.home_score = int(home_score)
        match.away_score = int(away_score)
        if status:
            match.status = status
        match.save()
    except (ValueError, TypeError):
        pass

@sync_to_async
def update_match_status(match, status):
    match.status = status
    match.save()
