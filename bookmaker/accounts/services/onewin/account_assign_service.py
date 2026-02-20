import logging
import random
from typing import Optional, Tuple, List
from django.db.models import F
from django.utils import timezone
from accounts.models import OneWinAccount

logger = logging.getLogger(__name__)


class AccountAssignmentService:
    """Service for automatically assigning 1Win accounts to card transactions"""

    @staticmethod
    def assign_account_to_transaction(amount: float) -> Tuple[bool, Optional[OneWinAccount], str]:
        """
        Find and assign the best 1Win account for a transaction

        Args:
            amount: Transaction amount in dollars

        Returns:
            Tuple of (success, account, message)
        """
        try:
            print(f"🔍 Looking for 1Win account for ${amount}...")

            # Get all available accounts
            available_accounts = AccountAssignmentService._get_available_accounts(float(amount))

            if not available_accounts:
                error_msg = "No available 1Win accounts found"
                print(f"❌ {error_msg}")
                return False, None, error_msg

            print(f"✅ Found {len(available_accounts)} available accounts")

            # Select the best account
            selected_account = AccountAssignmentService._select_best_account(available_accounts, float(amount))

            if not selected_account:
                error_msg = "Could not select suitable account"
                print(f"❌ {error_msg}")
                return False, None, error_msg

            print(f"✅ Selected account: {selected_account.username}")
            print(f"   Balance: ${selected_account.balance}")
            print(f"   Daily used: ${selected_account.total_used}/{selected_account.daily_limit}")

            if selected_account.proxy:
                print(f"   Proxy: {selected_account.proxy.ip}:{selected_account.proxy.port}")
            else:
                print(f"   ⚠️ No proxy assigned")

            return True, selected_account, f"Assigned account: {selected_account.username}"

        except Exception as e:
            error_msg = f"Error assigning account: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return False, None, error_msg

    @staticmethod
    def _get_available_accounts(amount: float) -> List[OneWinAccount]:
        """
        Get all 1Win accounts that can be used for a transaction

        Args:
            amount: Transaction amount

        Returns:
            List of available OneWinAccount instances
        """
        try:
            # Get active accounts with sufficient balance
            base_query = OneWinAccount.objects.filter(
                status__in=['active', 'logged_in'],
                balance__gte=amount
            ).select_related('proxy')

            # Filter by daily limit and proxy availability
            available_accounts = []

            for account in base_query:
                try:
                    # Check daily limit
                    if account.total_used + amount > account.daily_limit:
                        continue

                    # Check proxy if account has one
                    if account.proxy:
                        if not account.proxy.is_active:
                            continue
                        if account.proxy.current_uses >= account.proxy.max_uses:
                            continue

                    available_accounts.append(account)

                except Exception as e:
                    logger.error(f"Error checking account {account.username}: {str(e)}")
                    continue

            return available_accounts

        except Exception as e:
            logger.error(f"Error getting available accounts: {str(e)}")
            return []

    @staticmethod
    def _select_best_account(accounts: List[OneWinAccount], amount: float) -> Optional[OneWinAccount]:
        """
        Select the best account from available options

        Args:
            accounts: List of available accounts
            amount: Transaction amount

        Returns:
            Best OneWinAccount instance
        """
        if not accounts:
            return None

        # Sort by multiple criteria (best first)
        sorted_accounts = sorted(accounts, key=lambda x: (
            -float(x.balance),  # Highest balance first
            x.total_used,  # Least used today
            x.failed_logins,  # Fewest failed logins
            -x.successful_logins,  # Most successful logins
        ))

        return sorted_accounts[0]

    @staticmethod
    def mark_account_used(account: OneWinAccount, amount: float) -> bool:
        """
        Mark account as used for a transaction

        Args:
            account: OneWinAccount instance
            amount: Amount used

        Returns:
            Success status
        """
        try:
            # Update account usage
            account.total_used = F('total_used') + amount
            account.last_activity = timezone.now()

            # Check if daily limit reached
            if account.total_used >= account.daily_limit:
                account.status = 'inactive'
                print(f"⚠️ Account {account.username} reached daily limit")

            account.save(update_fields=['total_used', 'last_activity', 'status'])

            # Update proxy usage if exists
            if account.proxy:
                account.proxy.current_uses = F('current_uses') + 1
                account.proxy.last_used = timezone.now()
                account.proxy.save(update_fields=['current_uses', 'last_used'])

            print(f"📊 Account {account.username} usage updated")
            return True

        except Exception as e:
            logger.error(f"Error marking account used: {str(e)}")
            return False