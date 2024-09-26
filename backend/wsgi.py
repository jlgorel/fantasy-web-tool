import os
import sys

sys.path.append(os.path.dirname(__file__))


from app import create_app

app = create_app()

# The WSGI server will use this app variable to run your application.