from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.shortcuts import redirect
from django_q.models import Schedule
from django_q.tasks import async_task
from .models import Match, Team, Bookmaker, Odds, Bet, Market, Outcome

# --- Custom Schedule Admin ---
# Unregister the default Schedule admin to replace it
try:
    admin.site.unregister(Schedule)
except admin.sites.NotRegistered:
    pass

@admin.register(Schedule)
class CustomScheduleAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'func',
        'schedule_type',
        'repeats',
        'next_run',
        'last_run',
        'success',
        'run_now_button'
    )
    list_filter = ('next_run', 'schedule_type', 'cluster')
    search_fields = ('name', 'func')
    ordering = ('next_run',)

    def run_now_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Run Now</a>',
            reverse('admin:run_schedule_now', args=[obj.pk])
        )
    run_now_button.short_description = 'Actions'
    run_now_button.allow_tags = True

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:pk>/run_now/',
                self.admin_site.admin_view(self.run_now_view),
                name='run_schedule_now',
            ),
        ]
        return custom_urls + urls

    def run_now_view(self, request, pk):
        schedule = Schedule.objects.get(pk=pk)
        async_task(schedule.func, *schedule.args, **schedule.kwargs)
        self.message_user(request, f"Task '{schedule.name}' has been queued for execution.")
        return redirect('admin:django_q_schedule_changelist')


# --- Existing Admin Classes ---

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'logo_preview')
    search_fields = ('name',)

    def logo_preview(self, obj):
        if obj.logo_url:
            return format_html('<img src="{}" width="30" height="30" style="object-fit: contain;" />', obj.logo_url)
        return "-"
    logo_preview.short_description = "Logo"

@admin.register(Bookmaker)
class BookmakerAdmin(admin.ModelAdmin):
    list_display = ('name', 'website')
    search_fields = ('name',)

class OddsInline(admin.TabularInline):
    model = Odds
    extra = 0
    readonly_fields = ('timestamp',)

class OutcomeInline(admin.TabularInline):
    model = Outcome
    extra = 0

@admin.register(Market)
class MarketAdmin(admin.ModelAdmin):
    list_display = ('match', 'name')
    search_fields = ('match__home_team__name', 'match__away_team__name', 'name')
    list_filter = ('name',)
    inlines = [OutcomeInline]

@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'league', 'match_date', 'status', 'winner', 'scraped_at', 'updated_at', 'match_url_link')
    list_filter = ('league', 'status', 'match_date', 'scraped_at')
    search_fields = ('home_team__name', 'away_team__name', 'league')
    inlines = [OddsInline]
    actions = ['mark_as_live', 'mark_as_finished']
    readonly_fields = ('scraped_at', 'updated_at')

    fieldsets = (
        ('Match Info', {
            'fields': ('home_team', 'away_team', 'league', 'match_date', 'status', 'match_url')
        }),
        ('Timestamps', {
            'fields': ('scraped_at', 'updated_at')
        }),
        ('Results', {
            'fields': ('home_score', 'away_score', 'winner')
        }),
        ('Calculated Odds', {
            'fields': (
                ('odds_1x', 'odds_12', 'odds_x2'),
                ('odds_over_2_5', 'odds_under_2_5'),
                ('odds_handicap_home', 'odds_handicap_away'),
                ('odds_btts_yes', 'odds_btts_no'),
            ),
            'classes': ('collapse',),
        }),
    )

    def match_url_link(self, obj):
        if obj.match_url:
            # Displays the clear link text, but makes it clickable
            return format_html('<a href="{}" target="_blank">{}</a>', obj.match_url, obj.match_url)
        return "-"
    match_url_link.short_description = "Match Link"

    def mark_as_live(self, request, queryset):
        queryset.update(status=Match.STATUS_LIVE)
    mark_as_live.short_description = "Mark selected matches as Live"

    def mark_as_finished(self, request, queryset):
        queryset.update(status=Match.STATUS_FINISHED)
    mark_as_finished.short_description = "Mark selected matches as Finished"

@admin.register(Odds)
class OddsAdmin(admin.ModelAdmin):
    list_display = ('match', 'bookmaker', 'home_odds', 'draw_odds', 'away_odds', 'timestamp')
    list_filter = ('bookmaker', 'timestamp')
    search_fields = ('match__home_team__name', 'match__away_team__name')

@admin.register(Bet)
class BetAdmin(admin.ModelAdmin):
    list_display = ('user', 'match', 'bet_type', 'amount', 'odds', 'potential_payout', 'status', 'created_at')
    list_filter = ('status', 'bet_type', 'created_at')
    search_fields = ('user__username', 'match__home_team__name', 'match__away_team__name')
    actions = ['mark_won', 'mark_lost']

    def mark_won(self, request, queryset):
        for bet in queryset:
            if bet.status == Bet.STATUS_PENDING:
                bet.status = Bet.STATUS_WON
                # Credit user balance
                bet.user.profile.balance += bet.potential_payout
                bet.user.profile.save()
                bet.save()
    mark_won.short_description = "Mark selected bets as WON (Payout)"

    def mark_lost(self, request, queryset):
        queryset.update(status=Bet.STATUS_LOST)
    mark_lost.short_description = "Mark selected bets as LOST"
