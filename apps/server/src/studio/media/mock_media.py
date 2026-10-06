"""Test media for the mock provider: correct duration, aspect and fps, labelled with the request."""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from studio.media.ffmpeg import ffmpeg, font_file_arg
from studio.registry.pricing import dims_for


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


def mock_dims(resolution: str | None, aspect: str | None, cap: int = 720) -> tuple[int, int]:
    """Real dimensions, scaled down so mock renders stay fast (short side ≤ cap)."""
    w, h = dims_for(resolution, aspect)
    scale = min(1.0, cap / min(w, h))
    return _even(round(w * scale)), _even(round(h * scale))


def _label(model_id: str, mode: str, prompt: str, seed: int | None) -> list[str]:
    head = f"MOCK  {model_id}  ·  {mode}  ·  seed {seed if seed is not None else '-'}"
    body = textwrap.wrap(prompt or "(no prompt)", width=48)[:3]
    return [head, *body]


async def make_video(
    dest: Path,
    workdir: Path,
    *,
    model_id: str,
    mode: str,
    prompt: str,
    duration: float,
    resolution: str | None,
    aspect: str | None,
    fps: int = 24,
    audio: bool = False,
    seed: int | None = None,
) -> None:
    w, h = mock_dims(resolution, aspect)
    duration = max(0.5, float(duration))
    workdir.mkdir(parents=True, exist_ok=True)
    # Text goes through a file so prompt punctuation never needs drawtext escaping.
    (workdir / "label.txt").write_text("\n".join(_label(model_id, mode, prompt, seed)), encoding="utf-8")
    font = font_file_arg()
    font_opt = f"fontfile='{font}':" if font else ""
    size = max(14, h // 22)
    vf = (
        f"drawtext={font_opt}textfile=label.txt:fontcolor=white:fontsize={size}:line_spacing={size // 3}:"
        f"box=1:boxcolor=black@0.55:boxborderw={size // 2}:x=(w-text_w)/2:y=h*0.08,"
        f"drawtext={font_opt}text='%{{pts\\:hms}}':fontcolor=white:fontsize={size}:box=1:"
        f"boxcolor=black@0.55:boxborderw={size // 3}:x=w-text_w-{size}:y=h-text_h-{size}"
    )
    # Seed picks the test pattern hue so different seeds look different.
    hue = int(hashlib.sha1(str(seed).encode()).hexdigest(), 16) % 360 if seed is not None else 0
    args = ["-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}:duration={duration:.3f}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency={220 + hue}:sample_rate=48000:duration={duration:.3f}"]
    args += ["-vf", f"hue=h={hue},{vf}", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    args += ["-movflags", "+faststart", "out.mp4"]
    await ffmpeg(*args, cwd=workdir)
    (workdir / "out.mp4").replace(dest)


def make_image(
    dest: Path,
    *,
    model_id: str,
    mode: str,
    prompt: str,
    resolution: str | None,
    aspect: str | None,
    index: int = 0,
    seed: int | None = None,
) -> None:
    w, h = mock_dims(resolution, aspect, cap=1024)
    digest = hashlib.sha1(f"{seed}-{index}".encode()).digest()
    top, bottom = (digest[0], digest[1], digest[2]), (digest[3], digest[4], digest[5])
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        draw.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    try:
        font = ImageFont.truetype("arial.ttf", max(14, h // 22))
    except OSError:
        font = ImageFont.load_default()
    text = "\n".join([*_label(model_id, mode, prompt, seed), f"image {index + 1}"])
    box = draw.multiline_textbbox((0, 0), text, font=font)
    x, y = (w - (box[2] - box[0])) // 2, h // 10
    pad = 12
    draw.rectangle((x - pad, y - pad, x + box[2] - box[0] + pad, y + box[3] - box[1] + pad), fill=(0, 0, 0))
    draw.multiline_text((x, y), text, fill=(255, 255, 255), font=font)
    img.save(dest, "PNG")


async def make_audio(dest: Path, workdir: Path, *, duration: float, seed: int | None = None) -> None:
    freq = 220 + (int(hashlib.sha1(str(seed).encode()).hexdigest(), 16) % 440)
    workdir.mkdir(parents=True, exist_ok=True)
    await ffmpeg("-f", "lavfi", "-i", f"sine=frequency={freq}:duration={max(0.5, duration):.3f}",
                 "-c:a", "libmp3lame", "-b:a", "128k", "out.mp3", cwd=workdir)
    (workdir / "out.mp3").replace(dest)
