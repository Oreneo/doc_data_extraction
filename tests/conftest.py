"""
Pytest configuration for the test suite.

Puts the project root on sys.path so `src` imports resolve when tests are
collected by pytest from any working directory. The test modules also do
this themselves so they still work when run directly as scripts.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
