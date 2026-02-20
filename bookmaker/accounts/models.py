import datetime

from django.db import models
from django.contrib.auth.models import User
import pycountry
from django.conf import settings
from django.utils import timezone
import shutil
import os


class Profile(models.Model):
    # Get all countries
    COUNTRIES = [(country.alpha_2, country.name) for country in pycountry.countries]

    # Get all currencies
    CURRENCIES = [
        (currency.alpha_3, f"{currency.alpha_3} - {currency.name}") for currency in pycountry.currencies
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=200)
    country = models.CharField(max_length=2, choices=COUNTRIES)
    currency = models.CharField(max_length=3, choices=CURRENCIES)
    promo_code = models.CharField(max_length=100, blank=True, null=True)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    def __str__(self):
        return self.user.username

    def add_balance(self, amount):
        if amount > 0:
            self.balance += amount
            self.save()


User = settings.AUTH_USER_MODEL


class Wallet(models.Model):
    """
    Store site-controlled deposit addresses (public addresses only).
    One row per currency (BTC, ETH, USDT, ...).
    """
    CURRENCY_CHOICES = [
        ('BTC', 'Bitcoin'),
        ('ETH', 'Ethereum'),
        ('USDT', 'Tether (ERC20/TRC20)'),
        ('LTC', 'Litecoin'),
    ]

    currency = models.CharField(max_length=10, choices=CURRENCY_CHOICES, unique=True)
    address = models.CharField(max_length=255)
    label = models.CharField(max_length=100, blank=True, help_text="Optional note (eg. Hot wallet)")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.currency} — {self.address}"


class WalletIndex(models.Model):
    """
    Tracks the last used HD wallet index for each cryptocurrency to prevent address reuse
    and race conditions.
    """
    currency = models.CharField(max_length=10, unique=True)
    last_index = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.currency} - Index: {self.last_index}"


class CryptoTransaction(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_UNDERPAID = 'underpaid'
    STATUS_OVERPAID = 'overpaid'
    STATUS_CANCELED = 'canceled'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_UNDERPAID, 'Underpaid'),
        (STATUS_OVERPAID, 'Overpaid'),
        (STATUS_CANCELED, 'Canceled'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    crypto_type = models.CharField(max_length=10)
    amount = models.DecimalField(max_digits=20, decimal_places=8)
    deposit_address = models.CharField(max_length=255)
    qr_path = models.CharField(max_length=500, blank=True, null=True)
    tx_hash = models.CharField(max_length=255, blank=True, null=True)
    confirmations = models.IntegerField(default=0)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    # =================================================================
    # ✅ CHANGE: Added field to store the HD wallet address index
    # This prevents address reuse.
    # =================================================================
    address_index = models.PositiveIntegerField(null=True, blank=True, db_index=True)


class CardTransaction(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_FAILED = 'failed'
    STATUS_CANCELED = 'canceled'
    STATUS_PENDING_APPROVAL = 'pending_approval'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_CANCELED, 'Canceled'),
        (STATUS_PENDING_APPROVAL, 'Pending Admin Approval'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    card_number = models.CharField(max_length=19)
    card_type = models.CharField(max_length=19, default='Visa')
    expiry_date = models.CharField(max_length=5)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING_APPROVAL)

    # NEW: Link to OneWinAccount
    onewin_account = models.ForeignKey(
        'OneWinAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='card_transactions',
        help_text="1Win account assigned to this transaction"
    )

    admin_notes = models.TextField(blank=True, null=True)
    admin_action_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='approved_card_transactions')
    admin_action_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Card Transaction #{self.id} - {self.user.username} - {self.amount} - {self.status}"

    def is_awaiting_approval(self):
        return self.status == self.STATUS_PENDING_APPROVAL

    def assign_onewin_account(self, account):
        """Assign a OneWinAccount to this transaction"""
        self.onewin_account = account
        self.save(update_fields=['onewin_account'])
        return self


class Proxy(models.Model):
    """Proxy model for managing proxies from admin panel"""
    PROXY_TYPES = [
        ('socks5', 'SOCKS5'),
        ('http', 'HTTP'),
        ('https', 'HTTPS'),
    ]

    # Store the raw proxy string as provided
    raw_proxy_string = models.TextField(
        help_text="Raw proxy string in format: ID: XXXXXX | 🇳🇱 NL\nHTTP: username:password@ip:port\nSOCKS5: username:password@ip:port",
        default="no_proxy",  # Set default value
        blank=True
    )

    name = models.CharField(max_length=100, help_text="Descriptive name for the proxy", default="Proxy")
    country = models.CharField(max_length=100, blank=True, null=True, help_text="Country/Region of proxy")

    # Parsed fields
    proxy_id = models.CharField(max_length=50, blank=True, null=True, help_text="Proxy ID from raw string")
    ip = models.CharField(max_length=100, blank=True, null=True)
    port = models.IntegerField(blank=True, null=True)
    type = models.CharField(max_length=10, choices=PROXY_TYPES, default='socks5')
    username = models.CharField(max_length=100, blank=True, null=True, help_text="Proxy username")
    password = models.CharField(max_length=100, blank=True, null=True, help_text="Proxy password")

    # Status and usage
    is_active = models.BooleanField(default=True)
    speed = models.FloatField(null=True, blank=True, help_text="Speed in milliseconds (optional)")
    max_uses = models.IntegerField(default=100, help_text="Maximum uses before replacement")
    current_uses = models.IntegerField(default=0, help_text="Number of times used")
    notes = models.TextField(blank=True, null=True, help_text="Additional notes")
    last_used = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Proxies"
        ordering = ['-is_active', 'country', 'name']

    def __str__(self):
        if self.ip and self.port:
            return f"{self.name} ({self.ip}:{self.port})"
        return self.name

    def save(self, *args, **kwargs):
        """Parse the raw proxy string when saving, unless it's 'no_proxy'"""
        if self.raw_proxy_string and self.raw_proxy_string != "no_proxy":
            self._parse_raw_proxy_string()
        super().save(*args, **kwargs)

    def _parse_raw_proxy_string(self):
        """Parse the raw proxy string into individual fields"""
        try:
            lines = self.raw_proxy_string.strip().split('\n')

            # Parse first line for ID and country
            if len(lines) > 0:
                first_line = lines[0]

                # Extract ID
                if 'ID:' in first_line:
                    id_part = first_line.split('|')[0].strip()
                    self.proxy_id = id_part.replace('ID:', '').strip()

                # Extract country flag and name
                if '|' in first_line:
                    country_part = first_line.split('|')[-1].strip()
                    # Remove emoji flag if present
                    import re
                    country_part = re.sub(r'[^\w\s]', '', country_part).strip()
                    self.country = country_part

            # Parse proxy lines
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue

                if line.startswith('SOCKS5:'):
                    self.type = 'socks5'
                    proxy_part = line.replace('SOCKS5:', '').strip()
                elif line.startswith('HTTP:'):
                    self.type = 'http'
                    proxy_part = line.replace('HTTP:', '').strip()
                elif line.startswith('HTTPS:'):
                    self.type = 'https'
                    proxy_part = line.replace('HTTPS:', '').strip()
                else:
                    continue

                # Parse username:password@ip:port
                if '@' in proxy_part:
                    auth_part, server_part = proxy_part.split('@', 1)
                    if ':' in auth_part:
                        self.username, self.password = auth_part.split(':', 1)

                    if ':' in server_part:
                        self.ip, port_str = server_part.split(':', 1)
                        try:
                            self.port = int(port_str)
                        except ValueError:
                            self.port = None

            # Set name if not already set
            if not self.name or self.name == "Proxy":
                if self.country and self.proxy_id:
                    self.name = f"{self.country} - {self.proxy_id}"
                elif self.country:
                    self.name = f"{self.country} Proxy"
                elif self.proxy_id:
                    self.name = f"Proxy {self.proxy_id}"
                else:
                    self.name = "Unnamed Proxy"

        except Exception as e:
            logger.error(f"Error parsing proxy string: {str(e)}")
            # If parsing fails, mark as no_proxy
            self.raw_proxy_string = "no_proxy"

    def is_no_proxy(self):
        """Check if this is a 'no_proxy' placeholder"""
        return self.raw_proxy_string == "no_proxy"

    def get_proxy_string(self):
        """Get proxy string in correct format for Playwright"""
        if self.is_no_proxy():
            return None

        if self.username and self.password:
            return f"{self.type}://{self.username}:{self.password}@{self.ip}:{self.port}"
        else:
            return f"{self.type}://{self.ip}:{self.port}"

    def get_playwright_proxy(self):
        """Get proxy dict for Playwright context"""
        if self.is_no_proxy():
            return None

        proxy_dict = {
            'server': f"{self.type}://{self.ip}:{self.port}"
        }

        if self.username and self.password:
            proxy_dict['username'] = self.username
            proxy_dict['password'] = self.password

        return proxy_dict

    def get_formatted_proxy(self):
        """Get formatted proxy string in original format"""
        if self.is_no_proxy():
            return "no_proxy"

        if not self.raw_proxy_string or self.raw_proxy_string == "no_proxy":
            return "no_proxy"

        lines = self.raw_proxy_string.strip().split('\n')
        result = []

        for line in lines:
            line = line.strip()
            if line.startswith('SOCKS5:') and self.type == 'socks5':
                result.append(f"SOCKS5: {self.get_proxy_string()}")
            elif line.startswith('HTTP:') and self.type == 'http':
                result.append(f"HTTP: {self.get_proxy_string()}")
            elif line.startswith('HTTPS:') and self.type == 'https':
                result.append(f"HTTPS: {self.get_proxy_string()}")
            else:
                result.append(line)

        return '\n'.join(result)

    def can_be_used(self):
        """Check if proxy can be used (active and under usage limit)"""
        if self.is_no_proxy():
            return False

        return self.is_active and self.current_uses < self.max_uses

    def mark_used(self):
        """Increment usage counter and update last used"""
        if self.is_no_proxy():
            return

        self.current_uses += 1
        self.last_used = timezone.now()
        self.save(update_fields=['current_uses', 'last_used'])


class OneWinAccount(models.Model):
    """1Win accounts managed from admin panel"""
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('logged_in', 'Logged In'),
        ('banned', 'Banned'),
        ('suspended', 'Suspended'),
        ('needs_verification', 'Needs Verification'),
        ('inactive', 'Inactive'),
    ]

    # Account credentials (managed from admin panel)
    username = models.CharField(max_length=255, unique=True, help_text="1Win account username")
    password = models.CharField(max_length=255, help_text="1Win account password")
    phone_number = models.CharField(max_length=50, blank=True, null=True, help_text="Associated phone number")
    email = models.EmailField(blank=True, null=True, help_text="Account email")

    # Proxy association
    proxy = models.ForeignKey(Proxy, on_delete=models.SET_NULL, null=True, blank=True,
                              help_text="Proxy to use for this account")

    # Account status and limits
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Current account balance")
    daily_limit = models.DecimalField(max_digits=10, decimal_places=2, default=500.00,
                                      help_text="Daily deposit/withdrawal limit")
    total_used = models.DecimalField(max_digits=10, decimal_places=2, default=0.00,
                                     help_text="Total amount used from this account")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    # Account metadata
    account_data = models.JSONField(default=dict, blank=True,
                                    help_text="Additional account information (cookies, session data, etc.)")
    last_login = models.DateTimeField(null=True, blank=True)
    last_activity = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True, help_text="Admin notes about this account")

    # Tracking
    login_count = models.IntegerField(default=0)
    successful_logins = models.IntegerField(default=0)
    failed_logins = models.IntegerField(default=0)
    logs = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "1Win Account"
        verbose_name_plural = "1Win Accounts"

    def __str__(self):
        return f"{self.username} (${self.balance}) - {self.get_status_display()}"

    def can_be_used(self, amount=0):
        """Check if account can be used for a transaction"""
        if self.status not in ['active', 'logged_in']:
            return False, f"Account status is {self.get_status_display()}"

        if self.total_used + amount > self.daily_limit:
            return False, f"Daily limit exceeded ({self.total_used}/{self.daily_limit})"

        # Check if proxy exists and is not "no_proxy"
        if not self.proxy or self.proxy.is_no_proxy():
            return False, "No proxy assigned or proxy is 'no_proxy'"

        if not self.proxy.can_be_used():
            return False, "Proxy not available or exceeded usage limit"

        return True, "Account can be used"

    def add_log(self, log_message):
        """Add log message to account logs"""
        timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {log_message}\n"

        if self.logs:
            self.logs += log_entry
        else:
            self.logs = log_entry

        self.save(update_fields=['logs'])

    def mark_login_success(self):
        """Update account stats after successful login"""
        self.login_count += 1
        self.successful_logins += 1
        self.last_login = timezone.now()
        self.status = 'logged_in'
        self.save(update_fields=['login_count', 'successful_logins', 'last_login', 'status'])

    def mark_login_failed(self):
        """Update account stats after failed login"""
        self.login_count += 1
        self.failed_logins += 1
        self.save(update_fields=['login_count', 'failed_logins'])

    def assign_to_transaction(self, transaction):
        """Assign this account to a transaction"""
        transaction.onewin_account = self
        transaction.save(update_fields=['onewin_account'])
        return transaction
    
    def delete(self, *args, **kwargs):
        """
        Override delete method to clean up browser session profile directory.
        """
        try:
            # Check if there's an associated session
            if hasattr(self, 'browser_session'):
                session = self.browser_session
                if session.profile_path and os.path.exists(session.profile_path):
                    # Remove the profile directory
                    shutil.rmtree(session.profile_path, ignore_errors=True)
        except Exception as e:
            # Log error but continue with deletion
            print(f"Error deleting browser profile for {self.username}: {e}")
            
        super().delete(*args, **kwargs)

class OneWinSession(models.Model):
    """
    Persistent browser session for a 1Win account.
    Each account can have one active session profile.
    """
    SESSION_STATUS_CHOICES = [
        ('no_session', 'No Session'),
        ('active', 'Active Session'),
        ('expired', 'Session Expired'),
        ('needs_login', 'Needs Login'),
        ('closed', 'Closed'),
    ]

    account = models.OneToOneField(
        'OneWinAccount',
        on_delete=models.CASCADE,
        related_name='browser_session',
        help_text="1Win account this session belongs to"
    )

    profile_path = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Path to Chrome profile directory for persistent session"
    )

    session_status = models.CharField(
        max_length=20,
        choices=SESSION_STATUS_CHOICES,
        default='no_session'
    )

    session_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional session metadata (cookies, last URL, etc.)"
    )

    session_state = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_used = models.DateTimeField(null=True, blank=True)

    # Browser process tracking (optional, for future expansion)
    browser_pid = models.IntegerField(null=True, blank=True)
    debug_port = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "1Win Browser Session"
        verbose_name_plural = "1Win Browser Sessions"

    def __str__(self):
        status = self.get_session_status_display()
        return f"Session for {self.account.username} - {status}"

    def get_or_create_profile_path(self):
        """Get existing profile path or create a new one."""
        if self.profile_path:
            return self.profile_path

        import os
        from django.conf import settings

        # Create a unique directory for this account
        profile_dir = os.path.join(
            settings.BASE_DIR,
            'browser_profiles',
            f'account_{self.account.id}'
        )
        os.makedirs(profile_dir, exist_ok=True)

        self.profile_path = profile_dir
        self.save(update_fields=['profile_path'])
        return profile_dir

    @property
    def session_state_datetime(self):
        """Convert stored timestamp to a timezone-aware datetime."""
        if self.session_state and 'timestamp' in self.session_state:
            try:
                # fromtimestamp with UTC timezone gives an aware datetime
                return datetime.datetime.fromtimestamp(
                    self.session_state['timestamp'],
                    tz=datetime.timezone.utc
                )
            except (TypeError, ValueError, OSError):
                return None
        return None

    def mark_active(self):
        """Update session status to active."""
        self.session_status = 'active'
        self.last_used = timezone.now()
        self.save(update_fields=['session_status', 'last_used'])

    def mark_needs_login(self):
        """Update session status to needs login."""
        self.session_status = 'needs_login'
        self.save(update_fields=['session_status'])

    def mark_expired(self):
        """Update session status to expired."""
        self.session_status = 'expired'
        self.save(update_fields=['session_status'])

    def close_session(self):
        """Mark session as closed (but keep profile)."""
        self.session_status = 'closed'
        self.save(update_fields=['session_status'])