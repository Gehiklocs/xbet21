# accounts/services/webDriver/fingerprint_manager.py

import random
import json
from typing import Dict, List


class FingerprintManager:
    """Manages browser fingerprints to avoid detection"""

    # Common screen resolutions
    SCREEN_RESOLUTIONS = [
        (1366, 768), (1440, 900), (1536, 864),
        (1600, 900), (1680, 1050), (1920, 1080),
        (1280, 720), (1280, 800), (1280, 1024)
    ]

    # Common user agents
    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ]

    # WebGL vendors
    WEBGL_VENDORS = [
        'Intel Inc.', 'NVIDIA Corporation', 'AMD', 'Google Inc.', 'Apple Inc.'
    ]

    # WebGL renderers
    WEBGL_RENDERERS = [
        'Intel Iris OpenGL Engine', 'Intel HD Graphics 620', 'NVIDIA GeForce GTX 1060',
        'AMD Radeon Pro 555X', 'Apple M1', 'SwiftShader'
    ]

    @classmethod
    def get_random_fingerprint(cls) -> Dict:
        """Generate a random browser fingerprint"""
        width, height = random.choice(cls.SCREEN_RESOLUTIONS)

        # Color depth (almost always 24)
        color_depth = random.choice([24, 30, 48]) if random.random() > 0.9 else 24

        # Pixel ratio (mostly 1, sometimes 2 for high DPI)
        pixel_ratio = 2 if random.random() > 0.8 else 1

        # Timezone based on common locations
        timezones = [
            'America/New_York', 'America/Chicago', 'America/Denver',
            'America/Los_Angeles', 'Europe/London', 'Europe/Paris',
            'Asia/Tokyo', 'Australia/Sydney', 'America/Toronto'
        ]

        # Languages based on timezone
        language_map = {
            'America/New_York': ['en-US', 'en'],
            'America/Chicago': ['en-US', 'en'],
            'Europe/London': ['en-GB', 'en'],
            'Europe/Paris': ['fr-FR', 'fr', 'en'],
            'Asia/Tokyo': ['ja-JP', 'ja'],
            'Australia/Sydney': ['en-AU', 'en']
        }

        timezone = random.choice(timezones)
        languages = language_map.get(timezone, ['en-US', 'en'])

        return {
            'viewport': {'width': width, 'height': height},
            'screen': {'width': width, 'height': height, 'colorDepth': color_depth},
            'pixel_ratio': pixel_ratio,
            'user_agent': random.choice(cls.USER_AGENTS),
            'timezone': timezone,
            'languages': languages,
            'webgl_vendor': random.choice(cls.WEBGL_VENDORS),
            'webgl_renderer': random.choice(cls.WEBGL_RENDERERS),
            'hardware_concurrency': random.choice([4, 6, 8, 12, 16]),
            'device_memory': random.choice([4, 8, 16, 32]),
            'platform': random.choice(['Win32', 'MacIntel', 'Linux x86_64'])
        }