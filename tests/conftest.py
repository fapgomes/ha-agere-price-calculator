"""Shared test fixtures.

The suite is split in two, because the two halves have incompatible needs:

- `tests/` holds engine tests. They are plain synchronous functions over pure
  modules (calculator, readings, entry_options, forecast) and run anywhere,
  including on a Python version Home Assistant does not support.
- `tests/ha/` holds tests that need a running Home Assistant. Its own conftest
  enables custom integrations for every test in that directory.

Keeping the `enable_custom_integrations` fixture out of this file matters: it
depends on the async `hass` fixture, and a synchronous test that depends on an
async fixture is an error in pytest, so an autouse fixture here would break
every engine test.

`pytest_plugins` has to live in the top-level conftest, so the plugin is
registered here and only used by `tests/ha/`.
"""
try:
    import pytest_homeassistant_custom_component  # noqa: F401

    pytest_plugins = ["pytest_homeassistant_custom_component"]
except ImportError:  # engine tests still run without the Home Assistant harness
    pass
