from calldoctor import clone as cl
from calldoctor.schemas import Persona, Transcript, Turn

T = Transcript(call_id="c", source="synthetic", turns=[
    Turn(idx=0, speaker="bot", text="Aap toh bahut interested lag rahe ho sir!"),
    Turn(idx=1, speaker="customer", text="Nahi chahiye bhai"),
    Turn(idx=2, speaker="bot", text="Aapka CIBIL 800 hai"),
    Turn(idx=3, speaker="customer", text="Phone rakho"),
])


def test_cloner_sees_only_customer_lines():
    text = cl.customer_lines(T)
    assert "Nahi chahiye bhai" in text and "Phone rakho" in text
    assert "interested" not in text and "CIBIL" not in text


def test_non_verbatim_style_examples_detected():
    p = Persona(goals="g", objections=[], patience=0, verbosity=0, hindi_english_mix=0.8,
                interrupts=False, trust_level=0, will_convert_if="x", hard_stop_if="y",
                style_examples=["Nahi chahiye bhai", "Mujhe loan nahi chahiye"])
    assert cl._missing_verbatim(p, T) == ["Mujhe loan nahi chahiye"]
