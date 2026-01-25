import hashlib
import time
import traceback
import os
import logging
import uuid
from datetime import timedelta

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
from .models import Profile
from .models import Wallet, CryptoTransaction, WalletIndex
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from .services.crypto_invoice import generate_payment_qr
from .services.telegram_notifier import notify_new_user, notify_deposit_request, notify_deposit_confirmed
from django.http import JsonResponse
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes, Bip39Languages

logger = logging.getLogger('accounts')

def register_view(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get("email")
            password = form.cleaned_data["password"]
            full_name = form.cleaned_data["full_name"]

            # Generate username if email is missing
            if email:
                username = email
            else:
                # Create a unique username based on name or random string
                # e.g. "JohnDoe_1234"
                base_name = full_name.replace(" ", "").lower()[:10]
                random_suffix = str(uuid.uuid4())[:6]
                username = f"{base_name}_{random_suffix}"

            user = User.objects.create_user(
                username=username,
                email=email if email else "",
                password=password
            )

            profile = Profile.objects.create(
                user=user,
                full_name=full_name,
                country=form.cleaned_data["country"],
                currency=form.cleaned_data["currency"],
                promo_code=form.cleaned_data["promo_code"],
            )

            # Notify Telegram
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

@login_required
def balance_view(request):
    profile = request.user.profile
    cryptocurrencies = [
        ('BTC', 'Bitcoin'),
        ('ETH', 'Ethereum'),
        ('USDT', 'Tether (ERC20)'),
        ('LTC', 'Litecoin'),
    ]

    if request.method == 'POST':
        # Check if it's a crypto deposit (has crypto_type)
        if 'crypto_type' in request.POST:
            crypto_type = request.POST.get('crypto_type')
            amount_str = request.POST.get('amount')

            try:
                amount = Decimal(amount_str)
            except (InvalidOperation, TypeError):
                messages.error(request, "Invalid amount entered.")
                return redirect('balance')
            
            # Validate amount limits
            if amount < Decimal(str(settings.MIN_DEPOSIT_AMOUNT)) or amount > Decimal(str(settings.MAX_DEPOSIT_AMOUNT)):
                messages.error(request, f"Amount must be between {settings.MIN_DEPOSIT_AMOUNT} and {settings.MAX_DEPOSIT_AMOUNT}")
                return redirect('balance')

            if crypto_type not in COIN_MAP:
                messages.error(request, "Invalid cryptocurrency selected.")
                return redirect('balance')

            # Use transaction.atomic to ensure thread safety when getting/updating the index
            with transaction.atomic():
                # Get or create the index tracker for this currency
                wallet_index, created = WalletIndex.objects.select_for_update().get_or_create(
                    currency=crypto_type,
                    defaults={'last_index': 0}
                )
                
                # If it's not a new record, increment the index
                if not created:
                    wallet_index.last_index += 1
                    wallet_index.save()
                
                next_index = wallet_index.last_index

                try:
                    wallet_address = generate_new_deposit_address(crypto_type, next_index)
                    logger.info(f"Generated new {crypto_type} address for user {request.user.id} (Index: {next_index})")
                except ValueError as e:
                    logger.error(f"Could not generate address: {e}")
                    messages.error(request, "Could not generate deposit address. Please try again later.")
                    return redirect('balance')

                qr_path = generate_payment_qr(wallet_address, amount, crypto_type)

                tx = CryptoTransaction.objects.create(
                    user=request.user,
                    crypto_type=crypto_type,
                    amount=amount,
                    deposit_address=wallet_address,
                    address_index=next_index,
                    qr_path=qr_path,
                    status=CryptoTransaction.STATUS_PENDING,
                )
            
            # Notify Telegram
            notify_deposit_request(tx)

            return redirect('payment_pending', tx_id=tx.id)
        
        # Legacy/Demo deposit logic (if no crypto_type)
        else:
             try:
                amount = float(request.POST.get('amount', 0))
                if amount > 0:
                    profile.add_balance(amount)
                    messages.success(request, f"Successfully added {amount} to your balance! (Demo)")
                else:
                    messages.error(request, "Amount must be positive!")
             except ValueError:
                messages.error(request, "Invalid amount!")
             return redirect('balance')

    return render(request, 'accounts/balance.html', {
        'profile': profile,
        'cryptocurrencies': cryptocurrencies,
        'transactions': request.user.cryptotransaction_set.all().order_by('-created_at')[:5]
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
def payment_pending(request, tx_id):
    tx = get_object_or_404(CryptoTransaction, id=tx_id, user=request.user)

    # Check expiration on page load
    if tx.status == CryptoTransaction.STATUS_PENDING:
        expiration_time = tx.created_at + timedelta(minutes=20)
        if timezone.now() > expiration_time:
            tx.status = CryptoTransaction.STATUS_CANCELED
            tx.save()

    return render(request, 'accounts/payment_pending.html', {"tx": tx})



# ----------------------------- PAYMENT TRANSACTION CHECK UP -------------------------------------------------------------------

API = {
    "BTC": "https://blockchain.info/rawaddr/",
    "ETH": "https://api.etherscan.io/v2/api?chainid=1&action=balance&apikey={apikey}&address=",
    "USDT": (
        "https://api.etherscan.io/v2/api?chainid=1&action=tokenbalance"
        f"&contractaddress=0xdAC17F958D2ee523a2206206994597C13D831ec7"
        f"&apikey={{apikey}}&address="
    ),
    "LTC": "https://chain.so/api/v2/get_address_balance/LTC/"
}

@csrf_protect
def ajax_check_deposit(request, tx_id):
    # Rate limiting
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

    # Check for expiration (20 minutes)
    expiration_time = tx.created_at + timedelta(minutes=20)
    if timezone.now() > expiration_time:
        tx.status = CryptoTransaction.STATUS_CANCELED
        tx.save()
        return JsonResponse({"status": "canceled"})

    crypto = tx.crypto_type
    addr = tx.deposit_address
    expected = Decimal(tx.amount)
    
    # Tolerance for floating point issues (e.g. 0.00000001)
    tolerance = Decimal("0.00000001")
    # Fee tolerance (95-105%)
    min_expected = expected * Decimal("0.95")
    max_expected = expected * Decimal("1.05")

    try:
        received = Decimal(0)
        timeout = 10 # 10 seconds timeout for external API calls
        
        if crypto == "BTC":
            response = requests.get(API["BTC"] + addr, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            received = Decimal(data["final_balance"]) / Decimal(1e8)
            
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
            # Verify decimal places for USDT (usually 6)
            received = Decimal(data["result"]) / Decimal(1e6)
            
        elif crypto == "LTC":
            response = requests.get(API["LTC"] + addr, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            received = Decimal(data["data"]["confirmed_balance"])

        # Update DB status with tolerance
        if min_expected <= received <= max_expected:
            with transaction.atomic():
                # Re-fetch transaction to lock it
                tx = CryptoTransaction.objects.select_for_update().get(id=tx.id)
                if tx.status != "confirmed":
                    tx.status = "confirmed"
                    tx.user.profile.balance += expected # Credit the expected amount
                    tx.user.profile.save()
                    tx.save()
                    logger.info(f"Deposit confirmed for user {tx.user} | Crypto={tx.crypto_type} | Amount={tx.amount}")
                    # Notify Telegram
                    notify_deposit_confirmed(tx)
            
        elif received < min_expected and received > 0:
            tx.status = "underpaid"
            tx.save()
            
        elif received > max_expected:
            tx.status = "overpaid"
            # Credit the actual received amount for overpayments? Or just expected?
            # For now, let's credit expected + difference, or just handle manually.
            # Safer to mark as overpaid and let admin handle, or credit received amount.
            # Let's credit received amount for overpayment to be fair.
            with transaction.atomic():
                 tx = CryptoTransaction.objects.select_for_update().get(id=tx.id)
                 if tx.status != "confirmed":
                    tx.status = "overpaid"
                    tx.user.profile.balance += received
                    tx.user.profile.save()
                    tx.save()

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
