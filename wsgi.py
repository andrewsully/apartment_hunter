"""
PythonAnywhere WSGI entry point.

In PythonAnywhere Web tab, set:
  Source code:   /home/<your-username>/apartment_hunter
  Working dir:   /home/<your-username>/apartment_hunter
  WSGI file:     this file
  Python version: 3.11+

Point the WSGI file path to this file.
"""
import sys
import os

# Add project root to path
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Activate virtualenv if present (PythonAnywhere pattern)
venv_path = os.path.join(project_home, "venv", "lib")
if os.path.exists(venv_path):
    for d in os.listdir(venv_path):
        activate = os.path.join(venv_path, d, "site-packages")
        if activate not in sys.path:
            sys.path.insert(0, activate)

from app import app, db  # noqa: E402

with app.app_context():
    os.makedirs("data", exist_ok=True)
    db.create_all()

application = app
