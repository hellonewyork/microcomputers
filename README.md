# videogen — 5-second text-to-video test

A minimal app that turns a text prompt into a ~5-second MP4 using a hosted video API.

**Dry-run by default.** Without `--confirm` (CLI) or the "I approve this paid API call" checkbox (web UI),
nothing is sent to any provider. A dry run prints the exact model, parameters, and estimated cost instead.

## Providers

| `--provider` | Model | Env var | Free option? | Est. cost per 5 s clip* |
|---|---|---|---|---|
| `huggingface` (default) | `Lightricks/LTX-Video-0.9.7-distilled` via fal-ai (121 frames @ 24 fps) | `HF_TOKEN` | Yes — $0.10/month of free Inference Providers credit | ~$0.04 |
| `replicate` | `alibaba/wan-3`, 480p | `REPLICATE_API_TOKEN` | No | ~$0.125 |
| `gemini` | `gemini-omni-1.2-flash`, 360p | `GEMINI_API_KEY` | No (billing required) | ~$0.12 |
| `local` | AnimateDiff-Lightning (4-step) on SD 1.5 `emilianJR/epiCRealism`, CPU | none | Yes — open weights, runs on this machine | $0 |

\*From public pricing pages as of November 2026. Prices change, so check the provider before approving.
Google shut down the Veo 3.1 preview models in the Gemini API on 2026-10-22, so this app uses Gemini Omni Flash instead.

## Connect a provider

- **Hugging Face:** huggingface.co → Settings → Access Tokens → create a fine-grained token with
  "Make calls to Inference Providers".
- **Replicate:** replicate.com → Account → API tokens (requires billing).
- **Gemini:** aistudio.google.com → Get API key (on a billing-enabled project).

Expose the key as an environment variable (see `.env.example`). Never commit it.

## Usage

```bash
uv sync
uv run python videogen.py "A paper boat drifting down a rainy street"              # dry run
uv run python videogen.py "A paper boat drifting down a rainy street" --confirm    # real call -> output/video.mp4
uv run python videogen.py "..." -p replicate -o output/boat.mp4 --confirm
uv run python videogen.py --serve --port 8000                                       # web UI
uv run pytest                                                                        # offline tests, no API calls

# Free local generation (no key, no --confirm needed). First run downloads ~4 GB of weights.
uv sync --extra local
uv run python videogen.py "A paper boat drifting down a rainy street" -p local -o output/boat.mp4
```

The local provider renders 16 frames at 512×512 and uses ffmpeg (must be installed) to interpolate them to 5 s at 24 fps.
Expect test-clip quality, not hosted-API quality.
