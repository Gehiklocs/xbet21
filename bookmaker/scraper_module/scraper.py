# scraper/scraper.py
import asyncio
import time
from datetime import datetime, timedelta

import self
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import logging
import re
from tqdm import tqdm
from django.utils import timezone

from django.db import transaction
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
        Removes matches from DB that are no longer present on the page.

        Args:
            status_check_callback (callable): Async function that returns True if scraping should continue.
            log_callback (callable): Function to handle log messages.
        """

        async def log(msg):
            if log_callback:
                await log_callback(msg)
            else:
                print(msg)

        url = "https://gstake.net/line/201"
        await log(f"🟢 Opening persistent connection to Main List: {url}")

        try:
            if not self.page:
                await self.setup_driver()

            await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await log("   ✅ Page loaded. Waiting for dynamic content...")

            try:
                await self.page.wait_for_selector(".accordion.league-wrap", timeout=15000)
            except:
                await log("   ⚠️ Timeout waiting for league selector, proceeding anyway...")

            heartbeat = 0
            # Store unique identifiers for matches in current scrape
            current_match_identifiers = set()
            # Track when we last cleaned up old matches
            last_cleanup_time = timezone.now()

            while True:
                # Check if we should stop
                if status_check_callback:
                    should_continue = await status_check_callback()
                    if not should_continue:
                        await log("🛑 Stop signal received. Exiting monitor loop.")
                        # Clean up any old matches before exiting
                        await self.cleanup_old_matches(current_match_identifiers)
                        break

                # Clear identifiers for this iteration
                current_match_identifiers.clear()

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
                    await log(f"\n   📥 Extracted {len(scraped_data)} matches. Processing...")

                    processed_data = []
                    for m in scraped_data:
                        # Log match details
                        await log(f"      ⚽ {m['home_team']} vs {m['away_team']} ({m['league_name']})")
                        await log(
                            f"         🕒 {m['raw_time']} | Odds: 1: {m['home_odds']} | X: {m['draw_odds']} | 2: {m['away_odds']}")

                        # Parse odds
                        try:
                            m['home_odds'] = float(m['home_odds']) if m['home_odds'] else None
                        except:
                            m['home_odds'] = None
                        try:
                            m['draw_odds'] = float(m['draw_odds']) if m['draw_odds'] else None
                        except:
                            m['draw_odds'] = None
                        try:
                            m['away_odds'] = float(m['away_odds']) if m['away_odds'] else None
                        except:
                            m['away_odds'] = None

                        # Parse date and time
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

                        # Create a unique identifier for this match
                        # Using teams and date as identifier
                        match_identifier = f"{m['home_team']}_{m['away_team']}_{m['match_datetime'].strftime('%Y%m%d')}"
                        current_match_identifiers.add(match_identifier)

                        processed_data.append(m)

                    # Save matches to database
                    saved_count = await bulk_save_scraped_data(processed_data)
                    await log(f"   💾 Saved/Updated {saved_count} matches to DB.")

                    # Check if we should clean up old matches
                    now = timezone.now()
                    time_since_last_cleanup = (now - last_cleanup_time).total_seconds()

                    # Clean up old matches every 30 minutes or when match count decreases significantly
                    if time_since_last_cleanup > 1800:  # 30 minutes
                        await self.cleanup_old_matches(current_match_identifiers, log)
                        last_cleanup_time = now
                    elif len(scraped_data) > 0 and len(processed_data) > 0:
                        # Also clean up if we detect missing matches
                        await self.check_and_cleanup_matches(processed_data, log)

                else:
                    await log("   ⚠️ No matches found on page.")

                heartbeat += 1
                if heartbeat >= 6:
                    await log("   💓 Monitor active...")
                    heartbeat = 0

                await asyncio.sleep(5)

        except Exception as e:
            await log(f"   ❌ Error monitoring main list: {e}")
            # Don't close page here, let the loop or caller handle it, or retry

    async def monitor_live_page_persistent(self, status_check_callback=None, log_callback=None):
        """
        Persistently monitors the live matches page.
        Matches DB records with scraped data using unique match_url as the primary identifier.
        """

        async def log(msg):
            if log_callback:
                await log_callback(msg)
            else:
                print(msg)

        await log("🚀 Starting persistent live monitor (URL Matching Mode)...")
        url = "https://gstake.net/live/201"

        try:
            if not self.page:
                await self.setup_driver()

            await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)

            async def handle_console(msg):
                pass

            self.page.on("console", handle_console)

            from asgiref.sync import sync_to_async
            from matches.models import Match
            from django.utils import timezone

            @sync_to_async
            def get_db_live_identifiers():
                live_matches = Match.objects.filter(status='live').select_related('home_team', 'away_team')
                identifiers = set()
                for m in live_matches:
                    # 1. Prioritize URL. 2. Fallback to lowercase home|away
                    if m.match_url:
                        identifiers.add(m.match_url)
                    else:
                        home = m.home_team.name.strip().lower()
                        away = m.away_team.name.strip().lower()
                        identifiers.add(f"{home}|{away}")
                return identifiers

            iteration_count = 0

            while True:
                iteration_count += 1

                if status_check_callback and not await status_check_callback():
                    await log("🛑 Stop signal received. Exiting live monitor.")
                    break

                db_live_identifiers = await get_db_live_identifiers()
                current_identifiers = set()

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
                                league_name: leagueName, match_url: null, raw_date: "", raw_time: "",
                                home_team: "", away_team: "", home_odds: null, draw_odds: null, away_odds: null,
                                home_score: null, away_score: null, match_status: "live", markets: []
                            };

                            const link = event.querySelector('a.event__link') || event.querySelector('a');
                            if (link) {
                                const href = link.getAttribute('href');
                                match.match_url = href.startsWith('http') ? href : 'https://gstake.net' + (href.startsWith('/') ? href : '/' + href);
                            }

                            const timeEl = event.querySelector('.time__hours');
                            if (timeEl) match.raw_time = timeEl.innerText.trim();

                            const scoreEl = event.querySelector('.score');
                            if (scoreEl) {
                                const scoreText = scoreEl.innerText.trim();
                                if (scoreText.includes('-')) {
                                    const scores = scoreText.split('-');
                                    if (scores.length >= 2) {
                                        match.home_score = parseInt(scores[0].trim()) || null;
                                        match.away_score = parseInt(scores[1].trim()) || null;
                                    }
                                }
                            }

                            const opps = event.querySelectorAll('.opps__container .icon-title__text');
                            if (opps.length >= 2) {
                                match.home_team = opps[0].innerText.trim(); match.away_team = opps[1].innerText.trim();
                            } else {
                                const teams = event.querySelectorAll('.icon-title__text');
                                if (teams.length >= 2) {
                                    match.home_team = teams[0].innerText.trim(); match.away_team = teams[1].innerText.trim();
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

                            const statusEl = event.querySelector('.status, .match-status, .live-indicator');
                            if (statusEl) {
                                const statusText = statusEl.innerText.trim().toLowerCase();
                                if (statusText.includes('finished') || statusText.includes('ft')) {
                                    match.match_status = 'finished';
                                }
                            }

                            if (match.home_team && match.away_team) { matches.push(match); }
                        });
                    });
                    return matches;
                }""")

                if scraped_data:
                    processed_data = []

                    for m in scraped_data:
                        # 1. ALWAYS add the home|away string so older DB entries can find a match
                        home = m['home_team'].strip().lower()
                        away = m['away_team'].strip().lower()
                        string_identifier = f"{home}|{away}"
                        current_identifiers.add(string_identifier)

                        # 2. ALSO add the URL if it exists so newer DB entries can find a match
                        if m.get('match_url'):
                            current_identifiers.add(m['match_url'])

                        # For logging purposes, we'll pick one to display
                        display_ident = m.get('match_url') or string_identifier

                        # Clean up numeric odds
                        for odd_type in ['home_odds', 'draw_odds', 'away_odds']:
                            try:
                                m[odd_type] = float(m[odd_type]) if m[odd_type] else None
                            except:
                                m[odd_type] = None

                        # ---> ADD THESE 3 LINES <---
                        # If the match is currently at half-time, lock in the HT score!
                        if m.get('match_status') == 'halftime':
                            m['half_time_home_score'] = m.get('home_score')
                            m['half_time_away_score'] = m.get('away_score')

                        m['scraped_at'] = timezone.now()
                        processed_data.append(m)

                    await log(f"\n🔍 Scraped {len(scraped_data)} matches from page:")
                    # Just printing a summary so it's clean
                    for m in scraped_data:
                        display_id = m.get(
                            'match_url') or f"{m['home_team'].strip().lower()}|{m['away_team'].strip().lower()}"
                        await log(f"   -> 🟢 Active: [{display_id}]")

                    if hasattr(self, 'save_live_matches_structured'):
                        try:
                            saved_count = await self.save_live_matches_structured(processed_data)
                            await log(f"✅ DB Updated: {saved_count}")
                        except Exception as e:
                            await log(f"❌ DB Update Error: {e}")

                    disappeared_matches = db_live_identifiers - current_identifiers
                    if disappeared_matches:
                        await log(f"🚨 Missing matches detected: {len(disappeared_matches)} (Marking as Finished)")
                        for identifier in disappeared_matches:
                            await log(f"   -> ❌ Match vanished: [{identifier}]")
                            if hasattr(self, 'mark_match_as_finished_by_identifier'):
                                await self.mark_match_as_finished_by_identifier(identifier, log)

                    finished_matches = [m for m in processed_data if m.get('match_status') == 'finished']
                    if finished_matches and hasattr(self, 'update_finished_matches'):
                        await self.update_finished_matches(finished_matches, log)

                else:
                    if db_live_identifiers:
                        if iteration_count <= 2:
                            await log(
                                f"⏳ Page appears empty (Iteration {iteration_count}). Waiting for Javascript to render matches...")
                        else:
                            await log(f"🚨 Page empty. Marking all {len(db_live_identifiers)} DB matches as Finished.")
                            for identifier in db_live_identifiers:
                                await log(f"   -> ❌ Match vanished: [{identifier}]")
                                if hasattr(self, 'mark_match_as_finished_by_identifier'):
                                    await self.mark_match_as_finished_by_identifier(identifier, log)

                await asyncio.sleep(5)

        except Exception as e:
            await log(f"❌ Critical Error in live monitor: {e}")

    async def mark_match_as_finished_by_identifier(self, identifier, log_callback=None):
        """
        Mark a live match as finished in the database using its URL (or fallback identifier).
        Safeguarded against race conditions using row-level locking.
        """

        async def log(msg):
            if log_callback:
                await log_callback(msg)
            else:
                print(msg)

        try:
            from asgiref.sync import sync_to_async
            from matches.models import Match, Bet, ExpressBetSelection
            from django.db import transaction

            @sync_to_async
            def update_match_finished(identifier):
                with transaction.atomic():
                    # 1. Match by URL if it's a URL
                    if identifier.startswith('http'):
                        match_query = Match.objects.filter(match_url=identifier, status='live')
                    else:
                        # 2. Match by 2-part string (home|away) using case-insensitive lookup
                        parts = identifier.split('|')
                        if len(parts) == 2:
                            match_query = Match.objects.filter(
                                home_team__name__iexact=parts[0],
                                away_team__name__iexact=parts[1],
                                status='live'
                            )
                        else:
                            return {'updated': 0, 'bets_count': 0}

                    matches = list(match_query.select_for_update())
                    if not matches:
                        return {'updated': 0, 'bets_count': 0}

                    total_bets_settled = 0

                    for match in matches:
                        match.status = 'finished'
                        match.save()
                        match.settle_bets()

                        total_bets_settled += match.bets.exclude(status='pending').count()
                        total_bets_settled += ExpressBetSelection.objects.filter(match=match).exclude(
                            status='pending').count()

                    return {'updated': len(matches), 'bets_count': total_bets_settled}

            result = await update_match_finished(identifier)

            if result['updated'] > 0:
                await log(f"   -> 🏁 Finished {result['updated']} match(es) | 💰 Settled {result['bets_count']} bet(s)")

            return result

        except Exception as e:
            await log(f"      ❌ Failed to mark match as finished: {e}")
            return {'updated': 0, 'bets_count': 0}

    async def cleanup_old_live_matches(self, current_identifiers, log=None):
        """
        Clean up live matches that are no longer present on the live page.
        """

        async def log_msg(msg):
            if log:
                await log(msg)
            else:
                print(msg)

        try:
            # This would depend on your database structure
            # You need to implement this based on how you store live matches
            await log_msg("   🧹 Cleaning up old live matches...")

            # Example implementation:
            # 1. Get all live match IDs from database
            # 2. Compare with current_identifiers
            # 3. Remove those not in current_identifiers

            # Placeholder for actual implementation
            await log_msg("   ✅ Live match cleanup completed.")
        except Exception as e:
            await log_msg(f"   ❌ Error cleaning up live matches: {e}")

    async def check_and_cleanup_live_matches(self, current_matches, log=None):
        """
        Check and cleanup live matches when match count decreases.
        """

        async def log_msg(msg):
            if log:
                await log(msg)
            else:
                print(msg)

        try:
            # Implement logic to detect when live matches disappear from the page
            # and clean them up from the database
            pass
        except Exception as e:
            await log_msg(f"   ❌ Error in live match check/cleanup: {e}")

    async def process_live_structured_data(self, leagues_data, log_callback):
        """
        Process the structured live match data from the console/API.

        Args:
            leagues_data (list): List of league objects with events
            log_callback (function): Logging function

        Returns:
            list: Processed match data
        """
        processed_matches = []

        if not leagues_data or not isinstance(leagues_data, list):
            return processed_matches

        for league in leagues_data:
            if not league or not isinstance(league, dict):
                continue

            league_name = league.get('league', 'Unknown League')
            league_id = league.get('league_id', '')

            events = league.get('events', [])
            if not events:
                continue

            await log_callback(f"   📋 League: {league_name} ({len(events)} matches)")

            for event in events:
                try:
                    # Only process live events
                    event_type = event.get('type', '')
                    if event_type != 'live':
                        continue

                    # Extract basic match info
                    match_data = {
                        'match_id': event.get('id', ''),
                        'league_name': league_name,
                        'league_id': league_id,
                        'home_team': event.get('opp_1', ''),
                        'away_team': event.get('opp_2', ''),
                        'home_team_id': event.get('opp_1_id', ''),
                        'away_team_id': event.get('opp_2_id', ''),
                        'home_team_icon': event.get('opp_1_icon', ''),
                        'away_team_icon': event.get('opp_2_icon', ''),
                        'status': 'live',
                        'type': event_type,
                        'sr_id': event.get('sr_id', ''),
                        'count_odds': event.get('count_odds', 0)
                    }

                    # Parse start time
                    start_timestamp = event.get('start')
                    if start_timestamp:
                        try:
                            # Convert Unix timestamp to datetime
                            match_data['match_datetime'] = timezone.make_aware(
                                datetime.fromtimestamp(float(start_timestamp))
                            )
                        except:
                            match_data['match_datetime'] = timezone.now()
                    else:
                        match_data['match_datetime'] = timezone.now()

                    # Extract live stats
                    stats = event.get('stats', {})
                    if stats:
                        # Extract score
                        score = stats.get('score', {})
                        if score:
                            match_data['home_score'] = score.get('opp_1')
                            match_data['away_score'] = score.get('opp_2')
                            match_data['score'] = f"{match_data['home_score']}-{match_data['away_score']}"

                        # Check if match is finished
                        if stats.get('period') == 0 or stats.get('pNow') == 'Finished':
                            match_data['status'] = 'finished'

                    # Extract odds
                    odds_data = event.get('odds', [])
                    if odds_data:
                        for odds_market in odds_data:
                            market_name = odds_market.get('col_n', '')
                            period = odds_market.get('period', 0)

                            # Full-time odds (period 0)
                            if period == 0:
                                if market_name == 'Winner':  # 1X2 market
                                    od_values = odds_market.get('od', [])
                                    if len(od_values) >= 3:
                                        try:
                                            match_data['home_odds'] = float(od_values[0])
                                            match_data['draw_odds'] = float(od_values[1])
                                            match_data['away_odds'] = float(od_values[2])
                                        except:
                                            match_data['home_odds'] = od_values[0]
                                            match_data['draw_odds'] = od_values[1]
                                            match_data['away_odds'] = od_values[2]

                                elif market_name == 'Double chance':
                                    od_values = odds_market.get('od', [])
                                    if len(od_values) >= 3:
                                        try:
                                            match_data['odds_1x'] = float(od_values[0])  # 1X
                                            match_data['odds_12'] = float(od_values[1])  # 12
                                            match_data['odds_x2'] = float(od_values[2])  # X2
                                        except:
                                            match_data['odds_1x'] = od_values[0]
                                            match_data['odds_12'] = od_values[1]
                                            match_data['odds_x2'] = od_values[2]

                    # Log match details
                    score_display = f" ({match_data.get('score', '?-?')})" if match_data.get('score') else ""
                    status_display = f" [{match_data['status'].upper()}]"

                    await log_callback(
                        f"      ⚽ {match_data['home_team']} vs {match_data['away_team']}{score_display}{status_display}")

                    if match_data.get('home_odds') and match_data.get('away_odds'):
                        await log_callback(
                            f"         📊 Odds: 1: {match_data['home_odds']} | X: {match_data.get('draw_odds', 'N/A')} | 2: {match_data['away_odds']}")

                    processed_matches.append(match_data)

                except Exception as e:
                    await log_callback(f"         ⚠️ Error processing event: {e}")
                    continue

        return processed_matches

    async def save_live_matches_structured(self, matches_data):
        """
        Save structured live match data to the database (async version).
        Optimized by grouping all ORM operations into a single sync_to_async block.
        """
        from asgiref.sync import sync_to_async
        from django.utils import timezone

        @sync_to_async
        def _process_matches_synchronously(data_list):
            from django.db import transaction
            from matches.models import Match, Team, Odds, Bookmaker

            saved_count = 0

            # Get or create default bookmaker once
            bookmaker, _ = Bookmaker.objects.get_or_create(
                name="GStake",
                defaults={'website': 'https://gstake.net'}
            )

            for match_data in data_list:
                try:
                    # Use a transaction so if one match fails, it doesn't break the whole batch
                    with transaction.atomic():
                        # 1. Get or Create Teams
                        home_team, _ = Team.objects.get_or_create(
                            name=match_data['home_team'],
                            defaults={'logo_url': match_data.get('home_team_icon', '')}
                        )
                        away_team, _ = Team.objects.get_or_create(
                            name=match_data['away_team'],
                            defaults={'logo_url': match_data.get('away_team_icon', '')}
                        )

                        # 2. Find Existing Match (Prioritize URL, fallback to Teams)
                        match_url = match_data.get('match_url')
                        match_query = Match.objects.filter(status='live')

                        if match_url:
                            match = match_query.filter(match_url=match_url).first()
                        else:
                            match = match_query.filter(home_team=home_team, away_team=away_team).first()

                        # 3. Update or Create Match
                        if match:
                            # Update existing live data
                            match.status = match_data.get('match_status', 'live')
                            match.home_score = match_data.get('home_score')
                            match.away_score = match_data.get('away_score')
                            match.home_odds = match_data.get('home_odds')
                            match.draw_odds = match_data.get('draw_odds')
                            match.away_odds = match_data.get('away_odds')
                            match.scraped_at = timezone.now()

                            # Add Half-Time scores if the scraper caught them
                            if 'half_time_home_score' in match_data:
                                match.half_time_home_score = match_data['half_time_home_score']
                                match.half_time_away_score = match_data['half_time_away_score']

                            match.save()
                            saved_count += 1
                        else:
                            # Create brand new match
                            match = Match.objects.create(
                                home_team=home_team,
                                away_team=away_team,
                                match_date=match_data.get('match_datetime', timezone.now()),
                                league=match_data.get('league_name', 'Unknown'),
                                status=match_data.get('match_status', 'live'),
                                match_url=match_url,  # Critical for the vanishing logic!
                                home_score=match_data.get('home_score'),
                                away_score=match_data.get('away_score'),
                                home_odds=match_data.get('home_odds'),
                                draw_odds=match_data.get('draw_odds'),
                                away_odds=match_data.get('away_odds'),
                                scraped_at=timezone.now()
                            )

                            # Apply Half-Time scores if it started tracking during halftime
                            if 'half_time_home_score' in match_data:
                                match.half_time_home_score = match_data['half_time_home_score']
                                match.half_time_away_score = match_data['half_time_away_score']
                                match.save()

                            saved_count += 1

                        # 4. Update Odds Table
                        if match_data.get('home_odds') and match_data.get('away_odds'):
                            Odds.objects.update_or_create(
                                match=match,
                                bookmaker=bookmaker,
                                defaults={
                                    'home_odds': match_data.get('home_odds'),
                                    'draw_odds': match_data.get('draw_odds', 3.0),
                                    'away_odds': match_data.get('away_odds'),
                                }
                            )

                            # ---> ADD THIS ONE LINE <---
                            # Generate all 50+ derived odds (BTTS, Over/Under, etc.)
                            # using the fresh base odds we just saved!
                            match.calculate_derived_odds()

                except Exception as e:
                    print(
                        f"Error saving live match {match_data.get('home_team')} vs {match_data.get('away_team')}: {e}")
                    continue

            return saved_count

        # Execute the synchronized block
        try:
            return await _process_matches_synchronously(matches_data)
        except Exception as e:
            print(f"Error in save_live_matches_structured: {e}")
            return 0

    async def update_finished_matches(self, finished_matches_data, log_callback=None):
        """
        Update finished matches and settle bets.
        finished_matches_data: list of match dicts with 'home_team', 'away_team',
                               'home_score', 'away_score', etc.
        """

        async def log(msg):
            if log_callback:
                await log_callback(msg)
            else:
                print(msg)

        try:
            from asgiref.sync import sync_to_async
            from django.db import transaction
            from ..matches.models import Match

            @sync_to_async
            def update_and_settle(match_data):
                with transaction.atomic():
                    # Find the match
                    matches = Match.objects.filter(
                        home_team__name__iexact=match_data['home_team'],
                        away_team__name__iexact=match_data['away_team'],
                        status__in=['live', 'upcoming']  # only update if not already finished
                    ).order_by('-match_date')

                    if not matches.exists():
                        return 0

                    match = matches.first()
                    # Update scores if available
                    if match_data.get('home_score') is not None:
                        match.home_score = match_data['home_score']
                    if match_data.get('away_score') is not None:
                        match.away_score = match_data['away_score']

                    # Set status to finished
                    match.status = 'finished'
                    match.save()

                    # Settle all bets (this handles both single and express)
                    match.settle_bets()
                    return 1

            settled_count = 0
            for match_data in finished_matches_data:
                try:
                    result = await update_and_settle(match_data)
                    if result:
                        settled_count += 1
                        await log(f"   ✅ Settled bets for {match_data['home_team']} vs {match_data['away_team']}")
                except Exception as e:
                    await log(f"   ❌ Failed to settle {match_data['home_team']} vs {match_data['away_team']}: {e}")

            if settled_count:
                await log(f"   🏁 Settled {settled_count} finished matches.")

        except Exception as e:
            await log(f"   ❌ Error in update_finished_matches: {e}")

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

    async def cleanup_old_matches(self, current_match_identifiers, log_callback=None):
        """
        Remove matches from database that are no longer present in current scrape.

        Args:
            current_match_identifiers (set): Set of match identifiers from current scrape
            log_callback (callable): Function to handle log messages
        """

        async def log(msg):
            if log_callback:
                await log_callback(msg)
            else:
                print(msg)

        try:
            from django.db import transaction
            from django.db.models import Q
            from matches.models import Match, Team
            from django.utils import timezone

            await log("   🧹 Checking for old matches to clean up...")

            # Get all upcoming matches from database
            upcoming_matches = Match.objects.filter(
                status='upcoming',
                match_date__gte=timezone.now()
            ).select_related('home_team', 'away_team')

            matches_to_remove = []
            matches_to_keep = []

            for match in upcoming_matches:
                # Create identifier similar to what we use in scraping
                match_identifier = f"{match.home_team.name}_{match.away_team.name}_{match.match_date.strftime('%Y%m%d')}"

                if match_identifier in current_match_identifiers:
                    matches_to_keep.append(match.id)
                else:
                    matches_to_remove.append(match.id)

            if matches_to_remove:
                await log(f"   🗑️  Found {len(matches_to_remove)} old matches to remove")

                # Delete in batches to avoid long transactions
                batch_size = 50
                for i in range(0, len(matches_to_remove), batch_size):
                    batch = matches_to_remove[i:i + batch_size]
                    with transaction.atomic():
                        deleted_count, _ = Match.objects.filter(id__in=batch).delete()
                        await log(f"      Removed batch of {deleted_count} old matches")

                await log(f"   ✅ Total removed: {len(matches_to_remove)} old matches")
            else:
                await log("   ✅ No old matches to remove")

            return len(matches_to_remove)

        except Exception as e:
            await log(f"   ❌ Error cleaning up old matches: {e}")
            return 0

    async def check_and_cleanup_matches(self, current_matches_data, log_callback=None):
        """
        Check for missing matches and clean them up.
        """
        from asgiref.sync import sync_to_async

        async def log(msg):
            if log_callback:
                await log_callback(msg)
            else:
                print(msg)

        # Wrap the synchronous cleanup logic
        @sync_to_async
        def cleanup_sync(matches_data):
            from django.db import transaction
            from matches.models import Match
            from django.utils import timezone
            from datetime import timedelta

            try:
                # Skip if no current matches
                if not matches_data:
                    return 0

                # Get all upcoming matches from database
                db_matches = Match.objects.filter(
                    status='upcoming',
                    match_date__gte=timezone.now() - timedelta(days=1)
                ).select_related('home_team', 'away_team')

                # Create set of current match identifiers
                current_identifiers = set()
                for match_data in matches_data:
                    identifier = f"{match_data['home_team']}_{match_data['away_team']}_{match_data['match_datetime'].strftime('%Y%m%d')}"
                    current_identifiers.add(identifier)

                # Find matches in DB that are not in current scrape
                matches_to_remove = []
                for match in db_matches:
                    db_identifier = f"{match.home_team.name}_{match.away_team.name}_{match.match_date.strftime('%Y%m%d')}"

                    if db_identifier not in current_identifiers:
                        # Check if this match is within the next 24 hours
                        time_until_match = (match.match_date - timezone.now()).total_seconds()

                        # Only remove if match is more than 1 hour away
                        if time_until_match > 3600:  # 1 hour
                            matches_to_remove.append(match.id)

                if matches_to_remove:
                    # Delete matches
                    with transaction.atomic():
                        deleted_count, _ = Match.objects.filter(id__in=matches_to_remove).delete()
                        return deleted_count
                return 0

            except Exception as e:
                print(f"Error in cleanup_sync: {e}")
                return 0

        try:
            await log("   🔍 Checking for missing matches to clean up...")

            # Call the sync function
            deleted_count = await cleanup_sync(current_matches_data)

            if deleted_count > 0:
                await log(f"   ✅ Removed {deleted_count} missing matches")
            else:
                await log("   ✅ No missing matches to remove")

        except Exception as e:
            await log(f"   ⚠️ Error checking for missing matches: {e}")


@sync_to_async
def bulk_update_live_data(scraped_data):
    """
    Efficiently updates live match data.
    """
    team_names = set()
    for m in scraped_data:
        team_names.add(m['home_team'])
        team_names.add(m['away_team'])

    teams = {t.name: t for t in Team.objects.filter(name__in=team_names)}

    for m_data in scraped_data:
        home_team = teams.get(m_data['home_team'])
        away_team = teams.get(m_data['away_team'])

        if not home_team or not away_team:
            continue

        try:
            match = Match.objects.get(home_team=home_team, away_team=away_team, status=Match.STATUS_LIVE)

            # Update scores and odds
            match.home_score = m_data.get('home_score')
            match.away_score = m_data.get('away_score')

            for odd_name, odd_value in m_data['odds'].items():
                if odd_value:
                    setattr(match, odd_name, odd_value)

            match.save()
        except Match.DoesNotExist:
            # Could create a new match here if desired
            pass
        except Exception as e:
            print(f"Error updating match {m_data['home_team']} vs {m_data['away_team']}: {e}")

# ------------------------
# DB wrappers (sync_to_async)
# ------------------------


# Also update your bulk_save_scraped_data function to handle updates better
@sync_to_async
def bulk_save_scraped_data(matches_data):
    """
    Bulk save scraped matches to database with intelligent updating.

    Args:
        matches_data (list): List of match dictionaries

    Returns:
        int: Number of matches saved/updated
    """
    try:
        from django.db import transaction
        from django.db.models import Q
        from matches.models import Match, Team, Odds, Bookmaker
        from django.utils import timezone
        from datetime import datetime, timedelta

        saved_count = 0

        # Get or create default bookmaker
        bookmaker, _ = Bookmaker.objects.get_or_create(
            name="GStake",
            defaults={'website': 'https://gstake.net'}
        )

        for match_data in matches_data:
            try:
                with transaction.atomic():
                    # Get or create teams
                    home_team, _ = Team.objects.get_or_create(
                        name=match_data['home_team']
                    )
                    away_team, _ = Team.objects.get_or_create(
                        name=match_data['away_team']
                    )

                    # Try to find existing match
                    match = None

                    # Look for matches between these teams on the same day
                    match_date = match_data['match_datetime']

                    # Search with a 1-hour window to account for timezone differences
                    start_time = match_date - timedelta(hours=1)
                    end_time = match_date + timedelta(hours=1)

                    matches = Match.objects.filter(
                        home_team=home_team,
                        away_team=away_team,
                        match_date__range=(start_time, end_time)
                    )

                    if matches.exists():
                        match = matches.first()

                        # Update existing match if it's still upcoming
                        if match.status == 'upcoming':
                            match.home_odds = match_data.get('home_odds')
                            match.draw_odds = match_data.get('draw_odds')
                            match.away_odds = match_data.get('away_odds')
                            match.match_url = match_data.get('match_url', match.match_url)
                            match.scraped_at = timezone.now()
                            match.save()

                            saved_count += 1

                            # Update odds
                            if match_data.get('home_odds') and match_data.get('away_odds'):
                                Odds.objects.update_or_create(
                                    match=match,
                                    bookmaker=bookmaker,
                                    defaults={
                                        'home_odds': match_data.get('home_odds'),
                                        'draw_odds': match_data.get('draw_odds', 3.0),
                                        'away_odds': match_data.get('away_odds'),
                                    }
                                )
                    else:
                        # Create new match only if it's in the future
                        if match_date > timezone.now():
                            match = Match.objects.create(
                                home_team=home_team,
                                away_team=away_team,
                                match_date=match_date,
                                league=match_data.get('league_name', 'Unknown'),
                                match_url=match_data.get('match_url'),
                                home_odds=match_data.get('home_odds'),
                                draw_odds=match_data.get('draw_odds'),
                                away_odds=match_data.get('away_odds'),
                                scraped_at=timezone.now(),
                                status='upcoming'
                            )

                            saved_count += 1

                            # Create odds entry
                            if match_data.get('home_odds') and match_data.get('away_odds'):
                                Odds.objects.create(
                                    match=match,
                                    bookmaker=bookmaker,
                                    home_odds=match_data.get('home_odds'),
                                    draw_odds=match_data.get('draw_odds', 3.0),
                                    away_odds=match_data.get('away_odds')
                                )

            except Exception as e:
                print(f"Error saving match {match_data.get('home_team')} vs {match_data.get('away_team')}: {e}")
                continue

        return saved_count

    except Exception as e:
        print(f"Error in bulk_save_scraped_data: {e}")
        return 0


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
