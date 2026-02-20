import logging
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class ProxyParser:
    """Service for parsing proxy strings in various formats"""

    @staticmethod
    def parse_proxy_string(proxy_string: str, preferred_type: str = 'socks5') -> Dict:
        """
        Parse a proxy string and extract components

        Args:
            proxy_string: Raw proxy string
            preferred_type: Preferred proxy type (socks5, http, https)

        Returns:
            Dictionary with parsed proxy data
        """
        try:
            result = {
                'raw_string': proxy_string,
                'proxy_id': None,
                'country': None,
                'type': preferred_type,
                'ip': None,
                'port': None,
                'username': None,
                'password': None,
                'http_string': None,
                'socks5_string': None,
                'parsed_successfully': False
            }

            lines = proxy_string.strip().split('\n')

            # Parse first line for metadata
            if lines:
                first_line = lines[0].strip()

                # Extract ID
                id_match = re.search(r'ID:\s*(\d+)', first_line)
                if id_match:
                    result['proxy_id'] = id_match.group(1)

                # Extract country flag and name
                if '|' in first_line:
                    country_part = first_line.split('|')[-1].strip()
                    # Remove emoji flags
                    country_part = re.sub(r'[^\w\s]', '', country_part).strip()
                    result['country'] = country_part

            # Parse proxy configuration lines
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue

                # Check for HTTP proxy
                if line.startswith('HTTP:'):
                    proxy_part = line[5:].strip()  # Remove 'HTTP:'
                    parsed = ProxyParser._parse_proxy_part(proxy_part)
                    if parsed:
                        result['http_string'] = ProxyParser._format_proxy_string(parsed, 'http')
                        if preferred_type == 'http':
                            result.update(parsed)
                            result['type'] = 'http'

                # Check for SOCKS5 proxy
                elif line.startswith('SOCKS5:'):
                    proxy_part = line[7:].strip()  # Remove 'SOCKS5:'
                    parsed = ProxyParser._parse_proxy_part(proxy_part)
                    if parsed:
                        result['socks5_string'] = ProxyParser._format_proxy_string(parsed, 'socks5')
                        if preferred_type == 'socks5':
                            result.update(parsed)
                            result['type'] = 'socks5'

                # Check for HTTPS proxy
                elif line.startswith('HTTPS:'):
                    proxy_part = line[6:].strip()  # Remove 'HTTPS:'
                    parsed = ProxyParser._parse_proxy_part(proxy_part)
                    if parsed:
                        result['http_string'] = ProxyParser._format_proxy_string(parsed, 'https')
                        if preferred_type == 'https':
                            result.update(parsed)
                            result['type'] = 'https'

            # If preferred type not found, use first available
            if not result.get('ip') and lines:
                for line in lines[1:]:
                    line = line.strip()
                    if line.startswith('HTTP:'):
                        proxy_part = line[5:].strip()
                        parsed = ProxyParser._parse_proxy_part(proxy_part)
                        if parsed:
                            result.update(parsed)
                            result['type'] = 'http'
                            break
                    elif line.startswith('SOCKS5:'):
                        proxy_part = line[7:].strip()
                        parsed = ProxyParser._parse_proxy_part(proxy_part)
                        if parsed:
                            result.update(parsed)
                            result['type'] = 'socks5'
                            break

            result['parsed_successfully'] = bool(result.get('ip') and result.get('port'))

            return result

        except Exception as e:
            logger.error(f"Error parsing proxy string: {str(e)}")
            return {'raw_string': proxy_string, 'parsed_successfully': False, 'error': str(e)}

    @staticmethod
    def _parse_proxy_part(proxy_part: str) -> Optional[Dict]:
        """Parse the proxy part (username:password@ip:port)"""
        try:
            result = {
                'ip': None,
                'port': None,
                'username': None,
                'password': None
            }

            # Check if there's authentication
            if '@' in proxy_part:
                auth_part, server_part = proxy_part.split('@', 1)
                if ':' in auth_part:
                    result['username'], result['password'] = auth_part.split(':', 1)
            else:
                server_part = proxy_part

            # Parse server part (ip:port)
            if ':' in server_part:
                result['ip'], port_str = server_part.split(':', 1)
                try:
                    result['port'] = int(port_str)
                except ValueError:
                    result['port'] = None

            return result if result['ip'] and result['port'] else None

        except Exception as e:
            logger.error(f"Error parsing proxy part: {str(e)}")
            return None

    @staticmethod
    def _format_proxy_string(proxy_data: Dict, proxy_type: str) -> str:
        """Format proxy data into a string"""
        if proxy_data.get('username') and proxy_data.get('password'):
            auth = f"{proxy_data['username']}:{proxy_data['password']}@"
        else:
            auth = ""

        return f"{proxy_type.upper()}: {auth}{proxy_data['ip']}:{proxy_data['port']}"

    @staticmethod
    def create_proxy_from_string(proxy_string: str, preferred_type: str = 'socks5') -> Optional['Proxy']:
        """
        Create a Proxy instance from a proxy string

        Args:
            proxy_string: Raw proxy string
            preferred_type: Preferred proxy type

        Returns:
            Proxy instance or None
        """
        try:
            from accounts.models import Proxy

            # Parse the proxy string
            parsed = ProxyParser.parse_proxy_string(proxy_string, preferred_type)

            if not parsed['parsed_successfully']:
                logger.error(f"Failed to parse proxy string: {proxy_string[:100]}...")
                return None

            # Create Proxy instance
            proxy = Proxy(
                raw_proxy_string=proxy_string,
                proxy_id=parsed.get('proxy_id'),
                country=parsed.get('country'),
                type=parsed.get('type', preferred_type),
                ip=parsed.get('ip'),
                port=parsed.get('port'),
                username=parsed.get('username'),
                password=parsed.get('password'),
                name=f"{parsed.get('country', 'Unknown')} - {parsed.get('proxy_id', 'Proxy')}",
                is_active=True
            )

            return proxy

        except Exception as e:
            logger.error(f"Error creating proxy from string: {str(e)}")
            return None