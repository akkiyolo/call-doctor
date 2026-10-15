import pytest
from fastapi.testclient import TestClient

from calldoctor import web


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    s = web.get_settings()
    monkeypatch.setattr(s, "transcripts_dir", tmp_path)
    monkeypatch.setattr(s, "runs_dir", tmp_path / "runs")
    monkeypatch.setattr(web, "_enqueue", lambda job: web.JOBS.setdefault(job.id, job))
    with TestClient(web.app) as c:
        yield c


def login(c):
    return c.post("/login", data={"password": "s3cret"}, follow_redirects=False)


def test_everything_but_login_requires_auth(client):
    for path in ("/", "/new", "/jobs", "/calls/x"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/healthz").json() == {"ok": True}


def test_wrong_password_rejected_right_one_sets_cookie(client):
    assert client.post("/login", data={"password": "nope"}).status_code == 401
    r = login(client)
    assert r.status_code == 303 and web.COOKIE in r.cookies
    page = client.get("/").text
    assert "Find where the call broke" in page and "New analysis" in page


def test_paste_creates_transcript_and_job(client, tmp_path):
    login(client)
    text = "bot: Namaste ji\ncustomer: हाँ बोलिए\nbot: Loan offer hai\ncustomer: nahi chahiye"
    r = client.post("/new", data={"mode": "paste", "pasted": text, "name": "my call!", "n": "2"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    t = web.Transcript.model_validate_json((tmp_path / "my_call.json").read_text(encoding="utf-8"))
    assert [x.speaker for x in t.turns] == ["bot", "customer", "bot", "customer"]
    assert t.turns[1].text == "हाँ बोलिए"
    job = web.JOBS[r.headers["location"].rsplit("/", 1)[1]]
    assert job.call_id == "my_call" and job.n == 2
    assert "my_call" in client.get(r.headers["location"]).text


def test_bad_inputs_return_form_with_error(client):
    login(client)
    r = client.post("/new", data={"mode": "paste", "pasted": "hello"})
    assert r.status_code == 400 and "Paste at least 3 lines" in r.text
    r = client.post("/new", data={"mode": "json"},
                    files={"transcript_file": ("t.json", b'{"call_id": 1}', "application/json")})
    assert r.status_code == 400 and "Not a valid transcript" in r.text


def test_path_traversal_blocked(client):
    login(client)
    assert client.get("/audio/..%2F..%2F.env").status_code == 404
    assert client.get("/calls/..%2Fsecret").status_code == 404


def test_parse_pasted_continuation_lines():
    turns = web.parse_pasted("Agent: Hello\nji, Kosha se\nUser: haan\nuser: bolo")
    assert [(t.speaker, t.text) for t in turns] == [("bot", "Hello ji, Kosha se"), ("customer", "haan bolo")]
