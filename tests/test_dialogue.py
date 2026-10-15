from calldoctor import dialogue
from calldoctor.schemas import Turn


def T(i, s, x):
    return Turn(idx=i, speaker=s, text=x)


def test_bot_view_starts_with_user_and_alternates():
    turns = [T(0, "bot", "Namaste"), T(1, "customer", "Haan bolo"), T(2, "bot", "Offer hai")]
    msgs = dialogue.to_messages(turns, "bot")
    assert [m["role"] for m in msgs] == ["user", "model", "user", "model"]
    assert msgs[0]["text"] == dialogue.KICKOFF


def test_customer_view_flips_roles_and_merges_neighbours():
    turns = [T(0, "bot", "a"), T(1, "bot", "b"), T(2, "customer", "c")]
    msgs = dialogue.to_messages(turns, "customer")
    assert msgs == [{"role": "user", "text": "a\nb"}, {"role": "model", "text": "c"}]


def test_end_tags_parsed_and_stripped():
    assert dialogue.parse_customer('Theek hai, bye. <END reason="hung_up_annoyed">') == (
        "Theek hai, bye.", "hung_up_annoyed")
    assert dialogue.parse_customer("Haan bolo") == ("Haan bolo", None)
    assert dialogue.parse_bot("Dhanyavaad! <END_CALL>") == ("Dhanyavaad!", True)


def test_run_stops_on_customer_end_and_keeps_paired_seeds(monkeypatch):
    seeds = []

    def fake_bot(prompt, hist, seed):
        seeds.append(seed)
        return f"bot{len(hist)}", False

    monkeypatch.setattr(dialogue, "bot_reply", fake_bot)
    cust = lambda turns, s: ("bye", "refused") if len(turns) >= 3 else ("hmm", None)
    hist = [T(0, "bot", "hi"), T(1, "customer", "haan")]
    new, end = dialogue.run("P", cust, hist, max_turns=10, seed=7)
    assert end == "customer:refused"
    assert [t.idx for t in new] == [2, 3]
    assert seeds == [7000]
