from notebook_intelligence.llm_providers.github_copilot_llm_provider import GitHubCopilotLLMProvider


def test_chat_models_include_recent_github_copilot_models():
    provider = GitHubCopilotLLMProvider()

    models = {model.id: model for model in provider.chat_models}

    assert models["gpt-5.3-codex"].name == "GPT-5.3-Codex"
    assert models["claude-haiku-4.5"].name == "Claude Haiku 4.5"
    assert models["claude-sonnet-4.6"].name == "Claude Sonnet 4.6"
    assert models["claude-opus-4.6"].name == "Claude Opus 4.6"
    assert models["gemini-3.1-pro"].name == "Gemini 3.1 Pro"

    assert all(model.supports_tools for model in models.values())
