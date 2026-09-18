"""Regression tests for terminal-safe chat participant dispatch."""

import asyncio

import pytest
from unittest.mock import AsyncMock, Mock

from notebook_intelligence.ai_service_manager import AIServiceManager
from notebook_intelligence.api import (
    ChatRequest,
    ContextRequest,
    ContextRequestType,
)
from notebook_intelligence.claude import CLAUDE_CODE_CHAT_PARTICIPANT_ID
import time

from notebook_intelligence import perf
from notebook_intelligence.llm_providers.github_copilot_llm_provider import (
    GitHubCopilotLLMProvider,
)
from notebook_intelligence.llm_providers.litellm_compatible_llm_provider import (
    LiteLLMCompatibleLLMProvider,
)
from notebook_intelligence.llm_providers.ollama_llm_provider import OllamaLLMProvider
from notebook_intelligence.llm_providers.openai_compatible_llm_provider import (
    OpenAICompatibleLLMProvider,
)



class _RecordingResponse:
    def __init__(self):
        self.participant_id = ""
        self.streamed = []
        self.finish_count = 0

    def stream(self, data):
        self.streamed.append(data)

    def finish(self):
        self.finish_count += 1


def _make_manager():
    manager = AIServiceManager.__new__(AIServiceManager)
    config = Mock()
    config.claude_settings = {"enabled": False}
    config.acp_settings = {"enabled": False}
    manager._nbi_config = config
    manager._chat_model = Mock()

    default_participant = Mock()
    default_participant.handle_chat_request = AsyncMock()
    manager._default_chat_participant = default_participant
    manager.chat_participants = {"default": default_participant}
    return manager, default_participant


def test_get_chat_participant_returns_default_object_for_unknown_id():
    manager, default_participant = _make_manager()

    participant = manager.get_chat_participant("@missing explain this")

    assert participant is default_participant


def test_unknown_participant_falls_back_without_dropping_at_mention():
    manager, default_participant = _make_manager()
    request = ChatRequest(prompt="@missing explain this", chat_history=[])
    response = _RecordingResponse()

    asyncio.run(manager.handle_chat_request(request, response))

    default_participant.handle_chat_request.assert_awaited_once()
    assert request.prompt == "@missing explain this"
    assert response.participant_id == "default"
    assert response.finish_count == 0


def test_unknown_participant_fallback_preserves_parsed_command():
    manager, default_participant = _make_manager()
    request = ChatRequest(prompt="@missing /explain this", chat_history=[])
    response = _RecordingResponse()

    asyncio.run(manager.handle_chat_request(request, response))

    default_participant.handle_chat_request.assert_awaited_once()
    assert request.prompt == "@missing this"
    assert request.command == "explain"


def test_mcp_prompt_messages_are_marked_as_current_request_context():
    manager, default_participant = _make_manager()
    manager.get_mcp_server_prompt_value = Mock(
        return_value=[
            {"role": "assistant", "content": "Return strict JSON."},
            {"role": "user", "content": "Use the compact schema."},
        ]
    )
    request = ChatRequest(
        prompt="/mcp:docs:review: inspect this",
        chat_history=[
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ],
    )
    response = _RecordingResponse()

    asyncio.run(manager.handle_chat_request(request, response))

    default_participant.handle_chat_request.assert_awaited_once()
    assert request.mcp_prompt_message_count == 2
    assert request.chat_history[-3:] == [
        {"role": "assistant", "content": "Return strict JSON."},
        {"role": "user", "content": "Use the compact schema."},
        {"role": "user", "content": "inspect this"},
    ]


def test_claude_mode_resolves_active_default_outside_participant_map():
    manager, _default_participant = _make_manager()
    manager._nbi_config.claude_settings = {"enabled": True}
    claude_participant = Mock()
    claude_participant.id = CLAUDE_CODE_CHAT_PARTICIPANT_ID
    claude_participant.handle_chat_request = AsyncMock()
    manager._default_chat_participant = claude_participant
    manager.chat_participants = {"default": claude_participant}
    request = ChatRequest(prompt="explain this", chat_history=[])
    response = _RecordingResponse()

    asyncio.run(manager.handle_chat_request(request, response))

    claude_participant.handle_chat_request.assert_awaited_once()
    assert response.participant_id == CLAUDE_CODE_CHAT_PARTICIPANT_ID
    assert response.finish_count == 0


def test_claude_mode_does_not_fall_back_while_participant_is_starting():
    manager, default_participant = _make_manager()
    manager._nbi_config.claude_settings = {"enabled": True}
    request = ChatRequest(prompt="hello world", chat_history=[])
    response = _RecordingResponse()

    asyncio.run(manager.handle_chat_request(request, response))

    default_participant.handle_chat_request.assert_not_awaited()
    assert request.prompt == "hello world"
    assert response.participant_id == CLAUDE_CODE_CHAT_PARTICIPANT_ID
    assert response.finish_count == 1
    assert [item.content for item in response.streamed] == [
        "Claude Code mode is still starting. Please try again in a moment."
    ]


def test_completion_context_is_empty_when_no_participant_is_available():
    manager, _default_participant = _make_manager()
    request = ContextRequest(
        ContextRequestType.InlineCompletion,
        participant=None,
    )

    context = asyncio.run(manager.get_completion_context(request))

    assert context.items == []


@pytest.fixture(autouse=True)
def _reset_perf():
    """This module records turns, and the recorder is process-global."""
    yield
    perf.configure({"enabled": False}, None)
    perf._turns.clear()
    perf._ring.clear()


class TestPerfBackendLabel:
    """The perf report names the backend that served a turn.

    Calling every non-agent turn "copilot" names a provider the user may not
    have, and the label is stored in reports people paste into support
    tickets.
    """

    def _manager(self, provider_id):
        manager, _ = _make_manager()
        manager._nbi_config.chat_model = {
            "provider": provider_id,
            "model": "some-model",
        }
        return manager

    def test_the_native_path_is_named_by_its_provider(self):
        # The ids the shipped providers actually register.
        assert GitHubCopilotLLMProvider().id == "github-copilot"
        assert OpenAICompatibleLLMProvider().id == "openai-compatible"
        assert LiteLLMCompatibleLLMProvider().id == "litellm-compatible"
        assert OllamaLLMProvider().id == "ollama"

        for provider_id in (
            "github-copilot",
            "openai-compatible",
            "litellm-compatible",
            "ollama",
        ):
            assert self._manager(provider_id).perf_backend_label == provider_id

    def test_agent_modes_keep_their_own_labels(self):
        manager = self._manager("openai-compatible")
        manager._nbi_config.claude_settings = {"enabled": True}
        assert manager.perf_backend_label == "claude"

        manager = self._manager("openai-compatible")
        manager._nbi_config.claude_settings = {"enabled": False}
        manager._nbi_config.acp_settings = {"enabled": True, "agent": "gemini"}
        assert manager.perf_backend_label == "acp"

    def test_no_configured_provider_falls_back_to_the_path_not_a_name(self):
        """A report must not invent a provider when none is configured."""
        assert self._manager("").perf_backend_label == "chat"
        assert self._manager("none").perf_backend_label == "chat"

        manager, _ = _make_manager()
        manager._nbi_config.chat_model = None
        assert manager.perf_backend_label == "chat"

    def test_a_malformed_config_does_not_fail_the_request(self):
        """This runs on the chat path, so it must not be what raises.

        A hand-edited config can carry any shape here, and before this label
        existed such a config still reached the "Chat model is not set!"
        reply rather than an exception.
        """
        for chat_model in ({"provider": 5}, {"provider": ["ollama"]}, "ollama", None, {}):
            manager, _ = _make_manager()
            manager._nbi_config.chat_model = chat_model
            assert manager.perf_backend_label == "chat"

    def test_a_model_the_provider_cannot_resolve_still_names_the_provider(self):
        """Reading the resolved model instead would answer "chat" here.

        A pinned model id that the provider's current catalogue does not list
        leaves `chat_model` None, which says nothing about which provider the
        user configured.
        """
        manager = self._manager("github-copilot")
        manager._chat_model = None

        assert manager.perf_backend_label == "github-copilot"

    def test_the_dispatch_span_is_labelled_with_the_backend(self):
        """The label reaches the report, not just the property."""
        manager, participant = _make_manager()
        manager._nbi_config.chat_model = {
            "provider": "openai-compatible",
            "model": "m",
        }
        response = _RecordingResponse()
        response.message_id = "m-dispatch"

        perf.configure({"enabled": True, "attr_detail": "full"}, None)
        try:
            turn = perf.begin_turn(
                "m-dispatch",
                manager.perf_backend_label,
                time.time(),
                time.monotonic(),
            )
            request = ChatRequest(prompt="hello", chat_history=[])
            asyncio.run(manager.handle_chat_request(request, response))
            turn.close("ok")
            snapshot = perf.report_snapshot()
        finally:
            perf.configure({"enabled": False}, None)

        recorded = [t for t in snapshot["turns"] if t["message_id"] == "m-dispatch"]
        assert recorded, "the turn was not recorded"
        assert recorded[-1]["mode"] == "openai-compatible"
        dispatch = [s for s in recorded[-1]["spans"] if s["name"] == "dispatch"]
        assert dispatch, "no dispatch span"
        assert dispatch[0]["attrs"]["provider"] == "openai-compatible"
