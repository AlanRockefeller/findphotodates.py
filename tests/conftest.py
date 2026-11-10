import sys
from pathlib import Path

import pytest

# Add the project root to sys.path so test files can import the modules easily
sys.path.insert(0, str(Path(__file__).parent.parent))

_MODULES = ("findphotodates", "findphotodates_common", "findphotodates_drives",
            "findphotodates_health", "findphotodates_coverage", "findphotodates_menu")


@pytest.fixture
def patch_everywhere(monkeypatch):
    """Patch a name in every findphotodates module that has it, so a test
    doesn't depend on which module a function lives in or is imported into."""
    import importlib

    def patch(name, value):
        found = False
        for module_name in _MODULES:
            module = importlib.import_module(module_name)
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
                found = True
        assert found, f"no findphotodates module has {name!r}"

    return patch
