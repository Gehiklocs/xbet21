import asyncio
import random
from django.core.management.base import BaseCommand
from django.utils import timezone
from matches.models import Match
from scraper_module.scraper import XStakeScraper
from asgiref.sync import sync_to_async

class Command(BaseCommand):
    help = 'Monitors live matches, visits their detail pages, and prints all available odds concurrently.'

    async def process_match(self, scraper, match, semaphore):
        """
        Process a single match with concurrency control and staggered start.
        """
        async with semaphore:
            # Staggered start: wait 1-2 seconds before starting the actual scrape
            delay = random.uniform(1.0, 2.0)
            await asyncio.sleep(delay)

            self.stdout.write(self.style.HTTP_INFO(f"\n--- Processing: {match.home_team.name} vs {match.away_team.name} ---"))
            
            if not match.match_url:
                self.stdout.write(self.style.WARNING(f"  Skipping {match.home_team.name} vs {match.away_team.name}: No match URL."))
                return

            detailed_odds = await scraper.scrape_match_detail_page(match.match_url)

            if detailed_odds:
                self.stdout.write(self.style.SUCCESS(f"  ✅ Odds found for {match.home_team.name} vs {match.away_team.name}:"))
                for market_name, outcomes in detailed_odds.items():
                    self.stdout.write(self.style.NOTICE(f"    Market: {market_name}"))
                    for outcome in outcomes:
                        self.stdout.write(f"      - {outcome}")
            else:
                self.stdout.write(self.style.WARNING(f"  ⚠️ No detailed odds found for {match.home_team.name} vs {match.away_team.name}."))

    async def handle_async(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("🚀 Starting live match odds monitoring..."))

        # 1. Get matches
        now = timezone.now()
        live_matches_queryset = Match.objects.filter(
            match_date__lte=now,
            status__in=[Match.STATUS_UPCOMING, Match.STATUS_LIVE]
        ).exclude(status=Match.STATUS_FINISHED).select_related('home_team', 'away_team')

        live_matches = [match async for match in live_matches_queryset]

        if not live_matches:
            self.stdout.write(self.style.WARNING("No live or ongoing matches found to monitor."))
            return

        # 2. List all matches first
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n📋 Found {len(live_matches)} matches to monitor:"))
        for i, match in enumerate(live_matches, 1):
            self.stdout.write(f"  {i}. {match.home_team.name} vs {match.away_team.name} ({match.league})")
        self.stdout.write("\nStarting concurrent processing (3 at a time)...\n")

        # 3. Process concurrently
        semaphore = asyncio.Semaphore(3)  # Limit to 3 concurrent tasks
        
        async with XStakeScraper() as scraper:
            if not await scraper.setup_driver():
                self.stdout.write(self.style.ERROR("Failed to set up Playwright driver. Exiting."))
                return

            tasks = [self.process_match(scraper, match, semaphore) for match in live_matches]
            await asyncio.gather(*tasks)

        self.stdout.write(self.style.SUCCESS("\n✅ Live match odds monitoring complete."))

    def handle(self, *args, **options):
        asyncio.run(self.handle_async(*args, **options))
