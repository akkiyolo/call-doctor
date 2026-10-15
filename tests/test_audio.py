import json

from calldoctor import audio, voice
from calldoctor.config import get_settings
from calldoctor.schemas import Turn


def W(text, sid, typ="word"):
    return {"text": text, "type": typ, "speaker_id": sid}


def test_words_merge_into_turns_first_speaker_is_bot():
    words = [W("Namaste", "s0"), W(" ", "s0", "spacing"), W("ji", "s0"), W(" ", None, "spacing"),
             W("हाँ", "s1"), W(" ", "s1", "spacing"), W("bolo", "s1"), W("(laughs)", "s1", "audio_event"),
             W("Offer", "s0")]
    turns, mapping = audio.words_to_turns(words)
    assert mapping == {"s0": "bot", "s1": "customer"}
    assert [(t.speaker, t.text) for t in turns] == [("bot", "Namaste ji"), ("customer", "हाँ bolo"), ("bot", "Offer")]
    assert [t.idx for t in turns] == [0, 1, 2]


def test_bot_speaker_override():
    turns, _ = audio.words_to_turns([W("Hello?", "s0"), W("Kosha Finance se", "s1")], bot_speaker="s1")
    assert [t.speaker for t in turns] == ["customer", "bot"]


def test_voice_stitch_caches_per_turn(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "reports_dir", tmp_path)
    calls = []
    monkeypatch.setattr(voice.audio, "tts", lambda text, vid: calls.append(text) or b"\x00\x00" * 2400)
    turns = [Turn(idx=0, speaker="bot", text="a"), Turn(idx=1, speaker="customer", text="b")]
    seg = voice.stitch(turns)
    voice.stitch(turns)
    assert calls == ["a", "b"]  # second render served from cache
    assert abs(len(seg) - (2 * 100 + 2 * voice.GAP_MS)) <= 2  # 100ms per fake turn + gaps
    name = voice.export(seg, "x")
    assert (tmp_path / "audio" / name).stat().st_size > 0


def test_select_calls_prefers_env_then_improvement(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "runs_dir", tmp_path)
    monkeypatch.setattr(voice.store, "transcript_ids", lambda: ["a", "b", "c"])
    for cid, o, p in (("a", 1, 2), ("b", 1, 5), ("c", 3, 3)):
        (tmp_path / cid).mkdir()
        (tmp_path / cid / "summary.json").write_text(json.dumps(
            {"original": {"mean_composite": o}, "patched": {"mean_composite": p}}))
    monkeypatch.setattr(s, "voice_calls", [])
    assert voice.select_calls(2) == ["b", "a"]
    monkeypatch.setattr(s, "voice_calls", ["c"])
    assert voice.select_calls(2) == ["c"]
