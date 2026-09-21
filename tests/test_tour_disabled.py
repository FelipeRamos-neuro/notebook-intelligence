"""The NBI_TOUR_DISABLED switch: env var wins over the traitlet, and a typo
never stops the extension from loading."""

import logging
from unittest.mock import MagicMock

import pytest

from notebook_intelligence import extension as ext_module


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("NBI_TOUR_DISABLED", raising=False)
    # _setup_handlers publishes onto the class; restore it so no test depends
    # on the order the others ran in.
    monkeypatch.setattr(
        ext_module.GetCapabilitiesHandler, "tour_disabled", False, raising=False
    )


def test_default_is_enabled():
    assert ext_module.NotebookIntelligence().tour_disabled is False
    assert ext_module._resolve_tour_disabled(False) is False
    assert ext_module._resolve_tour_disabled(None) is False


def test_traitlet_disables():
    assert ext_module._resolve_tour_disabled(True) is True


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_env_var_disables(monkeypatch, value):
    monkeypatch.setenv("NBI_TOUR_DISABLED", value)
    assert ext_module._resolve_tour_disabled(False) is True


def test_env_var_overrides_traitlet(monkeypatch):
    monkeypatch.setenv("NBI_TOUR_DISABLED", "0")
    assert ext_module._resolve_tour_disabled(True) is False


@pytest.mark.parametrize("traitlet", [True, False])
def test_unrecognized_env_value_warns_and_uses_traitlet(
    monkeypatch, caplog, traitlet
):
    monkeypatch.setenv("NBI_TOUR_DISABLED", "maybe")
    with caplog.at_level(logging.WARNING, logger=ext_module.log.name):
        assert ext_module._resolve_tour_disabled(traitlet) is traitlet
    assert "NBI_TOUR_DISABLED" in caplog.text
    assert "true, false, 1, 0, yes, no, on, off" in caplog.text


@pytest.fixture
def restore_handler_class_state():
    """_setup_handlers publishes settings onto many handler classes at once
    (some are security gates other tests expect closed), so put every plain
    class attribute it could have touched back afterwards."""
    saved = []
    for cls in vars(ext_module).values():
        if isinstance(cls, type) and cls.__module__ == ext_module.__name__:
            saved.append((cls, dict(vars(cls))))
    yield
    for cls, attrs in saved:
        for name, value in attrs.items():
            if name.startswith("__") or callable(value):
                continue
            setattr(cls, name, value)
        for name in set(vars(cls)) - set(attrs):
            if not name.startswith("__"):
                delattr(cls, name)


def test_setup_handlers_publishes_the_resolved_value(
    monkeypatch, restore_handler_class_state
):
    monkeypatch.setenv("NBI_TOUR_DISABLED", "1")
    # _setup_handlers reads config through module globals that only exist once
    # the extension has started, and treats every *_env / list setting as data.
    monkeypatch.setattr(ext_module, "ai_service_manager", MagicMock())
    monkeypatch.setattr(ext_module, "nbi_config", MagicMock(), raising=False)
    web_app = MagicMock()
    web_app.settings = {"base_url": "/"}
    ext = MagicMock()
    ext.tour_disabled = False
    ext.tour_config_path = ""
    ext.skill_max_archive_mb = 100
    ext.upload_max_mb = 25
    ext.upload_retention_hours = 24
    for name in (
        "disabled_tools",
        "disabled_providers",
        "disabled_coding_agent_launchers",
        "additional_skipped_workspace_directories",
    ):
        setattr(ext, name, [])
    ext_module.NotebookIntelligence._setup_handlers(ext, web_app, {}, {})
    assert ext_module.GetCapabilitiesHandler.tour_disabled is True


@pytest.mark.parametrize("disabled", [True, False])
def test_capabilities_response_carries_the_flag(monkeypatch, disabled):
    monkeypatch.setattr(ext_module, "ai_service_manager", MagicMock())
    # The response is one big dict of mostly-mocked values; capture it before
    # it is serialized rather than mocking every field to be JSON-safe.
    monkeypatch.setattr(ext_module.json, "dumps", lambda obj, *a, **k: obj)
    handler = MagicMock()
    handler.tour_config_path = ""
    handler.tour_disabled = disabled
    for name in (
        "disabled_tools",
        "disabled_providers",
        "disabled_coding_agent_launchers",
        "additional_skipped_workspace_directories",
    ):
        setattr(handler, name, [])
    ext_module.GetCapabilitiesHandler.get(handler)
    response = handler.finish.call_args[0][0]
    assert response["tour_disabled"] is disabled
