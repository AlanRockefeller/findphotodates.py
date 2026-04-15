import sys
from pathlib import Path

# Add the project root to sys.path so test files can import the modules easily
sys.path.insert(0, str(Path(__file__).parent.parent))
