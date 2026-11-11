import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

import videogen


@pytest.fixture
def spy_backends(monkeypatch):
    calls = []

    def fake(p, prompt, token):
        calls.append((p.key, prompt, token))
        return b"MP4DATA"

    monkeypatch.setattr(videogen, "BACKENDS", {k: fake for k in videogen.PROVIDERS})
    return calls


@pytest.mark.parametrize("key", list(videogen.PROVIDERS))
def test_dry_run_cli_never_calls_backend(key, spy_backends, capsys):
    assert videogen.main(["a cat", "-p", key]) == 0
    assert spy_backends == []
    out = capsys.readouterr().out
    assert "Dry run" in out and videogen.PROVIDERS[key].model in out


def test_generate_requires_approval(spy_backends, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "t")
    with pytest.raises(videogen.ApprovalRequired):
        videogen.generate("huggingface", "a cat", tmp_path / "v.mp4", approved=False)
    assert spy_backends == []


def test_confirmed_generation_writes_file(spy_backends, tmp_path, monkeypatch):
    monkeypatch.setenv("REPLICATE_API_TOKEN", "t")
    out = tmp_path / "v.mp4"
    assert videogen.main(["a cat", "-p", "replicate", "-o", str(out), "--confirm"]) == 0
    assert out.read_bytes() == b"MP4DATA"
    assert spy_backends == [("replicate", "a cat", "t")]


def test_missing_token_fails_before_call(spy_backends, tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        videogen.generate("gemini", "a cat", tmp_path / "v.mp4", approved=True)
    assert spy_backends == []


def test_all_providers_target_five_seconds():
    p = videogen.PROVIDERS
    assert p["huggingface"].params["num_frames"] / p["huggingface"].params["frame_rate"] == pytest.approx(5, abs=0.1)
    assert p["replicate"].params["duration"] == 5
    assert p["gemini"].params["duration"] == "5s"


def test_web_unapproved_post_is_dry_run(spy_backends, tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), videogen.make_handler(tmp_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        assert b"5-second" in urllib.request.urlopen(url).read()
        body = urlencode({"prompt": "a <b>cat</b>", "provider": "gemini"}).encode()
        page = urllib.request.urlopen(url, data=body).read().decode()
        assert "Dry run" in page and "<b>cat" not in page
        assert spy_backends == []
    finally:
        server.shutdown()
