from django.core.management import call_command
from .models import ScraperStatus

def run_monitor_main_list():
    # Ensure status is running
    status, _ = ScraperStatus.objects.get_or_create(id=1)
    status.is_running = True
    status.save()
    
    try:
        call_command('monitor_main_list')
    except Exception as e:
        status.logs += f"\n❌ Crash: {e}"
        status.is_running = False
        status.save()
