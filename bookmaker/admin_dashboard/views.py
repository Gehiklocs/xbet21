from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth import get_user_model
from django.db.models import Sum, Q
from django.core.paginator import Paginator
from django.contrib import messages
from matches.models import Match, Bet, ExpressBet
from accounts.models import Profile, CryptoTransaction, Wallet
from scraper_module.models import ScraperStatus
from .forms import UserEditForm, WalletForm, MatchEditForm

User = get_user_model()

def is_admin(user):
    return user.is_authenticated and user.is_superuser

@user_passes_test(is_admin, login_url='/accounts/login/')
def dashboard_home(request):
    total_users = User.objects.count()
    total_matches = Match.objects.count()
    live_matches = Match.objects.filter(status=Match.STATUS_LIVE).count()
    total_bets = Bet.objects.count()
    total_wagered = Bet.objects.aggregate(Sum('amount'))['amount__sum'] or 0

    recent_bets = Bet.objects.select_related('user', 'match').order_by('-created_at')[:10]

    context = {
        'total_users': total_users,
        'total_matches': total_matches,
        'live_matches': live_matches,
        'total_bets': total_bets,
        'total_wagered': total_wagered,
        'recent_bets': recent_bets,
        'page': 'home'
    }
    return render(request, 'admin_dashboard/home.html', context)

@user_passes_test(is_admin)
def matches_list(request):
    query = request.GET.get('q')
    matches_list = Match.objects.all().order_by('-match_date')
    
    if query:
        matches_list = matches_list.filter(
            Q(home_team__name__icontains=query) | 
            Q(away_team__name__icontains=query) |
            Q(league__icontains=query)
        )
    
    paginator = Paginator(matches_list, 20)
    page = request.GET.get('page')
    matches = paginator.get_page(page)
    return render(request, 'admin_dashboard/matches.html', {'matches': matches, 'page': 'matches', 'query': query})

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

    bets_list = Bet.objects.select_related('user', 'match').order_by('-created_at')
    paginator = Paginator(bets_list, 20)
    page = request.GET.get('page')
    bets = paginator.get_page(page)
    return render(request, 'admin_dashboard/bets.html', {'bets': bets, 'page': 'bets'})

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
    tx_list = CryptoTransaction.objects.select_related('user').all().order_by('-created_at')
    paginator = Paginator(tx_list, 20)
    page = request.GET.get('page')
    transactions = paginator.get_page(page)
    return render(request, 'admin_dashboard/transactions.html', {'transactions': transactions, 'page': 'transactions'})

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
