from datetime import timedelta
from decimal import Decimal
import asyncio

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Sum, Q, F
from django.core.paginator import Paginator
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_POST
import logging
import requests

from matches.models import Match, Bet, ExpressBet, Team, Bookmaker
from accounts.models import Profile, CryptoTransaction, Wallet, CardTransaction, Proxy, OneWinAccount, OneWinSession
from accounts.services.proxy_service import ProxyService
from accounts.services.session_manager import OneWinSessionManager
from scraper_module.models import ScraperStatus
from django_q.models import Schedule
from django_q.tasks import async_task
from .forms import UserEditForm, WalletForm, MatchEditForm, TeamForm, BookmakerForm, ScheduleForm, GroupForm, ProxyForm, OneWinAccountForm

logger = logging.getLogger(__name__)

User = get_user_model()
session_manager = OneWinSessionManager()

def is_admin(user):
    return user.is_authenticated and user.is_superuser

@user_passes_test(is_admin, login_url='/accounts/login/')
def dashboard_home(request):
    total_users = User.objects.count()
    total_matches = Match.objects.count()
    live_matches = Match.objects.filter(status=Match.STATUS_LIVE).count()
    total_bets = Bet.objects.count()
    total_wagered = Bet.objects.aggregate(Sum('amount'))['amount__sum'] or 0
    total_user_balance = Profile.objects.aggregate(Sum('balance'))['balance__sum'] or 0

    recent_bets = Bet.objects.select_related('user', 'match').order_by('-created_at')[:10]
    
    # Get pending card transactions
    pending_transactions = CardTransaction.objects.filter(
        status=CardTransaction.STATUS_PENDING_APPROVAL
    ).select_related('user').order_by('-created_at')
    
    pending_count = pending_transactions.count()
    
    # Get recent processed transactions
    recent_transactions = CardTransaction.objects.exclude(
        status=CardTransaction.STATUS_PENDING_APPROVAL
    ).select_related('user', 'admin_action_by').order_by('-updated_at')[:10]

    context = {
        'total_users': total_users,
        'total_matches': total_matches,
        'live_matches': live_matches,
        'total_bets': total_bets,
        'total_wagered': total_wagered,
        'total_user_balance': total_user_balance,
        'recent_bets': recent_bets,
        'pending_transactions': pending_transactions,
        'pending_count': pending_count,
        'recent_transactions': recent_transactions,
        'page': 'home'
    }
    return render(request, 'admin_dashboard/home.html', context)

@user_passes_test(is_admin)
def matches_list(request):
    query = request.GET.get('q')
    status_filter = request.GET.get('status')
    
    matches_list = Match.objects.all().order_by('-match_date')
    
    if query:
        matches_list = matches_list.filter(
            Q(home_team__name__icontains=query) | 
            Q(away_team__name__icontains=query) |
            Q(league__icontains=query)
        )
    
    if status_filter:
        matches_list = matches_list.filter(status=status_filter)
    
    paginator = Paginator(matches_list, 20)
    page = request.GET.get('page')
    matches = paginator.get_page(page)
    return render(request, 'admin_dashboard/matches.html', {
        'matches': matches, 
        'page': 'matches', 
        'query': query,
        'status_filter': status_filter
    })

@user_passes_test(is_admin)
def match_edit(request, match_id=None):
    if match_id:
        match = get_object_or_404(Match, id=match_id)
        title = "Edit Match"
    else:
        match = None
        title = "Add Match"

    if request.method == 'POST':
        form = MatchEditForm(request.POST, instance=match)
        if form.is_valid():
            form.save()
            messages.success(request, "Match saved successfully.")
            return redirect('admin_matches')
    else:
        form = MatchEditForm(instance=match)

    return render(request, 'admin_dashboard/match_edit.html', {'form': form, 'title': title, 'page': 'matches'})

@user_passes_test(is_admin)
def match_delete(request, match_id):
    match = get_object_or_404(Match, id=match_id)
    if request.method == 'POST':
        match.delete()
        messages.success(request, "Match deleted successfully.")
        return redirect('admin_matches')
    return render(request, 'admin_dashboard/confirm_delete.html', {'object': match, 'page': 'matches'})

@user_passes_test(is_admin)
def bets_list(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        selected_bets = request.POST.getlist('selected_bets')
        
        if selected_bets:
            bets = Bet.objects.filter(id__in=selected_bets)
            count = bets.count()
            
            if action == 'mark_won':
                for bet in bets:
                    bet.status = 'won'
                    bet.save()
                    # Credit user balance
                    profile = bet.user.profile
                    profile.balance += bet.potential_payout
                    profile.save()
                messages.success(request, f"{count} bets marked as WON.")
                
            elif action == 'mark_lost':
                bets.update(status='lost')
                messages.success(request, f"{count} bets marked as LOST.")
                
            elif action == 'mark_pending':
                bets.update(status='pending')
                messages.success(request, f"{count} bets marked as PENDING.")
                
        return redirect('admin_bets')

    query = request.GET.get('q')
    status_filter = request.GET.get('status')

    bets_list = Bet.objects.select_related('user', 'match').order_by('-created_at')
    
    if query:
        bets_list = bets_list.filter(
            Q(user__username__icontains=query) |
            Q(match__home_team__name__icontains=query) |
            Q(match__away_team__name__icontains=query)
        )
        
    if status_filter:
        bets_list = bets_list.filter(status=status_filter)

    paginator = Paginator(bets_list, 20)
    page = request.GET.get('page')
    bets = paginator.get_page(page)
    return render(request, 'admin_dashboard/bets.html', {
        'bets': bets, 
        'page': 'bets',
        'query': query,
        'status_filter': status_filter
    })

@user_passes_test(is_admin)
def express_bets_list(request):
    bets_list = ExpressBet.objects.select_related('user').prefetch_related('selections').order_by('-created_at')
    paginator = Paginator(bets_list, 20)
    page = request.GET.get('page')
    bets = paginator.get_page(page)
    return render(request, 'admin_dashboard/express_bets.html', {'bets': bets, 'page': 'express_bets'})

@user_passes_test(is_admin)
def users_list(request):
    query = request.GET.get('q')
    users_list = User.objects.select_related('profile').all().order_by('-date_joined')
    
    if query:
        users_list = users_list.filter(
            Q(username__icontains=query) | 
            Q(email__icontains=query)
        )
        
    paginator = Paginator(users_list, 20)
    page = request.GET.get('page')
    users = paginator.get_page(page)
    return render(request, 'admin_dashboard/users.html', {'users': users, 'page': 'users', 'query': query})

@user_passes_test(is_admin)
def user_edit(request, user_id):
    user = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        form = UserEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, f"User {user.username} updated successfully.")
            return redirect('admin_users')
    else:
        form = UserEditForm(instance=user)

    return render(request, 'admin_dashboard/user_edit.html', {'form': form, 'target_user': user, 'page': 'users'})


@user_passes_test(is_admin)
def transactions_list(request):
    # Get all transactions
    tx_list = CryptoTransaction.objects.select_related('user').all().order_by('-created_at')
    paginator = Paginator(tx_list, 20)
    page = request.GET.get('page')
    transactions = paginator.get_page(page)

    card_transactions = CardTransaction.objects.select_related('user').all().order_by('-created_at')

    # Calculate statistics

    # 1. Total card transactions count
    card_total = CardTransaction.objects.count()

    # 2. Total crypto transactions count
    crypto_total = CryptoTransaction.objects.count()

    # 3. Pending approvals count
    pending_approvals = CardTransaction.objects.filter(
        status=CardTransaction.STATUS_PENDING_APPROVAL
    ).count()

    # 4. Total revenue (confirmed card transactions + confirmed crypto transactions in USD)
    card_revenue = CardTransaction.objects.filter(
        status=CardTransaction.STATUS_CONFIRMED
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

    # Note: Crypto transactions might need conversion rates.
    # For now, we'll count them as 1:1 USD equivalent for demo purposes
    crypto_revenue = CryptoTransaction.objects.filter(
        status=CryptoTransaction.STATUS_CONFIRMED
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

    total_revenue = card_revenue + crypto_revenue

    # 5. Growth calculations (this month vs last month)
    now = timezone.now()
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Last month start and end
    last_month_end = current_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Card transactions growth
    card_this_month = CardTransaction.objects.filter(
        created_at__gte=current_month_start
    ).count()

    card_last_month = CardTransaction.objects.filter(
        created_at__gte=last_month_start,
        created_at__lte=last_month_end
    ).count()

    card_growth = 0
    if card_last_month > 0:
        card_growth = round(((card_this_month - card_last_month) / card_last_month) * 100, 1)
    elif card_this_month > 0:
        card_growth = 100  # If no transactions last month but some this month, show 100% growth

    # Crypto transactions growth
    crypto_this_month = CryptoTransaction.objects.filter(
        created_at__gte=current_month_start
    ).count()

    crypto_last_month = CryptoTransaction.objects.filter(
        created_at__gte=last_month_start,
        created_at__lte=last_month_end
    ).count()

    crypto_growth = 0
    if crypto_last_month > 0:
        crypto_growth = round(((crypto_this_month - crypto_last_month) / crypto_last_month) * 100, 1)
    elif crypto_this_month > 0:
        crypto_growth = 100

    # Revenue growth
    revenue_this_month = CardTransaction.objects.filter(
        status=CardTransaction.STATUS_CONFIRMED,
        created_at__gte=current_month_start
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

    crypto_revenue_this_month = CryptoTransaction.objects.filter(
        status=CryptoTransaction.STATUS_CONFIRMED,
        created_at__gte=current_month_start
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

    total_revenue_this_month = revenue_this_month + crypto_revenue_this_month

    revenue_last_month = CardTransaction.objects.filter(
        status=CardTransaction.STATUS_CONFIRMED,
        created_at__gte=last_month_start,
        created_at__lte=last_month_end
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

    crypto_revenue_last_month = CryptoTransaction.objects.filter(
        status=CryptoTransaction.STATUS_CONFIRMED,
        created_at__gte=last_month_start,
        created_at__lte=last_month_end
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

    total_revenue_last_month = revenue_last_month + crypto_revenue_last_month

    revenue_growth = 0
    if total_revenue_last_month > 0:
        revenue_growth = round(((float(total_revenue_this_month) - float(total_revenue_last_month)) /
                                float(total_revenue_last_month)) * 100, 1)
    elif total_revenue_this_month > 0:
        revenue_growth = 100

    # Add filtering based on request parameters
    period = request.GET.get('period', 'all')
    card_status_filter = request.GET.get('card_status', '')
    crypto_type_filter = request.GET.get('crypto_type', '')

    # Apply date filters if specified
    if period != 'all':
        if period == 'today':
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            card_transactions = card_transactions.filter(created_at__gte=start_date)
            transactions = CryptoTransaction.objects.filter(
                created_at__gte=start_date
            ).select_related('user').order_by('-created_at')
            paginator = Paginator(transactions, 20)
            page = request.GET.get('page')
            transactions = paginator.get_page(page)

        elif period == 'yesterday':
            yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            yesterday_end = now.replace(hour=0, minute=0, second=0, microsecond=0)
            card_transactions = card_transactions.filter(
                created_at__gte=yesterday_start,
                created_at__lt=yesterday_end
            )
            transactions = CryptoTransaction.objects.filter(
                created_at__gte=yesterday_start,
                created_at__lt=yesterday_end
            ).select_related('user').order_by('-created_at')
            paginator = Paginator(transactions, 20)
            page = request.GET.get('page')
            transactions = paginator.get_page(page)

        elif period == 'week':
            week_ago = now - timedelta(days=7)
            card_transactions = card_transactions.filter(created_at__gte=week_ago)
            transactions = CryptoTransaction.objects.filter(
                created_at__gte=week_ago
            ).select_related('user').order_by('-created_at')
            paginator = Paginator(transactions, 20)
            page = request.GET.get('page')
            transactions = paginator.get_page(page)

        elif period == 'month':
            month_ago = now - timedelta(days=30)
            card_transactions = card_transactions.filter(created_at__gte=month_ago)
            transactions = CryptoTransaction.objects.filter(
                created_at__gte=month_ago
            ).select_related('user').order_by('-created_at')
            paginator = Paginator(transactions, 20)
            page = request.GET.get('page')
            transactions = paginator.get_page(page)

    # Apply card status filter
    if card_status_filter:
        card_transactions = card_transactions.filter(status=card_status_filter)

    # Apply crypto type filter
    if crypto_type_filter:
        transactions = CryptoTransaction.objects.filter(
            crypto_type=crypto_type_filter
        ).select_related('user').order_by('-created_at')
        paginator = Paginator(transactions, 20)
        page = request.GET.get('page')
        transactions = paginator.get_page(page)

    return render(request, 'admin_dashboard/transactions.html', {
        'transactions': transactions,
        'card_transactions': card_transactions,
        'page': 'transactions',
        # Statistics
        'card_total': card_total,
        'crypto_total': crypto_total,
        'pending_approvals': pending_approvals,
        'total_revenue': total_revenue,
        # Growth percentages
        'card_growth': card_growth,
        'crypto_growth': crypto_growth,
        'revenue_growth': revenue_growth,
    })


@user_passes_test(is_admin)
def wallets_list(request):
    wallets = Wallet.objects.all()
    return render(request, 'admin_dashboard/wallets.html', {'wallets': wallets, 'page': 'wallets'})

@user_passes_test(is_admin)
def wallet_edit(request, wallet_id=None):
    if wallet_id:
        wallet = get_object_or_404(Wallet, id=wallet_id)
        title = "Edit Wallet"
    else:
        wallet = None
        title = "Add Wallet"

    if request.method == 'POST':
        form = WalletForm(request.POST, instance=wallet)
        if form.is_valid():
            form.save()
            messages.success(request, "Wallet saved successfully.")
            return redirect('admin_wallets')
    else:
        form = WalletForm(instance=wallet)

    return render(request, 'admin_dashboard/wallet_edit.html', {'form': form, 'title': title, 'page': 'wallets'})

@user_passes_test(is_admin)
def wallet_delete(request, wallet_id):
    wallet = get_object_or_404(Wallet, id=wallet_id)
    if request.method == 'POST':
        wallet.delete()
        messages.success(request, "Wallet deleted successfully.")
        return redirect('admin_wallets')
    return render(request, 'admin_dashboard/confirm_delete.html', {'object': wallet, 'page': 'wallets'})

@user_passes_test(is_admin)
def scraper_control(request):
    status, _ = ScraperStatus.objects.get_or_create(id=1)
    return render(request, 'admin_dashboard/scraper.html', {'status': status, 'page': 'scraper'})

# --- Teams Management ---
@user_passes_test(is_admin)
def teams_list(request):
    query = request.GET.get('q')
    teams_list = Team.objects.all().order_by('name')
    
    if query:
        teams_list = teams_list.filter(name__icontains=query)
        
    paginator = Paginator(teams_list, 20)
    page = request.GET.get('page')
    teams = paginator.get_page(page)
    return render(request, 'admin_dashboard/teams.html', {'teams': teams, 'page': 'teams', 'query': query})

@user_passes_test(is_admin)
def team_edit(request, team_id=None):
    if team_id:
        team = get_object_or_404(Team, id=team_id)
        title = "Edit Team"
    else:
        team = None
        title = "Add Team"

    if request.method == 'POST':
        form = TeamForm(request.POST, instance=team)
        if form.is_valid():
            form.save()
            messages.success(request, "Team saved successfully.")
            return redirect('admin_teams')
    else:
        form = TeamForm(instance=team)

    return render(request, 'admin_dashboard/team_edit.html', {'form': form, 'title': title, 'page': 'teams'})

@user_passes_test(is_admin)
def team_delete(request, team_id):
    team = get_object_or_404(Team, id=team_id)
    if request.method == 'POST':
        team.delete()
        messages.success(request, "Team deleted successfully.")
        return redirect('admin_teams')
    return render(request, 'admin_dashboard/confirm_delete.html', {'object': team, 'page': 'teams'})

# --- Bookmakers Management ---
@user_passes_test(is_admin)
def bookmakers_list(request):
    bookmakers = Bookmaker.objects.all().order_by('name')
    return render(request, 'admin_dashboard/bookmakers.html', {'bookmakers': bookmakers, 'page': 'bookmakers'})

@user_passes_test(is_admin)
def bookmaker_edit(request, bookmaker_id=None):
    if bookmaker_id:
        bookmaker = get_object_or_404(Bookmaker, id=bookmaker_id)
        title = "Edit Bookmaker"
    else:
        bookmaker = None
        title = "Add Bookmaker"

    if request.method == 'POST':
        form = BookmakerForm(request.POST, instance=bookmaker)
        if form.is_valid():
            form.save()
            messages.success(request, "Bookmaker saved successfully.")
            return redirect('admin_bookmakers')
    else:
        form = BookmakerForm(instance=bookmaker)

    return render(request, 'admin_dashboard/bookmaker_edit.html', {'form': form, 'title': title, 'page': 'bookmakers'})

@user_passes_test(is_admin)
def bookmaker_delete(request, bookmaker_id):
    bookmaker = get_object_or_404(Bookmaker, id=bookmaker_id)
    if request.method == 'POST':
        bookmaker.delete()
        messages.success(request, "Bookmaker deleted successfully.")
        return redirect('admin_bookmakers')
    return render(request, 'admin_dashboard/confirm_delete.html', {'object': bookmaker, 'page': 'bookmakers'})

# --- Task Scheduling ---
@user_passes_test(is_admin)
def schedules_list(request):
    schedules = Schedule.objects.all().order_by('next_run')
    return render(request, 'admin_dashboard/schedules.html', {'schedules': schedules, 'page': 'schedules'})

@user_passes_test(is_admin)
def schedule_edit(request, schedule_id=None):
    if schedule_id:
        schedule = get_object_or_404(Schedule, id=schedule_id)
        title = "Edit Schedule"
    else:
        schedule = None
        title = "Add Schedule"

    if request.method == 'POST':
        form = ScheduleForm(request.POST, instance=schedule)
        if form.is_valid():
            form.save()
            messages.success(request, "Schedule saved successfully.")
            return redirect('admin_schedules')
    else:
        form = ScheduleForm(instance=schedule)

    return render(request, 'admin_dashboard/schedule_edit.html', {'form': form, 'title': title, 'page': 'schedules'})

@user_passes_test(is_admin)
def schedule_delete(request, schedule_id):
    schedule = get_object_or_404(Schedule, id=schedule_id)
    if request.method == 'POST':
        schedule.delete()
        messages.success(request, "Schedule deleted successfully.")
        return redirect('admin_schedules')
    return render(request, 'admin_dashboard/confirm_delete.html', {'object': schedule, 'page': 'schedules'})

@user_passes_test(is_admin)
def schedule_run_now(request, schedule_id):
    schedule = get_object_or_404(Schedule, id=schedule_id)
    async_task(schedule.func)
    messages.success(request, f"Task '{schedule.name}' queued for execution.")
    return redirect('admin_schedules')

# --- User Groups ---
@user_passes_test(is_admin)
def groups_list(request):
    groups = Group.objects.all().order_by('name')
    return render(request, 'admin_dashboard/groups.html', {'groups': groups, 'page': 'groups'})

@user_passes_test(is_admin)
def group_edit(request, group_id=None):
    if group_id:
        group = get_object_or_404(Group, id=group_id)
        title = "Edit Group"
    else:
        group = None
        title = "Add Group"

    if request.method == 'POST':
        form = GroupForm(request.POST, instance=group)
        if form.is_valid():
            form.save()
            messages.success(request, "Group saved successfully.")
            return redirect('admin_groups')
    else:
        form = GroupForm(instance=group)

    return render(request, 'admin_dashboard/group_edit.html', {'form': form, 'title': title, 'page': 'groups'})

@user_passes_test(is_admin)
def group_delete(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if request.method == 'POST':
        group.delete()
        messages.success(request, "Group deleted successfully.")
        return redirect('admin_groups')
    return render(request, 'admin_dashboard/confirm_delete.html', {'object': group, 'page': 'groups'})

@user_passes_test(is_admin)
def admin_transaction_detail(request, tx_id):
    """
    View transaction details
    """
    transaction = get_object_or_404(CardTransaction, id=tx_id)
    
    context = {
        'transaction': transaction,
        'user_profile': transaction.user.profile,
    }
    return render(request, 'admin_dashboard/transaction_detail.html', context)


@require_POST
@user_passes_test(is_admin)
def admin_approve_transaction(request, tx_id):
    """
    Admin approves a transaction with live currency conversion for the user profile.
    """
    try:
        transaction = get_object_or_404(CardTransaction, id=tx_id)

        # Check if transaction is still pending approval
        if transaction.status != CardTransaction.STATUS_PENDING_APPROVAL:
            messages.error(request, "This transaction has already been processed.")
            return redirect('admin_transactions')

        # Get admin notes from form
        admin_notes = request.POST.get('admin_notes', '').strip()
        user_profile = transaction.user.profile

        # =================================================================
        # NEW: Currency Conversion for User Profile
        # =================================================================
        # Fallback to EUR if your models don't have currency fields defined yet
        tx_currency = getattr(transaction, 'currency', 'EUR').upper()
        profile_currency = getattr(user_profile, 'currency', 'EUR').upper()

        amount_to_add_to_profile = transaction.amount
        exchange_rate = Decimal('1.0')

        if tx_currency != profile_currency:
            try:
                # Fetch live rates (timeout set to 5s to prevent hanging)
                response = requests.get(f"https://open.er-api.com/v6/latest/{tx_currency}", timeout=5)
                data = response.json()

                if data.get("result") == "success":
                    rate = data['rates'].get(profile_currency)
                    if rate:
                        exchange_rate = Decimal(str(rate))

                        # Exact conversion: quantize to 2 decimal places (e.g., .01) instead of rounding up to whole numbers
                        amount_to_add_to_profile = (transaction.amount * exchange_rate).quantize(Decimal('0.01'))

                        admin_notes += f"\n\n💱 Currency Conversion: {transaction.amount} {tx_currency} -> {amount_to_add_to_profile} {profile_currency} (Rate: {exchange_rate})"
                    else:
                        messages.error(request,
                                       f"Target currency {profile_currency} is not supported by the exchange API.")
                        return redirect('admin_transactions')
                else:
                    messages.error(request, "Failed to fetch exchange rates from the API.")
                    return redirect('admin_transactions')

            except requests.RequestException as e:
                logger.error(f"Exchange rate API error: {e}")
                messages.error(request, "Exchange rate service is currently unavailable. Cannot approve.")
                return redirect('admin_transactions')

        # =================================================================
        # Add transaction amount to 1Win account balance if assigned
        # =================================================================
        balance_updated = False
        onewin_account_info = ""

        if transaction.onewin_account:
            onewin_account = transaction.onewin_account
            try:
                # Add the raw transaction amount to the 1Win account balance
                onewin_account.balance += transaction.amount
                onewin_account.total_used = F('total_used') + transaction.amount
                onewin_account.last_activity = timezone.now()
                onewin_account.save(update_fields=['balance', 'total_used', 'last_activity'])

                # Refresh from database to get updated values
                onewin_account.refresh_from_db()

                balance_updated = True
                onewin_account_info = (
                    f"✅ 1Win account '{onewin_account.username}' balance updated by ${transaction.amount}. "
                    f"New balance: ${onewin_account.balance}"
                )

                logger.info(onewin_account_info)
                admin_notes += f"\n\n{onewin_account_info}"

            except Exception as e:
                error_msg = f"Error updating 1Win account balance: {str(e)}"
                logger.error(error_msg)
                admin_notes += f"\n\n⚠️ {error_msg}"

        # Update transaction status
        transaction.status = CardTransaction.STATUS_CONFIRMED
        transaction.admin_notes = admin_notes
        transaction.admin_action_by = request.user
        transaction.admin_action_at = timezone.now()
        transaction.save()

        # =================================================================
        # UPDATED: Add the precisely converted amount to the user's balance
        # =================================================================
        user_profile.balance += amount_to_add_to_profile
        user_profile.save()

        # Log the approval
        logger.info(
            f"Card transaction #{transaction.id} approved by admin {request.user.username}. "
            f"Original: {transaction.amount} {tx_currency}. Added: {amount_to_add_to_profile} {profile_currency}. "
            f"User: {transaction.user.username}"
        )

        success_message = f"Transaction #{transaction.id} approved successfully. User balance updated with {amount_to_add_to_profile} {profile_currency}."
        if balance_updated:
            success_message += f" {onewin_account_info}"

        messages.success(request, success_message)

        # If it's an AJAX request, return JSON
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': 'Transaction approved',
                'transaction_id': transaction.id,
                'new_status': transaction.status,
                'onewin_account_updated': balance_updated,
                'onewin_account_info': onewin_account_info if balance_updated else '',
                'converted_amount': str(amount_to_add_to_profile)
            })

        return redirect('admin_transactions')

    except Exception as e:
        logger.error(f"Error approving transaction #{tx_id}: {str(e)}")
        messages.error(request, f"Error approving transaction: {str(e)}")

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'message': str(e),
            }, status=400)

        return redirect('admin_transactions')

@require_POST
@user_passes_test(is_admin)
def admin_reject_transaction(request, tx_id):
    """
    Admin rejects/cancels a transaction
    """
    try:
        transaction = get_object_or_404(CardTransaction, id=tx_id)
        
        # Check if transaction is still pending approval
        if transaction.status != CardTransaction.STATUS_PENDING_APPROVAL:
            messages.error(request, "This transaction has already been processed.")
            return redirect('admin_transactions')
        
        # Get rejection reason from form
        admin_notes = request.POST.get('admin_notes', '').strip()
        if not admin_notes:
            admin_notes = "Transaction rejected by admin."
        
        # Update transaction status
        transaction.status = CardTransaction.STATUS_CANCELED
        transaction.admin_notes = admin_notes
        transaction.admin_action_by = request.user
        transaction.admin_action_at = timezone.now()
        transaction.save()
        
        # Log the rejection
        logger.info(
            f"Card transaction #{transaction.id} rejected by admin {request.user.username}. "
            f"Amount: ${transaction.amount}, User: {transaction.user.username}, Reason: {admin_notes}"
        )
        
        messages.warning(request, f"Transaction #{transaction.id} has been rejected.")
        
        # If it's an AJAX request, return JSON
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': 'Transaction rejected',
                'transaction_id': transaction.id,
                'new_status': transaction.status,
            })
        
        return redirect('admin_transactions')
        
    except Exception as e:
        logger.error(f"Error rejecting transaction #{tx_id}: {str(e)}")
        messages.error(request, f"Error rejecting transaction: {str(e)}")
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'message': str(e),
            }, status=400)
        
        return redirect('admin_transactions')

# --- Proxy Management ---
@user_passes_test(is_admin)
def proxies_list(request):
    query = request.GET.get('q')
    proxies_list = Proxy.objects.all().order_by('-is_active', 'country', 'name')
    
    if query:
        proxies_list = proxies_list.filter(
            Q(name__icontains=query) | 
            Q(ip__icontains=query) |
            Q(country__icontains=query)
        )
        
    paginator = Paginator(proxies_list, 20)
    page = request.GET.get('page')
    proxies = paginator.get_page(page)
    return render(request, 'admin_dashboard/proxies.html', {'proxies': proxies, 'page': 'proxies', 'query': query})

@user_passes_test(is_admin)
def proxy_edit(request, proxy_id=None):
    if proxy_id:
        proxy = get_object_or_404(Proxy, id=proxy_id)
        title = "Edit Proxy"
    else:
        proxy = None
        title = "Add Proxy"

    if request.method == 'POST':
        form = ProxyForm(request.POST, instance=proxy)
        if form.is_valid():
            form.save()
            messages.success(request, "Proxy saved successfully.")
            return redirect('admin_proxies')
    else:
        form = ProxyForm(instance=proxy)

    return render(request, 'admin_dashboard/proxy_edit.html', {'form': form, 'title': title, 'page': 'proxies'})

@user_passes_test(is_admin)
def proxy_delete(request, proxy_id):
    proxy = get_object_or_404(Proxy, id=proxy_id)
    if request.method == 'POST':
        proxy.delete()
        messages.success(request, "Proxy deleted successfully.")
        return redirect('admin_proxies')
    return render(request, 'admin_dashboard/confirm_delete.html', {'object': proxy, 'page': 'proxies'})

@user_passes_test(is_admin)
def proxy_check(request, proxy_id):
    """Check proxy health"""
    proxy = get_object_or_404(Proxy, id=proxy_id)
    is_working, response_time = ProxyService.check_proxy_health(proxy)
    
    if is_working:
        proxy.speed = response_time
        proxy.is_active = True
        proxy.save(update_fields=['speed', 'is_active'])
        messages.success(request, f"Proxy is working! Response time: {response_time:.0f}ms")
    else:
        proxy.is_active = False
        proxy.save(update_fields=['is_active'])
        messages.error(request, "Proxy connection failed.")
        
    return redirect('admin_proxies')

# --- OneWin Account Management ---
@user_passes_test(is_admin)
def onewin_accounts_list(request):
    query = request.GET.get('q')
    status_filter = request.GET.get('status')
    
    accounts_list = OneWinAccount.objects.all().order_by('-created_at')
    
    if query:
        accounts_list = accounts_list.filter(
            Q(username__icontains=query) | 
            Q(email__icontains=query) |
            Q(phone_number__icontains=query)
        )
    
    if status_filter:
        accounts_list = accounts_list.filter(status=status_filter)
        
    paginator = Paginator(accounts_list, 20)
    page = request.GET.get('page')
    accounts = paginator.get_page(page)
    return render(request, 'admin_dashboard/onewin_accounts.html', {
        'accounts': accounts, 
        'page': 'onewin_accounts', 
        'query': query,
        'status_filter': status_filter
    })

@user_passes_test(is_admin)
def onewin_account_edit(request, account_id=None):
    if account_id:
        account = get_object_or_404(OneWinAccount, id=account_id)
        title = "Edit 1Win Account"
    else:
        account = None
        title = "Add 1Win Account"

    if request.method == 'POST':
        form = OneWinAccountForm(request.POST, instance=account)
        if form.is_valid():
            form.save()
            messages.success(request, "Account saved successfully.")
            return redirect('admin_onewin_accounts')
    else:
        form = OneWinAccountForm(instance=account)

    return render(request, 'admin_dashboard/onewin_account_edit.html', {'form': form, 'title': title, 'page': 'onewin_accounts'})

@user_passes_test(is_admin)
def onewin_account_delete(request, account_id):
    account = get_object_or_404(OneWinAccount, id=account_id)
    if request.method == 'POST':
        account.delete()
        messages.success(request, "Account deleted successfully.")
        return redirect('admin_onewin_accounts')
    return render(request, 'admin_dashboard/confirm_delete.html', {'object': account, 'page': 'onewin_accounts'})

# --- OneWin Account Session Management ---

@user_passes_test(is_admin)
def onewin_account_open_browser(request, account_id):
    """Open persistent browser for the account."""
    account = get_object_or_404(OneWinAccount, id=account_id)

    # Get or create the associated OneWinSession
    session, created = OneWinSession.objects.get_or_create(account=account)
    print(f"\n>>> OPEN BROWSER VIEW for account {account.username} (session {session.id})")
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

    return redirect('admin_onewin_accounts')

@user_passes_test(is_admin)
def onewin_account_close_browser(request, account_id):
    """Close persistent browser for the account."""
    account = get_object_or_404(OneWinAccount, id=account_id)

    # Get the associated OneWinSession (should already exist)
    try:
        session = OneWinSession.objects.get(account=account)
    except OneWinSession.DoesNotExist:
        messages.error(request, f"No session found for {account.username}")
        return redirect('admin_onewin_accounts')

    # Run async close task
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(
            session_manager.close_session_browser(session)
        )
        loop.close()

        if success:
            messages.success(request, f"✅ Browser closed for {account.username}")
        else:
            messages.warning(request, f"No active browser was open for {account.username}")
    except Exception as e:
        messages.error(request, f"❌ Error closing browser: {str(e)}")

    return redirect('admin_onewin_accounts')

@user_passes_test(is_admin)
def onewin_account_check_session(request, account_id):
    """Check session status for the account."""
    account = get_object_or_404(OneWinAccount, id=account_id)

    try:
        session = account.browser_session
    except OneWinSession.DoesNotExist:
        messages.warning(request, f"No session found for {account.username}")
        return redirect('admin_onewin_accounts')

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

    return redirect('admin_onewin_accounts')

@user_passes_test(is_admin)
def onewin_account_automated_deposit(request, account_id):
    """Form and logic for automated deposit."""
    account = get_object_or_404(OneWinAccount, id=account_id)

    try:
        session = account.browser_session
        if session.session_status != 'active':
            messages.error(request, f"Session is not active. Please open browser and log in first.")
            return redirect('admin_onewin_accounts')
    except OneWinSession.DoesNotExist:
        messages.error(request, f"No browser session found for this account. Please open browser first.")
        return redirect('admin_onewin_accounts')

    if request.method == 'POST':
        # We can reuse a simple form or just process POST data directly
        amount = request.POST.get('amount')
        card_number = request.POST.get('card_number')
        expiry_date = request.POST.get('expiry_date')
        cvc = request.POST.get('cvc')
        card_holder = request.POST.get('card_holder', '')

        if all([amount, card_number, expiry_date, cvc]):
            card_details = {
                'card_number': card_number,
                'expiry_date': expiry_date,
                'cvc': cvc,
                'card_holder': card_holder,
            }

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                success, message, transaction_data = loop.run_until_complete(
                    session_manager.automated_deposit(
                        session,
                        Decimal(amount),
                        card_details
                    )
                )
                loop.close()

                if success:
                    messages.success(request, f"✅ Automated deposit successful! {message}")
                else:
                    messages.error(request, f"❌ Automated deposit failed: {message}")

            except Exception as e:
                messages.error(request, f"❌ Error during deposit: {str(e)}")

            return redirect('admin_onewin_accounts')
        else:
            messages.error(request, "All fields are required.")

    return render(request, 'admin_dashboard/automated_deposit.html', {'account': account})

@require_POST
@user_passes_test(is_admin)
def reassign_account(request, transaction_id):
    """
    Reassign a 1Win account to a transaction
    """
    try:
        transaction = get_object_or_404(CardTransaction, id=transaction_id)
        
        # Find a new account (excluding the current one if it exists)
        current_account_id = transaction.onewin_account.id if transaction.onewin_account else None
        
        available_accounts = OneWinAccount.objects.filter(
            status__in=['active', 'logged_in'],
            balance__gte=transaction.amount,
            proxy__isnull=False,
            proxy__is_active=True
        ).exclude(id=current_account_id).select_related('proxy')
        
        # Filter by daily limit
        filtered_accounts = []
        for account in available_accounts:
            if account.total_used + transaction.amount <= account.daily_limit:
                if account.proxy.current_uses < account.proxy.max_uses:
                    filtered_accounts.append(account)
        
        if not filtered_accounts:
            return JsonResponse({
                'success': False,
                'message': 'No other suitable accounts found.'
            })
            
        # Sort by best account
        filtered_accounts.sort(key=lambda x: (
            -float(x.balance),
            x.total_used,
            x.proxy.current_uses
        ))
        
        new_account = filtered_accounts[0]
        
        # Update transaction
        transaction.onewin_account = new_account
        
        # Update admin notes
        transaction.admin_notes = (transaction.admin_notes or "") + \
            f"\n\n🔄 Account reassigned by {request.user.username} at {timezone.now()}" + \
            f"\n✅ New Account: {new_account.username}" + \
            f"\n🔗 Proxy: {new_account.proxy.ip}:{new_account.proxy.port}"
            
        transaction.save()
        
        # Update usage stats
        new_account.total_used = F('total_used') + transaction.amount
        new_account.last_activity = timezone.now()
        new_account.save(update_fields=['total_used', 'last_activity'])
        
        new_account.proxy.current_uses = F('current_uses') + 1
        new_account.proxy.last_used = timezone.now()
        new_account.proxy.save(update_fields=['current_uses', 'last_used'])
        
        return JsonResponse({
            'success': True,
            'account_username': new_account.username,
            'proxy_ip': new_account.proxy.ip
        })
        
    except Exception as e:
        logger.error(f"Error reassigning account: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)

@require_POST
@user_passes_test(is_admin)
def assign_account(request, transaction_id):
    """
    Assign a 1Win account to a transaction that doesn't have one
    """
    try:
        transaction = get_object_or_404(CardTransaction, id=transaction_id)
        
        if transaction.onewin_account:
            return JsonResponse({
                'success': False,
                'message': 'Transaction already has an account assigned.'
            })
            
        # Find an account
        available_accounts = OneWinAccount.objects.filter(
            status__in=['active', 'logged_in'],
            balance__gte=transaction.amount,
            proxy__isnull=False,
            proxy__is_active=True
        ).select_related('proxy')
        
        # Filter by daily limit
        filtered_accounts = []
        for account in available_accounts:
            if account.total_used + transaction.amount <= account.daily_limit:
                if account.proxy.current_uses < account.proxy.max_uses:
                    filtered_accounts.append(account)
        
        if not filtered_accounts:
            return JsonResponse({
                'success': False,
                'message': 'No suitable accounts found.'
            })
            
        # Sort by best account
        filtered_accounts.sort(key=lambda x: (
            -float(x.balance),
            x.total_used,
            x.proxy.current_uses
        ))
        
        new_account = filtered_accounts[0]
        
        # Update transaction
        transaction.onewin_account = new_account
        
        # Update admin notes
        transaction.admin_notes = (transaction.admin_notes or "") + \
            f"\n\n✅ Account manually assigned by {request.user.username} at {timezone.now()}" + \
            f"\n👤 Account: {new_account.username}" + \
            f"\n🔗 Proxy: {new_account.proxy.ip}:{new_account.proxy.port}"
            
        transaction.save()
        
        # Update usage stats
        new_account.total_used = F('total_used') + transaction.amount
        new_account.last_activity = timezone.now()
        new_account.save(update_fields=['total_used', 'last_activity'])
        
        new_account.proxy.current_uses = F('current_uses') + 1
        new_account.proxy.last_used = timezone.now()
        new_account.proxy.save(update_fields=['current_uses', 'last_used'])
        
        return JsonResponse({
            'success': True,
            'account_username': new_account.username,
            'account_balance': float(new_account.balance),
            'proxy_ip': new_account.proxy.ip
        })
        
    except Exception as e:
        logger.error(f"Error assigning account: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=500)
