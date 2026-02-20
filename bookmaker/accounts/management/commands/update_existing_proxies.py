from django.core.management.base import BaseCommand
from accounts.models import Proxy
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Update existing proxies to use the new raw_proxy_string format'

    def handle(self, *args, **options):
        self.stdout.write("🔧 Updating existing proxies to new format...")

        updated_count = 0
        skipped_count = 0

        for proxy in Proxy.objects.all():
            # Skip if already has a raw_proxy_string
            if proxy.raw_proxy_string and proxy.raw_proxy_string != "no_proxy":
                self.stdout.write(f"  ⏭️  Proxy {proxy.id} already has raw string, skipping")
                skipped_count += 1
                continue

            # Create raw string from existing fields
            raw_parts = []

            # First line: ID and country
            first_line = f"ID: {proxy.id}"
            if proxy.country:
                first_line += f" | {proxy.country}"
            raw_parts.append(first_line)

            # Proxy details line
            if proxy.ip and proxy.port:
                auth_part = ""
                if proxy.username and proxy.password:
                    auth_part = f"{proxy.username}:{proxy.password}@"

                if proxy.type == 'socks5':
                    raw_parts.append(f"SOCKS5: {auth_part}{proxy.ip}:{proxy.port}")
                else:
                    raw_parts.append(f"HTTP: {auth_part}{proxy.ip}:{proxy.port}")

                proxy.raw_proxy_string = "\n".join(raw_parts)
                self.stdout.write(f"  ✅ Updated proxy {proxy.id}: {proxy.ip}:{proxy.port}")
            else:
                # No IP/port, mark as no_proxy
                proxy.raw_proxy_string = "no_proxy"
                self.stdout.write(f"  ⚠️  Proxy {proxy.id} has no IP/port, marked as 'no_proxy'")

            proxy.save(update_fields=['raw_proxy_string'])
            updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"\n✅ Successfully updated {updated_count} proxies")
        )
        if skipped_count > 0:
            self.stdout.write(f"⏭️  Skipped {skipped_count} proxies (already had raw strings)")