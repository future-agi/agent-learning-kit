"""Create portable copies; preserve the original artifact-kit compile outputs."""
import hashlib
import gzip
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "https://static.devin.ai/artifact-kit/cc8965d841fb2ec5/"
dependencies = {}
for name in ("ds-kit.js", "ds-kit.css"):
    payload = urllib.request.urlopen(BASE + name).read()
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    dependencies[name] = payload.decode()
provenance = [
    {"url": BASE + name, "sha256": hashlib.sha256(payload.encode()).hexdigest(),
     "bytes": len(payload.encode()), "packaging": "embedded inline in portable dashboard.html"}
    for name, payload in dependencies.items()
]
for experiment in ("b3", "c3"):
    directory = ROOT / experiment
    html = (directory / "dashboard.compiled.html").read_text()
    html, css_count = re.subn(
        r'<link rel="stylesheet" href="' + re.escape(BASE) + r'ds-kit.css"[^>]*>',
        lambda _: "<style>" + dependencies["ds-kit.css"] + "</style>", html
    )
    html, js_count = re.subn(
        r'<script src="' + re.escape(BASE) + r'ds-kit.js"[^>]*></script>',
        lambda _: "<script>" + dependencies["ds-kit.js"].replace("</script", r"<\/script") + "</script>", html
    )
    assert css_count == js_count == 1
    (directory / "dashboard.html").write_text(html)
    (directory / "dependency-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(experiment, "portable bytes", len(html.encode()))
