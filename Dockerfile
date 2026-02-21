# Use an official Python runtime as a parent image
FROM python:3.11-bookworm

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE bookmaker.settings

# Set work directory
WORKDIR /app

# Install system dependencies
# We need these for Playwright and general build tools
RUN apt-get update && apt-get install -y \
    curl \
    git \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY bookmaker/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip
RUN pip install -r requirements.txt
RUN pip install gunicorn

# Install Playwright and its dependencies
# This is crucial for the scraper to work inside Docker
RUN pip install playwright
RUN playwright install chromium
RUN playwright install-deps

# Copy project
COPY . /app/

# Create a directory for static files
RUN mkdir -p /app/bookmaker/staticfiles

# Expose port 8000
EXPOSE 8000

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
