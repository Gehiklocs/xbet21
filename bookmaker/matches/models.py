# matches/models.py
from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone
import datetime
import math
from decimal import Decimal, InvalidOperation

# Add this utility function at the top of the file (after imports, before classes)
def check_bet_result(match, bet_type):
    """
    Determine if a bet on the given match with the given bet type would win.
    match: a Match instance (must be finished with scores)
    bet_type: string from Bet.BET_TYPE_CHOICES
    Returns boolean.
    """
    home_score = match.home_score or 0
    away_score = match.away_score or 0
    total_goals = home_score + away_score

    # Half-time result
    ht_home = match.half_time_home_score or 0
    ht_away = match.half_time_away_score or 0
    ht_result = 'draw'
    if ht_home > ht_away:
        ht_result = 'home'
    elif ht_away > ht_home:
        ht_result = 'away'

    # Full-time result
    ft_result = 'draw'
    if home_score > away_score:
        ft_result = 'home'
    elif away_score > home_score:
        ft_result = 'away'

    bet_checks = {
        # Basic 1X2
        'home': ft_result == 'home',
        'draw': ft_result == 'draw',
        'away': ft_result == 'away',

        # Double Chance
        '1x': ft_result in ['home', 'draw'],
        '12': ft_result in ['home', 'away'],
        'x2': ft_result in ['draw', 'away'],

        # Total Goals
        'over_1_5': total_goals > 1.5,
        'under_1_5': total_goals < 1.5,
        'over_2_5': total_goals > 2.5,
        'under_2_5': total_goals < 2.5,
        'over_3_5': total_goals > 3.5,
        'under_3_5': total_goals < 3.5,

        # Handicap
        'handicap_home': (home_score - away_score) > 1.5,
        'handicap_away': (away_score - home_score) > 1.5,

        # BTTS
        'btts_yes': home_score > 0 and away_score > 0,
        'btts_no': home_score == 0 or away_score == 0,

        # HT/FT
        'htft_hh': ht_result == 'home' and ft_result == 'home',
        'htft_hd': ht_result == 'home' and ft_result == 'draw',
        'htft_ha': ht_result == 'home' and ft_result == 'away',
        'htft_dh': ht_result == 'draw' and ft_result == 'home',
        'htft_dd': ht_result == 'draw' and ft_result == 'draw',
        'htft_da': ht_result == 'draw' and ft_result == 'away',
        'htft_ah': ht_result == 'away' and ft_result == 'home',
        'htft_ad': ht_result == 'away' and ft_result == 'draw',
        'htft_aa': ht_result == 'away' and ft_result == 'away',

        # Asian Handicap
        'ah_home_minus_05': (home_score - away_score) > 0.5,
        'ah_away_plus_05': (away_score - home_score) > -0.5,
        'ah_home_minus_1': (home_score - away_score) > 1,
        'ah_away_plus_1': (away_score - home_score) > -1,

        # Correct Score
        'cs_1_0': home_score == 1 and away_score == 0,
        'cs_2_0': home_score == 2 and away_score == 0,
        'cs_2_1': home_score == 2 and away_score == 1,
        'cs_0_0': home_score == 0 and away_score == 0,
        'cs_1_1': home_score == 1 and away_score == 1,
        'cs_0_1': home_score == 0 and away_score == 1,
        'cs_0_2': home_score == 0 and away_score == 2,
        'cs_1_2': home_score == 1 and away_score == 2,

        # Half Time
        'ht_home': ht_result == 'home',
        'ht_draw': ht_result == 'draw',
        'ht_away': ht_result == 'away',

        # Odd/Even
        'odd': total_goals % 2 == 1,
        'even': total_goals % 2 == 0,

        # Draw No Bet (if draw, bet loses – no refund for simplicity)
        'dnb_home': ft_result == 'home',
        'dnb_away': ft_result == 'away',

        # Win to Nil
        'win_to_nil_home': ft_result == 'home' and away_score == 0,
        'win_to_nil_away': ft_result == 'away' and home_score == 0,

        # HT Double Chance
        'ht_1x': ht_result in ['home', 'draw'],
        'ht_12': ht_result in ['home', 'away'],
        'ht_x2': ht_result in ['draw', 'away'],

        # BTTS & Win
        'btts_win_home': ft_result == 'home' and home_score > 0 and away_score > 0,
        'btts_win_away': ft_result == 'away' and home_score > 0 and away_score > 0,

        # Team Goals
        'home_over_1_5': home_score > 1.5,
        'home_under_1_5': home_score < 1.5,
        'away_over_1_5': away_score > 1.5,
        'away_under_1_5': away_score < 1.5,
    }
    return bet_checks.get(bet_type, False)



class Bookmaker(models.Model):
    """
    Represents a bookmaker/odds provider
    """
    name = models.CharField(max_length=100)
    website = models.URLField(blank=True)

    def __str__(self):
        return self.name


class Team(models.Model):
    """
    Represents a sports team
    """
    name = models.CharField(max_length=100, unique=True)
    logo_url = models.URLField(blank=True, null=True)

    def __str__(self):
        return self.name


def get_default_scraped_at():
    """
    Returns a default datetime for scraped_at field
    """
    return timezone.make_aware(datetime.datetime(1900, 1, 1))


class Match(models.Model):
    """
    Represents a sports match with betting odds
    """
    STATUS_UPCOMING = 'upcoming'
    STATUS_LIVE = 'live'
    STATUS_FINISHED = 'finished'
    STATUS_CANCELED = 'canceled'

    STATUS_CHOICES = [
        (STATUS_UPCOMING, 'Upcoming'),
        (STATUS_LIVE, 'Live'),
        (STATUS_FINISHED, 'Finished'),
        (STATUS_CANCELED, 'Canceled'),
    ]

    # Basic match information
    home_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='home_matches')
    away_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='away_matches')
    match_date = models.DateTimeField()
    league = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_UPCOMING)
    match_url = models.URLField(blank=True, null=True)

    live_minute = models.CharField(max_length=10, blank=True, null=True,
                                   help_text="Current minute for live matches (e.g., '45', 'HT')")

    # Timestamps
    scraped_at = models.DateTimeField(default=get_default_scraped_at, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    # Match results
    home_score = models.IntegerField(null=True, blank=True)
    away_score = models.IntegerField(null=True, blank=True)
    winner = models.CharField(max_length=10, null=True, blank=True)  # 'home', 'draw', 'away'

    # Additional match data for calculations
    total_goals = models.IntegerField(null=True, blank=True)
    half_time_home_score = models.IntegerField(null=True, blank=True)
    half_time_away_score = models.IntegerField(null=True, blank=True)
    first_goal_scorer = models.CharField(max_length=50, null=True, blank=True)  # 'home', 'away', 'none'

    # --- BASE ODDS FIELDS (1X2) ---
    home_odds = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    draw_odds = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    away_odds = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # --- DERIVED ODDS FIELDS ---

    # 1. DOUBLE CHANCE
    odds_1x = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Home or Draw
    odds_12 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Home or Away
    odds_x2 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Draw or Away

    # 2. TOTAL GOALS (OVER/UNDER)
    odds_over_1_5 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_under_1_5 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_over_2_5 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_under_2_5 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_over_3_5 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_under_3_5 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # 3. HANDICAP
    handicap_value = models.DecimalField(max_digits=4, decimal_places=1, default=1.5)
    odds_handicap_home = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_handicap_away = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # 4. BOTH TEAMS TO SCORE (BTTS)
    odds_btts_yes = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_btts_no = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # 5. HALF TIME / FULL TIME (HT/FT)
    odds_htft_hh = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Home/Home
    odds_htft_hd = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Home/Draw
    odds_htft_ha = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Home/Away
    odds_htft_dh = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Draw/Home
    odds_htft_dd = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Draw/Draw
    odds_htft_da = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Draw/Away
    odds_htft_ah = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Away/Home
    odds_htft_ad = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Away/Draw
    odds_htft_aa = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Away/Away

    # 6. ASIAN HANDICAP
    odds_ah_home_minus_05 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Home -0.5
    odds_ah_away_plus_05 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Away +0.5
    odds_ah_home_minus_1 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Home -1
    odds_ah_away_plus_1 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # Away +1

    # 7. CORRECT SCORE
    odds_cs_1_0 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # 1-0
    odds_cs_2_0 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # 2-0
    odds_cs_2_1 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # 2-1
    odds_cs_0_0 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # 0-0
    odds_cs_1_1 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # 1-1
    odds_cs_0_1 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # 0-1
    odds_cs_0_2 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # 0-2
    odds_cs_1_2 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)  # 1-2

    # 8. HALF TIME RESULT
    odds_ht_home = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_ht_draw = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_ht_away = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # 9. ODD/EVEN TOTAL GOALS
    odds_odd = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_even = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # 10. DRAW NO BET
    odds_dnb_home = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_dnb_away = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # 11. TO WIN TO NIL
    odds_win_to_nil_home = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_win_to_nil_away = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # 12. HALF TIME DOUBLE CHANCE
    odds_ht_1x = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_ht_12 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_ht_x2 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # 13. BOTH TEAMS TO SCORE & WIN
    odds_btts_win_home = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_btts_win_away = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # 14. TOTAL TEAM GOALS
    odds_home_over_15 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_home_under_15 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_away_over_15 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_away_under_15 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"{self.home_team} vs {self.away_team}"

    def settle_bets(self):
        from django.db import transaction
        from accounts.models import Profile

        if self.status != 'finished':
            return

        with transaction.atomic():
            # --- Settle single bets ---
            for bet in self.bets.filter(status='pending').select_for_update():
                profile = Profile.objects.select_for_update().get(user=bet.user)

                # Check for "Draw No Bet" Refund Scenario
                is_draw = (self.home_score == self.away_score)
                is_dnb_bet = bet.bet_type in [bet.BET_TYPE_DNB_HOME, bet.BET_TYPE_DNB_AWAY]

                if is_dnb_bet and is_draw:
                    bet.status = 'refunded'
                    profile.balance += bet.amount  # Return the original stake only
                elif bet.check_result():
                    bet.status = 'won'
                    profile.balance += bet.potential_payout
                else:
                    bet.status = 'lost'

                bet.save()
                profile.save()

            # --- Settle express selections ---
            selections = ExpressBetSelection.objects.filter(
                match=self, status='pending'
            ).select_related('express_bet').select_for_update()

            express_bets_to_check = set()
            for sel in selections:
                # Check for Draw No Bet refund inside an express bet
                is_dnb_bet = sel.bet_type in [Bet.BET_TYPE_DNB_HOME, Bet.BET_TYPE_DNB_AWAY]
                is_draw = (self.home_score == self.away_score)

                if is_dnb_bet and is_draw:
                    sel.status = 'refunded'  # A refunded selection usually means odds become 1.0 for this leg
                elif sel.check_result():
                    sel.status = 'won'
                else:
                    sel.status = 'lost'
                sel.save()
                express_bets_to_check.add(sel.express_bet_id)

            # --- Settle affected express bets ---
            for eb_id in express_bets_to_check:
                eb = ExpressBet.objects.select_for_update().get(id=eb_id)

                # Only process if all selections are resolved (no pending)
                if eb.selections.filter(status='pending').exists():
                    continue

                profile = Profile.objects.select_for_update().get(user=eb.user)

                if eb.selections.filter(status='lost').exists():
                    eb.status = 'lost'
                else:
                    # If it's not lost, it's a mix of won/refunded.
                    eb.status = 'won'

                    # Recalculate total odds for the express ticket (ignoring refunded legs)
                    final_odds = Decimal('1.00')
                    for leg in eb.selections.all():
                        if leg.status == 'won':
                            final_odds *= leg.odds
                        # If refunded, odds for that leg act as 1.00, so we don't multiply

                    actual_payout = eb.amount * final_odds
                    profile.balance += actual_payout

                eb.save()
                profile.save()

    def save(self, *args, **kwargs):
        # Update total goals if scores are available
        if self.home_score is not None and self.away_score is not None:
            self.total_goals = self.home_score + self.away_score
        super().save(*args, **kwargs)

    def calculate_derived_odds(self):
        """
        Calculate/update derived odds for all bet types.
        Uses base 1X2 odds as foundation and scraped markets when available.
        """
        # Get the primary odds from first Odds object
        main_odds = self.odds.first()
        if not main_odds:
            return

        try:
            # Extract base probabilities from 1X2 odds
            o1 = float(main_odds.home_odds)
            ox = float(main_odds.draw_odds)
            o2 = float(main_odds.away_odds)

            margin_factor = 0.95  # House edge factor

            # Store base odds if not already set
            if not self.home_odds:
                self.home_odds = Decimal(o1)
                self.draw_odds = Decimal(ox)
                self.away_odds = Decimal(o2)

            # Calculate all derived odds
            self.calculate_double_chance_odds(o1, ox, o2, margin_factor)
            self.calculate_total_goals_odds(o1, ox, o2, margin_factor)
            self.calculate_handicap_odds(o1, ox, o2, margin_factor)
            self.calculate_btts_odds(o1, ox, o2, margin_factor)
            self.calculate_htft_odds(o1, ox, o2, margin_factor)
            self.calculate_asian_handicap_odds(o1, ox, o2, margin_factor)
            self.calculate_correct_score_odds(o1, ox, o2, margin_factor)
            self.calculate_halftime_odds(o1, ox, o2, margin_factor)
            self.calculate_odd_even_odds(o1, ox, o2, margin_factor)
            self.calculate_dnb_odds(o1, ox, o2, margin_factor)
            self.calculate_win_to_nil_odds(o1, ox, o2, margin_factor)
            self.calculate_ht_double_chance_odds(o1, ox, o2, margin_factor)
            self.calculate_btts_win_odds(o1, ox, o2, margin_factor)
            self.calculate_team_goals_odds(o1, ox, o2, margin_factor)

            self.save()

        # 🛑 THE GLITCH SHIELD 🛑
        # Catch the noodle! 🍝
        except (ValueError, ZeroDivisionError, TypeError, InvalidOperation) as e:
            print(f"Error calculating odds for match {self.id}: {e}")

    def calculate_double_chance_odds(self, o1, ox, o2, margin_factor):
        """Calculate Double Chance odds"""
        prob_1x = (1 / o1) + (1 / ox)
        self.odds_1x = round(Decimal(1 / prob_1x * margin_factor), 2)

        prob_12 = (1 / o1) + (1 / o2)
        self.odds_12 = round(Decimal(1 / prob_12 * margin_factor), 2)

        prob_x2 = (1 / ox) + (1 / o2)
        self.odds_x2 = round(Decimal(1 / prob_x2 * margin_factor), 2)

    def calculate_total_goals_odds(self, o1, ox, o2, margin_factor):
        """Calculate Over/Under odds for multiple thresholds"""
        # Try to find from scraped markets first
        total_market = self.markets.filter(name__icontains="Total").first()
        if total_market:
            for outcome in total_market.outcomes.all():
                if "Over 1.5" in outcome.name:
                    self.odds_over_1_5 = outcome.odds
                elif "Under 1.5" in outcome.name:
                    self.odds_under_1_5 = outcome.odds
                elif "Over 2.5" in outcome.name:
                    self.odds_over_2_5 = outcome.odds
                elif "Under 2.5" in outcome.name:
                    self.odds_under_2_5 = outcome.odds
                elif "Over 3.5" in outcome.name:
                    self.odds_over_3_5 = outcome.odds
                elif "Under 3.5" in outcome.name:
                    self.odds_under_3_5 = outcome.odds

        # Fallback calculations based on Poisson distribution
        if not self.odds_over_2_5:
            # Estimate goal expectation based on odds
            goal_expectation = 2.5  # Base

            if o1 < 1.5 or o2 < 1.5:  # Strong favorite
                goal_expectation = 3.0
            elif ox < 3.0:  # Likely draw/tight game
                goal_expectation = 2.0

            # Poisson probabilities
            lam = goal_expectation
            p_0 = math.exp(-lam)
            p_1 = lam * math.exp(-lam)
            p_2 = (lam ** 2 * math.exp(-lam)) / 2
            p_3 = (lam ** 3 * math.exp(-lam)) / 6

            # Calculate probabilities
            prob_over_15 = 1 - (p_0 + p_1)
            prob_under_15 = p_0 + p_1
            prob_over_25 = 1 - (p_0 + p_1 + p_2)
            prob_under_25 = p_0 + p_1 + p_2
            prob_over_35 = 1 - (p_0 + p_1 + p_2 + p_3)
            prob_under_35 = p_0 + p_1 + p_2 + p_3

            # Set odds with margin
            self.odds_over_1_5 = round(Decimal(1 / prob_over_15 * margin_factor), 2)
            self.odds_under_1_5 = round(Decimal(1 / prob_under_15 * margin_factor), 2)
            self.odds_over_2_5 = round(Decimal(1 / prob_over_25 * margin_factor), 2)
            self.odds_under_2_5 = round(Decimal(1 / prob_under_25 * margin_factor), 2)
            self.odds_over_3_5 = round(Decimal(1 / prob_over_35 * margin_factor), 2)
            self.odds_under_3_5 = round(Decimal(1 / prob_under_35 * margin_factor), 2)

    def calculate_handicap_odds(self, o1, ox, o2, margin_factor):
        """Calculate handicap odds"""
        handicap_market = self.markets.filter(name__icontains="Handicap").first()
        if handicap_market:
            for outcome in handicap_market.outcomes.all():
                if f"{self.home_team.name} (-1.5)" in outcome.name or "(-1.5)" in outcome.name:
                    self.odds_handicap_home = outcome.odds
                elif f"{self.away_team.name} (+1.5)" in outcome.name or "(+1.5)" in outcome.name:
                    self.odds_handicap_away = outcome.odds

        if not self.odds_handicap_home:
            # Estimate based on win probability
            prob_home = 1 / o1
            prob_away = 1 / o2

            # For handicap -1.5, home needs to win by 2+ goals
            prob_home_handicap = prob_home * 0.4  # Rough estimation
            prob_away_handicap = 1 - prob_home_handicap

            self.odds_handicap_home = round(Decimal(1 / prob_home_handicap * margin_factor), 2)
            self.odds_handicap_away = round(Decimal(1 / prob_away_handicap * margin_factor), 2)

    def calculate_btts_odds(self, o1, ox, o2, margin_factor):
        """Calculate Both Teams To Score odds"""
        btts_market = self.markets.filter(name__icontains="Both To Score").first()
        if btts_market:
            for outcome in btts_market.outcomes.all():
                if "Yes" in outcome.name:
                    self.odds_btts_yes = outcome.odds
                elif "No" in outcome.name:
                    self.odds_btts_no = outcome.odds

        if not self.odds_btts_yes:
            # Estimate based on match dynamics
            if ox < 3.2:  # Likely close game
                prob_btts_yes = 0.55
            elif o1 < 1.8 or o2 < 1.8:  # Strong favorite
                prob_btts_yes = 0.45
            else:
                prob_btts_yes = 0.50

            prob_btts_no = 1 - prob_btts_yes

            self.odds_btts_yes = round(Decimal(1 / prob_btts_yes * margin_factor), 2)
            self.odds_btts_no = round(Decimal(1 / prob_btts_no * margin_factor), 2)

    def calculate_htft_odds(self, o1, ox, o2, margin_factor):
        """Calculate Half Time/Full Time odds"""
        htft_market = self.markets.filter(name__icontains="Half Time/Full Time").first()
        if htft_market:
            for outcome in htft_market.outcomes.all():
                if "Home/Home" in outcome.name:
                    self.odds_htft_hh = outcome.odds
                elif "Home/Draw" in outcome.name:
                    self.odds_htft_hd = outcome.odds
                elif "Home/Away" in outcome.name:
                    self.odds_htft_ha = outcome.odds
                elif "Draw/Home" in outcome.name:
                    self.odds_htft_dh = outcome.odds
                elif "Draw/Draw" in outcome.name:
                    self.odds_htft_dd = outcome.odds
                elif "Draw/Away" in outcome.name:
                    self.odds_htft_da = outcome.odds
                elif "Away/Home" in outcome.name:
                    self.odds_htft_ah = outcome.odds
                elif "Away/Draw" in outcome.name:
                    self.odds_htft_ad = outcome.odds
                elif "Away/Away" in outcome.name:
                    self.odds_htft_aa = outcome.odds

        if not self.odds_htft_hh:
            # Simplified estimation
            prob_home_win = 1 / o1
            prob_draw = 1 / ox
            prob_away_win = 1 / o2

            # Set odds for each HT/FT combination
            self.odds_htft_hh = round(Decimal((1 / (prob_home_win * 0.6)) * margin_factor), 2)
            self.odds_htft_dd = round(Decimal((1 / (prob_draw * 0.4)) * margin_factor), 2)
            self.odds_htft_aa = round(Decimal((1 / (prob_away_win * 0.6)) * margin_factor), 2)
            self.odds_htft_hd = round(Decimal((1 / (prob_home_win * 0.2)) * margin_factor), 2)
            self.odds_htft_ha = round(Decimal((1 / (prob_home_win * 0.05)) * margin_factor), 2)
            self.odds_htft_dh = round(Decimal((1 / (prob_draw * 0.3)) * margin_factor), 2)
            self.odds_htft_da = round(Decimal((1 / (prob_draw * 0.3)) * margin_factor), 2)
            self.odds_htft_ah = round(Decimal((1 / (prob_away_win * 0.05)) * margin_factor), 2)
            self.odds_htft_ad = round(Decimal((1 / (prob_away_win * 0.2)) * margin_factor), 2)

    def calculate_asian_handicap_odds(self, o1, ox, o2, margin_factor):
        """Calculate Asian Handicap odds"""
        ah_market = self.markets.filter(name__icontains="Asian Handicap").first()
        if ah_market:
            for outcome in ah_market.outcomes.all():
                if "-0.5" in outcome.name and self.home_team.name in outcome.name:
                    self.odds_ah_home_minus_05 = outcome.odds
                elif "+0.5" in outcome.name and self.away_team.name in outcome.name:
                    self.odds_ah_away_plus_05 = outcome.odds
                elif "-1" in outcome.name and self.home_team.name in outcome.name:
                    self.odds_ah_home_minus_1 = outcome.odds
                elif "+1" in outcome.name and self.away_team.name in outcome.name:
                    self.odds_ah_away_plus_1 = outcome.odds

        if not self.odds_ah_home_minus_05:
            prob_home = 1 / o1
            prob_draw = 1 / ox
            prob_away = 1 / o2

            # AH -0.5: Home needs to win (no draw)
            prob_ah_home_minus_05 = prob_home + (prob_draw * 0.5)  # Half push on draw
            self.odds_ah_home_minus_05 = round(Decimal(1 / prob_ah_home_minus_05 * margin_factor), 2)
            self.odds_ah_away_plus_05 = round(Decimal(1 / (1 - prob_ah_home_minus_05) * margin_factor), 2)

            # AH -1: Home needs to win by 2+
            prob_ah_home_minus_1 = prob_home * 0.7
            self.odds_ah_home_minus_1 = round(Decimal(1 / prob_ah_home_minus_1 * margin_factor), 2)
            self.odds_ah_away_plus_1 = round(Decimal(1 / (1 - prob_ah_home_minus_1) * margin_factor), 2)

    def calculate_correct_score_odds(self, o1, ox, o2, margin_factor):
        """Calculate Correct Score odds"""
        cs_market = self.markets.filter(name__icontains="Correct Score").first()
        if cs_market:
            for outcome in cs_market.outcomes.all():
                if "1-0" in outcome.name:
                    self.odds_cs_1_0 = outcome.odds
                elif "2-0" in outcome.name:
                    self.odds_cs_2_0 = outcome.odds
                elif "2-1" in outcome.name:
                    self.odds_cs_2_1 = outcome.odds
                elif "0-0" in outcome.name:
                    self.odds_cs_0_0 = outcome.odds
                elif "1-1" in outcome.name:
                    self.odds_cs_1_1 = outcome.odds
                elif "0-1" in outcome.name:
                    self.odds_cs_0_1 = outcome.odds
                elif "0-2" in outcome.name:
                    self.odds_cs_0_2 = outcome.odds
                elif "1-2" in outcome.name:
                    self.odds_cs_1_2 = outcome.odds

        if not self.odds_cs_1_0:
            # Estimate based on Poisson distribution
            lam_home = 1.5  # Expected home goals
            lam_away = 1.0  # Expected away goals

            if o1 < 1.5:  # Strong home favorite
                lam_home = 2.0
                lam_away = 0.8
            elif o2 < 1.5:  # Strong away favorite
                lam_home = 0.8
                lam_away = 2.0

            # Calculate probabilities for common scores
            scores = {
                "1_0": (lam_home * math.exp(-lam_home)) * math.exp(-lam_away),
                "2_0": ((lam_home ** 2 * math.exp(-lam_home)) / 2) * math.exp(-lam_away),
                "2_1": ((lam_home ** 2 * math.exp(-lam_home)) / 2) * (lam_away * math.exp(-lam_away)),
                "0_0": math.exp(-lam_home) * math.exp(-lam_away),
                "1_1": (lam_home * math.exp(-lam_home)) * (lam_away * math.exp(-lam_away)),
                "0_1": math.exp(-lam_home) * (lam_away * math.exp(-lam_away)),
                "0_2": math.exp(-lam_home) * ((lam_away ** 2 * math.exp(-lam_away)) / 2),
                "1_2": (lam_home * math.exp(-lam_home)) * ((lam_away ** 2 * math.exp(-lam_away)) / 2),
            }

            for score_key, prob in scores.items():
                odds = round(Decimal(1 / (prob * 1.3) * margin_factor), 2)  # Adjusted for reality
                setattr(self, f"odds_cs_{score_key}", odds)

    def calculate_halftime_odds(self, o1, ox, o2, margin_factor):
        """Calculate Half Time result odds"""
        ht_market = self.markets.filter(name__icontains="Half Time").first()
        if ht_market and "Result" in ht_market.name:
            for outcome in ht_market.outcomes.all():
                if "Home" in outcome.name and not "Draw" in outcome.name:
                    self.odds_ht_home = outcome.odds
                elif "Draw" in outcome.name:
                    self.odds_ht_draw = outcome.odds
                elif "Away" in outcome.name and not "Draw" in outcome.name:
                    self.odds_ht_away = outcome.odds

        if not self.odds_ht_home:
            # HT odds are typically higher than FT odds
            self.odds_ht_home = round(Decimal(o1 * 1.4), 2)
            self.odds_ht_draw = round(Decimal(ox * 0.8), 2)  # More draws at HT
            self.odds_ht_away = round(Decimal(o2 * 1.4), 2)

    def calculate_odd_even_odds(self, o1, ox, o2, margin_factor):
        """Calculate Odd/Even total goals odds"""
        oe_market = self.markets.filter(name__icontains="Odd/Even").first()
        if oe_market:
            for outcome in oe_market.outcomes.all():
                if "Odd" in outcome.name:
                    self.odds_odd = outcome.odds
                elif "Even" in outcome.name:
                    self.odds_even = outcome.odds

        if not self.odds_odd:
            # Slightly favor even (0, 2, 4 goals are even)
            self.odds_odd = Decimal('1.90')
            self.odds_even = Decimal('1.90')

    def calculate_dnb_odds(self, o1, ox, o2, margin_factor):
        """Calculate Draw No Bet odds"""
        dnb_market = self.markets.filter(name__icontains="Draw No Bet").first()
        if dnb_market:
            for outcome in dnb_market.outcomes.all():
                if self.home_team.name in outcome.name:
                    self.odds_dnb_home = outcome.odds
                elif self.away_team.name in outcome.name:
                    self.odds_dnb_away = outcome.odds

        if not self.odds_dnb_home:
            prob_home = 1 / o1
            prob_away = 1 / o2
            total_without_draw = prob_home + prob_away

            prob_dnb_home = prob_home / total_without_draw
            prob_dnb_away = prob_away / total_without_draw

            self.odds_dnb_home = round(Decimal(1 / prob_dnb_home * margin_factor), 2)
            self.odds_dnb_away = round(Decimal(1 / prob_dnb_away * margin_factor), 2)

    def calculate_win_to_nil_odds(self, o1, ox, o2, margin_factor):
        """Calculate Win to Nil odds"""
        wtn_market = self.markets.filter(name__icontains="Win to Nil").first()
        if wtn_market:
            for outcome in wtn_market.outcomes.all():
                if self.home_team.name in outcome.name:
                    self.odds_win_to_nil_home = outcome.odds
                elif self.away_team.name in outcome.name:
                    self.odds_win_to_nil_away = outcome.odds

        if not self.odds_win_to_nil_home:
            prob_home_win = 1 / o1
            prob_away_win = 1 / o2

            if o1 < 1.8:  # Strong home favorite
                prob_home_wtn = prob_home_win * 0.4
                prob_away_wtn = prob_away_win * 0.15
            else:
                prob_home_wtn = prob_home_win * 0.3
                prob_away_wtn = prob_away_win * 0.2

            self.odds_win_to_nil_home = round(Decimal(1 / prob_home_wtn * margin_factor), 2)
            self.odds_win_to_nil_away = round(Decimal(1 / prob_away_wtn * margin_factor), 2)

    def calculate_ht_double_chance_odds(self, o1, ox, o2, margin_factor):
        """Calculate Half Time Double Chance odds"""
        if not self.odds_ht_1x:
            # Based on HT odds
            ht_home = float(self.odds_ht_home) if self.odds_ht_home else o1 * 1.4
            ht_draw = float(self.odds_ht_draw) if self.odds_ht_draw else ox * 0.8
            ht_away = float(self.odds_ht_away) if self.odds_ht_away else o2 * 1.4

            prob_ht_1x = (1 / ht_home) + (1 / ht_draw)
            prob_ht_12 = (1 / ht_home) + (1 / ht_away)
            prob_ht_x2 = (1 / ht_draw) + (1 / ht_away)

            self.odds_ht_1x = round(Decimal(1 / prob_ht_1x * margin_factor), 2)
            self.odds_ht_12 = round(Decimal(1 / prob_ht_12 * margin_factor), 2)
            self.odds_ht_x2 = round(Decimal(1 / prob_ht_x2 * margin_factor), 2)

    def calculate_btts_win_odds(self, o1, ox, o2, margin_factor):
        """Calculate BTTS & Win odds"""
        if not self.odds_btts_win_home:
            prob_btts_yes = 0.5  # Default
            if self.odds_btts_yes:
                prob_btts_yes = 1 / float(self.odds_btts_yes)

            prob_home_win = 1 / o1
            prob_away_win = 1 / o2

            # Probability of both happening
            prob_btts_win_home = prob_btts_yes * prob_home_win * 0.7
            prob_btts_win_away = prob_btts_yes * prob_away_win * 0.7

            self.odds_btts_win_home = round(Decimal(1 / prob_btts_win_home * margin_factor), 2)
            self.odds_btts_win_away = round(Decimal(1 / prob_btts_win_away * margin_factor), 2)

    def calculate_team_goals_odds(self, o1, ox, o2, margin_factor):
        """Calculate Total Team Goals odds"""
        if not self.odds_home_over_15:
            # Estimate based on team strength
            if o1 < 1.8:  # Strong home favorite
                prob_home_over_15 = 0.65
                prob_away_over_15 = 0.35
            elif o2 < 1.8:  # Strong away favorite
                prob_home_over_15 = 0.35
                prob_away_over_15 = 0.65
            else:  # Balanced
                prob_home_over_15 = 0.5
                prob_away_over_15 = 0.4

            self.odds_home_over_15 = round(Decimal(1 / prob_home_over_15 * margin_factor), 2)
            self.odds_home_under_15 = round(Decimal(1 / (1 - prob_home_over_15) * margin_factor), 2)
            self.odds_away_over_15 = round(Decimal(1 / prob_away_over_15 * margin_factor), 2)
            self.odds_away_under_15 = round(Decimal(1 / (1 - prob_away_over_15) * margin_factor), 2)

    def get_available_bet_types(self):
        """Return list of available bet types with odds"""
        available = []

        # Helper function to add bet type if odds exist
        def add_bet_type(code, name, odds_field):
            odds = getattr(self, odds_field)
            if odds:
                available.append((code, name, odds))

        # Basic 1X2
        add_bet_type('home', 'Home Win', 'home_odds')
        add_bet_type('draw', 'Draw', 'draw_odds')
        add_bet_type('away', 'Away Win', 'away_odds')

        # Double Chance
        add_bet_type('1x', 'Double Chance 1X', 'odds_1x')
        add_bet_type('12', 'Double Chance 12', 'odds_12')
        add_bet_type('x2', 'Double Chance X2', 'odds_x2')

        # Total Goals
        add_bet_type('over_1_5', 'Over 1.5 Goals', 'odds_over_1_5')
        add_bet_type('under_1_5', 'Under 1.5 Goals', 'odds_under_1_5')
        add_bet_type('over_2_5', 'Over 2.5 Goals', 'odds_over_2_5')
        add_bet_type('under_2_5', 'Under 2.5 Goals', 'odds_under_2_5')
        add_bet_type('over_3_5', 'Over 3.5 Goals', 'odds_over_3_5')
        add_bet_type('under_3_5', 'Under 3.5 Goals', 'odds_under_3_5')

        # Handicap
        add_bet_type('handicap_home', f'Handicap Home (-{self.handicap_value})', 'odds_handicap_home')
        add_bet_type('handicap_away', f'Handicap Away (+{self.handicap_value})', 'odds_handicap_away')

        # BTTS
        add_bet_type('btts_yes', 'Both Teams To Score - Yes', 'odds_btts_yes')
        add_bet_type('btts_no', 'Both Teams To Score - No', 'odds_btts_no')

        # Add other bet types similarly...

        return available


class Odds(models.Model):
    """
    Represents odds from a specific bookmaker for a match
    """
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='odds')
    bookmaker = models.ForeignKey(Bookmaker, on_delete=models.CASCADE)
    home_odds = models.DecimalField(max_digits=6, decimal_places=2)
    draw_odds = models.DecimalField(max_digits=6, decimal_places=2)
    away_odds = models.DecimalField(max_digits=6, decimal_places=2)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['match', 'bookmaker']

    def __str__(self):
        return f"{self.match} - {self.bookmaker}"

    # Helper methods for template
    def get_1x_odds(self):
        # Fallback calculation if Match model fields are empty
        try:
            o1 = float(self.home_odds)
            ox = float(self.draw_odds)
            prob = (1 / o1) + (1 / ox)
            return round(Decimal(1 / prob * 0.95), 2)
        except:
            return Decimal('1.20')  # Default fallback

    def get_12_odds(self):
        try:
            o1 = float(self.home_odds)
            o2 = float(self.away_odds)
            prob = (1 / o1) + (1 / o2)
            return round(Decimal(1 / prob * 0.95), 2)
        except:
            return Decimal('1.20')

    def get_x2_odds(self):
        try:
            ox = float(self.draw_odds)
            o2 = float(self.away_odds)
            prob = (1 / ox) + (1 / o2)
            return round(Decimal(1 / prob * 0.95), 2)
        except:
            return Decimal('1.30')

    def get_margin(self):
        try:
            o1 = float(self.home_odds)
            ox = float(self.draw_odds)
            o2 = float(self.away_odds)
            margin = ((1 / o1 + 1 / ox + 1 / o2) - 1) * 100
            return round(margin, 1)
        except:
            return None


class Market(models.Model):
    """
    Represents a betting market (e.g., 'Winner', 'Total', 'Handicap')
    """
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='markets')
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = ['match', 'name']

    def __str__(self):
        return f"{self.match} - {self.name}"


class Outcome(models.Model):
    """
    Represents a specific outcome in a market (e.g., 'Over 2.5', 'Home Win')
    """
    market = models.ForeignKey(Market, on_delete=models.CASCADE, related_name='outcomes')
    name = models.CharField(max_length=100)
    odds = models.DecimalField(max_digits=6, decimal_places=2)

    def __str__(self):
        return f"{self.market.name} - {self.name} ({self.odds})"


class Bet(models.Model):
    """
    Represents a user's bet on a match
    """
    # Original bet types
    BET_TYPE_HOME = 'home'
    BET_TYPE_DRAW = 'draw'
    BET_TYPE_AWAY = 'away'
    BET_TYPE_1X = '1x'
    BET_TYPE_12 = '12'
    BET_TYPE_X2 = 'x2'
    BET_TYPE_OVER_25 = 'over_2_5'
    BET_TYPE_UNDER_25 = 'under_2_5'
    BET_TYPE_HANDICAP_HOME = 'handicap_home'
    BET_TYPE_HANDICAP_AWAY = 'handicap_away'
    BET_TYPE_BTTS_YES = 'btts_yes'
    BET_TYPE_BTTS_NO = 'btts_no'

    # New bet types
    # Half Time/Full Time
    BET_TYPE_HTFT_HH = 'htft_hh'
    BET_TYPE_HTFT_HD = 'htft_hd'
    BET_TYPE_HTFT_HA = 'htft_ha'
    BET_TYPE_HTFT_DH = 'htft_dh'
    BET_TYPE_HTFT_DD = 'htft_dd'
    BET_TYPE_HTFT_DA = 'htft_da'
    BET_TYPE_HTFT_AH = 'htft_ah'
    BET_TYPE_HTFT_AD = 'htft_ad'
    BET_TYPE_HTFT_AA = 'htft_aa'

    # Total Goals
    BET_TYPE_OVER_15 = 'over_1_5'
    BET_TYPE_UNDER_15 = 'under_1_5'
    BET_TYPE_OVER_35 = 'over_3_5'
    BET_TYPE_UNDER_35 = 'under_3_5'

    # Asian Handicap
    BET_TYPE_AH_HOME_MINUS_05 = 'ah_home_minus_05'
    BET_TYPE_AH_AWAY_PLUS_05 = 'ah_away_plus_05'
    BET_TYPE_AH_HOME_MINUS_1 = 'ah_home_minus_1'
    BET_TYPE_AH_AWAY_PLUS_1 = 'ah_away_plus_1'

    # Correct Score
    BET_TYPE_CS_1_0 = 'cs_1_0'
    BET_TYPE_CS_2_0 = 'cs_2_0'
    BET_TYPE_CS_2_1 = 'cs_2_1'
    BET_TYPE_CS_0_0 = 'cs_0_0'
    BET_TYPE_CS_1_1 = 'cs_1_1'
    BET_TYPE_CS_0_1 = 'cs_0_1'
    BET_TYPE_CS_0_2 = 'cs_0_2'
    BET_TYPE_CS_1_2 = 'cs_1_2'

    # Half Time
    BET_TYPE_HT_HOME = 'ht_home'
    BET_TYPE_HT_DRAW = 'ht_draw'
    BET_TYPE_HT_AWAY = 'ht_away'

    # Odd/Even
    BET_TYPE_ODD = 'odd'
    BET_TYPE_EVEN = 'even'

    # Draw No Bet
    BET_TYPE_DNB_HOME = 'dnb_home'
    BET_TYPE_DNB_AWAY = 'dnb_away'

    # Win to Nil
    BET_TYPE_WIN_TO_NIL_HOME = 'win_to_nil_home'
    BET_TYPE_WIN_TO_NIL_AWAY = 'win_to_nil_away'

    # Half Time Double Chance
    BET_TYPE_HT_1X = 'ht_1x'
    BET_TYPE_HT_12 = 'ht_12'
    BET_TYPE_HT_X2 = 'ht_x2'

    # BTTS & Win
    BET_TYPE_BTTS_WIN_HOME = 'btts_win_home'
    BET_TYPE_BTTS_WIN_AWAY = 'btts_win_away'

    # Team Goals
    BET_TYPE_HOME_OVER_15 = 'home_over_1_5'
    BET_TYPE_HOME_UNDER_15 = 'home_under_1_5'
    BET_TYPE_AWAY_OVER_15 = 'away_over_1_5'
    BET_TYPE_AWAY_UNDER_15 = 'away_under_1_5'

    BET_TYPE_CHOICES = [
        # Original bets
        (BET_TYPE_HOME, 'Home Win'),
        (BET_TYPE_DRAW, 'Draw'),
        (BET_TYPE_AWAY, 'Away Win'),
        (BET_TYPE_1X, 'Double Chance 1X'),
        (BET_TYPE_12, 'Double Chance 12'),
        (BET_TYPE_X2, 'Double Chance X2'),
        (BET_TYPE_OVER_25, 'Over 2.5 Goals'),
        (BET_TYPE_UNDER_25, 'Under 2.5 Goals'),
        (BET_TYPE_HANDICAP_HOME, 'Handicap Home'),
        (BET_TYPE_HANDICAP_AWAY, 'Handicap Away'),
        (BET_TYPE_BTTS_YES, 'Both Teams To Score - Yes'),
        (BET_TYPE_BTTS_NO, 'Both Teams To Score - No'),

        # New bets
        (BET_TYPE_OVER_15, 'Over 1.5 Goals'),
        (BET_TYPE_UNDER_15, 'Under 1.5 Goals'),
        (BET_TYPE_OVER_35, 'Over 3.5 Goals'),
        (BET_TYPE_UNDER_35, 'Under 3.5 Goals'),

        # Half Time/Full Time
        (BET_TYPE_HTFT_HH, 'Half Time/Full Time - Home/Home'),
        (BET_TYPE_HTFT_HD, 'Half Time/Full Time - Home/Draw'),
        (BET_TYPE_HTFT_HA, 'Half Time/Full Time - Home/Away'),
        (BET_TYPE_HTFT_DH, 'Half Time/Full Time - Draw/Home'),
        (BET_TYPE_HTFT_DD, 'Half Time/Full Time - Draw/Draw'),
        (BET_TYPE_HTFT_DA, 'Half Time/Full Time - Draw/Away'),
        (BET_TYPE_HTFT_AH, 'Half Time/Full Time - Away/Home'),
        (BET_TYPE_HTFT_AD, 'Half Time/Full Time - Away/Draw'),
        (BET_TYPE_HTFT_AA, 'Half Time/Full Time - Away/Away'),

        # Asian Handicap
        (BET_TYPE_AH_HOME_MINUS_05, 'Asian Handicap Home -0.5'),
        (BET_TYPE_AH_AWAY_PLUS_05, 'Asian Handicap Away +0.5'),
        (BET_TYPE_AH_HOME_MINUS_1, 'Asian Handicap Home -1'),
        (BET_TYPE_AH_AWAY_PLUS_1, 'Asian Handicap Away +1'),

        # Correct Score
        (BET_TYPE_CS_1_0, 'Correct Score 1-0'),
        (BET_TYPE_CS_2_0, 'Correct Score 2-0'),
        (BET_TYPE_CS_2_1, 'Correct Score 2-1'),
        (BET_TYPE_CS_0_0, 'Correct Score 0-0'),
        (BET_TYPE_CS_1_1, 'Correct Score 1-1'),
        (BET_TYPE_CS_0_1, 'Correct Score 0-1'),
        (BET_TYPE_CS_0_2, 'Correct Score 0-2'),
        (BET_TYPE_CS_1_2, 'Correct Score 1-2'),

        # Half Time
        (BET_TYPE_HT_HOME, 'Half Time - Home Win'),
        (BET_TYPE_HT_DRAW, 'Half Time - Draw'),
        (BET_TYPE_HT_AWAY, 'Half Time - Away Win'),

        # Odd/Even
        (BET_TYPE_ODD, 'Total Goals Odd'),
        (BET_TYPE_EVEN, 'Total Goals Even'),

        # Draw No Bet
        (BET_TYPE_DNB_HOME, 'Draw No Bet - Home'),
        (BET_TYPE_DNB_AWAY, 'Draw No Bet - Away'),

        # Win to Nil
        (BET_TYPE_WIN_TO_NIL_HOME, 'Home Win to Nil'),
        (BET_TYPE_WIN_TO_NIL_AWAY, 'Away Win to Nil'),

        # Half Time Double Chance
        (BET_TYPE_HT_1X, 'Half Time Double Chance 1X'),
        (BET_TYPE_HT_12, 'Half Time Double Chance 12'),
        (BET_TYPE_HT_X2, 'Half Time Double Chance X2'),

        # BTTS & Win
        (BET_TYPE_BTTS_WIN_HOME, 'BTTS & Home Win'),
        (BET_TYPE_BTTS_WIN_AWAY, 'BTTS & Away Win'),

        # Team Goals
        (BET_TYPE_HOME_OVER_15, 'Home Team Over 1.5 Goals'),
        (BET_TYPE_HOME_UNDER_15, 'Home Team Under 1.5 Goals'),
        (BET_TYPE_AWAY_OVER_15, 'Away Team Over 1.5 Goals'),
        (BET_TYPE_AWAY_UNDER_15, 'Away Team Under 1.5 Goals'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_WON = 'won'
    STATUS_LOST = 'lost'
    STATUS_REFUNDED = 'refunded'  # <--- ADD THIS

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_WON, 'Won'),
        (STATUS_LOST, 'Lost'),
        (STATUS_REFUNDED, 'Refunded'),  # <--- ADD THIS
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bets')
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='bets')
    bet_type = models.CharField(max_length=30, choices=BET_TYPE_CHOICES)
    odds = models.DecimalField(max_digits=6, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    potential_payout = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    # Additional field for score bets
    selected_score = models.CharField(max_length=10, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.match} - {self.amount} on {self.bet_type}"

    def check_result(self):
        if self.match.status != 'finished':
            return False
        return check_bet_result(self.match, self.bet_type)

    def save(self, *args, **kwargs):
        # Calculate potential payout
        self.potential_payout = self.amount * self.odds
        super().save(*args, **kwargs)

    def check_result(self):
        """Check if bet won based on match result"""
        if not self.match.status == 'finished':
            return False

        home_score = self.match.home_score or 0
        away_score = self.match.away_score or 0
        total_goals = home_score + away_score

        # Determine HT result if available
        ht_home = self.match.half_time_home_score or 0
        ht_away = self.match.half_time_away_score or 0
        ht_result = 'draw'
        if ht_home > ht_away:
            ht_result = 'home'
        elif ht_away > ht_home:
            ht_result = 'away'

        # Determine FT result
        ft_result = 'draw'
        if home_score > away_score:
            ft_result = 'home'
        elif away_score > home_score:
            ft_result = 'away'

        # Check each bet type
        bet_checks = {
            # Basic 1X2
            self.BET_TYPE_HOME: ft_result == 'home',
            self.BET_TYPE_DRAW: ft_result == 'draw',
            self.BET_TYPE_AWAY: ft_result == 'away',

            # Double Chance
            self.BET_TYPE_1X: ft_result in ['home', 'draw'],
            self.BET_TYPE_12: ft_result in ['home', 'away'],
            self.BET_TYPE_X2: ft_result in ['draw', 'away'],

            # Total Goals
            self.BET_TYPE_OVER_15: total_goals > 1.5,
            self.BET_TYPE_UNDER_15: total_goals < 1.5,
            self.BET_TYPE_OVER_25: total_goals > 2.5,
            self.BET_TYPE_UNDER_25: total_goals < 2.5,
            self.BET_TYPE_OVER_35: total_goals > 3.5,
            self.BET_TYPE_UNDER_35: total_goals < 3.5,

            # Handicap
            self.BET_TYPE_HANDICAP_HOME: (home_score - away_score) > 1.5,
            self.BET_TYPE_HANDICAP_AWAY: (away_score - home_score) > 1.5,

            # BTTS
            self.BET_TYPE_BTTS_YES: home_score > 0 and away_score > 0,
            self.BET_TYPE_BTTS_NO: home_score == 0 or away_score == 0,

            # HT/FT
            self.BET_TYPE_HTFT_HH: ht_result == 'home' and ft_result == 'home',
            self.BET_TYPE_HTFT_HD: ht_result == 'home' and ft_result == 'draw',
            self.BET_TYPE_HTFT_HA: ht_result == 'home' and ft_result == 'away',
            self.BET_TYPE_HTFT_DH: ht_result == 'draw' and ft_result == 'home',
            self.BET_TYPE_HTFT_DD: ht_result == 'draw' and ft_result == 'draw',
            self.BET_TYPE_HTFT_DA: ht_result == 'draw' and ft_result == 'away',
            self.BET_TYPE_HTFT_AH: ht_result == 'away' and ft_result == 'home',
            self.BET_TYPE_HTFT_AD: ht_result == 'away' and ft_result == 'draw',
            self.BET_TYPE_HTFT_AA: ht_result == 'away' and ft_result == 'away',

            # Asian Handicap
            self.BET_TYPE_AH_HOME_MINUS_05: (home_score - away_score) > 0.5,
            self.BET_TYPE_AH_AWAY_PLUS_05: (away_score - home_score) > -0.5,
            self.BET_TYPE_AH_HOME_MINUS_1: (home_score - away_score) > 1,
            self.BET_TYPE_AH_AWAY_PLUS_1: (away_score - home_score) > -1,

            # Correct Score
            self.BET_TYPE_CS_1_0: home_score == 1 and away_score == 0,
            self.BET_TYPE_CS_2_0: home_score == 2 and away_score == 0,
            self.BET_TYPE_CS_2_1: home_score == 2 and away_score == 1,
            self.BET_TYPE_CS_0_0: home_score == 0 and away_score == 0,
            self.BET_TYPE_CS_1_1: home_score == 1 and away_score == 1,
            self.BET_TYPE_CS_0_1: home_score == 0 and away_score == 1,
            self.BET_TYPE_CS_0_2: home_score == 0 and away_score == 2,
            self.BET_TYPE_CS_1_2: home_score == 1 and away_score == 2,

            # Half Time
            self.BET_TYPE_HT_HOME: ht_result == 'home',
            self.BET_TYPE_HT_DRAW: ht_result == 'draw',
            self.BET_TYPE_HT_AWAY: ht_result == 'away',

            # Odd/Even
            self.BET_TYPE_ODD: total_goals % 2 == 1,
            self.BET_TYPE_EVEN: total_goals % 2 == 0,

            # Draw No Bet (push on draw - stake returned)
            self.BET_TYPE_DNB_HOME: ft_result == 'home',
            self.BET_TYPE_DNB_AWAY: ft_result == 'away',

            # Win to Nil
            self.BET_TYPE_WIN_TO_NIL_HOME: ft_result == 'home' and away_score == 0,
            self.BET_TYPE_WIN_TO_NIL_AWAY: ft_result == 'away' and home_score == 0,

            # HT Double Chance
            self.BET_TYPE_HT_1X: ht_result in ['home', 'draw'],
            self.BET_TYPE_HT_12: ht_result in ['home', 'away'],
            self.BET_TYPE_HT_X2: ht_result in ['draw', 'away'],

            # BTTS & Win
            self.BET_TYPE_BTTS_WIN_HOME: ft_result == 'home' and home_score > 0 and away_score > 0,
            self.BET_TYPE_BTTS_WIN_AWAY: ft_result == 'away' and home_score > 0 and away_score > 0,

            # Team Goals
            self.BET_TYPE_HOME_OVER_15: home_score > 1.5,
            self.BET_TYPE_HOME_UNDER_15: home_score < 1.5,
            self.BET_TYPE_AWAY_OVER_15: away_score > 1.5,
            self.BET_TYPE_AWAY_UNDER_15: away_score < 1.5,
        }

        return bet_checks.get(self.bet_type, False)


class ExpressBet(models.Model):
    """
    Represents an accumulator/parlay bet
    """
    STATUS_PENDING = 'pending'
    STATUS_WON = 'won'
    STATUS_LOST = 'lost'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_WON, 'Won'),
        (STATUS_LOST, 'Lost'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='express_bets')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    total_odds = models.DecimalField(max_digits=10, decimal_places=2)
    potential_payout = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Express Bet - {self.user.username} - {self.amount}"

    def save(self, *args, **kwargs):
        # Calculate potential payout
        self.potential_payout = self.amount * self.total_odds
        super().save(*args, **kwargs)

    def check_result(self):
        """Check if all selections in express bet are won"""
        selections = self.selections.all()
        if not selections:
            return False

        # Check if all selections are won
        for selection in selections:
            if selection.status != 'won':
                return False

        return True


class ExpressBetSelection(models.Model):
    """
    Represents a single selection in an express bet
    """
    express_bet = models.ForeignKey(ExpressBet, on_delete=models.CASCADE, related_name='selections')
    match = models.ForeignKey(Match, on_delete=models.CASCADE)
    bet_type = models.CharField(max_length=30)  # Using the same bet types as Bet model
    odds = models.DecimalField(max_digits=6, decimal_places=2)
    status = models.CharField(max_length=10, default='pending')  # pending, won, lost

    def check_result(self):
        if self.match.status != 'finished':
            return False
        return check_bet_result(self.match, self.bet_type)

    @property
    def get_bet_type_display(self):
        return dict(Bet.BET_TYPE_CHOICES).get(self.bet_type, self.bet_type)

    def __str__(self):
        return f"{self.match} - {self.bet_type}"