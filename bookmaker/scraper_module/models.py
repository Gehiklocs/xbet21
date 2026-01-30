from django.db import models

class ScraperStatus(models.Model):
    is_running = models.BooleanField(default=False)
    last_run = models.DateTimeField(auto_now=True)
    logs = models.TextField(blank=True, default="")

    def __str__(self):
        return f"Scraper Status (Running: {self.is_running})"

    class Meta:
        verbose_name_plural = "Scraper Status"
