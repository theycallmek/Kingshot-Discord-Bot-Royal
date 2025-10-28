"""
Configuration settings for the web application.

This module loads configuration from environment variables with default
fallbacks for development. It includes settings for security, API keys,
and template paths.
"""

import os
from fastapi.templating import Jinja2Templates

# --- Security Configuration ---
SECRET_KEY = os.environ.get("SECRET_KEY", "your-secret-key")
WEB_DASHBOARD_PASSWORD = os.environ.get("WEB_DASHBOARD_PASSWORD", "password")

# --- API Configuration ---
API_SECRET = "mN4!pQs6JrYwV9"  # Secret for game API requests

# --- Template Configuration ---
templates = Jinja2Templates(directory="web/templates")
