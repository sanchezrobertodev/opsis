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
    voice: Path | None = None,
) -> None:
    """`voice`: a speech file to play under the pattern (lipsync stand-in); the clip then lasts as long as it."""
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
    if voice is not None:
        args += ["-i", str(voice)]
        audio = True
    elif audio:
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


def make_mask(dest: Path, *, image: Path | None, points: list | None) -> None:
    """A white blob around each positive click (holes at negative ones), the size of the clicked image."""
    w, h = Image.open(image).size if image and image.exists() else (1280, 720)
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    pts = points or [[0.5, 0.5, 1]]
    r = int(min(w, h) * 0.18)
    for x, y, label in sorted(pts, key=lambda p: -p[2]):  # positives first, then negatives cut holes
        rr = r if label else r // 2
        draw.ellipse((x * w - rr, y * h - rr * 1.3, x * w + rr, y * h + rr * 1.3), fill=255 if label else 0)
    mask.save(dest, "PNG")


async def make_mask_video(dest: Path, workdir: Path, *, mask: Path | None, video: Path | None) -> None:
    """The one-frame mask held for the whole clip, at the clip's size and frame rate."""
    from studio.media.ffmpeg import probe

    workdir.mkdir(parents=True, exist_ok=True)
    info = await probe(video) if video and video.exists() else None
    w, h = (info.width or 1280, info.height or 720) if info else (1280, 720)
    seconds = (info.duration_ms or 5000) / 1000 if info else 5.0
    fps = (info.fps or 24) if info else 24
    if mask is None or not mask.exists():
        mask = workdir / "mask.png"
        make_mask(mask, image=None, points=None)
    await ffmpeg("-loop", "1", "-i", str(mask), "-t", f"{seconds:.3f}", "-r", f"{fps}",
                 "-vf", f"scale={w // 2 * 2}:{h // 2 * 2},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                 "out.mp4", cwd=workdir)
    (workdir / "out.mp4").replace(dest)


# ---------------------------------------------------------------- 3D stand-ins


def make_glb(dest: Path, *, seed: int | None = None, size: float = 0.8) -> None:
    """A valid binary glTF of one coloured box standing on the ground (y from 0 to `size`)."""
    import json
    import struct

    h = hashlib.sha1(str(seed).encode()).digest()
    color = [0.25 + h[0] / 400, 0.25 + h[1] / 400, 0.25 + h[2] / 400, 1.0]
    s = size / 2
    faces = [  # normal, then the face's four corners
        ((1, 0, 0), [(s, 0, -s), (s, size, -s), (s, size, s), (s, 0, s)]),
        ((-1, 0, 0), [(-s, 0, s), (-s, size, s), (-s, size, -s), (-s, 0, -s)]),
        ((0, 1, 0), [(-s, size, -s), (-s, size, s), (s, size, s), (s, size, -s)]),
        ((0, -1, 0), [(-s, 0, s), (-s, 0, -s), (s, 0, -s), (s, 0, s)]),
        ((0, 0, 1), [(-s, 0, s), (s, 0, s), (s, size, s), (-s, size, s)]),
        ((0, 0, -1), [(s, 0, -s), (-s, 0, -s), (-s, size, -s), (s, size, -s)]),
    ]
    positions, normals, indices = [], [], []
    for n, corners in faces:
        base = len(positions)
        positions += corners
        normals += [n] * 4
        indices += [base, base + 1, base + 2, base, base + 2, base + 3]
    pos = b"".join(struct.pack("<3f", *p) for p in positions)
    nor = b"".join(struct.pack("<3f", *v) for v in normals)
    idx = struct.pack(f"<{len(indices)}H", *indices)
    idx += b"\x00" * (-len(idx) % 4)
    binary = pos + nor + idx
    gltf = {
        "asset": {"version": "2.0", "generator": "ai-media-studio mock"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "mock"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": color, "metallicFactor": 0, "roughnessFactor": 0.8}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(nor), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos) + len(nor), "byteLength": len(indices) * 2, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
             "min": [-s, 0, -s], "max": [s, size, s]},
            {"bufferView": 1, "componentType": 5126, "count": len(normals), "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": len(indices), "type": "SCALAR"},
        ],
    }
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary)
    dest.write_bytes(struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(text), b"JSON") + text
                     + struct.pack("<I4s", len(binary), b"BIN\x00") + binary)


def make_splat_ply(dest: Path, *, seed: int | None = None, count: int = 12000) -> None:
    """A small Gaussian-splat world in the common 3DGS PLY layout: a tiled floor and a ring of coloured pillars."""
    import math
    import random
    import struct

    rnd = random.Random(seed)
    sh0 = 0.28209479177387814
    points = []
    for _ in range(count):
        if rnd.random() < 0.6:  # floor
            x, z, y = rnd.uniform(-6, 6), rnd.uniform(-6, 6), 0.0
            tile = (math.floor(x) + math.floor(z)) % 2
            rgb = (0.55, 0.5, 0.45) if tile else (0.35, 0.32, 0.3)
        else:  # pillars
            k = rnd.randrange(8)
            a = k / 8 * math.tau
            x, z, y = 4 * math.cos(a) + rnd.gauss(0, 0.15), 4 * math.sin(a) + rnd.gauss(0, 0.15), rnd.uniform(0, 3)
            rgb = (0.5 + 0.5 * math.cos(a), 0.5 + 0.5 * math.sin(a), 0.6)
        points.append((x, y, z, *[(c - 0.5) / sh0 for c in rgb], 3.0, *[math.log(0.06)] * 3, 1.0, 0.0, 0.0, 0.0))
    props = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
             "rot_0", "rot_1", "rot_2", "rot_3"]
    header = "ply\nformat binary_little_endian 1.0\n" + f"element vertex {len(points)}\n" + \
        "".join(f"property float {p}\n" for p in props) + "end_header\n"
    dest.write_bytes(header.encode() + b"".join(struct.pack(f"<{len(props)}f", *p) for p in points))
