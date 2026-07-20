"""Shared test fixtures.

The Home Assistant test harness (pytest-homeassistant-custom-component) is only
present in a Python 3.13 environment / CI. When absent, engine tests
(test_calculator, test_cycle) still collect and run — the HA plugin and its
autouse fixture load only when the plugin is importable.
"""
import pytest

try:
    import pytest_homeassistant_custom_component  # noqa: F401

    _HAS_HA_HARNESS = True
except ImportError:
    _HAS_HA_HARNESS = False

if _HAS_HA_HARNESS:
    pytest_plugins = ["pytest_homeassistant_custom_component"]

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Enable loading custom integrations in all tests."""
        yield
