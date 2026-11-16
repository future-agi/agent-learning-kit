# B3 / C3 controlled experiment evidence

These are **reports of completed controlled public-SDK experiments**, not the
production Future AGI platform and not browser-executed SDK calls. The authoritative
`result.json` and SDK `command.log` were supplied before this browser work and were
not modified or rerun.

## View

Open either `b3/dashboard.html` or `c3/dashboard.html` directly in a browser. The
portable HTML contains the recorded data, report code, and pinned artifact-kit
JavaScript/CSS. It requires no server, login, network, or secret. The browser
recording used a local ephemeral HTTP server and blocked all external requests.

Each experiment directory contains:

- `dashboard.html`: portable report to share.
- `dashboard.body.html`: editable JSX body for the Devin artifact kit.
- `dashboard-data.json`: original result data plus verbatim harness source excerpts.
- `dashboard.compiled.html`: original, untouched artifact compiler output, which
  references the pinned kit CDN. Use `dashboard.html` for offline viewing.
- `dependency-provenance.json`: URLs, decoded-content SHA256 hashes, and sizes of
  the embedded kit dependencies.
- `browser.native.webm`: single continuous **native Playwright** video.
- `browser.mp4`: one H.264 transcode of that native video, not a slideshow.
- `final.png`: 1440×900 overview screenshot.
- `browser-validation.json`: actual UI checks, native capture provenance and error list.
- `media-validation.json`: codec, dimensions, duration, return codes and provenance.
- `ffprobe.json`, `native-ffprobe.json`, ffmpeg/ffprobe logs: media validation evidence.
- `browser-command.log`: browser/media commands, separate from the original SDK log.
- `browser-run.log`: UI assertion output for the recorded run.
- `SHA256SUMS`: files within that experiment directory, excluding itself.
- `*.source.py`: exact snapshots of evidence-only automation stored at the
  parent evidence root. To rerun, place these at that parent root with their
  original names (remove `.source`), alongside `run_experiments.py` and `b3/`, `c3/`.

## Reproduce the report build

All tooling and output stay outside the product checkout. No product code change,
branch operation, commit, SDK experiment, pytest, or Ruff rerun is required.

1. `python /home/ubuntu/alk-rerun/prepare_dashboards.py`
2. Use `html_artifact_compile` on each `dashboard.body.html`, with its
   `dashboard-data.json` as `data_path`, writing `dashboard.compiled.html`.
3. `python /home/ubuntu/alk-rerun/package_dashboards.py`

The artifact tool's output references
`https://static.devin.ai/artifact-kit/cc8965d841fb2ec5/{ds-kit.js,ds-kit.css}`.
The portable packaging script creates a **separate copy**, embedding those exact
dependencies and preserving the original compile output. The corresponding
provenance file records decoded-content hashes (CDN responses may be gzip encoded).
The Devin artifact skill was unavailable in this session; the built-in
`html_artifact_create` / `html_artifact_compile` tools and generated `kit.d.ts`
provided the actual build API.

## Reproduce native capture and media checks

The captured dependency versions were Playwright 1.65.2, greenlet 3.5.6,
Chromium 159.0.8154.6 (Playwright revision 1255), and Playwright FFmpeg revision 1013.
System `ffmpeg` / `ffprobe` perform the MP4 transcode/validation. Exact commands
and runtime browser version are in the per-experiment logs/JSON.

```sh
python -m venv /home/ubuntu/alk-rerun/browser-tools
/home/ubuntu/alk-rerun/browser-tools/bin/pip install playwright==1.65.2 greenlet==3.5.6
/home/ubuntu/alk-rerun/browser-tools/bin/playwright install chromium
/home/ubuntu/alk-rerun/browser-tools/bin/python /home/ubuntu/alk-rerun/record_dashboards.py
/home/ubuntu/alk-rerun/browser-tools/bin/python /home/ubuntu/alk-rerun/record_dashboards.py --record --experiment b3
/home/ubuntu/alk-rerun/browser-tools/bin/python /home/ubuntu/alk-rerun/record_dashboards.py --record --experiment c3
python /home/ubuntu/alk-rerun/validate_media.py
```

The capture script requires clean product files at exact SHA
`023abdf3655435a4b63177b3e24ee4c14df874ca`, B3 status pass with 41 passing
assertions, and C3 status pass with 15 passing assertions, before recording.
One browser context and one page produce each native recording, with viewport
and `record_video_size` both 1440×900. The script clicks tabs, selects fixtures,
paginates all assertions, filters representative assertions and scrolls real
browser content. There is no fake run control, screenshot-to-video pipeline,
synthetic cursor or edited-together capture.

UI comparisons check assertion IDs and expressions exactly and observed results
as parsed JSON values. JavaScript displays integral floats as `1`/`0` rather than
Python's `1.0`/`0.0`; these are the same recorded JSON numeric values. Original
`result.json` remains authoritative, with its original formatting.

## Interpretation

B3: malformed inputs produce typed errors; valid controls preserve environments;
collisions and ordinary tampering are rejected. Recomputed unkeyed reviewer
metadata checksum acceptance is explicitly an **authenticity limitation**. It
does not imply source text tampering passes the source-content checks.

C3: the completion gate takes the **complete GeneratedScenarioDraftSet**.
Public-scenario structural coverage rate 1 is not readiness. All-blocked
evaluator-only unsupported prose declares no public tools. Private evaluator
blocking can change trusted readiness without changing public payload hashes.
Neither structural coverage nor draft readiness is executed agent success.
