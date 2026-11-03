#!/usr/bin/env python
import math
import os
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


for stream_name in ("stdin", "stdout", "stderr"):
    stream = getattr(sys, stream_name)
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
VIDEO_DIR = ROOT / "video"
DEFAULT_AUDIO_FILE = ROOT / "audio" / "voice.mp3"
DEFAULT_OVERLAY_FILE = ROOT / "overlay.png"
AUDIO_FILE = DEFAULT_AUDIO_FILE
OVERLAY_FILE = DEFAULT_OVERLAY_FILE
FX_AUDIO_FILE = None
MEDIUM_FILE = ROOT / "medium.mp4"
SILENT_FILE = ROOT / "silent_fin.mp4"
FINAL_FILE = ROOT / "youtube_ready.mp4"
MAX_TEXT_LENGTH = 20
PAUSE_ON_ERROR = True
TEXT_FILE_NAMES = {
    "heading": "drawtext_heading.txt",
    "name": "drawtext_name.txt",
    "extra": "drawtext_extra.txt",
    "date": "drawtext_date.txt",
}


def natural_key(path):
    parts = []
    current = ""
    is_digit = False
    for char in path.name.lower():
        if char.isdigit() != is_digit:
            if current:
                parts.append(int(current) if is_digit else current)
            current = char
            is_digit = char.isdigit()
        else:
            current += char
    if current:
        parts.append(int(current) if is_digit else current)
    return parts


def app_path(*parts):
    return ROOT.joinpath(*parts)


def ffmpeg_path():
    local = app_path("bin", "ffmpeg.exe")
    if local.exists():
        return str(local)
    local_unix = app_path("bin", "ffmpeg")
    if local_unix.exists():
        return str(local_unix)
    found = shutil.which("ffmpeg")
    if found:
        return found
    fail("ffmpeg not found. In the portable Windows build it should be in the bin folder.")


def ffprobe_path():
    local = app_path("bin", "ffprobe.exe")
    if local.exists():
        return str(local)
    local_unix = app_path("bin", "ffprobe")
    if local_unix.exists():
        return str(local_unix)
    found = shutil.which("ffprobe")
    if found:
        return found
    fail("ffprobe not found. In the portable Windows build it should be in the bin folder.")


def fail(message):
    print("")
    print("ERROR: " + message)
    print("")
    if PAUSE_ON_ERROR:
        input("Press Enter to close this window...")
    sys.exit(1)


def prompt_text(label, default):
    value = input(f"{label} ({MAX_TEXT_LENGTH} chars, Enter = {default}): ").strip()
    if not value:
        return default
    if len(value) > MAX_TEXT_LENGTH:
        fail(f"Text is too long: {label}")
    return value


def validate_text(label, value, default):
    value = (value or "").strip()
    if not value:
        return default
    if len(value) > MAX_TEXT_LENGTH:
        fail(f"Text is too long: {label}")
    return value


def run(command):
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    pending = ""
    for chunk in iter(lambda: process.stdout.read(1), ""):
        if chunk == "\r":
            if pending.strip():
                print(pending.strip(), flush=True)
            pending = ""
        elif chunk == "\n":
            if pending.strip():
                print(pending.strip(), flush=True)
            pending = ""
        else:
            pending += chunk
    if pending.strip():
        print(pending.strip(), flush=True)
    result = process.wait()
    if result != 0:
        fail("ffmpeg exited with an error. Check the input files and try again.")


def probe_duration(path):
    result = subprocess.run(
        [
            ffprobe_path(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"Could not read the duration of {path.name}")
    try:
        return float(result.stdout.strip())
    except ValueError:
        fail(f"ffprobe returned an unreadable duration for {path.name}")


def escape_filter_value(value):
    return str(value).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def font_path(bold=False):
    font_name = "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"
    bundled = ROOT / "fonts" / font_name
    if bundled.exists():
        return f"fonts/{font_name}"
    if os.name == "nt":
        return "C\\:/Windows/Fonts/arialbd.ttf" if bold else "C\\:/Windows/Fonts/arial.ttf"
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate.replace(":", "\\:")
    return "Arial"


def write_text_files(texts):
    text_files = {}
    for key, file_name in TEXT_FILE_NAMES.items():
        path = ROOT / file_name
        path.write_text(texts[key], encoding="utf-8", errors="replace")
        text_files[key] = file_name
    return text_files


def drawtext(text_file, size, y, bold=False):
    font = escape_filter_value(font_path(bold))
    text_file = escape_filter_value(text_file)
    return (
        "drawtext="
        f"fontfile='{font}':"
        f"textfile='{text_file}':"
        f"fontsize={size}:"
        "fontcolor=white:"
        "x=(w-text_w)/2:"
        f"y={y}"
    )


def collect_sources(selected_sources=None):
    if selected_sources:
        sources = [Path(source) for source in selected_sources]
    else:
        if not VIDEO_DIR.exists():
            fail("The video folder is missing.")
        sources = sorted(
            [
                path
                for path in VIDEO_DIR.iterdir()
                if path.is_file()
                and path.suffix.lower() == ".mp4"
                and path.stem.lower().startswith("source")
            ],
            key=natural_key,
        )
    if not sources:
        fail("No source1.mp4, source2.mp4, ... files found in the video folder.")
    missing = [str(source) for source in sources if not source.exists()]
    if missing:
        fail("Video files not found: " + ", ".join(missing))
    return sources


def check_inputs():
    if not AUDIO_FILE.exists():
        fail("No audio file: " + str(AUDIO_FILE))
    if not OVERLAY_FILE.exists():
        fail("No PNG overlay: " + str(OVERLAY_FILE))


def position_expr(value, axis):
    value = str(value).strip().lower()
    if value == "center":
        return "(w-text_w)/2" if axis == "x" else "(h-text_h)/2"
    if value == "left" and axis == "x": return "40"
    if value == "right" and axis == "x": return "w-text_w-40"
    if value == "top" and axis == "y": return "40"
    if value == "bottom" and axis == "y": return "h-text_h-40"
    try: return str(max(0, min(4096, float(value))))
    except ValueError: return "(w-text_w)/2" if axis == "x" else "(h-text_h)/2"


def create_medium(sources, texts, text_layers=None, logo=None):
    ffmpeg = ffmpeg_path()
    text_files = write_text_files(texts)
    command = [ffmpeg, "-hide_banner", "-stats", "-y"]
    for source in sources:
        command.extend(["-i", str(source)])
    command.extend(["-i", str(OVERLAY_FILE)])
    logo_index = None
    if logo and logo.get("path"):
        logo_path = Path(logo["path"])
        if not logo_path.exists():
            fail("Logo image not found: " + str(logo_path))
        logo_index = len(sources) + 1
        command.extend(["-loop", "1", "-i", str(logo_path)])

    filters = []
    scaled_labels = []
    for index, _source in enumerate(sources):
        label = f"v{index}"
        scaled_labels.append(f"[{label}]")
        filters.append(
            f"[{index}:v]scale=1280:720:force_original_aspect_ratio=increase,"
            f"crop=1280:720,setsar=1[{label}]"
        )

    filters.append("".join(scaled_labels) + f"concat=n={len(sources)}:v=1:a=0[base]")
    overlay_index = len(sources)
    filters.append(f"[{overlay_index}:v]scale=1280:720[overlay]")
    filters.append("[base][overlay]overlay=0:0:format=auto[with_overlay]")
    current_label = "with_overlay"
    layers = list(text_layers or [])
    if not layers:
        for key, size, y in [("heading", 68, 150), ("name", 42, 250), ("extra", 42, 300), ("date", 36, 400)]:
            layers.append({"text": texts.get(key, ""), "size": size, "x": "center", "y": y,
                           "color": "white", "start": 0, "end": 999999, "animation": "none"})
    for index, layer in enumerate(layers):
        value = str(layer.get("text", "")).strip()
        if not value: continue
        text_path = ROOT / f"loopizz_text_{index}.txt"
        text_path.write_text(value, encoding="utf-8", errors="replace")
        font = escape_filter_value(layer.get("font") or font_path(bool(layer.get("bold", False))))
        text_file = escape_filter_value(str(text_path))
        start_time = max(0.0, float(layer.get("start", 0)))
        end_time = max(start_time, float(layer.get("end", 999999)))
        fade_in = max(0.001, float(layer.get("fade_in", 0.5)))
        fade_out = max(0.001, float(layer.get("fade_out", 0.5)))
        animation = str(layer.get("animation", "none")).lower()
        x_expr = position_expr(layer.get("x", "center"), "x")
        y_expr = position_expr(layer.get("y", "center"), "y")
        if animation == "slide":
            x_expr = f"if(lt(t\\,{start_time + fade_in:.3f})\\,w-text_w+(t-{start_time:.3f})/{fade_in:.3f}*(text_w+w)\\,{x_expr})"
        alpha = "1"
        if animation in ("fade", "slide"):
            alpha = f"if(lt(t\\,{start_time + fade_in:.3f})\\,(t-{start_time:.3f})/{fade_in:.3f}\\,if(gt(t\\,{end_time-fade_out:.3f})\\,({end_time:.3f}-t)/{fade_out:.3f}\\,1))"
        color = str(layer.get("color", "white")).replace("#", "0x")
        size = max(8, min(200, int(layer.get("size", 48))))
        label = f"text{index}"
        filters.append(f"[{current_label}]drawtext=fontfile='{font}':textfile='{text_file}':fontsize={size}:fontcolor={color}:x={x_expr}:y={y_expr}:enable='between(t,{start_time:.3f},{end_time:.3f})':alpha='{alpha}'[{label}]")
        current_label = label

    if logo_index is not None:
        width = max(24, min(1200, int(logo.get("width", 220))))
        x_expr = position_expr(logo.get("x", "right"), "x")
        y_expr = position_expr(logo.get("y", "top"), "y")
        start_time = max(0.0, float(logo.get("start", 0)))
        end_time = max(start_time, float(logo.get("end", 999999)))
        fade_in = max(0.001, float(logo.get("fade_in", 0.5)))
        fade_out = max(0.001, float(logo.get("fade_out", 0.5)))
        animation = str(logo.get("animation", "fade")).lower()
        logo_filter = f"[{logo_index}:v]scale={width}:-1,format=rgba"
        if animation in ("fade", "slide"):
            logo_filter += f",fade=t=in:st={start_time}:d={fade_in}:alpha=1,fade=t=out:st={max(start_time,end_time-fade_out)}:d={fade_out}:alpha=1"
        filters.append(logo_filter + "[logo_layer]")
        if animation == "slide":
            x_expr = f"if(lt(t\\,{start_time+fade_in:.3f})\\,w-overlay_w+(t-{start_time:.3f})/{fade_in:.3f}*(overlay_w+w)\\,{x_expr})"
        filters.append(f"[{current_label}][logo_layer]overlay=x={x_expr}:y={y_expr}:enable='between(t,{start_time:.3f},{end_time:.3f})':format=auto[with_logo]")
        current_label = "with_logo"
    filters.append(f"[{current_label}]null[outv]")

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-an",
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            str(MEDIUM_FILE),
        ]
    )
    run(command)


def create_final(duration_minutes=None, fx_audio=None, main_volume=1.0, fx_volume=0.35, loop_audio=False):
    ffmpeg = ffmpeg_path()
    audio_duration = probe_duration(AUDIO_FILE)
    video_duration = probe_duration(MEDIUM_FILE)
    target_duration = float(duration_minutes) * 60 if duration_minutes is not None else audio_duration
    if target_duration <= 0:
        fail("The target duration must be greater than zero.")
    if video_duration <= 0:
        fail("The intermediate video has zero length.")
    if not 0 <= main_volume <= 2:
        fail("Main audio volume must be between 0 and 2.")
    if not 0 <= fx_volume <= 2:
        fail("FX audio volume must be between 0 and 2.")
    if fx_audio and not Path(fx_audio).exists():
        fail("FX / ambience audio file not found: " + str(fx_audio))

    print(f"Main audio duration: {math.ceil(audio_duration)} seconds")
    print(f"Target video duration: {target_duration:.1f} seconds")
    print(f"Video loop duration: {video_duration:.1f} seconds")
    if fx_audio:
        print("Mixing background FX / ambience audio...")
    if loop_audio:
        print("Main audio looping: enabled")
    else:
        print("Main audio looping: disabled (silence fills any remaining time)")

    command = [ffmpeg, "-hide_banner", "-stats", "-y", "-stream_loop", "-1", "-i", str(MEDIUM_FILE)]
    if loop_audio:
        command.extend(["-stream_loop", "-1"])
    command.extend(["-i", str(AUDIO_FILE)])
    if fx_audio:
        command.extend(["-stream_loop", "-1", "-i", str(fx_audio)])

    filters = [
        f"[1:a]volume={main_volume},apad,atrim=duration={target_duration:.6f}[main]"
    ]
    if fx_audio:
        filters.append(
            f"[2:a]volume={fx_volume},atrim=duration={target_duration:.6f}[fx]"
        )
        filters.append(
            f"[main][fx]amix=inputs=2:duration=longest:dropout_transition=0,"
            f"alimiter=limit=0.95,atrim=duration={target_duration:.6f}[aout]"
        )
    else:
        filters.append("[main]anull[aout]")

    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", "0:v:0",
        "-map", "[aout]",
        "-t", f"{target_duration:.6f}",
        # The intermediate video already has its final overlay and text.
        # Stream-copy H.264 here to avoid re-encoding every frame for long outputs.
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(FINAL_FILE),
    ])
    print("Rendering the final video and audio mix...")
    run(command)


def cleanup():
    temp_text_files = [ROOT / file_name for file_name in TEXT_FILE_NAMES.values()] + list(ROOT.glob("loopizz_text_*.txt"))
    for path in (SILENT_FILE, MEDIUM_FILE, *temp_text_files):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def parse_args():
    parser = argparse.ArgumentParser(description="Replicator video builder")
    parser.add_argument("--heading", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--extra", default=None)
    parser.add_argument("--date", default=None)
    parser.add_argument("--video", action="append", default=[])
    parser.add_argument("--audio", default=None)
    parser.add_argument("--overlay", default=None)
    parser.add_argument("--fx-audio", default=None, help="Optional looping FX / ambience audio file")
    parser.add_argument("--duration-minutes", type=float, default=None, help="Output duration in minutes; defaults to main audio duration")
    parser.add_argument("--main-volume", type=float, default=1.0, help="Main audio volume (0.0 to 2.0)")
    parser.add_argument("--fx-volume", type=float, default=0.35, help="FX audio volume (0.0 to 2.0)")
    parser.add_argument("--loop-audio", action="store_true", help="Loop the main audio to fill the target duration")
    parser.add_argument("--text-config", default=None, help="JSON file containing Loopizz text layers and logo")
    parser.add_argument("--no-pause", action="store_true")
    return parser.parse_args()


def main():
    global AUDIO_FILE, OVERLAY_FILE, FX_AUDIO_FILE, PAUSE_ON_ERROR
    args = parse_args()
    PAUSE_ON_ERROR = not args.no_pause
    if args.audio:
        AUDIO_FILE = Path(args.audio)
    if args.overlay:
        OVERLAY_FILE = Path(args.overlay)
    if args.fx_audio:
        FX_AUDIO_FILE = Path(args.fx_audio)
    if args.duration_minutes is not None and args.duration_minutes <= 0:
        fail("--duration-minutes must be greater than zero.")
    if not 0 <= args.main_volume <= 2:
        fail("--main-volume must be between 0 and 2.")
    if not 0 <= args.fx_volume <= 2:
        fail("--fx-volume must be between 0 and 2.")
    os.chdir(ROOT)
    print("Loopizz: preparing a YouTube-ready video")
    print("")
    check_inputs()
    sources = collect_sources(args.video)
    print("Found videos:")
    for source in sources:
        print("  " + source.name)
    print("")

    if args.text_config:
        texts = {"heading": "", "name": "", "extra": "", "date": ""}
    elif any(value is not None for value in (args.heading, args.name, args.extra, args.date)):
        texts = {
            "heading": validate_text("Enter the heading", args.heading, "#HEADING"),
            "name": validate_text("Enter the name", args.name, "Name"),
            "extra": validate_text("Enter the extra text", args.extra, "Country"),
            "date": validate_text("Enter the date", args.date, "Event date"),
        }
    else:
        texts = {
            "heading": prompt_text("Enter the heading", "#HEADING"),
            "name": prompt_text("Enter the name", "Name"),
            "extra": prompt_text("Enter the extra text", "Country"),
            "date": prompt_text("Enter the date", "Event date"),
        }

    text_layers = None
    logo = None
    if args.text_config:
        config_path = Path(args.text_config)
        if not config_path.exists(): fail("Text configuration file not found: " + str(config_path))
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            text_layers = config.get("text_layers", [])
            logo = config.get("logo")
        except (OSError, json.JSONDecodeError) as error:
            fail("Could not read Loopizz configuration: " + str(error))

    cleanup()
    print("")
    print("Building video with text layers and logo...")
    create_medium(sources, texts, text_layers=text_layers, logo=logo)
    create_final(
        duration_minutes=args.duration_minutes,
        fx_audio=FX_AUDIO_FILE,
        main_volume=args.main_volume,
        fx_volume=args.fx_volume,
        loop_audio=args.loop_audio,
    )
    cleanup()
    print("")
    print("Done! Output file:")
    print(str(FINAL_FILE))
    print("")
    if PAUSE_ON_ERROR:
        input("Press Enter to close this window...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("")
        print("Stopped by user.")
