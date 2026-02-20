import logging
import random
import requests
from typing import Optional
from django.utils import timezone
from ..models import Proxy

logger = logging.getLogger(__name__)


class ProxyService:
    """Service for managing proxies"""

    @staticmethod
    def get_proxy_for_account(account) -> Optional[Proxy]:
        """
        Get proxy for an account, preferring the assigned proxy

        Args:
            account: OneWinAccount instance

        Returns:
            Proxy instance or None
        """
        try:
            # First, try to use the account's assigned proxy
            if account.proxy and account.proxy.can_be_used():
                logger.info(f"Using assigned proxy for account {account.username}: {account.proxy}")
                return account.proxy

            # If no assigned proxy or it can't be used, get any available proxy
            available_proxies = Proxy.objects.filter(is_active=True)

            if not available_proxies:
                logger.warning("No active proxies available")
                return None

            # Filter proxies that can be used
            usable_proxies = [p for p in available_proxies if p.can_be_used()]

            if not usable_proxies:
                logger.warning("All proxies have reached usage limit")
                return None

            # Select proxy based on strategy (random, least used, fastest, etc.)
            selected_proxy = ProxyService._select_proxy_strategy(usable_proxies)

            # Update the account with the selected proxy
            account.proxy = selected_proxy
            account.save(update_fields=['proxy'])

            logger.info(f"Selected proxy {selected_proxy} for account {account.username}")
            return selected_proxy

        except Exception as e:
            logger.error(f"Error getting proxy for account: {str(e)}")
            return None

    @staticmethod
    def _select_proxy_strategy(proxies):
        """Select proxy based on strategy"""
        # Strategy 1: Least used
        proxies = sorted(proxies, key=lambda p: p.current_uses)

        # Strategy 2: With some randomness
        if len(proxies) >= 3:
            # Randomly select from the 3 least used proxies
            selected = random.choice(proxies[:3])
        else:
            selected = proxies[0]

        return selected

    @staticmethod
    def mark_proxy_used(proxy):
        """Mark proxy as used"""
        try:
            proxy.mark_used()
            logger.debug(f"Proxy {proxy.ip}:{proxy.port} marked as used (total: {proxy.current_uses})")
        except Exception as e:
            logger.error(f"Error marking proxy as used: {str(e)}")

    @staticmethod
    def check_proxy_health(proxy):
        """
        Check if proxy is working by making a request to a reliable service.
        Returns tuple (is_working, response_time_ms)
        """
        try:
            # Ensure clean data
            ip = proxy.ip.strip()
            port = proxy.port
            username = proxy.username.strip() if proxy.username else ''
            password = proxy.password.strip() if proxy.password else ''
            
            # Construct auth string
            auth = f"{username}:{password}@" if username and password else ""
            proxy_url = f"{auth}{ip}:{port}"
            
            # Use socks5h for SOCKS5 to ensure remote DNS resolution
            scheme = proxy.type
            if scheme == 'socks5':
                scheme = 'socks5h'
                
            proxies = {
                'http': f"{scheme}://{proxy_url}",
                'https': f"{scheme}://{proxy_url}",
            }
            
            start_time = timezone.now()
            
            # Use http instead of https to reduce SSL complexity for basic health check
            # Use a timeout to prevent hanging
            response = requests.get('http://api.ipify.org?format=json', proxies=proxies, timeout=10)

            end_time = timezone.now()
            
            if response.status_code == 200:
                response_time = (end_time - start_time).total_seconds() * 1000
                return True, response_time
            return False, 0
            
        except Exception as e:
            logger.warning(f"Proxy check failed for {proxy}: {str(e)}")
            return False, 0
