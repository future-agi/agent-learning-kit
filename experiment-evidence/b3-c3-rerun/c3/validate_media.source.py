"""Evidence-only native video transcode, probe and full decode (never synthesizes frames)."""
import hashlib
import json
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def run(command, stdout, stderr):
    with stdout.open("w") as out, stderr.open("w") as err:
        return subprocess.run(command, stdout=out, stderr=err).returncode

for name in ("b3", "c3"):
    directory = ROOT / name
    native = directory / "browser.native.webm"
    mp4 = directory / "browser.mp4"
    commands = [
        ["ffmpeg", "-y", "-i", str(native), "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(mp4)],
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(mp4)],
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(mp4), "-f", "null", "-"],
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(native)],
    ]
    with (directory / "browser-command.log").open("a") as log:
        for command in commands:
            log.write("$ " + shlex.join(command) + "\n")
    rc_transcode = run(commands[0], directory/"ffmpeg-transcode.stdout.log", directory/"ffmpeg-transcode.log")
    rc_probe = run(commands[1], directory/"ffprobe.json", directory/"ffprobe.log")
    rc_decode = run(commands[2], directory/"ffmpeg-decode.stdout.log", directory/"ffmpeg-decode.log")
    rc_native = run(commands[3], directory/"native-ffprobe.json", directory/"native-ffprobe.log")
    assert (rc_transcode, rc_probe, rc_decode, rc_native) == (0, 0, 0, 0)
    info = json.loads((directory/"ffprobe.json").read_text())
    original = json.loads((directory/"native-ffprobe.json").read_text())
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    original_video = next(s for s in original["streams"] if s["codec_type"] == "video")
    assert video["codec_name"] == "h264" and video["width"] == 1440 and video["height"] == 900
    assert original_video["width"] == 1440 and original_video["height"] == 900
    assert float(info["format"]["duration"]) > 30
    assert abs(float(info["format"]["duration"]) - float(original["format"]["duration"])) < .15
    metadata = {
        "status": "pass", "codec": video["codec_name"], "pixel_format": video["pix_fmt"],
        "width": video["width"], "height": video["height"], "duration_seconds": float(info["format"]["duration"]),
        "transcode_return_code": rc_transcode, "ffprobe_return_code": rc_probe,
        "full_ffmpeg_decode_return_code": rc_decode, "native_ffprobe_return_code": rc_native,
        "full_decode_error_log_empty": not (directory/"ffmpeg-decode.log").read_text().strip(),
        "native_capture_provenance": json.loads((directory/"browser-validation.json").read_text()),
        "native_duration_seconds": float(original["format"]["duration"]),
        "native_codec": original_video["codec_name"], "transcode_count": 1,
        "capture_method": "Playwright BrowserContext record_video_dir + record_video_size, continuous single page/context",
        "not_screenshot_synthesis": True, "synthetic_cursor_added": False,
        "mp4_sha256": hashlib.sha256(mp4.read_bytes()).hexdigest(),
        "native_sha256": hashlib.sha256(native.read_bytes()).hexdigest(), "commands": commands,
    }
    assert metadata["full_decode_error_log_empty"]
    (directory/"media-validation.json").write_text(json.dumps(metadata, indent=2)+"\n")
    print(name, "H.264", video["width"], video["height"], metadata["duration_seconds"], "seconds; full decode passed", flush=True)
