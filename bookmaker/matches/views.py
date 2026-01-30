# matches/views.py
import requests
from django.http import JsonResponse
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from .models import Match, Odds, Bookmaker, Team, Bet, ExpressBet, ExpressBetSelection
from accounts.models import Profile
from django.db.models import Prefetch, Sum, Q
from accounts.services.telegram_notifier import notify_site_visit
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from decimal import Decimal
import json

API_KEY = "973fb2112f51a596e7c5f0138fecce97"  # Change this


def matches_dashboard(request):
    # Notify Telegram about the visit
    notify_site_visit(request)

    # Search functionality
    search_query = request.GET.get('q', '')
    
    # Base querysets
    live_qs = Match.objects.filter(status=Match.STATUS_LIVE)
    upcoming_qs = Match.objects.filter(
        match_date__gte=timezone.now(),
        match_date__lte=timezone.now() + timezone.timedelta(days=7),
        status=Match.STATUS_UPCOMING
    )
    today_qs = Match.objects.filter(
        match_date__date=timezone.now().date(),
        status=Match.STATUS_UPCOMING
    )
    finished_qs = Match.objects.filter(status=Match.STATUS_FINISHED)

    # Apply search filter if query exists
    if search_query:
        search_filter = Q(home_team__name__icontains=search_query) | \
                        Q(away_team__name__icontains=search_query) | \
                        Q(league__icontains=search_query)
        
        live_qs = live_qs.filter(search_filter)
        upcoming_qs = upcoming_qs.filter(search_filter)
        today_qs = today_qs.filter(search_filter)
        finished_qs = finished_qs.filter(search_filter)

    # Filter out matches with no odds for non-admin users
    if not request.user.is_staff:
        live_qs = live_qs.filter(odds__isnull=False).distinct()
        upcoming_qs = upcoming_qs.filter(odds__isnull=False).distinct()
        today_qs = today_qs.filter(odds__isnull=False).distinct()
        # Finished matches can be shown even without odds as they are historical

    # Prefetch related data
    prefetch_odds = Prefetch('odds', queryset=Odds.objects.select_related('bookmaker'))
    
    live_matches = live_qs.select_related('home_team', 'away_team').prefetch_related(prefetch_odds).order_by('match_date')
    upcoming_matches = upcoming_qs.select_related('home_team', 'away_team').prefetch_related(prefetch_odds).order_by('match_date')
    today_matches = today_qs.select_related('home_team', 'away_team').prefetch_related(prefetch_odds)
    finished_matches = finished_qs.select_related('home_team', 'away_team').order_by('-match_date')[:20]

    # Get all bookmakers
    bookmakers = Bookmaker.objects.all()

    context = {
        'live_matches': live_matches,
        'upcoming_matches': upcoming_matches,
        'today_matches': today_matches,
        'finished_matches': finished_matches,
        'bookmakers': bookmakers,
        'current_time': timezone.now(),
        'search_query': search_query,
    }
    return render(request, 'dashboard.html', context)


def match_detail(request, match_id):
    """Detailed view for a specific match"""
    match = get_object_or_404(Match.objects.select_related('home_team', 'away_team').prefetch_related(
        Prefetch('odds', queryset=Odds.objects.select_related('bookmaker'))
    ), id=match_id)

    # Check if odds exist for non-admin users
    if not request.user.is_staff and not match.odds.exists():
        messages.warning(request, "This match is currently unavailable for betting.")
        return redirect('dashboard')

    # Handle AJAX request for live updates
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        odds = match.odds.first()
        data = {
            'status': match.status,
            'home_score': match.home_score,
            'away_score': match.away_score,
            'odds': {
                'home': odds.home_odds if odds else None,
                'draw': odds.draw_odds if odds else None,
                'away': odds.away_odds if odds else None,
                '1x': match.odds_1x,
                '12': match.odds_12,
                'x2': match.odds_x2,
                'over_25': match.odds_over_2_5,
                'under_25': match.odds_under_2_5,
                'handicap_home': match.odds_handicap_home,
                'handicap_away': match.odds_handicap_away,
                'btts_yes': match.odds_btts_yes,
                'btts_no': match.odds_btts_no,
            }
        }
        return JsonResponse(data)

    # Get user's bets for this match if logged in
    user_bets = []
    if request.user.is_authenticated:
        user_bets = Bet.objects.filter(user=request.user, match=match).order_by('-created_at')

    context = {
        'match': match,
        'user_bets': user_bets,
    }
    return render(request, 'match_detail.html', context)


def teams_list(request):
    """View to display all teams with logos"""
    teams = Team.objects.all().order_by('name')

    context = {
        'teams': teams,
    }
    return render(request, 'matches/teams.html', context)

@login_required
def place_bet(request, match_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)

    match = get_object_or_404(Match, id=match_id)

    # Check if match is finished (Block betting)
    if match.status == Match.STATUS_FINISHED:
        return JsonResponse({'error': 'This match has finished!'}, status=400)
        
    # Check if match has started but is NOT live (Block betting)
    if match.status != Match.STATUS_LIVE and match.match_date < timezone.now():
        return JsonResponse({'error': 'This match has already started!'}, status=400)

    # Handle both JSON and Form data
    try:
        if request.content_type == 'application/json':
            data = json.loads(request.body)
            bet_type = data.get('bet_type')
            amount_str = data.get('amount')
            odds_value_str = data.get('odds')
        else:
            bet_type = request.POST.get('bet_type')
            amount_str = request.POST.get('amount')
            odds_value_str = request.POST.get('odds')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)

    try:
        amount = Decimal(str(amount_str))
        odds_value = Decimal(str(odds_value_str))
    except (ValueError, TypeError, InvalidOperation):
        return JsonResponse({'error': 'Invalid bet amount or odds.'}, status=400)

    if amount <= 0:
        return JsonResponse({'error': 'Bet amount must be positive.'}, status=400)

    # Atomic transaction to ensure balance is deducted only if bet is created
    try:
        with transaction.atomic():
            # Re-fetch profile to lock it for update
            profile = Profile.objects.select_for_update().get(user=request.user)

            if profile.balance < amount:
                return JsonResponse({'error': 'Insufficient balance!'}, status=400)

            # Deduct balance
            profile.balance -= amount
            profile.save()

            # Create Bet
            potential_payout = amount * odds_value
            bet = Bet.objects.create(
                user=request.user,
                match=match,
                bet_type=bet_type,
                odds=odds_value,
                amount=amount,
                potential_payout=potential_payout
            )
            
            return JsonResponse({
                'success': True,
                'message': f'Bet placed successfully! Potential payout: {potential_payout}',
                'new_balance': float(profile.balance),
                'bet_id': bet.id
            })

    except Exception as e:
        return JsonResponse({'error': f'An error occurred: {str(e)}'}, status=500)

@login_required
def place_express_bet(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)

    try:
        data = json.loads(request.body)
        bets = data.get('bets', [])
        stake = Decimal(str(data.get('stake', 0)))
        is_express = data.get('is_express', False)
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'error': 'Invalid data'}, status=400)

    if stake <= 0:
        return JsonResponse({'error': 'Stake must be positive'}, status=400)

    if not bets:
        return JsonResponse({'error': 'No bets selected'}, status=400)

    try:
        with transaction.atomic():
            profile = Profile.objects.select_for_update().get(user=request.user)

            if profile.balance < stake:
                return JsonResponse({'error': 'Insufficient balance'}, status=400)

            # Calculate total odds
            total_odds = Decimal('1.0')
            selections = []

            for bet_data in bets:
                match_id = bet_data.get('match_id')
                bet_type = bet_data.get('selection') # 'home', 'draw', '1x', etc.
                odds_val = Decimal(str(bet_data.get('odds')))

                match = Match.objects.get(id=match_id)

                # Basic validation: check if match is bettable
                if match.status == Match.STATUS_FINISHED:
                    raise ValueError(f"Match {match} is finished")

                total_odds *= odds_val
                selections.append({
                    'match': match,
                    'bet_type': bet_type,
                    'odds': odds_val
                })

            # Deduct balance
            profile.balance -= stake
            profile.save()

            # Create Express Bet
            potential_payout = stake * total_odds
            express_bet = ExpressBet.objects.create(
                user=request.user,
                amount=stake,
                total_odds=total_odds,
                potential_payout=potential_payout
            )

            # Create Selections
            for sel in selections:
                ExpressBetSelection.objects.create(
                    express_bet=express_bet,
                    match=sel['match'],
                    bet_type=sel['bet_type'],
                    odds=sel['odds']
                )

            return JsonResponse({
                'success': True,
                'new_balance': float(profile.balance),
                'message': 'Express bet placed successfully!'
            })

    except Match.DoesNotExist:
        return JsonResponse({'error': 'One or more matches not found'}, status=404)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'error': f'Server error: {str(e)}'}, status=500)

@login_required
def my_bets(request):
    """View to display user's betting history"""
    bets = Bet.objects.filter(user=request.user).select_related('match', 'match__home_team', 'match__away_team').order_by('-created_at')
    express_bets = ExpressBet.objects.filter(user=request.user).prefetch_related('selections__match').order_by('-created_at')

    # Calculate stats (simplified for now)
    total_bets = bets.count() + express_bets.count()

    context = {
        'bets': bets,
        'express_bets': express_bets,
        'total_bets': total_bets,
    }
    return render(request, 'matches/my_bets.html', context)
