from django.core.management.base import BaseCommand
from accounts.services.proxy_manager import ProxyManager


class Command(BaseCommand):
    help = 'Import proxies from a text file or string'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            help='Path to file containing proxy strings'
        )
        parser.add_argument(
            '--text',
            type=str,
            help='Proxy strings as text'
        )
        parser.add_argument(
            '--type',
            type=str,
            default='socks5',
            help='Preferred proxy type (socks5, http, https)'
        )

    def handle(self, *args, **options):
        proxy_text = ""

        # Get proxy text from file or argument
        if options['file']:
            try:
                with open(options['file'], 'r') as f:
                    proxy_text = f.read()
                self.stdout.write(f"Reading proxies from file: {options['file']}")
            except Exception as e:
                self.stderr.write(f"Error reading file: {str(e)}")
                return
        elif options['text']:
            proxy_text = options['text']
        else:
            self.stderr.write("Please provide either --file or --text argument")
            return

        # Import proxies
        self.stdout.write("Importing proxies...")
        proxies = ProxyManager.import_proxies_from_text(
            proxy_text,
            preferred_type=options['type']
        )

        self.stdout.write(
            self.style.SUCCESS(f"Successfully imported {len(proxies)} proxies")
        )

        # Show imported proxies
        for proxy in proxies:
            self.stdout.write(f"  • {proxy.name}: {proxy.ip}:{proxy.port} ({proxy.type})")