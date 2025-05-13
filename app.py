#!/usr/bin/env python3
"""
Main entry point for the CAD Accessibility web application.
"""
import os
from src.webapp.app import create_app
from src.webapp.config import config

# Get configuration from environment or use default
config_name = os.environ.get('FLASK_CONFIG', 'default')
app = create_app(config[config_name])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050)