from django.urls import path
from . import views

urlpatterns = [
    path('', views.matches_dashboard, name='dashboard'),
    path('match/<int:match_id>/', views.match_detail, name='match_detail'),
    path('match/<int:match_id>/bet/', views.place_bet, name='place_bet'),
    path('api/place-express-bet/', views.place_express_bet, name='place_express_bet'),
    path('my-bets/', views.my_bets, name='my_bets'),
    path('teams/', views.teams_list, name='teams_list'),
]
