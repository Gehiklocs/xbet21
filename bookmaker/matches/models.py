# matches/models.py
from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from decimal import Decimal
from django.utils import timezone
import datetime

class Bookmaker(models.Model):
    name = models.CharField(max_length=100)
    website = models.URLField(blank=True)

    def __str__(self):
        return self.name


class Team(models.Model):  # New model for teams with logos
    name = models.CharField(max_length=100, unique=True)
    logo_url = models.URLField(blank=True, null=True)

    def __str__(self):
        return self.name


def get_default_scraped_at():
    return timezone.make_aware(datetime.datetime(1900, 1, 1))

class Match(models.Model):
    STATUS_UPCOMING = 'upcoming'
    STATUS_LIVE = 'live'
    STATUS_FINISHED = 'finished'

    STATUS_CHOICES = [
        (STATUS_UPCOMING, 'Upcoming'),
        (STATUS_LIVE, 'Live'),
        (STATUS_FINISHED, 'Finished'),
    ]

    home_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='home_matches')
    away_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='away_matches')
    match_date = models.DateTimeField()
    league = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_UPCOMING)
    
    # New field for match link
    match_url = models.URLField(blank=True, null=True)

    # Timestamps for scraping - Made nullable to fix migration issues

    scraped_at = models.DateTimeField(default=get_default_scraped_at, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    # Result fields (for settling bets)
    home_score = models.IntegerField(null=True, blank=True)
    away_score = models.IntegerField(null=True, blank=True)
    winner = models.CharField(max_length=10, null=True, blank=True) # 'home', 'draw', 'away'

    # --- Calculated/Derived Odds Fields ---
    # Double Chance
    odds_1x = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_12 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_x2 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    
    # Total (Over/Under 2.5 is standard)
    odds_over_2_5 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_under_2_5 = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    
    # Handicap (Standard -1.5 / +1.5 or similar)
    # We'll store a generic "Home Handicap" and "Away Handicap" and the value
    handicap_value = models.DecimalField(max_digits=4, decimal_places=1, default=1.5)
    odds_handicap_home = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_handicap_away = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # Both Teams To Score (BTTS)
    odds_btts_yes = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    odds_btts_no = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"{self.home_team} vs {self.away_team}"

    def calculate_derived_odds(self):
        """
        Algorithm to calculate/update derived odds (Double Chance, Total, Handicap, BTTS).
        Uses scraped 1X2 odds to calculate Double Chance.
        Tries to fetch Total/Handicap/BTTS from scraped Markets, or estimates them.
        """
        # Get the primary odds (e.g., from XStake or the first available)
        main_odds = self.odds.first()
        if not main_odds:
            return

        try:
            o1 = float(main_odds.home_odds)
            ox = float(main_odds.draw_odds)
            o2 = float(main_odds.away_odds)

            # --- 1. Double Chance Calculation ---
            # Formula: 1/Odds_DC = 1/Odds_A + 1/Odds_B
            # We apply a small margin factor (e.g., 0.95) to ensure the house edge is maintained or slightly increased
            margin_factor = 0.95
            
            # 1X (Home or Draw)
            prob_1x = (1/o1) + (1/ox)
            self.odds_1x = round(Decimal(1 / prob_1x * margin_factor), 2)
            
            # 12 (Home or Away)
            prob_12 = (1/o1) + (1/o2)
            self.odds_12 = round(Decimal(1 / prob_12 * margin_factor), 2)
            
            # X2 (Draw or Away)
            prob_x2 = (1/ox) + (1/o2)
            self.odds_x2 = round(Decimal(1 / prob_x2 * margin_factor), 2)

            # --- 2. Total (Over/Under 2.5) ---
            # Try to find from scraped markets first
            total_market = self.markets.filter(name__icontains="Total").first()
            if total_market:
                over = total_market.outcomes.filter(name__icontains="Over 2.5").first()
                under = total_market.outcomes.filter(name__icontains="Under 2.5").first()
                if over: self.odds_over_2_5 = over.odds
                if under: self.odds_under_2_5 = under.odds
            
            # Fallback Estimation (Very basic heuristic if not scraped)
            if not self.odds_over_2_5:
                # Heuristic: Lower draw odds => tighter game => Under favored
                # Higher favorite odds => more goals => Over favored
                base_over = 1.90
                base_under = 1.90
                if o1 < 1.5 or o2 < 1.5: # Strong favorite
                    base_over = 1.60
                    base_under = 2.20
                elif ox < 3.0: # Likely draw/tight
                    base_over = 2.10
                    base_under = 1.65
                
                self.odds_over_2_5 = Decimal(base_over)
                self.odds_under_2_5 = Decimal(base_under)

            # --- 3. Handicap ---
            # Try to find from scraped markets
            handicap_market = self.markets.filter(name__icontains="Handicap").first()
            if handicap_market:
                # Look for standard 1.5 handicap
                h_home = handicap_market.outcomes.filter(name__icontains=f"{self.home_team.name} (-1.5)").first()
                h_away = handicap_market.outcomes.filter(name__icontains=f"{self.away_team.name} (1.5)").first()
                
                if h_home: self.odds_handicap_home = h_home.odds
                if h_away: self.odds_handicap_away = h_away.odds
            
            # Fallback Estimation
            if not self.odds_handicap_home:
                # Rough estimation: Handicap -1.5 is usually much higher than Win
                self.odds_handicap_home = Decimal(o1 * 2.5) if o1 < 2.0 else Decimal(o1 * 3.5)
                self.odds_handicap_away = Decimal(1.2) if o1 < 2.0 else Decimal(1.5) # Favorite implies underdog handicap is low odds

            # --- 4. Both Teams To Score (BTTS) ---
            btts_market = self.markets.filter(name__icontains="Both To Score").first()
            if btts_market:
                yes = btts_market.outcomes.filter(name__icontains="Yes").first()
                no = btts_market.outcomes.filter(name__icontains="No").first()
                if yes: self.odds_btts_yes = yes.odds
                if no: self.odds_btts_no = no.odds
            
            # Fallback Estimation
            if not self.odds_btts_yes:
                # Heuristic: Balanced games (high draw prob) -> Higher chance of BTTS
                if ox < 3.2:
                    self.odds_btts_yes = Decimal(1.75)
                    self.odds_btts_no = Decimal(2.05)
                else:
                    self.odds_btts_yes = Decimal(2.00)
                    self.odds_btts_no = Decimal(1.75)

            self.save()
            
        except (ValueError, ZeroDivisionError, TypeError):
            pass


class Odds(models.Model):
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
            prob = (1/o1) + (1/ox)
            return round(Decimal(1 / prob * 0.95), 2)
        except: return Decimal('1.20') # Default fallback

    def get_12_odds(self):
        try:
            o1 = float(self.home_odds)
            o2 = float(self.away_odds)
            prob = (1/o1) + (1/o2)
            return round(Decimal(1 / prob * 0.95), 2)
        except: return Decimal('1.20') # Default fallback

    def get_x2_odds(self):
        try:
            ox = float(self.draw_odds)
            o2 = float(self.away_odds)
            prob = (1/ox) + (1/o2)
            return round(Decimal(1 / prob * 0.95), 2)
        except: return Decimal('1.30') # Default fallback
        
    def get_margin(self):
        try:
            o1 = float(self.home_odds)
            ox = float(self.draw_odds)
            o2 = float(self.away_odds)
            margin = ((1/o1 + 1/ox + 1/o2) - 1) * 100
            return round(margin, 1)
        except: return None


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
    BET_TYPE_HOME = 'home'
    BET_TYPE_DRAW = 'draw'
    BET_TYPE_AWAY = 'away'
    
    # New Bet Types
    BET_TYPE_1X = '1x'
    BET_TYPE_12 = '12'
    BET_TYPE_X2 = 'x2'
    BET_TYPE_OVER_25 = 'over_25'
    BET_TYPE_UNDER_25 = 'under_25'
    BET_TYPE_HANDICAP_HOME = 'handicap_home'
    BET_TYPE_HANDICAP_AWAY = 'handicap_away'
    BET_TYPE_BTTS_YES = 'btts_yes'
    BET_TYPE_BTTS_NO = 'btts_no'

    BET_TYPE_CHOICES = [
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
    ]

    STATUS_PENDING = 'pending'
    STATUS_WON = 'won'
    STATUS_LOST = 'lost'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_WON, 'Won'),
        (STATUS_LOST, 'Lost'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bets')
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='bets')
    bet_type = models.CharField(max_length=20, choices=BET_TYPE_CHOICES) # Increased max_length
    odds = models.DecimalField(max_digits=6, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    potential_payout = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.match} - {self.amount} on {self.bet_type}"


class ExpressBet(models.Model):
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

    def __str__(self):
        return f"Express Bet - {self.user.username} - {self.amount}"


class ExpressBetSelection(models.Model):
    express_bet = models.ForeignKey(ExpressBet, on_delete=models.CASCADE, related_name='selections')
    match = models.ForeignKey(Match, on_delete=models.CASCADE)
    bet_type = models.CharField(max_length=20)
    odds = models.DecimalField(max_digits=6, decimal_places=2)
    status = models.CharField(max_length=10, default='pending') # pending, won, lost

    def __str__(self):
        return f"{self.match} - {self.bet_type}"
