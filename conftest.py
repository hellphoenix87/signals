"""Root-level pytest configuration."""

import sys
from pathlib import Path

# Add the project root to sys.path so tests can import app and other modules
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
