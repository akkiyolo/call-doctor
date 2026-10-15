import pytest

from calldoctor import replay as rp
from calldoctor.schemas import Transcript, Turn

T = Transcript(call_id="c", source="synthetic", turns=[
    Turn(idx=i, speaker="bot" if i % 2 == 0 else "customer", text=f"t{i}") for i in range(6)
])


def test_fork_keeps_turns_before_break_only():
    h = rp.fork(T, 4)
    assert [x.idx for x in h] == [0, 1, 2, 3]
    assert h[-1].speaker == "customer"  # the bot speaks next, at the break turn


def test_fork_rejects_customer_or_out_of_range_break():
    with pytest.raises(ValueError):
        rp.fork(T, 3)
    with pytest.raises(ValueError):
        rp.fork(T, 99)


def test_arms_get_identical_seeds(monkeypatch):
    calls = []
    monkeypatch.setattr(rp, "one_run", lambda cid, arm, bp, p, h, sd, mt: calls.append((arm, sd)) or
                        rp.ReplayRun(call_id=cid, arm=arm, seed=sd, turns=[], end_reason="x"))
    jobs = [("c", arm, "P", None, [], sd) for sd in rp.seeds(3) for arm in ("original", "patched")]
    rp.run_jobs(jobs, 4)
    by_arm = {a: sorted(sd for x, sd in calls if x == a) for a in ("original", "patched")}
    assert by_arm["original"] == by_arm["patched"] and len(by_arm["original"]) == 3
