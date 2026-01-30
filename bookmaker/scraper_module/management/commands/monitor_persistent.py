import asyncio
import random
from datetime import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from matches.models import Match
from scraper_module.scraper import XStakeScraper
from asgiref.sync import sync_to_async

class Command(BaseCommand):
    help = 'Opens a match page and keeps it open, continuously scraping updates with real-time logging.'

    def log(self, message, style=None):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"[{timestamp}] {message}"
        if style:
            self.stdout.write(style(formatted_msg))
        else:
            self.stdout.write(formatted_msg)

    async def monitor_match_continuously(self, scraper, match):
        self.log(f"🟢 Opening persistent connection for: {match.home_team.name} vs {match.away_team.name}", self.style.SUCCESS)
        
        try:
            # Open the page
            page = await scraper.browser.new_page()
            self.log(f"   🌐 Navigating to {match.match_url}...")
            
            await page.goto(match.match_url, wait_until="domcontentloaded", timeout=60000)
            self.log(f"   ✅ Page loaded. Starting continuous monitoring loop...", self.style.SUCCESS)

            last_data = {'scores': '', 'odds': {}}
            heartbeat_counter = 0

            while True:
                # 1. Extract Data from the open page
                current_data = await page.evaluate("""() => {
                    const scores = document.querySelector('.score') ? document.querySelector('.score').innerText : '';
                    const odds = {};
                    
                    document.querySelectorAll('.stake-button').forEach(btn => {
                        const title = btn.querySelector('.stake__title');
                        const val = btn.querySelector('.formated-odd');
                        if (title && val) {
                            odds[title.innerText.trim()] = val.innerText.trim();
                        }
                    });
                    
                    return { scores, odds };
                }""")

                # 2. Check for Score Changes
                if current_data['scores'] != last_data['scores']:
                    self.log(f"   ⚽ SCORE UPDATE: {last_data['scores']} -> {current_data['scores']}", self.style.SUCCESS)

                # 3. Check for Odds Changes
                changes_found = False
                for market, odd_val in current_data['odds'].items():
                    old_val = last_data['odds'].get(market)
                    if old_val != odd_val:
                        arrow = "⬆️" if old_val and float(odd_val) > float(old_val) else "⬇️"
                        self.log(f"   ⚡ ODDS CHANGE: {market} | {old_val or 'New'} -> {odd_val} {arrow}", self.style.WARNING)
                        changes_found = True
                
                if changes_found:
                    self.log("   💾 Saving updates to database...")
                    # await update_match_in_db(match, current_data)

                last_data = current_data
                
                # 4. Heartbeat (every 10 seconds)
                heartbeat_counter += 1
                if heartbeat_counter >= 10:
                    self.log(f"   💓 Still monitoring... (Current Score: {current_data['scores']})")
                    heartbeat_counter = 0

                # 5. Wait before next check
                await asyncio.sleep(1)

        except Exception as e:
            self.log(f"   ❌ Error monitoring {match.home_team.name}: {e}", self.style.ERROR)
            await page.close()

    async def handle_async(self, *args, **options):
        now = timezone.now()
        match = await Match.objects.filter(
            match_date__lte=now,
            status__in=[Match.STATUS_UPCOMING, Match.STATUS_LIVE]
        ).exclude(status=Match.STATUS_FINISHED).select_related('home_team', 'away_team').afirst()

        if not match:
            self.log("⚠️ No live matches found to monitor.", self.style.WARNING)
            return

        async with XStakeScraper() as scraper:
            if not await scraper.setup_driver():
                return

            await self.monitor_match_continuously(scraper, match)

    def handle(self, *args, **options):
        asyncio.run(self.handle_async(*args, **options))
