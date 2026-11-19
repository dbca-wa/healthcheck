"""Pytest configuration shared by the test suite.

``status.py`` requires USER_SSO and PASS_SSO at import time. The tests mock all
HTTP calls, so placeholder credentials are enough; setting them here keeps the
suite collectable from a clean checkout without a local .env file.
"""

import os

os.environ.setdefault("USER_SSO", "test-user")
os.environ.setdefault("PASS_SSO", "test-password")
