#!/usr/bin/env python3
from flask import Flask
from flask_cors import CORS

def create_app(config=None):
    """Create and configure the Flask application."""
    app = Flask(__name__, 
                static_folder='../static', 
                template_folder='../templates')
    
    # Enable CORS
    CORS(app)
    
    # Load configuration
    if config:
        app.config.from_object(config)
    
    # Register blueprints
    from src.webapp.routes import main_routes
    app.register_blueprint(main_routes)
    
    return app