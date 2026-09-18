# Pressing Stop marks the request's cancel token, but the streaming chat loops
# in these providers never read it, so the turn ran to completion on the
# provider's side and kept being billed after the user stopped it. Reading the
# token is only half of it: leaving the iteration does not disconnect, so the
# stream has to be closed as well.
from unittest.mock import MagicMock, patch

import pytest

from notebook_intelligence.llm_providers.litellm_compatible_llm_provider import (
    LiteLLMCompatibleLLMProvider,
)
from notebook_intelligence.llm_providers.ollama_llm_provider import (
    OllamaChatModel,
    OllamaLLMProvider,
)
from notebook_intelligence.llm_providers.openai_compatible_llm_provider import (
    OpenAICompatibleLLMProvider,
)


class CancelAfter:
    """Cancels once `after` chunks have been pulled from the stream."""

    def __init__(self, counter: list, after: int):
        self._counter = counter
        self._after = after

    @property
    def is_cancel_requested(self) -> bool:
        return len(self._counter) > self._after


class Cancelled:
    is_cancel_requested = True


def chunks(counter: list, chunk_factory, total: int = 50):
    for index in range(total):
        counter.append(index)
        yield chunk_factory(index)


class FakeOpenAIStream:
    """Stands in for `openai.Stream`: iterable, and a context manager whose
    exit closes the underlying HTTP response."""

    def __init__(self, counter: list, chunk_factory, total: int = 50):
        self._chunks = chunks(counter, chunk_factory, total)
        self.closed = False

    def __iter__(self):
        return self._chunks

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


class FakeLiteLLMStream:
    """litellm's wrapper exposes only an async close; the closable stream is
    the provider's, hanging off `completion_stream`."""

    def __init__(self, counter: list, chunk_factory, total: int = 50):
        self._chunks = chunks(counter, chunk_factory, total)
        self.completion_stream = MagicMock()
        self.completion_stream.closed = False

        def close():
            self.completion_stream.closed = True

        self.completion_stream.close.side_effect = close

    def __iter__(self):
        return self._chunks


def openai_chunk(index: int):
    delta = MagicMock()
    delta.role = "assistant"
    delta.content = f"chunk-{index} "
    delta.reasoning_content = None
    delta.reasoning = None
    return MagicMock(choices=[MagicMock(delta=delta)])


def ollama_chunk(index: int):
    return {"message": {"role": "assistant", "content": f"chunk-{index} "}}


def openai_model():
    provider = OpenAICompatibleLLMProvider()
    model = provider.chat_models[0]
    model.set_property_value("model_id", "test-model")
    model.set_property_value("api_key", "test-key")
    model.set_property_value("base_url", "https://example.com/v1")
    return model


def litellm_model():
    model = LiteLLMCompatibleLLMProvider().chat_models[0]
    model.set_property_value("model_id", "test-model")
    model.set_property_value("api_key", "test-key")
    return model


@patch("openai.OpenAI")
def test_openai_compatible_stops_and_disconnects_once_cancelled(mock_openai_cls):
    pulled = []
    stream = FakeOpenAIStream(pulled, openai_chunk)
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = stream
    response = MagicMock()

    openai_model().completions(
        [{"role": "user", "content": "hi"}],
        response=response,
        cancel_token=CancelAfter(pulled, 3),
    )

    # The chunk is pulled before the token is read, so the iteration that sees
    # the cancellation still counts.
    assert len(pulled) == 4
    # Leaving the loop is not enough: without closing the response the
    # provider keeps generating into a socket nobody reads.
    assert stream.closed is True
    # The frontend is waiting on StreamEnd, so a cancelled turn still has to be
    # closed out rather than left hanging.
    response.finish.assert_called_once()


@patch("openai.OpenAI")
def test_openai_compatible_streams_everything_without_a_cancel_token(mock_openai_cls):
    pulled = []
    stream = FakeOpenAIStream(pulled, openai_chunk, total=7)
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = stream
    response = MagicMock()

    openai_model().completions(
        [{"role": "user", "content": "hi"}], response=response, cancel_token=None
    )

    assert len(pulled) == 7
    assert response.stream.call_count == 7
    assert stream.closed is True
    response.finish.assert_called_once()


@patch("openai.OpenAI")
def test_openai_compatible_does_not_ask_for_a_turn_already_cancelled(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    response = MagicMock()

    openai_model().completions(
        [{"role": "user", "content": "hi"}],
        response=response,
        cancel_token=Cancelled(),
    )

    mock_client.chat.completions.create.assert_not_called()
    response.finish.assert_called_once()


@patch("litellm.completion")
def test_litellm_compatible_stops_and_closes_the_provider_stream(mock_completion):
    pulled = []
    stream = FakeLiteLLMStream(pulled, openai_chunk)
    mock_completion.return_value = stream
    response = MagicMock()

    litellm_model().completions(
        [{"role": "user", "content": "hi"}],
        response=response,
        cancel_token=CancelAfter(pulled, 2),
    )

    assert len(pulled) == 3
    assert stream.completion_stream.closed is True
    response.finish.assert_called_once()


@patch("litellm.completion")
def test_litellm_compatible_survives_a_stream_it_cannot_close(mock_completion):
    # `completion_stream` is litellm's own attribute rather than a contract, so
    # a version without it must not turn a cancelled turn into an exception.
    pulled = []
    stream = FakeLiteLLMStream(pulled, openai_chunk)
    del stream.completion_stream
    mock_completion.return_value = stream
    response = MagicMock()

    litellm_model().completions(
        [{"role": "user", "content": "hi"}],
        response=response,
        cancel_token=CancelAfter(pulled, 1),
    )

    assert len(pulled) == 2
    response.finish.assert_called_once()


@patch("ollama.chat")
def test_ollama_stops_pulling_the_stream_once_cancelled(mock_chat):
    # Ollama's client returns a plain generator wrapping `with client.stream`,
    # with no reference cycle holding it, so leaving the loop is enough here:
    # the generator is finalized on return and closes the response.
    pulled = []
    mock_chat.return_value = chunks(pulled, ollama_chunk)
    response = MagicMock()

    # OllamaLLMProvider lists no models unless an Ollama daemon answers, so the
    # chat model is built directly.
    model = OllamaChatModel(OllamaLLMProvider(), "test-model", "Test Model", 8192)
    model.completions(
        [{"role": "user", "content": "hi"}],
        response=response,
        cancel_token=CancelAfter(pulled, 1),
    )

    assert len(pulled) == 2
    response.finish.assert_called_once()


@pytest.mark.parametrize("pulled_before_cancel", [0, 1, 5])
def test_openai_compatible_cancels_at_any_point_in_the_stream(pulled_before_cancel):
    with patch("openai.OpenAI") as mock_openai_cls:
        pulled = []
        stream = FakeOpenAIStream(pulled, openai_chunk)
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = stream
        response = MagicMock()

        openai_model().completions(
            [{"role": "user", "content": "hi"}],
            response=response,
            cancel_token=CancelAfter(pulled, pulled_before_cancel),
        )

        assert len(pulled) == pulled_before_cancel + 1
        assert response.stream.call_count == pulled_before_cancel
        assert stream.closed is True
