from __future__ import annotations

import json
from pathlib import Path

from app import bot_profile


def test_editor_context_contains_current_profile_and_json_contract():
    context = bot_profile.editor_operation_context()
    assert "Truth outranks virality" in context
    assert "black text box or pill" in context
    assert "safe zone" in context
    assert "JSON format and allowed operations" in context


def test_rule_memory_saves_only_explicit_non_secret_rules(tmp_path: Path, monkeypatch):
    path = tmp_path / "memory.json"
    monkeypatch.setenv("CUPCAT_MEMORY_FILE", str(path))

    assert bot_profile.remember_rule_if_requested("Från och med nu: inga rutor bakom text.") is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["rules"][0]["text"] == "Från och med nu: inga rutor bakom text."
    assert bot_profile.load_user_rules()[0]["text"] == "Från och med nu: inga rutor bakom text."

    assert bot_profile.remember_rule_if_requested("Ta bort den sista texten.") is False
    assert bot_profile.remember_rule_if_requested("Kom ihåg, min API key is sk-live-very-secret-value") is False


def test_rule_memory_deduplicates_and_loads_system_prompt():
    assert bot_profile.load_system_prompt()
    assert bot_profile.CLONE_SPEC_PATH.exists()
