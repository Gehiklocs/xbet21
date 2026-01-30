from django.contrib import admin
from django.urls import path
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.utils.html import format_html
from django.contrib import messages
from .models import ScraperStatus
from django_q.tasks import async_task

@admin.register(ScraperStatus)
class ScraperStatusAdmin(admin.ModelAdmin):
    list_display = ('is_running', 'last_run', 'monitor_link')
    
    def monitor_link(self, obj):
        return format_html('<a class="button" href="monitor/" style="background-color: #417690; color: white; padding: 5px 10px; border-radius: 5px; text-decoration: none;">Open Monitor Dashboard</a>')
    monitor_link.short_description = "Actions"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('monitor/', self.admin_site.admin_view(self.monitor_view), name='scraper_monitor'),
            path('control/<str:action>/', self.admin_site.admin_view(self.control_view), name='scraper_control'),
        ]
        return custom_urls + urls

    def monitor_view(self, request):
        status, _ = ScraperStatus.objects.get_or_create(id=1)
        context = dict(
           self.admin_site.each_context(request),
           status=status,
        )
        return render(request, "admin/scraper_monitor.html", context)

    def control_view(self, request, action):
        if request.method == "POST":
            status, _ = ScraperStatus.objects.get_or_create(id=1)
            
            if action == "start":
                if not status.is_running:
                    status.is_running = True
                    status.logs += "\n🚀 Starting via Admin..."
                    status.save()
                    # Launch background task
                    async_task('scraper_module.tasks.run_monitor_main_list')
                    return JsonResponse({'status': 'ok', 'message': 'Started'})
                else:
                    return JsonResponse({'status': 'error', 'message': 'Already running'})
            
            elif action == "stop":
                if status.is_running:
                    status.is_running = False
                    status.logs += "\n🛑 Stopping via Admin (waiting for loop to exit)..."
                    status.save()
                    return JsonResponse({'status': 'ok', 'message': 'Stopping...'})
                else:
                    return JsonResponse({'status': 'error', 'message': 'Not running'})
        
        return JsonResponse({'status': 'error', 'message': 'Invalid request'})
