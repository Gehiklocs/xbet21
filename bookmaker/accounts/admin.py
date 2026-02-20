from django.contrib import admin, messages
from django.urls import path
from django.shortcuts import redirect, render, get_object_or_404
from django import forms
from django.utils.html import format_html
from decimal import Decimal
import asyncio

from .models import Wallet, CryptoTransaction, WalletIndex, Proxy, OneWinAccount, OneWinSession
from .services.session_manager import OneWinSessionManager

# Global session manager instance
session_manager = OneWinSessionManager()


class DepositForm(forms.Form):
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('10.00'))
    card_number = forms.CharField(max_length=19)
    expiry_date = forms.CharField(max_length=5, label='Expiry Date (MM/YY)')
    cvc = forms.CharField(max_length=4, label='CVC')
    card_holder = forms.CharField(max_length=100, required=False)


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('currency', 'address', 'label', 'created_at')
    search_fields = ('currency', 'address', 'label')


@admin.register(WalletIndex)
class WalletIndexAdmin(admin.ModelAdmin):
    list_display = ('currency', 'last_index')
    readonly_fields = ('currency', 'last_index')

    def has_add_permission(self, request):
        return False  # Prevent manual creation to avoid messing up the sequence


@admin.register(CryptoTransaction)
class CryptoTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'crypto_type', 'amount', 'deposit_address',
        'status', 'confirmations', 'tx_hash', 'created_at'
    )
    list_filter = ('status', 'crypto_type', 'created_at')
    search_fields = ('user__username', 'deposit_address', 'crypto_type')

    actions = ['approve_transactions', 'reject_transactions']

    def approve_transactions(self, request, queryset):
        pending = queryset.filter(status=CryptoTransaction.STATUS_PENDING)
        approved_count = 0

        for tx in pending:
            tx.status = CryptoTransaction.STATUS_CONFIRMED
            tx.user.profile.balance += tx.amount
            tx.user.profile.save()
            tx.save()
            approved_count += 1

        self.message_user(request, f"Approved {approved_count} deposits.")

    approve_transactions.short_description = "Force Approve (manual credit)"

    def reject_transactions(self, request, queryset):
        updated = queryset.filter(status=CryptoTransaction.STATUS_PENDING).update(
            status=CryptoTransaction.STATUS_UNDERPAID
        )
        self.message_user(request, f"Rejected {updated} transactions.")

    reject_transactions.short_description = "Mark as rejected / underpaid"


@admin.register(Proxy)
class ProxyAdmin(admin.ModelAdmin):
    list_display = ('name', 'ip', 'port', 'type', 'country', 'is_active', 
                   'current_uses_display', 'last_used')
    list_filter = ('type', 'is_active', 'country')
    search_fields = ('name', 'ip', 'country')
    list_editable = ('is_active',)
    readonly_fields = ('created_at', 'updated_at', 'current_uses')
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'ip', 'port', 'type', 'country')
        }),
        ('Authentication', {
            'fields': ('username', 'password'),
            'classes': ('collapse',)
        }),
        ('Usage Limits', {
            'fields': ('is_active', 'max_uses', 'current_uses')
        }),
        ('Additional Information', {
            'fields': ('speed', 'notes', 'last_used'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def current_uses_display(self, obj):
        return f"{obj.current_uses}/{obj.max_uses}"
    current_uses_display.short_description = 'Uses'


@admin.register(OneWinAccount)
class OneWinAccountAdmin(admin.ModelAdmin):
    list_display = ('username', 'balance_display', 'status_display', 
                   'proxy_info', 'login_stats', 'session_status_display', 'session_actions')
    list_filter = ('status', 'created_at', 'proxy')
    search_fields = ('username', 'email', 'phone_number', 'notes')
    readonly_fields = ('created_at', 'updated_at', 'last_login', 
                      'last_activity', 'login_count', 'successful_logins',
                      'failed_logins', 'total_used', 'logs')
    fieldsets = (
        ('Account Credentials', {
            'fields': ('username', 'password', 'email', 'phone_number')
        }),
        ('Balance & Limits', {
            'fields': ('balance', 'daily_limit', 'total_used')
        }),
        ('Proxy & Status', {
            'fields': ('proxy', 'status')
        }),
        ('Login Statistics', {
            'fields': ('login_count', 'successful_logins', 'failed_logins',
                      'last_login', 'last_activity'),
            'classes': ('collapse',)
        }),
        ('Additional Information', {
            'fields': ('account_data', 'notes', 'logs'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Prefetch related session to avoid N+1 queries
        return qs.prefetch_related('browser_session')

    def balance_display(self, obj):
        return f"${obj.balance}"
    balance_display.short_description = 'Balance'
    
    def status_display(self, obj):
        colors = {
            'active': 'green',
            'logged_in': 'blue',
            'banned': 'red',
            'suspended': 'orange',
            'needs_verification': 'yellow',
            'inactive': 'gray',
        }
        color = colors.get(obj.status, 'black')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_display.short_description = 'Status'
    
    def proxy_info(self, obj):
        if obj.proxy:
            return f"{obj.proxy.name} ({obj.proxy.ip}:{obj.proxy.port})"
        return "No proxy"
    proxy_info.short_description = 'Proxy'
    
    def login_stats(self, obj):
        return f"{obj.successful_logins}/{obj.login_count}"
    login_stats.short_description = 'Login Success Rate'

    def session_status_display(self, obj):
        """Display session status from the related OneWinSession."""
        try:
            session = obj.browser_session
            status = session.get_session_status_display()
            color_map = {
                'No Session': 'gray',
                'Active Session': 'green',
                'Needs Login': 'red',
                'Session Expired': 'orange',
                'Closed': 'blue',
            }
            color = color_map.get(status, 'gray')
            return format_html(
                '<span style="color: {}; font-weight: bold;">{}</span>',
                color,
                status
            )
        except OneWinSession.DoesNotExist:
            return format_html('<span style="color: gray;">No Session</span>')
    session_status_display.short_description = 'Browser Session'
    
    def session_actions(self, obj):
        """Render action buttons for managing browser sessions."""
        buttons = []
        
        # Open Browser button
        open_url = f"/admin/accounts/onewinaccount/{obj.id}/open-browser/"
        buttons.append(
            f'<a href="{open_url}" class="button" style="background-color: #4CAF50; color: white; '
            f'padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-right: 5px;">🖥️ Open Browser</a>'
        )
        
        # Check Session button
        check_url = f"/admin/accounts/onewinaccount/{obj.id}/check-session/"
        buttons.append(
            f'<a href="{check_url}" class="button" style="background-color: #2196F3; color: white; '
            f'padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-right: 5px;">🔍 Check Session</a>'
        )
        
        # Automated Deposit button (only show if session exists and is active)
        try:
            session = obj.browser_session
            if session.session_status == 'active':
                deposit_url = f"/admin/accounts/onewinaccount/{obj.id}/automated-deposit/"
                buttons.append(
                    f'<a href="{deposit_url}" class="button" style="background-color: #FF9800; color: white; '
                    f'padding: 5px 10px; text-decoration: none; border-radius: 3px;">💰 Auto Deposit</a>'
                )
        except OneWinSession.DoesNotExist:
            pass
        
        return format_html(' '.join(buttons))
    session_actions.short_description = 'Session Actions'
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:account_id>/open-browser/',
                self.admin_site.admin_view(self.open_browser_view),
                name='onewinaccount-open-browser'
            ),
            path(
                '<int:account_id>/check-session/',
                self.admin_site.admin_view(self.check_session_view),
                name='onewinaccount-check-session'
            ),
            path(
                '<int:account_id>/automated-deposit/',
                self.admin_site.admin_view(self.automated_deposit_view),
                name='onewinaccount-automated-deposit'
            ),
        ]
        return custom_urls + urls
    
    # -----------------------------------------------------------------
    # Custom admin views for session management
    # -----------------------------------------------------------------
    
    def open_browser_view(self, request, account_id):
        """Open persistent browser for the account."""
        account = get_object_or_404(OneWinAccount, id=account_id)
        
        # Get or create the associated OneWinSession
        session, created = OneWinSession.objects.get_or_create(account=account)
        
        # Run async task
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success, message, data = loop.run_until_complete(
                session_manager.open_session_browser(session)
            )
            loop.close()
            
            if success:
                messages.success(request, f"✅ Browser opened for {account.username}")
            else:
                messages.error(request, f"❌ {message}")
        except Exception as e:
            messages.error(request, f"❌ Error: {str(e)}")
        
        return redirect('admin:accounts_onewinaccount_changelist')
    
    def check_session_view(self, request, account_id):
        """Check session status for the account."""
        account = get_object_or_404(OneWinAccount, id=account_id)
        
        try:
            session = account.browser_session
        except OneWinSession.DoesNotExist:
            messages.warning(request, f"No session found for {account.username}")
            return redirect('admin:accounts_onewinaccount_changelist')
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            status_data = loop.run_until_complete(
                session_manager.check_session_status(session)
            )
            loop.close()
            
            if status_data.get('is_logged_in'):
                messages.success(request, f"✅ {account.username} is logged in (session active)")
            else:
                messages.warning(request, f"⚠️ {account.username} needs login")
        except Exception as e:
            messages.error(request, f"❌ Error checking session: {str(e)}")
        
        return redirect('admin:accounts_onewinaccount_changelist')
    
    def automated_deposit_view(self, request, account_id):
        """Form and logic for automated deposit."""
        account = get_object_or_404(OneWinAccount, id=account_id)
        
        try:
            session = account.browser_session
            if session.session_status != 'active':
                messages.error(request, f"Session is not active. Please open browser and log in first.")
                return redirect('admin:accounts_onewinaccount_changelist')
        except OneWinSession.DoesNotExist:
            messages.error(request, f"No browser session found for this account. Please open browser first.")
            return redirect('admin:accounts_onewinaccount_changelist')
        
        if request.method == 'POST':
            form = DepositForm(request.POST)
            if form.is_valid():
                card_details = {
                    'card_number': form.cleaned_data['card_number'],
                    'expiry_date': form.cleaned_data['expiry_date'],
                    'cvc': form.cleaned_data['cvc'],
                    'card_holder': form.cleaned_data.get('card_holder', ''),
                }
                
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    success, message, transaction_data = loop.run_until_complete(
                        session_manager.automated_deposit(
                            session,
                            form.cleaned_data['amount'],
                            card_details
                        )
                    )
                    loop.close()
                    
                    if success:
                        messages.success(request, f"✅ Automated deposit successful! {message}")
                        # Optionally log transaction in CardTransaction model
                    else:
                        messages.error(request, f"❌ Automated deposit failed: {message}")
                        
                except Exception as e:
                    messages.error(request, f"❌ Error during deposit: {str(e)}")
                
                return redirect('admin:accounts_onewinaccount_changelist')
        else:
            form = DepositForm()
        
        context = {
            'title': f'Automated Deposit - {account.username}',
            'form': form,
            'account': account,
            'session': session,
            'opts': self.model._meta,
            'app_label': self.model._meta.app_label,
        }
        
        return render(request, 'admin/accounts/automated_deposit.html', context)
    
    # Add custom actions
    actions = ['mark_as_active', 'reset_usage_counters', 'open_browser_bulk', 'check_session_bulk']
    
    def mark_as_active(self, request, queryset):
        queryset.update(status='active')
        self.message_user(request, f"{queryset.count()} accounts marked as active.")
    mark_as_active.short_description = "Mark selected accounts as active"
    
    def reset_usage_counters(self, request, queryset):
        queryset.update(total_used=0)
        self.message_user(request, f"{queryset.count()} accounts usage counters reset.")
    reset_usage_counters.short_description = "Reset usage counters"

    def open_browser_bulk(self, request, queryset):
        """Bulk action to open browsers for selected accounts."""
        for account in queryset:
            session, _ = OneWinSession.objects.get_or_create(account=account)
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                success, message, _ = loop.run_until_complete(
                    session_manager.open_session_browser(session)
                )
                loop.close()
                if success:
                    self.message_user(request, f"Opened browser for {account.username}", messages.SUCCESS)
                else:
                    self.message_user(request, f"Failed for {account.username}: {message}", messages.ERROR)
            except Exception as e:
                self.message_user(request, f"Error for {account.username}: {str(e)}", messages.ERROR)
    
    open_browser_bulk.short_description = "🖥️ Open browser for selected accounts"
    
    def check_session_bulk(self, request, queryset):
        """Bulk action to check session status for selected accounts."""
        for account in queryset:
            try:
                session = account.browser_session
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                status_data = loop.run_until_complete(
                    session_manager.check_session_status(session)
                )
                loop.close()
                if status_data.get('is_logged_in'):
                    self.message_user(request, f"{account.username}: Logged in", messages.SUCCESS)
                else:
                    self.message_user(request, f"{account.username}: Needs login", messages.WARNING)
            except OneWinSession.DoesNotExist:
                self.message_user(request, f"{account.username}: No session", messages.WARNING)
            except Exception as e:
                self.message_user(request, f"{account.username}: Error - {str(e)}", messages.ERROR)
    
    check_session_bulk.short_description = "🔍 Check session status for selected accounts"


@admin.register(OneWinSession)
class OneWinSessionAdmin(admin.ModelAdmin):
    list_display = ('account', 'session_status', 'profile_path', 'last_used', 'created_at')
    list_filter = ('session_status',)
    search_fields = ('account__username', 'account__email')
    readonly_fields = ('profile_path', 'created_at', 'updated_at', 'last_used')
    
    actions = ['check_session', 'open_browser', 'close_browser']
    
    def check_session(self, request, queryset):
        for session in queryset:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                status_data = loop.run_until_complete(
                    session_manager.check_session_status(session)
                )
                loop.close()
                if status_data.get('is_logged_in'):
                    self.message_user(request, f"{session.account.username}: Logged in", messages.SUCCESS)
                else:
                    self.message_user(request, f"{session.account.username}: Needs login", messages.WARNING)
            except Exception as e:
                self.message_user(request, f"Error: {str(e)}", messages.ERROR)
    check_session.short_description = "Check login status"
    
    def open_browser(self, request, queryset):
        for session in queryset:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                success, msg, _ = loop.run_until_complete(
                    session_manager.open_session_browser(session)
                )
                loop.close()
                if success:
                    self.message_user(request, f"Opened browser for {session.account.username}", messages.SUCCESS)
                else:
                    self.message_user(request, f"Failed: {msg}", messages.ERROR)
            except Exception as e:
                self.message_user(request, f"Error: {str(e)}", messages.ERROR)
    open_browser.short_description = "Open browser (persistent)"
    
    def close_browser(self, request, queryset):
        for session in queryset:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                success = loop.run_until_complete(
                    session_manager.close_session_browser(session)
                )
                loop.close()
                if success:
                    self.message_user(request, f"Closed browser for {session.account.username}", messages.SUCCESS)
                else:
                    self.message_user(request, f"No active browser for {session.account.username}", messages.WARNING)
            except Exception as e:
                self.message_user(request, f"Error: {str(e)}", messages.ERROR)
    close_browser.short_description = "Close browser (if open)"
