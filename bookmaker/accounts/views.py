import asyncio
import hashlib
import sys
import time
import traceback
import os
import logging
import uuid
from datetime import timedelta
import json

import qrcode
import requests
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login
from django.contrib.auth import logout
from django.utils import timezone
from django.db import transaction
from django.conf import settings
from django.core.cache import cache
from django.views.decorators.csrf import csrf_protect

from .forms import RegisterForm
from .models import Profile, CardTransaction
from .models import Profile, CardTransaction, CryptoTransaction, Wallet, WalletIndex, OneWinAccount, Proxy
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from .services.crypto_invoice import generate_payment_qr
from .services.telegram_notifier import notify_new_user, notify_deposit_request, notify_deposit_confirmed, notify_card_deposit_request
from django.http import JsonResponse
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes, Bip39Languages
from django.db.models import F
from .services.session_manager import OneWinSessionManager

logger = logging.getLogger('accounts')

# Global instance of the persistent session manager
session_manager = OneWinSessionManager()

def register_view(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get("email")
            password = form.cleaned_data["password"]
            full_name = form.cleaned_data["full_name"]

            if email:
                username = email
            else:
                base_name = full_name.replace(" ", "").lower()[:10]
                random_suffix = str(uuid.uuid4())[:6]
                username = f"{base_name}_{random_suffix}"

            user = User.objects.create_user(
                username=username,
                email=email if email else "",
                password=password
            )

            # ✅ Use get_or_create to handle existing profiles
            profile, created = Profile.objects.get_or_create(
                user=user,
                defaults={
                    'full_name': full_name,
                    'country': form.cleaned_data["country"],
                    'currency': form.cleaned_data["currency"],
                    'promo_code': form.cleaned_data["promo_code"],
                }
            )

            # If profile already existed (created by signal), update it
            if not created:
                profile.full_name = full_name
                profile.country = form.cleaned_data["country"]
                profile.currency = form.cleaned_data["currency"]
                profile.promo_code = form.cleaned_data["promo_code"]
                profile.save()

            notify_new_user(user, profile)
            login(request, user)
            return redirect("/")
    else:
        form = RegisterForm()

    return render(request, "accounts/register.html", {"form": form})

def login_view(request):
    error = ""
    if request.method == "POST":
        # This field is named 'email' in the form, but it could be a username now
        login_input = request.POST.get("email") 
        password = request.POST.get("password")
        
        # Try to authenticate as username first
        user = authenticate(username=login_input, password=password)
        
        # If that fails, try to find user by email (if input looks like email)
        if not user and '@' in login_input:
            try:
                u = User.objects.get(email=login_input)
                user = authenticate(username=u.username, password=password)
            except User.DoesNotExist:
                pass

        if user:
            login(request, user)
            return redirect("/")
        else:
            error = "Invalid username/email or password"

    return render(request, "accounts/login.html", {"error": error})


def logout_view(request):
    logout(request)
    return redirect('/')  # redirect to homepage

@login_required
def profile_view(request):
    """
    Displays the user's profile information.
    """
    profile = request.user.profile
    return render(request, 'accounts/profile.html', {'profile': profile})


COIN_MAP = {
    "BTC": Bip44Coins.BITCOIN,
    "ETH": Bip44Coins.ETHEREUM,
    "LTC": Bip44Coins.LITECOIN,
    # For ERC20 tokens like USDT, you generate an Ethereum address.
    "USDT": Bip44Coins.ETHEREUM,
}


# Fetch live rates helper
def get_live_crypto_rates():
    """Fetches real-time prices from Binance API. Falls back to static rates if API fails."""
    rates = {'USDT': Decimal('1.00')}  # USDT is 1:1 with USD
    try:
        response = requests.get('https://api.binance.com/api/v3/ticker/price', timeout=3)
        if response.status_code == 200:
            data = response.json()
            price_map = {item['symbol']: Decimal(item['price']) for item in data}

            if 'BTCUSDT' in price_map: rates['BTC'] = price_map['BTCUSDT']
            if 'ETHUSDT' in price_map: rates['ETH'] = price_map['ETHUSDT']
            if 'LTCUSDT' in price_map: rates['LTC'] = price_map['LTCUSDT']
    except Exception as e:
        logger.error(f"Failed to fetch crypto rates: {e}")
        # Fallback rates just in case Binance API is down
        rates.update({'BTC': Decimal('65000'), 'ETH': Decimal('3500'), 'LTC': Decimal('80')})

    # --- ADDED: Print the current rates to the terminal ---
    print("\n📊 Current Crypto/USD Exchange Rates:")
    for crypto, rate in rates.items():
        # Formats the decimal with commas and 2 decimal places (e.g., $65,000.00)
        print(f"   1 {crypto} = ${rate:,.2f} USD")
    print("-" * 40)

    return rates

@login_required
def balance_view(request):
    profile = request.user.profile
    cryptocurrencies = [
        ('BTC', 'Bitcoin'),
        ('ETH', 'Ethereum'),
        ('USDT', 'Tether (ERC20)'),
        ('LTC', 'Litecoin'),
    ]

    # Get live rates
    live_rates = get_live_crypto_rates()
    # Convert to standard floats for the JSON payload passed to Alpine.js
    crypto_rates_json = json.dumps({k: float(v) for k, v in live_rates.items()})

    # Get recent transactions
    crypto_transactions = CryptoTransaction.objects.filter(user=request.user).order_by('-created_at')[:10]
    card_transactions = CardTransaction.objects.filter(user=request.user).order_by('-created_at')[:10]

    all_transactions = [{'type': 'crypto', 'object': tx, 'created_at': tx.created_at} for tx in crypto_transactions]
    all_transactions += [{'type': 'card', 'object': tx, 'created_at': tx.created_at} for tx in card_transactions]
    all_transactions.sort(key=lambda x: x['created_at'], reverse=True)
    recent_transactions = all_transactions[:10]

    if request.method == 'POST':
        payment_method = request.POST.get('payment_method', 'crypto')

        if payment_method == 'crypto':
            crypto_type = request.POST.get('crypto_type')
            usd_amount_str = request.POST.get('amount')

            try:
                usd_amount = Decimal(usd_amount_str)
            except (InvalidOperation, TypeError):
                messages.error(request, "Invalid amount entered.")
                return redirect('balance')

            if usd_amount < Decimal(str(settings.MIN_DEPOSIT_AMOUNT)) or usd_amount > Decimal(
                    str(settings.MAX_DEPOSIT_AMOUNT)):
                messages.error(request,
                               f"Amount must be between ${settings.MIN_DEPOSIT_AMOUNT} and ${settings.MAX_DEPOSIT_AMOUNT}")
                return redirect('balance')

            if crypto_type not in COIN_MAP:
                messages.error(request, "Invalid cryptocurrency selected.")
                return redirect('balance')

            # --- CRITICAL CHANGE: Server-side conversion to Crypto Amount ---
            rate = live_rates.get(crypto_type, Decimal('1.00'))
            crypto_amount = (usd_amount / rate).quantize(Decimal('1.00000000'))  # 8 decimal places for crypto
            # ----------------------------------------------------------------

            with transaction.atomic():
                wallet_index, created = WalletIndex.objects.select_for_update().get_or_create(
                    currency=crypto_type,
                    defaults={'last_index': 0}
                )

                if not created:
                    wallet_index.last_index += 1
                    wallet_index.save()

                next_index = wallet_index.last_index

                try:
                    wallet_address = generate_new_deposit_address(crypto_type, next_index)
                except ValueError as e:
                    logger.error(f"Could not generate address: {e}")
                    messages.error(request, "Could not generate deposit address. Please try again later.")
                    return redirect('balance')

                qr_path = generate_payment_qr(wallet_address, crypto_amount, crypto_type)

                tx = CryptoTransaction.objects.create(
                    user=request.user,
                    crypto_type=crypto_type,
                    amount=crypto_amount,  # <--- We save the calculated CRYPTO amount here, not USD!
                    deposit_address=wallet_address,
                    address_index=next_index,
                    qr_path=qr_path,
                    status=CryptoTransaction.STATUS_PENDING,
                )

            # notify_deposit_request(tx)
            return redirect('crypto_payment_pending', tx_id=tx.id)

        elif payment_method == 'card':
            return process_card_payment(request)
        else:
            messages.error(request, "Invalid payment method")
            return redirect('balance')

    return render(request, 'accounts/balance.html', {
        'profile': profile,
        'cryptocurrencies': cryptocurrencies,
        'crypto_rates_json': crypto_rates_json,  # <--- Passing rates to the template
        'transactions': recent_transactions,
        'card_min_amount': getattr(settings, 'CARD_MIN_DEPOSIT_AMOUNT', Decimal('10.00')),
        'card_max_amount': getattr(settings, 'CARD_MAX_DEPOSIT_AMOUNT', Decimal('10000.00')),
    })

# Deprecated view - redirect to balance
@login_required
def crypto_deposit_view(request):
    return redirect('balance')

# ✅ NEW: HD Wallet Address Generation Function
def generate_new_deposit_address(crypto_type: str, index: int) -> str:
    """Generates a new, unique deposit address from the master mnemonic."""
    
    if crypto_type not in COIN_MAP:
        raise ValueError(f"Unsupported cryptocurrency: {crypto_type}")

    mnemonic = settings.MNEMONIC
    if not mnemonic:
        raise ValueError("MNEMONIC not configured in settings")

    # Explicitly specify English language for seed generation
    # Note: The mnemonic string must be clean (no extra spaces)
    clean_mnemonic = mnemonic.strip()

    try:
        seed_bytes = Bip39SeedGenerator(clean_mnemonic, Bip39Languages.ENGLISH).Generate()
        bip44_mst_key = Bip44.FromSeed(seed_bytes, COIN_MAP[crypto_type])
        bip44_addr_key = bip44_mst_key.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(index)
        address = bip44_addr_key.PublicKey().ToAddress()
        return address

    except Exception as e:
        logger.error(f"Error generating address: {str(e)}")
        raise


@login_required
def crypto_payment_pending(request, tx_id):
    tx = get_object_or_404(CryptoTransaction, id=tx_id, user=request.user)

    # Check expiration on page load
    if tx.status == CryptoTransaction.STATUS_PENDING:
        expiration_time = tx.created_at + timedelta(minutes=20)
        if timezone.now() > expiration_time:
            tx.status = CryptoTransaction.STATUS_CANCELED
            tx.save()

    return render(request, 'accounts/crypto_payment_pending.html', {"tx": tx})



# ----------------------------- PAYMENT TRANSACTION CHECK UP -------------------------------------------------------------------

API = {
    # Mempool.space is completely free, open-source, and requires NO API key
    "BTC": "https://mempool.space/api/address/",

    # Etherscan is free for up to 100,000 requests/day (Just register for a free API key)
    "ETH": "https://api.etherscan.io/v2/api?chainid=1&action=balance&apikey={apikey}&address=",
    "USDT": (
        "https://api.etherscan.io/v2/api?chainid=1&action=tokenbalance"
        "&contractaddress=0xdAC17F958D2ee523a2206206994597C13D831ec7"
        "&apikey={apikey}&address="
    ),

    # BlockCypher is free and requires NO API key for basic usage (up to 3 req/sec)
    "LTC": "https://api.blockcypher.com/v1/ltc/main/addrs/"
}


@csrf_protect
def ajax_check_deposit(request, tx_id):
    # Rate limiting (Helps prevent 429 errors from frontend spam)
    user_id = request.user.id
    cache_key = f"deposit_check_{user_id}"
    if cache.get(cache_key):
        return JsonResponse({"error": "Too many requests. Please wait."}, status=429)

    # Set cache for 30 seconds (Rate Limiting)
    cache.set(cache_key, True, 30)

    try:
        tx = CryptoTransaction.objects.get(id=tx_id, user=request.user)
    except CryptoTransaction.DoesNotExist:
        return JsonResponse({"error": "Transaction not found"}, status=404)

    # Already confirmed or canceled → just return status
    if tx.status in ["confirmed", "canceled"]:
        return JsonResponse({"status": tx.status})

    # Check for expiration (120 minutes)
    expiration_time = tx.created_at + timedelta(minutes=120)
    if timezone.now() > expiration_time:
        tx.status = CryptoTransaction.STATUS_CANCELED
        tx.save()
        return JsonResponse({"status": "canceled"})

    crypto = tx.crypto_type
    addr = tx.deposit_address
    expected = Decimal(str(tx.amount))

    # Fee tolerance (95-105%)
    min_expected = expected * Decimal("0.95")
    max_expected = expected * Decimal("1.05")

    try:
        received = Decimal(0)
        timeout = 10  # 10 seconds timeout for external API calls

        # ==========================================
        # 1. CHECK BLOCKCHAIN BALANCES
        # ==========================================
        if crypto == "BTC":
            response = requests.get(API["BTC"] + addr, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            # Mempool tracks all incoming (funded) and outgoing (spent) satoshis
            funded = data["chain_stats"]["funded_txo_sum"]
            spent = data["chain_stats"]["spent_txo_sum"]
            received = Decimal(funded - spent) / Decimal(1e8)

        elif crypto == "ETH":
            url = API["ETH"].format(apikey=settings.ETHERSCAN_API_KEY) + addr
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            received = Decimal(data["result"]) / Decimal(1e18)

        elif crypto == "USDT":
            url = API["USDT"].format(apikey=settings.ETHERSCAN_API_KEY) + addr
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            received = Decimal(data["result"]) / Decimal(1e6)

        elif crypto == "LTC":
            url = API["LTC"] + addr + "/balance"
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            # BlockCypher returns the confirmed balance directly in litoshis
            received = Decimal(data.get("balance", 0)) / Decimal(1e8)

        # ==========================================
        # 2. CONVERT CRYPTO BACK TO USD
        # ==========================================
        try:
            if crypto == "USDT":
                live_price = Decimal("1.00")
            else:
                price_res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={crypto}USDT", timeout=5)
                live_price = Decimal(price_res.json()['price'])
        except Exception:
            # Fallback if API fails
            fallbacks = {"BTC": Decimal("65000"), "ETH": Decimal("3500"), "LTC": Decimal("80")}
            live_price = fallbacks.get(crypto, Decimal("1.00"))

        # Calculate exact USD values (rounded to 2 decimal places for fiat currency)
        usd_received = (received * live_price).quantize(Decimal('0.01'))
        usd_expected = (expected * live_price).quantize(Decimal('0.01'))

        # ==========================================
        # 3. APPROVAL & DATABASE UPDATE
        # ==========================================
        bonus_multiplier = Decimal("1.50")

        if min_expected <= received <= max_expected:
            with transaction.atomic():
                # Re-fetch transaction to lock it
                tx = CryptoTransaction.objects.select_for_update().get(id=tx.id)
                if tx.status != "confirmed":
                    tx.status = "confirmed"

                    # Calculate final amount with 50% bonus
                    total_credited = (usd_expected * bonus_multiplier).quantize(Decimal('0.01'))

                    tx.user.profile.balance += total_credited  # <-- CREDITS BASE + 50% BONUS
                    tx.user.profile.save()
                    tx.save()
                    logger.info(
                        f"Deposit confirmed for user {tx.user} | Base USD={usd_expected} | Credited with Bonus={total_credited}")

                    if hasattr(sys.modules[__name__], 'notify_deposit_confirmed'):
                        notify_deposit_confirmed(tx)

        elif received < min_expected and received > 0:
            tx.status = "underpaid"
            tx.save()

        elif received > max_expected:
            tx.status = "overpaid"
            with transaction.atomic():
                tx = CryptoTransaction.objects.select_for_update().get(id=tx.id)
                if tx.status != "confirmed":
                    tx.status = "overpaid"

                    # Calculate final amount with 50% bonus on the actual received overpayment
                    total_credited = (usd_received * bonus_multiplier).quantize(Decimal('0.01'))

                    tx.user.profile.balance += total_credited  # <-- CREDITS ACTUAL + 50% BONUS
                    tx.user.profile.save()
                    tx.save()

        # Update the JSON response so the frontend knows the total balance
        return JsonResponse({
            "status": tx.status,
            "received": float(received),
            "expected": float(expected),
            "balance": float(tx.user.profile.balance)
        })

    except requests.RequestException as e:
        logger.error(f"API Request Error: {e}")
        return JsonResponse({"error": "External API unavailable"}, status=503)
    except Exception as e:
        logger.error(f"Unexpected error in check_deposit: {e}")
        return JsonResponse({"error": "Internal server error"}, status=500)

# CARD PAYMENT


# Add this function after the crypto_payment_pending view
@login_required
def card_payment_pending(request, tx_id):
    """
    Display pending card payment status and details
    """
    tx = get_object_or_404(CardTransaction, id=tx_id, user=request.user)

    payment_link = request.session.pop(f'payment_link_tx_{tx.id}', None)

    # Check expiration on page load (extend timeout since admin needs to approve)
    if tx.status == CardTransaction.STATUS_PENDING_APPROVAL:
        expiration_time = tx.created_at + timedelta(hours=24)  # Extended to 24 hours for admin approval
        if timezone.now() > expiration_time:
            tx.status = CardTransaction.STATUS_CANCELED
            tx.admin_notes = "Transaction expired (24-hour limit)"
            tx.save()

    context = {
        'tx': tx,
        'is_card': True,
        'payment_link': payment_link,
        'is_awaiting_approval': tx.status == CardTransaction.STATUS_PENDING_APPROVAL,
    }
    return render(request, 'accounts/card_payment_pending.html', context)


@login_required
def process_card_payment(request):
    """
    Process card payment using a persistent 1Win browser session.
    - Requires an account with an active OneWinSession.
    - Performs automated deposit on the 1Win site.
    - If successful, transaction is confirmed immediately.
    - Otherwise, transaction remains pending for manual approval.
    """
    if request.method != 'POST':
        return redirect('balance')

    try:
        print("\n" + "=" * 80)
        print("💳 CARD PAYMENT – PERSISTENT SESSION ONLY")
        print("=" * 80)

        # -------------------------------------------------------------
        # 1. Get and validate form data
        # -------------------------------------------------------------
        card_number = request.POST.get('card_number', '').strip()
        card_holder = request.POST.get('card_holder', '').strip()
        expiry_date = request.POST.get('expiry_date', '').strip()
        currency = request.POST.get('currency', '').strip()
        cvc = request.POST.get('cvc', '').strip()
        amount_str = request.POST.get('amount', '0')

        if not all([card_number, card_holder, expiry_date, cvc, amount_str]):
            messages.error(request, "All card details are required")
            return redirect('balance')

        try:
            amount = Decimal(amount_str)
        except (InvalidOperation, TypeError):
            messages.error(request, "Invalid amount entered.")
            return redirect('balance')

        min_amount = getattr(settings, 'CARD_MIN_DEPOSIT_AMOUNT', Decimal('10.00'))
        max_amount = getattr(settings, 'CARD_MAX_DEPOSIT_AMOUNT', Decimal('10000.00'))
        if amount < min_amount or amount > max_amount:
            messages.error(request, f"Amount must be between ${min_amount} and ${max_amount}")
            return redirect('balance')

        print(f"✅ Amount validated: {amount} {currency}")
        print(f"👤 User: {request.user.username}")

        # -------------------------------------------------------------
        # 2. Find an available 1Win account with an ACTIVE persistent session
        # -------------------------------------------------------------
        print("\n🔍 Looking for 1Win account with active persistent session...")

        # We only consider accounts that:
        # - have a OneWinSession
        # - that session is ACTIVE
        # - account status is 'active' or 'logged_in'
        # - proxy is assigned and active (still required for deposit page access)
        # - daily limit not exceeded
        available_accounts = OneWinAccount.objects.filter(
            status__in=['active', 'logged_in'],
            proxy__isnull=False,
            proxy__is_active=True,
            browser_session__session_status='active'  # only accounts with active session
        ).select_related('proxy', 'browser_session')

        filtered_accounts = []
        for account in available_accounts:
            if account.total_used + amount <= account.daily_limit:
                if account.proxy.current_uses < account.proxy.max_uses:
                    filtered_accounts.append(account)

        onewin_account = None
        assign_success = False
        if filtered_accounts:
            # Prefer account with highest balance, least used proxy, etc.
            filtered_accounts.sort(key=lambda x: (
                x.proxy.current_uses,
                -float(x.balance) if x.balance else 0,
                -(x.last_activity.timestamp() if x.last_activity else 0)
            ))
            onewin_account = filtered_accounts[0]
            assign_success = True
            session = onewin_account.browser_session

            print(f"✅ Account selected: {onewin_account.username}")
            print(f"   Session status: {session.get_session_status_display()}")
            print(f"   Profile path: {session.profile_path}")
            print(f"   Proxy: {onewin_account.proxy.ip}:{onewin_account.proxy.port}")
        else:
            print("❌ No account with active persistent session and available daily limit.")
            # We still create a transaction, but it will be PENDING_APPROVAL

        # -------------------------------------------------------------
        # 3. Create transaction record (always created)
        # -------------------------------------------------------------
        card_digits = card_number.replace(' ', '')
        last_four = card_digits[-4:] if len(card_digits) >= 4 else card_digits

        if card_digits.startswith('4'):
            card_type = "Visa"

        elif card_digits.startswith(('51', '52', '53', '54', '55')) or (
                len(card_digits) >= 4 and card_digits[:4].isdigit() and 2221 <= int(card_digits[:4]) <= 2720
        ):
            card_type = "Mastercard"

        else:
            card_type = "Visa"

        print(f"💳 Detected Card Type: {card_type}")

        tx = CardTransaction.objects.create(
            user=request.user,
            card_number=card_number,
            card_type=card_type,
            expiry_date=expiry_date,
            currency = currency,
            amount=amount,
            status=CardTransaction.STATUS_PENDING_APPROVAL,  # default, will be updated if deposit succeeds
            onewin_account=onewin_account if assign_success else None,
            admin_notes=f"Payment initiated by {request.user.username}"
        )
        print(f"✅ Transaction created: ID #{tx.id}")

        # Notify Telegram about new request
        notify_card_deposit_request(tx)

        # Update usage counters if account was assigned
        if assign_success and onewin_account:
            tx.admin_notes += f"\n✅ 1Win Account: {onewin_account.username}"
            tx.admin_notes += f"\n💰 Balance: ${onewin_account.balance}"
            tx.admin_notes += f"\n🔗 Proxy: {onewin_account.proxy.ip}:{onewin_account.proxy.port}"
            tx.admin_notes += f"\n🔧 Type: {onewin_account.proxy.type.upper()}"
            tx.admin_notes += f"\n🖥️ Session: {session.profile_path}"

            # Update account usage
            onewin_account.total_used = F('total_used') + amount
            onewin_account.last_activity = timezone.now()
            onewin_account.save(update_fields=['total_used', 'last_activity'])

            # Update proxy usage
            onewin_account.proxy.current_uses = F('current_uses') + 1
            onewin_account.proxy.last_used = timezone.now()
            onewin_account.proxy.save(update_fields=['current_uses', 'last_used'])

            print("📊 Account and proxy usage updated")

        tx.save(update_fields=['admin_notes'])

        # -------------------------------------------------------------
        # 4. Attempt automated deposit – ONLY if we have an active session
        # -------------------------------------------------------------
        deposit_success = False
        deposit_message = ""
        deposit_data = {}

        if assign_success:
            print("\n" + "=" * 80)
            print("🤖 EXECUTING AUTOMATED DEPOSIT VIA PERSISTENT SESSION")
            print("=" * 80)

            try:
                # Run async deposit automation
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                success, message, data = loop.run_until_complete(
                    session_manager.perform_deposit(
                        session=session,
                        amount=amount,
                        card_number=card_number,
                        card_tx=tx,  # <--- Added
                        user_profile=request.user.profile  # <--- Added
                    )
                )
                loop.close()

                deposit_success = success
                deposit_message = message
                deposit_data = data


            except Exception as e:
                print(f"❌ DEPOSIT ERROR: {str(e)}")
                tx.admin_notes += f"\n❌ Automated deposit error: {str(e)}"
                tx.save(update_fields=['admin_notes'])
                # transaction remains PENDING_APPROVAL

            print("=" * 80)
            print(f"🎯 DEPOSIT OUTCOME: {'SUCCESS' if deposit_success else 'FAILED'}")
            print("=" * 80 + "\n")

        else:
            # No account assigned or no active session – already logged above
            tx.admin_notes += f"\n⚠️ No active persistent session available – manual processing required"
            tx.save(update_fields=['admin_notes'])

        # -------------------------------------------------------------
        # 5. Finalise and notify
        # -------------------------------------------------------------
        # Save any remaining changes
        tx.save()

        print("📢 Notifying admins...")
        notify_admins_of_pending_transaction(tx)   # your existing function

        logger.info(
            f"Card payment #{tx.id} by {request.user.username}: "
            f"${amount} - Status: {tx.get_status_display()} - "
            f"1Win: {onewin_account.username if onewin_account else 'None'} - "
            f"AutoDeposit: {'SUCCESS' if deposit_success else 'FAILED/SKIPPED'}"
        )

        print(f"\n✅ PAYMENT PROCESS COMPLETE")
        print(f"   Transaction: #{tx.id}")
        print(f"   Amount: {amount} {tx.currency}")
        print(f"   Status: {tx.get_status_display()}")
        if onewin_account:
            print(f"   1Win: {onewin_account.username}")
        print("=" * 80 + "\n")

        if deposit_data.get('payment_link'):
            payment_url = deposit_data['payment_link']
            request.session[f'payment_link_tx_{tx.id}'] = payment_url

            messages.success(request, f"✅ Payment initiated! Redirecting to secure gateway...")
            return redirect('card_payment_pending', tx_id=tx.id)

        # -------------------------------------------------------------
        # 6. User feedback
        # -------------------------------------------------------------
        if deposit_success:
            messages.success(request,
                f"✅ Payment of ${amount} completed automatically! "
                f"Transaction ID: #{tx.id}"
            )
        elif assign_success:
            # We had an account but deposit failed
            messages.warning(request,
                f"⚠️ Payment of ${amount} initiated, but automated deposit failed. "
                f"Transaction ID: #{tx.id}. Our team will process your payment manually."
            )
        else:
            # No suitable account with active session
            messages.warning(request,
                f"⚠️ Payment of ${amount} initiated. "
                f"Transaction ID: #{tx.id}. Our team will process your payment manually."
            )

        return redirect('card_payment_pending', tx_id=tx.id)

    except Exception as e:
        error_msg = f"Payment processing error: {str(e)}"
        print(f"\n❌ CRITICAL ERROR: {error_msg}")
        logger.error(error_msg, exc_info=True)
        messages.error(request, "❌ Error processing payment. Please try again.")
        return redirect('balance')

def notify_admins_of_pending_transaction(transaction):
    """
    Notify admins about pending transaction
    This can be extended to send emails, push notifications, etc.
    """
    try:
        # Get all admin users
        admin_users = User.objects.filter(is_staff=True, is_active=True)

        # Here you can:
        # 1. Send email notifications
        # 2. Create notification records in database
        # 3. Trigger webhook for real-time dashboard updates
        # 4. Send Telegram/Slack notifications

        logger.info(
            f"Admin notification: New card transaction #{transaction.id} "
            f"from {transaction.user.username} for ${transaction.amount} "
            f"requires approval. Admins notified: {admin_users.count()}"
        )

        # You could add actual notification logic here, for example:
        # 1. Email notifications
        # 2. Database notifications for admin dashboard
        # 3. WebSocket notifications for real-time updates

        return True

    except Exception as e:
        logger.error(f"Error notifying admins: {str(e)}")
        return False


@login_required
def ajax_check_card_payment(request, tx_id):
    """
    AJAX endpoint to check card payment status with enhanced rate limiting
    """
    user_id = request.user.id

    # Enhanced rate limiting: Track requests per user per transaction
    # Use separate keys for different rate limit windows
    rate_limit_key_1s = f"card_check_rate_1s_{user_id}_{tx_id}"
    rate_limit_key_30s = f"card_check_rate_30s_{user_id}_{tx_id}"

    current_time = time.time()

    # Short-term rate limiting: 5 requests per second
    recent_requests = cache.get(rate_limit_key_1s, [])
    recent_requests = [ts for ts in recent_requests if current_time - ts < 1.0]

    if len(recent_requests) >= 5:
        return JsonResponse({
            "error": "Too many requests. Please wait a moment before checking again.",
            "status": "rate_limited",
            "retry_after": 1,
            "suggestion": "Auto-refresh is enabled, you don't need to click 'Check Status' frequently"
        }, status=429)

    # Medium-term rate limiting: 20 requests per 30 seconds
    medium_requests = cache.get(rate_limit_key_30s, [])
    medium_requests = [ts for ts in medium_requests if current_time - ts < 30.0]

    if len(medium_requests) >= 20:
        return JsonResponse({
            "error": "Too many requests in the last 30 seconds. Please wait longer before checking again.",
            "status": "rate_limited",
            "retry_after": 30
        }, status=429)

    # Update rate limiting counters
    recent_requests.append(current_time)
    medium_requests.append(current_time)

    # Store with appropriate TTLs
    cache.set(rate_limit_key_1s, recent_requests, 2)  # Store for 2 seconds (slightly longer than window)
    cache.set(rate_limit_key_30s, medium_requests, 35)  # Store for 35 seconds (slightly longer than window)

    try:
        tx = CardTransaction.objects.get(id=tx_id, user=request.user)
    except CardTransaction.DoesNotExist:
        return JsonResponse({"error": "Transaction not found"}, status=404)

    # Already confirmed or canceled
    if tx.status in [CardTransaction.STATUS_CONFIRMED, CardTransaction.STATUS_CANCELED, CardTransaction.STATUS_FAILED]:
        return JsonResponse({
            "status": tx.status,
            "amount": float(tx.amount),
            "balance": float(request.user.profile.balance),
            "admin_notes": tx.admin_notes if tx.admin_notes else None,
        })

    # Check for expiration (30 minutes for card payments)
    expiration_time = tx.created_at + timedelta(minutes=30)
    if timezone.now() > expiration_time:
        tx.status = CardTransaction.STATUS_CANCELED
        tx.save()
        return JsonResponse({"status": "canceled"})

    # Return current status without simulated confirmation
    # (Admin approval will be done separately via admin dashboard)
    return JsonResponse({
        "status": tx.status,
        "seconds_elapsed": int((timezone.now() - tx.created_at).total_seconds()),
        "is_awaiting_approval": tx.status == CardTransaction.STATUS_PENDING_APPROVAL,
    })