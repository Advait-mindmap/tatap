"""Application package.

Environment is loaded FIRST: database.py reads DATABASE_URL at import time, so anything
that needs configuration must have it before the imports below run.
"""

from backend.app.env import load_env_file

load_env_file()

from backend.app.database import init_db

__all__ = ['init_db']
