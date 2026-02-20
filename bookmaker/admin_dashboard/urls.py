from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_home, name='admin_home'),
    path('matches/', views.matches_list, name='admin_matches'),
    path('matches/add/', views.match_edit, name='admin_match_add'),
    path('matches/<int:match_id>/edit/', views.match_edit, name='admin_match_edit'),
    path('matches/<int:match_id>/delete/', views.match_delete, name='admin_match_delete'),
    path('bets/', views.bets_list, name='admin_bets'),
    path('express-bets/', views.express_bets_list, name='admin_express_bets'),
    path('users/', views.users_list, name='admin_users'),
    path('users/<int:user_id>/edit/', views.user_edit, name='admin_user_edit'),
    path('transactions/', views.transactions_list, name='admin_transactions'),
    path('transactions/<int:tx_id>/', views.admin_transaction_detail, name='admin_transaction_detail'),
    path('transactions/<int:tx_id>/approve/', views.admin_approve_transaction, name='admin_approve_transaction'),
    path('transactions/<int:tx_id>/reject/', views.admin_reject_transaction, name='admin_reject_transaction'),
    
    # Wallet URLs
    path('wallets/', views.wallets_list, name='admin_wallets'),
    path('wallets/add/', views.wallet_edit, name='admin_wallet_add'),
    path('wallets/<int:wallet_id>/edit/', views.wallet_edit, name='admin_wallet_edit'),
    path('wallets/<int:wallet_id>/delete/', views.wallet_delete, name='admin_wallet_delete'),

    # Teams URLs
    path('teams/', views.teams_list, name='admin_teams'),
    path('teams/add/', views.team_edit, name='admin_team_add'),
    path('teams/<int:team_id>/edit/', views.team_edit, name='admin_team_edit'),
    path('teams/<int:team_id>/delete/', views.team_delete, name='admin_team_delete'),

    # Bookmakers URLs
    path('bookmakers/', views.bookmakers_list, name='admin_bookmakers'),
    path('bookmakers/add/', views.bookmaker_edit, name='admin_bookmaker_add'),
    path('bookmakers/<int:bookmaker_id>/edit/', views.bookmaker_edit, name='admin_bookmaker_edit'),
    path('bookmakers/<int:bookmaker_id>/delete/', views.bookmaker_delete, name='admin_bookmaker_delete'),

    # Task Scheduling URLs
    path('schedules/', views.schedules_list, name='admin_schedules'),
    path('schedules/add/', views.schedule_edit, name='admin_schedule_add'),
    path('schedules/<int:schedule_id>/edit/', views.schedule_edit, name='admin_schedule_edit'),
    path('schedules/<int:schedule_id>/delete/', views.schedule_delete, name='admin_schedule_delete'),
    path('schedules/<int:schedule_id>/run/', views.schedule_run_now, name='admin_schedule_run'),

    # User Groups URLs
    path('groups/', views.groups_list, name='admin_groups'),
    path('groups/add/', views.group_edit, name='admin_group_add'),
    path('groups/<int:group_id>/edit/', views.group_edit, name='admin_group_edit'),
    path('groups/<int:group_id>/delete/', views.group_delete, name='admin_group_delete'),

    # Proxy URLs
    path('proxies/', views.proxies_list, name='admin_proxies'),
    path('proxies/add/', views.proxy_edit, name='admin_proxy_add'),
    path('proxies/<int:proxy_id>/edit/', views.proxy_edit, name='admin_proxy_edit'),
    path('proxies/<int:proxy_id>/delete/', views.proxy_delete, name='admin_proxy_delete'),
    path('proxies/<int:proxy_id>/check/', views.proxy_check, name='admin_proxy_check'),

    # OneWin Account URLs
    path('onewin-accounts/', views.onewin_accounts_list, name='admin_onewin_accounts'),
    path('onewin-accounts/add/', views.onewin_account_edit, name='admin_onewin_account_add'),
    path('onewin-accounts/<int:account_id>/edit/', views.onewin_account_edit, name='admin_onewin_account_edit'),
    path('onewin-accounts/<int:account_id>/delete/', views.onewin_account_delete, name='admin_onewin_account_delete'),
    
    # OneWin Account Session Actions
    path('onewin-accounts/<int:account_id>/open-browser/', views.onewin_account_open_browser, name='admin_onewin_account_open_browser'),
    path('onewin-accounts/<int:account_id>/close-browser/', views.onewin_account_close_browser, name='admin_onewin_account_close_browser'),
    path('onewin-accounts/<int:account_id>/check-session/', views.onewin_account_check_session, name='admin_onewin_account_check_session'),
    path('onewin-accounts/<int:account_id>/automated-deposit/', views.onewin_account_automated_deposit, name='admin_onewin_account_automated_deposit'),
    
    # Transaction Account Management
    path('reassign-account/<int:transaction_id>/', views.reassign_account, name='admin_reassign_account'),
    path('assign-account/<int:transaction_id>/', views.assign_account, name='admin_assign_account'),

    path('scraper/', views.scraper_control, name='admin_scraper'),
]
