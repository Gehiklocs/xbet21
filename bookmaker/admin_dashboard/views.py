from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.core.paginator import Paginator
from django.contrib import messages
from matches.models import Match, Bet, ExpressBet
from accounts.models import Profile, CryptoTransaction
from scraper_module.models import ScraperStatus
from .forms import UserEditForm

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
    matches_list = Match.objects.all().order_by('-match_date')
    paginator = Paginator(matches_list, 20)
    page = request.GET.get('page')
    matches = paginator.get_page(page)
    return render(request, 'admin_dashboard/matches.html', {'matches': matches, 'page': 'matches'})

@user_passes_test(is_admin)
def bets_list(request):
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
    users_list = User.objects.select_related('profile').all().order_by('-date_joined')
    paginator = Paginator(users_list, 20)
    page = request.GET.get('page')
    users = paginator.get_page(page)
    return render(request, 'admin_dashboard/users.html', {'users': users, 'page': 'users'})

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
def scraper_control(request):
    status, _ = ScraperStatus.objects.get_or_create(id=1)
    return render(request, 'admin_dashboard/scraper.html', {'status': status, 'page': 'scraper'})
