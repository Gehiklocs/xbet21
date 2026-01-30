from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_home, name='admin_home'),
    path('matches/', views.matches_list, name='admin_matches'),
    path('bets/', views.bets_list, name='admin_bets'),
    path('express-bets/', views.express_bets_list, name='admin_express_bets'),
    path('users/', views.users_list, name='admin_users'),
    path('users/<int:user_id>/edit/', views.user_edit, name='admin_user_edit'),
    path('transactions/', views.transactions_list, name='admin_transactions'),
    path('scraper/', views.scraper_control, name='admin_scraper'),
]
