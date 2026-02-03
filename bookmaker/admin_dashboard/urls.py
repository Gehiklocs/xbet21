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
    
    # Wallet URLs
    path('wallets/', views.wallets_list, name='admin_wallets'),
    path('wallets/add/', views.wallet_edit, name='admin_wallet_add'),
    path('wallets/<int:wallet_id>/edit/', views.wallet_edit, name='admin_wallet_edit'),
    path('wallets/<int:wallet_id>/delete/', views.wallet_delete, name='admin_wallet_delete'),

    path('scraper/', views.scraper_control, name='admin_scraper'),
]
