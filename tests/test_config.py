"""Text-LLM config resolution: canonical LLM_* names win over legacy DEEPSEEK_*."""


def test_llm_config_prefers_canonical_names(monkeypatch):
    from ai import config

    monkeypatch.setenv("LLM_API_KEY", "canonical-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-key")
    assert config.llm_api_key() == "canonical-key"


def test_llm_config_falls_back_to_legacy_names(monkeypatch):
    from ai import config

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-key")
    assert config.llm_api_key() == "legacy-key"


def test_llm_config_uses_defaults_when_unset(monkeypatch):
    from ai import config

    monkeypatch.delenv("LLM_API_BASE", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_BASE", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    assert config.llm_api_base() == "https://api.deepseek.com/v1"
    assert config.llm_model() == "deepseek-v4-pro"


def test_require_llm_config_reports_canonical_names(monkeypatch):
    from ai import config

    # Base and model have built-in defaults; only the missing key raises.
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    try:
        config.require_llm_config()
        raise AssertionError("require_llm_config should raise when key is unset")
    except RuntimeError as error:
        assert "LLM_API_KEY" in str(error)
