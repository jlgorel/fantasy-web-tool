from flask import Flask
import os
from config import Config
from flask_cors import CORS  # Import CORS
from flask_caching import Cache
import redis


def create_app():
    app = Flask(__name__)
    CORS(app) 
    
    # Load configurations
    app.config.from_object(Config)
    
    # Configure logging
    Config.configure_logging(app)

    # Get the connection string from the environment variable
    redis_connection_string = os.environ.get('AZURE_REDIS_CONNECTIONSTRING')

    # Create a Redis client
    if redis_connection_string:
        app.redis_client = redis.from_url(redis_connection_string)
        # Optionally set a timeout for your Redis operations
        app.redis_client.timeout = 5  # Timeout in seconds
    else:
        raise ValueError("AZURE_REDIS_CONNECTIONSTRING is not set.")

    # Register blueprints
    from app.routes import main
    app.register_blueprint(main)
    
    return app
