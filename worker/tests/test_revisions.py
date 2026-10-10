"""Chat lines become plan parameters. No renderer of their own."""

from app.mvp import build_storyboard, plan_from_storyboard
from app.revisions import apply_edits
from test_mvp import _analysis


def _plan(monkeypatch):
    monkeypatch.setattr("app.mvp._ask_model", lambda prompt, fallback: None)
    doc = _analysis()
    board = build_storyboard(doc, "make a short")
    plan = plan_from_storyboard(board, doc)
    plan["effects"].append({
        "id": "e99",
        "type": "arrow",
        "start": 0.2,
        "end": 1.0,
        "track": "trk_f0",
        "anchor": "below",
    })
    return plan


def _headline(plan):
    return next(effect for effect in plan["effects"] if effect.get("kind") == "headline")


def test_the_four_chat_examples_change_the_plan(monkeypatch):
    plan = _plan(monkeypatch)
    before = int(_headline(plan)["fontSize"])
    zoom_before = next(clip["zoomEnd"] for clip in plan["clips"] if clip.get("focusTrack"))

    bigger = apply_edits(plan, ["Gör hooken större"])
    assert bigger["ok"]
    assert int(_headline(bigger["plan"])["fontSize"]) > before

    removed = apply_edits(bigger["plan"], ["Ta bort pilen"])
    assert removed["ok"]
    assert not any(effect.get("type") == "arrow" for effect in removed["plan"]["effects"])

    zoomed = apply_edits(removed["plan"], ["Mer zoom på personen"])
    assert zoomed["ok"]
    zoom_after = max(clip["zoomEnd"] for clip in zoomed["plan"]["clips"] if clip.get("focusTrack"))
    assert zoom_after > zoom_before

    funny = apply_edits(zoomed["plan"], ["Gör texten roligare"])
    assert funny["ok"]
    stories = [effect["text"] for effect in funny["plan"]["effects"] if effect.get("kind") == "story"]
    assert stories
    assert any(text.lower().startswith("wait") or "no way" in text for text in stories)
    assert "invented lyrics" not in " ".join(stories).lower()


def test_one_rerender_can_apply_several_lines(monkeypatch):
    plan = _plan(monkeypatch)
    result = apply_edits(plan, ["Gör hooken större", "Ta bort pilen"])
    assert result["ok"]
    assert int(_headline(result["plan"])["fontSize"]) > 120
    assert not any(effect.get("type") == "arrow" for effect in result["plan"]["effects"])
    assert result["skipped"] == []


def test_unknown_line_does_not_render_a_copy(monkeypatch):
    plan = _plan(monkeypatch)
    result = apply_edits(plan, ["byt musik till jazz"])
    assert result["ok"] is False
    assert result["plan"] is plan
