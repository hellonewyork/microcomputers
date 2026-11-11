"""Minimal 5-second text-to-video generator.

Dry-run by default: nothing is sent to a provider (and nothing is billed)
unless --confirm is passed on the CLI or "approve charge" is ticked in the web UI.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs

DURATION_SECONDS = 5


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    env_var: str
    model: str
    params: dict[str, Any]
    est_cost_usd: float
    pricing_note: str
    free_tier: str


# Prices were checked on the providers' public pricing pages in November 2026 and are estimates only.
PROVIDERS: dict[str, Provider] = {
    "huggingface": Provider(
        key="huggingface",
        label="Hugging Face Inference Providers (fal-ai, LTX-Video)",
        env_var="HF_TOKEN",
        model="Lightricks/LTX-Video-0.9.7-distilled",
        # 121 frames at 24 fps is about 5.04 s.
        params={"provider": "fal-ai", "num_frames": 121, "frame_rate": 24},
        est_cost_usd=0.04,
        pricing_note="fal-ai lists $0.04 per video for this model",
        free_tier="Yes: free HF accounts get $0.10/month of Inference Providers credit",
    ),
    "replicate": Provider(
        key="replicate",
        label="Replicate (Alibaba Wan 3.0)",
        env_var="REPLICATE_API_TOKEN",
        model="alibaba/wan-3",
        params={"duration": DURATION_SECONDS, "resolution": "480p", "aspect_ratio": "16:9"},
        est_cost_usd=0.125,
        pricing_note="$0.025 per output second at 480p",
        free_tier="No: pay-per-use, billing required",
    ),
    "gemini": Provider(
        key="gemini",
        label="Google Gemini API (Gemini Omni 1.2 Flash)",
        env_var="GEMINI_API_KEY",
        model="gemini-omni-1.2-flash",
        params={"duration": f"{DURATION_SECONDS}s", "resolution": "360p", "aspect_ratio": "16:9"},
        est_cost_usd=0.12,
        pricing_note="about $0.023 per output second at 360p",
        free_tier="No: video generation requires a billing-enabled project",
    ),
}


class ApprovalRequired(Exception):
    """Raised when a paid call is attempted without explicit approval."""


def plan(provider_key: str, prompt: str) -> dict[str, Any]:
    """Describe the request that would be sent, without sending it."""
    if provider_key not in PROVIDERS:
        raise ValueError(f"Unknown provider {provider_key!r}; choose from {', '.join(PROVIDERS)}")
    if not prompt.strip():
        raise ValueError("Prompt must not be empty")
    p = PROVIDERS[provider_key]
    return {
        "provider": p.label,
        "model": p.model,
        "prompt": prompt,
        "params": p.params,
        "duration_seconds": DURATION_SECONDS,
        "credential": f"{p.env_var} ({'set' if os.environ.get(p.env_var) else 'NOT set'})",
        "estimated_cost_usd": p.est_cost_usd,
        "pricing_note": p.pricing_note,
        "free_tier": p.free_tier,
    }


def _hf(p: Provider, prompt: str, token: str) -> bytes:
    from huggingface_hub import InferenceClient

    params = dict(p.params)
    client = InferenceClient(provider=params.pop("provider"), api_key=token)
    return client.text_to_video(
        prompt,
        model=p.model,
        num_frames=params.pop("num_frames"),
        extra_body=params,
    )


def _replicate(p: Provider, prompt: str, token: str) -> bytes:
    import replicate

    output = replicate.Client(api_token=token).run(p.model, input={"prompt": prompt, **p.params})
    return output.read()


def _gemini(p: Provider, prompt: str, token: str) -> bytes:
    from google import genai

    client = genai.Client(api_key=token)
    interaction = client.interactions.create(
        model=p.model,
        input=prompt,
        response_format={"type": "video", "delivery": "uri", **p.params},
    )
    video = interaction.output_video
    if not video or not video.uri:
        raise RuntimeError("Gemini did not return a video")
    file_name = f"files/{video.uri.rstrip('/').split('/')[-1]}"
    while (state := client.files.get(name=file_name).state.name) != "ACTIVE":
        if state == "FAILED":
            raise RuntimeError("Gemini video generation failed")
        time.sleep(5)
    return client.files.download(file=video.uri)


BACKENDS: dict[str, Callable[[Provider, str, str], bytes]] = {
    "huggingface": _hf,
    "replicate": _replicate,
    "gemini": _gemini,
}


def generate(provider_key: str, prompt: str, out: Path, *, approved: bool) -> Path:
    """Call the provider and save the MP4. Refuses to run unless approved=True."""
    plan(provider_key, prompt)
    if not approved:
        raise ApprovalRequired("Paid API call blocked: re-run with --confirm to approve the charge")
    p = PROVIDERS[provider_key]
    token = os.environ.get(p.env_var)
    if not token:
        raise RuntimeError(f"{p.env_var} is not set")
    data = BACKENDS[provider_key](p, prompt, token)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


# --- Web UI ---------------------------------------------------------------

PAGE = """<!doctype html><meta charset="utf-8"><title>5-second video test</title>
<style>body{{font-family:system-ui;max-width:720px;margin:2rem auto;padding:0 1rem}}
textarea{{width:100%}}pre{{background:#f4f4f4;padding:1rem;overflow:auto}}</style>
<h1>5-second text-to-video test</h1>
<form method="post">
<p><textarea name="prompt" rows="3" required placeholder="A paper boat drifting down a rainy street">{prompt}</textarea></p>
<p><select name="provider">{options}</select></p>
<p><label><input type="checkbox" name="approve" value="1"> I approve this paid API call</label>
<br><small>Unchecked = dry run, no request is sent.</small></p>
<p><button>Generate</button></p>
</form>{result}"""


def _render(prompt: str = "", provider: str = "huggingface", result: str = "") -> bytes:
    options = "".join(
        f'<option value="{k}"{" selected" if k == provider else ""}>{html.escape(p.label)} '
        f"(~${p.est_cost_usd:.3f})</option>"
        for k, p in PROVIDERS.items()
    )
    return PAGE.format(prompt=html.escape(prompt), options=options, result=result).encode()


def make_handler(out_dir: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str = "text/html; charset=utf-8", code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.startswith("/videos/"):
                f = out_dir / Path(self.path).name
                if f.is_file():
                    return self._send(f.read_bytes(), "video/mp4")
                return self._send(b"not found", "text/plain", 404)
            self._send(_render())

        def do_POST(self) -> None:
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            prompt = form.get("prompt", [""])[0]
            provider = form.get("provider", ["huggingface"])[0]
            approved = form.get("approve", [""])[0] == "1"
            try:
                details = plan(provider, prompt)
                if not approved:
                    result = "<h2>Dry run (nothing sent)</h2><pre>" + html.escape(json.dumps(details, indent=2)) + "</pre>"
                else:
                    out = generate(provider, prompt, out_dir / f"video-{int(time.time())}.mp4", approved=True)
                    result = f'<h2>Done</h2><video src="/videos/{out.name}" controls autoplay loop width="100%"></video>'
            except Exception as e:  # surface provider errors in the page
                result = f"<h2>Error</h2><pre>{html.escape(str(e))}</pre>"
            self._send(_render(prompt, provider, result))

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a 5-second video from a text prompt (dry-run by default).")
    ap.add_argument("prompt", nargs="?", help="text prompt")
    ap.add_argument("-p", "--provider", choices=PROVIDERS, default="huggingface")
    ap.add_argument("-o", "--out", type=Path, default=Path("output/video.mp4"))
    ap.add_argument("--confirm", action="store_true", help="actually call the API (may incur charges)")
    ap.add_argument("--serve", action="store_true", help="run the web UI instead")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    if args.serve:
        server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(args.out.parent))
        print(f"Serving on http://localhost:{args.port}")
        server.serve_forever()
        return 0
    if not args.prompt:
        ap.error("prompt is required (or use --serve)")

    print(json.dumps(plan(args.provider, args.prompt), indent=2))
    if not args.confirm:
        print("\nDry run: nothing was sent. Add --confirm to make the real (possibly paid) call.")
        return 0
    path = generate(args.provider, args.prompt, args.out, approved=True)
    print(f"\nSaved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
