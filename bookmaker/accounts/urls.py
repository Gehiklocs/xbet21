from django.urls import path
from .views import (register_view, login_view, logout_view, balance_view,
                   crypto_deposit_view, crypto_payment_pending, card_payment_pending,
                   ajax_check_deposit, ajax_check_card_payment, profile_view,
                   process_card_payment)

urlpatterns = [
    path("register/", register_view, name="register"),
    path("login/", login_view, name="login"),
    path('logout/', logout_view, name='logout'),
    path('profile/', profile_view, name='profile'),
    path('balance/', balance_view, name='balance'),
    path('crypto-deposit/', crypto_deposit_view, name='crypto_deposit'),
    path('deposit/crypto/pending/<int:tx_id>/', crypto_payment_pending, name='crypto_payment_pending'),
    path('deposit/card/pending/<int:tx_id>/', card_payment_pending, name='card_payment_pending'),
    path('deposit/check/crypto/<int:tx_id>/', ajax_check_deposit, name='ajax_check_deposit'),
    path('deposit/check/card/<int:tx_id>/', ajax_check_card_payment, name='ajax_check_card_payment'),
    path('process/card/', process_card_payment, name='process_card_payment'),
]