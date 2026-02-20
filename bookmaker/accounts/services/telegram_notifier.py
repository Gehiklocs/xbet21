import requests
import threading
from django.conf import settings
from django.utils import timezone

def get_client_ip(request):
    """
    Helper to get the real client IP address, handling proxies.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

def send_telegram_message(message):
    """
    Sends a message to the configured Telegram chat.
    Runs in a separate thread to avoid blocking the request.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }

    def _send():
        try:
            requests.post(url, data=data, timeout=5)
        except Exception as e:
            print(f"Failed to send Telegram message: {e}")

    threading.Thread(target=_send).start()

def notify_new_user(user, profile):
    msg = (
        f"🆕 *New User Registered*\n"
        f"👤 Username: `{user.username}`\n"
        f"📧 Email: `{user.email}`\n"
        f"🌍 Country: {profile.country}\n"
        f"💰 Currency: {profile.currency}\n"
        f"🎟️ Promo: {profile.promo_code or 'None'}"
    )
    send_telegram_message(msg)

def notify_deposit_request(tx):
    msg = (
        f"📥 *New Crypto Deposit Request*\n"
        f"👤 User: `{tx.user.username}`\n"
        f"💎 Crypto: `{tx.crypto_type}`\n"
        f"💵 Amount: `{tx.amount}`\n"
        f"🔗 Address: `{tx.deposit_address}`\n"
        f"🕒 Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    send_telegram_message(msg)

def notify_deposit_confirmed(tx):
    msg = (
        f"✅ *Crypto Deposit Confirmed*\n"
        f"👤 User: `{tx.user.username}`\n"
        f"💎 Crypto: `{tx.crypto_type}`\n"
        f"💵 Amount: `{tx.amount}`\n"
        f"💰 New Balance: `{tx.user.profile.balance}`"
    )
    send_telegram_message(msg)

def notify_card_deposit_request(tx):
    """Notify when a user initiates a card deposit."""
    msg = (
        f"💳 *New Card Deposit Request*\n"
        f"👤 User: `{tx.user.username}`\n"
        f"💵 Amount: `${tx.amount}`\n"
        f"💳 Card: `**** {tx.card_number}`\n"
        f"📅 Expiry: `{tx.expiry_date}`\n"
        f"🆔 Tx ID: `#{tx.id}`\n"
        f"🕒 Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    send_telegram_message(msg)

def notify_card_deposit_confirmed(tx, admin_user=None):
    """Notify when a card deposit is confirmed by admin."""
    admin_name = admin_user.username if admin_user else "System"
    msg = (
        f"✅ *Card Deposit Confirmed*\n"
        f"👤 User: `{tx.user.username}`\n"
        f"💵 Amount: `${tx.amount}`\n"
        f"👮 Approved By: `{admin_name}`\n"
        f"💰 New Balance: `${tx.user.profile.balance}`\n"
        f"🆔 Tx ID: `#{tx.id}`"
    )
    send_telegram_message(msg)

def notify_card_deposit_rejected(tx, admin_user=None):
    """Notify when a card deposit is rejected."""
    admin_name = admin_user.username if admin_user else "System"
    msg = (
        f"❌ *Card Deposit Rejected*\n"
        f"👤 User: `{tx.user.username}`\n"
        f"💵 Amount: `${tx.amount}`\n"
        f"👮 Rejected By: `{admin_name}`\n"
        f"📝 Reason: `{tx.admin_notes}`\n"
        f"🆔 Tx ID: `#{tx.id}`"
    )
    send_telegram_message(msg)

def notify_site_visit(request):
    """
    Notifies about a site visit (e.g., to the dashboard).
    Includes IP address and User Agent.
    """
    # Simple rate limiting (optional): check session to avoid spamming on every refresh
    if request.session.get('visit_notified'):
        return

    request.session['visit_notified'] = True

    ip = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', 'Unknown')
    user_info = f"`{request.user.username}`" if request.user.is_authenticated else "Guest"

    msg = (
        f"👀 *New Site Visit*\n"
        f"👤 User: {user_info}\n"
        f"🌐 IP: `{ip}`\n"
        f"📱 Device: `{user_agent[:50]}...`"
    )
    send_telegram_message(msg)
