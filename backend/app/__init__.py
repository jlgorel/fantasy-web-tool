from flask import Flask
import os, json
from config import Config
from flask_cors import CORS  # Import CORS
from flask_caching import Cache
import redis

def _load_local_settings():
    """Load local.settings.json if present (for local dev only)."""
    print("Loading local settings")
    settings_path = os.path.join(os.path.dirname(__file__), "..", "..", "azure-functions", "local.settings.json")
    print(settings_path)
    if os.path.exists(settings_path):
        print("it exists")
        with open(settings_path) as f:
            settings = json.load(f)
            print(settings)
            for k, v in settings.get("Values", {}).items():
                # Only set if not already defined (lets Azure override in prod)
                print("Setting " + str(k) + " to " + str(v))
                os.environ.setdefault(k, v)

def create_app():
    _load_local_settings()
    app = Flask(__name__)
    frontend_connections = os.environ.get('FRONTEND_URL')
    allowed_origins = [origin.strip() for origin in frontend_connections.split(',') if origin]

    CORS(app, resources={r"/*": {"origins": allowed_origins}})
    
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
