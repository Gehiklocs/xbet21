from django.http import HttpResponseForbidden
from django.conf import settings

class IPWhitelistMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Get client IP
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')

        # Check if IP is allowed
        # We allow all if ALLOWED_IPS is empty or not set (optional safety)
        # But user asked for restriction, so we enforce it if set.
        allowed_ips = getattr(settings, 'ALLOWED_IPS', [])
        
        # Define paths to protect (e.g., only admin) or protect everything
        # If you want to protect EVERYTHING, remove the path check.
        # For now, I'll protect /admin/ and /dashboard/
        path = request.path_info
        if path.startswith('/admin/') or path.startswith('/dashboard/'):
            if allowed_ips and ip not in allowed_ips:
                return HttpResponseForbidden(f"Access Denied. Your IP: {ip}")

        return self.get_response(request)
