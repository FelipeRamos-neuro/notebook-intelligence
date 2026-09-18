# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
import time
from typing import Any
from notebook_intelligence.api import ChatModel, EmbeddingModel, InlineCompletionModel, LLMProvider, CancelToken, ChatResponse, CompletionContext
import logging

from notebook_intelligence.inline_completion import (
    chatbook_inline_prefix_hint,
    extract_inline_completion,
    is_chatbook_inline_language,
)
from notebook_intelligence.util import extract_llm_generated_code

log = logging.getLogger(__name__)

OLLAMA_EMBEDDING_FAMILIES = set(["nomic-bert", "bert"])
# Bounds one enumeration request. The ollama package passes timeout=None to
# httpx, so a host that drops packets instead of refusing them takes the OS
# connect timeout (75s on macOS), and the capabilities handler that reads
# chat_models is synchronous: that wait lands on the event-loop thread serving
# every other request in the process (#427). Deliberately generous rather than
# tight, because /api/show is a metadata read that a loaded host still answers
# slowly, and a model whose metadata times out drops out of the list.
OLLAMA_ENUMERATION_TIMEOUT_S = 5.0
# Wall clock for a whole enumeration. The per-request bound alone scales with
# the model count, one /api/show apiece, which is how a host that answers the
# listing and then stalls still held the event loop for tens of seconds. Past
# the budget the remaining models are left out and the warning says how many.
# The completion calls in this file stay unbounded on purpose: they run on the
# threaded request path, and capping a streaming generation would break it.
OLLAMA_ENUMERATION_BUDGET_S = 6.0
QWEN_INLINE_COMPL_PROMPT = """<|fim_prefix|>{prefix}<|fim_suffix|>{suffix}<|fim_middle|>"""
DEEPSEEK_INLINE_COMPL_PROMPT = """<｜fim▁begin｜>{prefix}<｜fim▁hole｜>{suffix}<｜fim▁end｜>"""
CODELLAMA_INLINE_COMPL_PROMPT = """<PRE> {prefix} <SUF>{suffix} <MID>"""
STARCODER_INLINE_COMPL_PROMPT = """<fim_prefix>{prefix}<fim_suffix>{suffix}<fim_middle>"""
CODESTRAL_INLINE_COMPL_PROMPT = """[SUFFIX]{suffix}[PREFIX]{prefix}"""

class OllamaChatModel(ChatModel):
    def __init__(self, provider: LLMProvider, model_id: str, model_name: str, context_window: int):
        super().__init__(provider)
        self._model_id = model_id
        self._model_name = model_name
        self._context_window = context_window

    @property
    def id(self) -> str:
        return self._model_id
    
    @property
    def name(self) -> str:
        return self._model_name
    
    @property
    def context_window(self) -> int:
        return self._context_window

    def completions(self, messages: list[dict], tools: list[dict] = None, response: ChatResponse = None, cancel_token: CancelToken = None, options: dict = {}) -> Any:
        import ollama
        stream = response is not None
        completion_args = {
            "model": self._model_id, 
            "messages": messages.copy(),
            "stream": stream,
        }
        if tools is not None and len(tools) > 0:
            completion_args["tools"] = tools

        ollama_response = ollama.chat(**completion_args)

        if stream:
            for chunk in ollama_response:
                if cancel_token is not None and cancel_token.is_cancel_requested:
                    break
                delta = chunk['message']
                reasoning = delta.get('reasoning_content') or delta.get('reasoning')
                if reasoning is not None:
                    reasoning = str(reasoning)
                response.stream({
                        "choices": [{
                            "delta": {
                                "role": delta['role'],
                                "content": delta['content'],
                                "reasoning_content": reasoning
                            }
                        }]
                    })
            response.finish()
            return
        else:
            json_resp = json.loads(ollama_response.model_dump_json())
            message = ollama_response.message
            reasoning = getattr(message, 'reasoning_content', None) or getattr(message, 'reasoning', None)
            if reasoning:
                json_resp['message']['reasoning_content'] = str(reasoning)

            return {
                'choices': [
                    {
                        'message': json_resp['message']
                    }
                ]
            }


class OllamaInlineCompletionModel(InlineCompletionModel):
    def __init__(self, provider: LLMProvider, model_id: str, model_name: str, context_window: int, prompt_template: str):
        super().__init__(provider)
        self._model_id = model_id
        self._model_name = model_name
        self._context_window = context_window
        self._prompt_template = prompt_template

    @property
    def id(self) -> str:
        return self._model_id
    
    @property
    def name(self) -> str:
        return self._model_name
    
    @property
    def context_window(self) -> int:
        return self._context_window

    def inline_completions(self, prefix, suffix, language, filename, context: CompletionContext, cancel_token: CancelToken) -> str:
        import ollama
        if is_chatbook_inline_language(language):
            prefix = chatbook_inline_prefix_hint() + prefix
        has_suffix = suffix.strip() != ""
        if has_suffix:
            prompt = self._prompt_template.format(prefix=prefix, suffix=suffix.strip())
        else:
            prompt = prefix

        try:
            generate_args = {
                "model": self._model_id, 
                "prompt": prompt,
                "raw": True,
                "options": {
                    'num_predict': 128,
                    "temperature": 0,
                    "stop" : [
                        "<|end▁of▁sentence|>",
                        "<｜end▁of▁sentence｜>",
                        "<|EOT|>",
                        "<EOT>",
                        "\\n",
                        "</s>",
                        "<|eot_id|>",
                    ],
                },
            }

            ollama_response = ollama.generate(**generate_args)
            code = ollama_response.response
            if is_chatbook_inline_language(language):
                return extract_inline_completion(code, language)
            code = extract_llm_generated_code(code)

            return code
        except Exception as e:
            log.error(f"Error occurred while generating using completions ollama: {e}")
            return ""

class OllamaLLMProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self._chat_models = []
        self._chat_models_loaded = False

    @property
    def id(self) -> str:
        return "ollama"
    
    @property
    def name(self) -> str:
        return "Ollama"

    @property
    def chat_models(self) -> list[ChatModel]:
        # Enumerating imports the ollama SDK and calls the Ollama host, so it
        # waits for a caller that wants the list instead of running in the
        # constructor, which every server start paid for whatever the
        # configured provider was (#427).
        if not self._chat_models_loaded:
            self.update_chat_model_list()
        return self._chat_models

    @property
    def inline_completion_models(self) -> list[InlineCompletionModel]:
        return [
            OllamaInlineCompletionModel(self, "deepseek-coder-v2", "deepseek-coder-v2", 163840, DEEPSEEK_INLINE_COMPL_PROMPT),
            OllamaInlineCompletionModel(self, "qwen2.5-coder", "qwen2.5-coder", 32768, QWEN_INLINE_COMPL_PROMPT),
            OllamaInlineCompletionModel(self, "codestral", "codestral", 32768, CODESTRAL_INLINE_COMPL_PROMPT),
            OllamaInlineCompletionModel(self, "starcoder2", "starcoder2", 16384, STARCODER_INLINE_COMPL_PROMPT),
            OllamaInlineCompletionModel(self, "codellama:7b-code", "codellama:7b-code", 16384, CODELLAMA_INLINE_COMPL_PROMPT),
        ]
    
    @property
    def embedding_models(self) -> list[EmbeddingModel]:
        return []
    
    def update_chat_model_list(self):
        # Set before the attempt, not after: an unreachable host would
        # otherwise retry and re-log on every access of the property.
        self._chat_models_loaded = True
        try:
            import ollama
            with ollama.Client(timeout=OLLAMA_ENUMERATION_TIMEOUT_S) as client:
                response = client.list()
                models = []
                skipped = 0
                deadline = time.monotonic() + OLLAMA_ENUMERATION_BUDGET_S
                for model in response.models:
                    try:
                        model_family = model.details.family
                        if model_family in OLLAMA_EMBEDDING_FAMILIES:
                            continue
                        if time.monotonic() >= deadline:
                            skipped += 1
                            continue
                        model_show = client.show(model.model)
                        model_info = model_show.modelinfo
                        context_window = model_info[f"{model_family}.context_length"]
                        models.append(
                            OllamaChatModel(self, model.model, model.model, context_window)
                        )
                    except Exception as e:
                        log.error(f"Error getting Ollama model info {model}: {e}")
                if skipped:
                    log.warning(
                        f"Ollama model list is short {skipped} model(s): the host did "
                        f"not answer within {OLLAMA_ENUMERATION_BUDGET_S:.0f}s. Use "
                        f"Refresh models in NBI Settings to try again."
                    )
                # Rebound once, after the list is complete, so a reader on
                # another thread cannot serialize a half-built list: readiness
                # reads this property on a pool thread while a capabilities GET
                # reads it on the event loop. Assigning only on success also
                # keeps the models the dropdown already had when a refresh
                # fails.
                self._chat_models = models
        except Exception as e:
            log.warning(f"Failed to update supported Ollama models: {e}")
