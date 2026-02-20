import logging
import random
from typing import Optional, List
from django.db.models import F
from django.utils import timezone
from accounts.models import Proxy

logger = logging.getLogger(__name__)


class ProxyManager:
    """Service for managing and selecting proxies"""

    @staticmethod
    def get_available_proxy(preferred_type: str = 'socks5') -> Optional[Proxy]:
        """
        Get an available proxy (excluding 'no_proxy' entries)

        Args:
            preferred_type: Preferred proxy type

        Returns:
            Proxy instance or None
        """
        try:
            # Filter out 'no_proxy' entries and get active proxies
            proxies = Proxy.objects.filter(
                is_active=True,
                type=preferred_type,
                current_uses__lt=F('max_uses')
            ).exclude(raw_proxy_string='no_proxy').order_by('current_uses', '?')

            if proxies.exists():
                proxy = proxies.first()
                logger.info(f"Selected {preferred_type} proxy: {proxy.ip}:{proxy.port}")
                return proxy

            # If no preferred type, try any available proxy (excluding no_proxy)
            all_proxies = Proxy.objects.filter(
                is_active=True,
                current_uses__lt=F('max_uses')
            ).exclude(raw_proxy_string='no_proxy').order_by('current_uses', '?')

            if all_proxies.exists():
                proxy = all_proxies.first()
                logger.info(f"Selected {proxy.type} proxy: {proxy.ip}:{proxy.port}")
                return proxy

            logger.warning("No available proxies found (excluding 'no_proxy' entries)")
            return None

        except Exception as e:
            logger.error(f"Error getting proxy: {str(e)}")
            return None

    @staticmethod
    def create_proxy_from_string(raw_string: str, name: str = None) -> Optional[Proxy]:
        """
        Create a new proxy from raw string

        Args:
            raw_string: Raw proxy string
            name: Optional name for the proxy

        Returns:
            Proxy instance or None
        """
        try:
            proxy = Proxy.objects.create(
                raw_proxy_string=raw_string,
                name=name or f"Proxy from import"
            )

            # The save() method will automatically parse the raw string
            proxy.save()

            logger.info(f"Created proxy from string: {proxy.ip}:{proxy.port}")
            return proxy

        except Exception as e:
            logger.error(f"Error creating proxy from string: {str(e)}")
            return None

    @staticmethod
    def get_proxy_stats() -> dict:
        """Get statistics about proxies"""
        try:
            total_proxies = Proxy.objects.count()
            active_proxies = Proxy.objects.filter(is_active=True).count()
            no_proxy_count = Proxy.objects.filter(raw_proxy_string='no_proxy').count()
            with_ip_port = Proxy.objects.exclude(ip=None).exclude(port=None).count()

            # Count by type
            socks5_count = Proxy.objects.filter(type='socks5').count()
            http_count = Proxy.objects.filter(type='http').count()
            https_count = Proxy.objects.filter(type='https').count()

            return {
                'total_proxies': total_proxies,
                'active_proxies': active_proxies,
                'no_proxy_placeholders': no_proxy_count,
                'proxies_with_ip_port': with_ip_port,
                'socks5_proxies': socks5_count,
                'http_proxies': http_count,
                'https_proxies': https_count,
                'available_proxies': active_proxies - no_proxy_count,
            }
        except Exception as e:
            logger.error(f"Error getting proxy stats: {str(e)}")
            return {}