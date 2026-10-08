"""SAM 3's run-length masks (fal sam-3/video-rle) decoded into a matte clip, and the route that asks for it."""

from __future__ import annotations

import subprocess

import fal_client
import pytest
from test_live_adapters import FakeFal

from studio.config import get_settings
from studio.media.ffmpeg import FFMPEG
from studio.media.rle import decode_frame, mask_video
from studio.providers.fal import FalAdapter
from studio.registry.loader import load_registry
from studio.registry.validate import MediaRef


def test_runs_are_start_and_length_pairs_row_by_row():
    # a 4 x 3 frame: pixels 1-2 of the first row, then the whole last row (and a run past the end is cut)
    frame = decode_frame("1 2 8 10", 4, 3)
    assert list(frame) == [0, 255, 255, 0, 0, 0, 0, 0, 255, 255, 255, 255]
    assert decode_frame("", 4, 3) == bytes(12) and decode_frame(None, 2, 2) == bytes(4)


def read_back(path, width: int, height: int) -> list[bytes]:
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    size = width * height
    return [raw[i:i + size] for i in range(0, len(raw), size)]


async def test_masks_become_a_matte_clip_at_the_clips_size_and_rate(tmp_path):
    clip = MediaRef("input_video", "v", "video", duration_ms=250, fps=12.0, width=64, height=48)
    square = " ".join(f"{row * 64 + 16} 16" for row in range(16, 32))  # a 16 x 16 square at (16, 16)
    dest = tmp_path / "matte.mp4"
    mime = await mask_video([square, square, None], media=[clip], dest=dest, workdir=tmp_path)
    assert mime == "video/mp4"
    frames = read_back(dest, 64, 48)
    assert len(frames) == 3
    first = frames[0]
    assert first[24 * 64 + 24] > 200 and first[2 * 64 + 2] < 50  # inside the square, and the corner
    assert max(frames[2]) < 50  # a frame without a mask is black


async def test_a_matte_needs_the_clip_size_and_an_object(tmp_path):
    with pytest.raises(ValueError, match="no frame"):
        await mask_video(["", None], media=[], dest=tmp_path / "m.mp4", workdir=tmp_path)
    with pytest.raises(ValueError, match="size is unknown"):
        await mask_video(["0 4"], media=[MediaRef("input_video", "v", "video")], dest=tmp_path / "m.mp4",
                         workdir=tmp_path)


async def test_the_tracker_route_keeps_the_masks_for_ingest():
    route = load_registry(get_settings().resolve_registry_dir()).model("sam-3-video").route("sam-3-video@fal")
    answer = {"rle": ["657580 2 658859 4", "657581 3"], "boxes": [[0.74, 0.74, 0.04, 0.05]] * 2,
              "metadata": [{"index": 0}, {"index": 1}], "boundingbox_frames_zip": None}
    fake = FakeFal([fal_client.Completed(logs=[], metrics={})], result=answer)
    done = await FalAdapter(client=fake).poll(route, "fal-ai/sam-3::req-1")
    assert done.status == "succeeded" and len(done.outputs) == 1
    out = done.outputs[0]
    assert out.kind == "mask_video" and out.data == answer["rle"] and out.url is None
    empty = FakeFal([fal_client.Completed(logs=[], metrics={})], result={**answer, "rle": []})
    assert (await FalAdapter(client=empty).poll(route, "fal-ai/sam-3::req-2")).status == "failed"


@pytest.mark.parametrize("body", [
    '["0 4", "", "4 4"]',                     # a JSON list
    '{"rle": ["0 4", "", "4 4"]}',            # a JSON object holding it
    "0 4\n\n4 4",                             # one mask per line
])
async def test_masks_in_a_linked_file_are_downloaded_first(tmp_path, body):
    import respx

    from studio.media.rle import parse_masks

    assert parse_masks(body) == ["0 4", "", "4 4"]
    clip = MediaRef("input_video", "v", "video", fps=24.0, width=4, height=2)
    with respx.mock:
        respx.get("https://v3.fal.media/files/masks.json").respond(200, text=body)
        dest = tmp_path / "matte.mp4"
        await mask_video({"url": "https://v3.fal.media/files/masks.json", "content_type": "application/json"},
                         media=[clip], dest=dest, workdir=tmp_path)
    frames = read_back(dest, 4, 2)
    assert len(frames) == 3 and frames[0][1] > 200 and frames[0][5] < 50 and max(frames[1]) < 50
