import logging

class Config:

    # Logging configuration
    LOG_LEVEL = logging.INFO  # Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    LOG_FILE = 'app.log'       # Log file location

    @staticmethod
    def configure_logging(app):
        handler = logging.FileHandler(Config.LOG_FILE)
        handler.setLevel(Config.LOG_LEVEL)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        app.logger.addHandler(handler)
        app.logger.setLevel(Config.LOG_LEVEL)