"""Character zips (export and import): the manifest keeps what an element is and nothing that ties it to this studio,
and reading a zip trusts nothing in it."""

from __future__ import annotations

import json
import stat
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from studio.api.routers.assets import check_safetensors, file_type
from studio.api.routers.elements import _in_zip, _portable_voice
from studio.characters import bundle
from studio.characters.bundle import BundleError, Manifest, unpack
from studio.director.elements import unique_slug
from studio.domain.enums import AssetKind
from studio.registry.loader import get_registry

FRONT = "files/001-front.png"


def manifest(**over: object) -> dict:
    return {
        "format": 1,
        "kind": "character",
        "name": "Maya",
        "slug": "maya",
        "images": [{"file": FRONT, "view": "front", "primary": True}],
        "cover": FRONT,
        **over,
    }


def make_zip(
    path: Path, members: dict[str, bytes], doc: dict | None = None, links: tuple[str, ...] = ()
) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        if doc is not None:
            z.writestr(bundle.MANIFEST, json.dumps(doc))
        for name, data in members.items():
            z.writestr(name, data)
        for name in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
    return path


def test_a_manifest_keeps_an_element_and_lists_its_pictures_then_its_weights():
    doc = manifest(
        looks={"winter": {"name": "Winter", "description": "wearing a red wool coat"}},
        images=[{"file": FRONT, "primary": True}, {"file": "files/002-front.png", "look": "winter"}],
        lora={
            "wan-2.2-a14b": {
                "trigger": "mayazq01",
                "steps": 600,
                "low": "files/lora-wan-low.safetensors",
                "high": "files/lora-wan-high.safetensors",
            }
        },
        profile={"personality": "dry wit", "catchphrases": ["Steady now."]},
    )
    m = Manifest.model_validate(doc)
    assert m.files() == [
        FRONT,
        "files/002-front.png",
        "files/lora-wan-low.safetensors",
        "files/lora-wan-high.safetensors",
    ]
    assert Manifest.model_validate_json(m.model_dump_json()) == m


@pytest.mark.parametrize("extra", [{"id": "x"}, {"user_id": "x"}, {"cloned_voice_id": "x"}, {"format": 2}])
def test_a_manifest_refuses_what_ties_an_element_to_a_studio(extra):
    with pytest.raises(ValidationError):
        Manifest.model_validate(manifest(**extra))
    with pytest.raises(ValidationError):  # nor a run on an identity
        Manifest.model_validate(
            manifest(
                lora={"flux-3-klein": {"trigger": "mayazq01", "low": "files/l.safetensors", "run_id": "x"}}
            )
        )


@pytest.mark.parametrize(
    "over",
    [
        {"images": [{"file": FRONT, "look": "summer"}]},  # a look it doesn't have
        {"cover": "files/009-front.png"},  # a cover that isn't one of its pictures
        {"images": [{"file": FRONT}, {"file": FRONT}]},  # a file twice
        {"images": [{"file": "files/../../evil.png"}], "cover": None},  # nothing outside files/
        {"images": [{"file": "/etc/passwd"}], "cover": None},
        {"slug": "Maya Lighthouse"},
        # a trigger is a made-up word, as training makes one: a mention writes it into every prompt
        {"lora": {"flux-3-klein": {"trigger": "x), nsfw, (y", "low": "files/l.safetensors"}}},
    ],
)
def test_a_manifest_must_hang_together(over):
    with pytest.raises(ValidationError):
        Manifest.model_validate(manifest(**over))


def test_a_picture_kept_as_bin_comes_back_as_the_type_the_manifest_gives():
    """Ingest keeps a picture of a type it has no extension for (a GIF, an "image/jpg") as .bin, so the import reads
    each by its type, as an upload's, and then its name."""
    assert file_type("image/gif", "files/001-front.bin") == (AssetKind.image, "image/gif", ".gif")
    assert file_type("image/jpg", "files/001-front.bin")[:2] == (AssetKind.image, "image/jpg")
    assert file_type(None, "files/001-front.png")[:2] == (AssetKind.image, "image/png")
    with pytest.raises(HTTPException):  # neither says it's a picture
        file_type(None, "files/001-front.bin")


def test_a_file_an_upload_check_refuses_is_named():
    with pytest.raises(HTTPException) as err, _in_zip("files/004-side.webp"):
        raise HTTPException(415, "could not read this file as media")
    assert (err.value.status_code, err.value.detail) == (
        415,
        "files/004-side.webp in the zip: could not read this file as media",
    )


class Handles:
    """A session whose elements have these handles, answering unique_slug's query by its prefix as Postgres would."""

    def __init__(self, *slugs: str):
        self.slugs = slugs

    async def execute(self, statement):
        like = next(v for v in statement.compile().params.values() if isinstance(v, str))
        return SimpleNamespace(
            scalars=lambda: [s for s in self.slugs if s.startswith(like.removesuffix("%"))]
        )


async def test_a_long_handle_already_taken_is_numbered_within_40_characters():
    """Importing your own export always finds its handle taken; a 40-character one makes room for the number (it used
    to loop forever, holding up every request)."""
    base, user = "captain-bartholomew-fitzgerald-the-third", uuid.uuid4()
    assert len(base) == 40
    assert await unique_slug(Handles(base), user, base) == f"{base[:38]}-2"
    numbered = [base, *(f"{base[:38]}-{n}" for n in range(2, 10))]
    assert await unique_slug(Handles(*numbered), user, base) == f"{base[:37]}-10"
    dashed = "a" * 37 + "-bc"  # cut right after a dash: the dash goes
    assert await unique_slug(Handles(dashed), user, dashed) == "a" * 37 + "-2"
    assert await unique_slug(Handles("maya", "maya-2"), user, "Maya") == "maya-3"


def test_unpack_reads_only_the_members_the_manifest_lists(tmp_path):
    src = make_zip(
        tmp_path / "c.zip", {FRONT: b"picture", "../evil.txt": b"x", "__MACOSX/._junk": b"x"}, manifest()
    )
    work = tmp_path / "work"
    work.mkdir()
    m, files = unpack(src, work)
    assert m.name == "Maya" and list(files) == [FRONT]
    assert files[FRONT].path.read_bytes() == b"picture" and files[FRONT].size == 7
    assert [p.name for p in work.iterdir()] == ["000-001-front.png"]
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.parametrize(
    ("members", "doc", "links", "why"),
    [
        ({}, manifest(), (), "isn't in the zip"),  # a missing member
        ({FRONT: b"x"}, None, (), "no character.json"),  # no manifest
        ({}, manifest(), (FRONT,), "plain file"),  # a link, not a file
        ({FRONT: b"x"}, {"kind": "character"}, (), "character.json"),  # a manifest that isn't one
    ],
)
def test_a_zip_that_isnt_a_whole_character_is_refused(tmp_path, members, doc, links, why):
    src = make_zip(tmp_path / "c.zip", members, doc, links)
    with pytest.raises(BundleError, match=why):
        unpack(src, tmp_path)


def test_too_many_entries_or_too_many_bytes_are_refused(tmp_path, monkeypatch):
    src = make_zip(
        tmp_path / "c.zip", {FRONT: b"x" * 100, **{f"files/x{i}": b"" for i in range(4)}}, manifest()
    )
    monkeypatch.setattr(bundle, "MAX_ENTRIES", 3)
    with pytest.raises(BundleError, match="at most 3 files"):
        unpack(src, tmp_path)
    monkeypatch.setattr(bundle, "MAX_ENTRIES", 500)
    monkeypatch.setattr(bundle, "MAX_UNPACKED", 99)
    with pytest.raises(BundleError, match="at most"):
        unpack(src, tmp_path)
    (tmp_path / "junk.zip").write_bytes(b"PK not really a zip")
    with pytest.raises(BundleError, match="isn't a zip"):
        unpack(tmp_path / "junk.zip", tmp_path)


def test_a_broken_zip_is_refused_as_one_never_a_server_error(tmp_path):
    """A member that doesn't inflate, one packed in a way the studio doesn't read, or a manifest nested deeper than
    JSON reads."""
    with zipfile.ZipFile(tmp_path / "broken.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(bundle.MANIFEST, json.dumps(manifest()))
        z.writestr(FRONT, bytes(range(256)) * 20)
        info = z.getinfo(FRONT)
    data = bytearray((tmp_path / "broken.zip").read_bytes())
    # past its local header (30 bytes, then the name): the deflated bytes
    at = info.header_offset + 30 + len(FRONT)
    data[at : at + 8] = b"\xff" * 8
    (tmp_path / "broken.zip").write_bytes(data)
    with zipfile.ZipFile(tmp_path / "bzip2.zip", "w", zipfile.ZIP_BZIP2) as z:
        z.writestr(bundle.MANIFEST, json.dumps(manifest()))
        z.writestr(FRONT, b"picture")
    with zipfile.ZipFile(tmp_path / "deep.zip", "w") as z:
        z.writestr(bundle.MANIFEST, "[" * 100_000 + "]" * 100_000)
    for name, why in (
        ("broken.zip", "isn't a zip"),
        ("bzip2.zip", "isn't a zip"),
        ("deep.zip", "nested too deep"),
    ):
        with pytest.raises(BundleError, match=why):
            unpack(tmp_path / name, tmp_path)


def test_the_entry_count_comes_from_the_zip64_end_record_as_zipfile_reads_it(tmp_path, monkeypatch):
    plain = make_zip(tmp_path / "plain.zip", {f"files/x{i}": b"" for i in range(5)})
    assert bundle._directory(plain)[0] == 5
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 2)  # zipfile then writes ZIP64 end records
    big = make_zip(tmp_path / "zip64.zip", {f"files/x{i}": b"" for i in range(5)})
    assert b"PK\x06\x06" in big.read_bytes() and bundle._directory(big)[0] == 5


def test_lora_weights_must_be_safetensors(tmp_path):
    header = json.dumps(
        {"__metadata__": {"format": "pt"}, "w": {"dtype": "F16", "shape": [2], "data_offsets": [0, 4]}}
    )
    good = tmp_path / "good.safetensors"
    good.write_bytes(len(header).to_bytes(8, "little") + header.encode() + b"\x00" * 4)
    check_safetensors(good)
    deep = b"[" * 100_000 + b"]" * 100_000
    for name, data in {
        "page.safetensors": b"<html>hello</html>",
        "short.safetensors": len(header).to_bytes(8, "little") + header.encode(),
        "deep.safetensors": len(deep).to_bytes(8, "little") + deep,  # too deep for JSON to read
    }.items():
        (tmp_path / name).write_bytes(data)  # not one; the tensor's bytes lie past the end of the file
        with pytest.raises(HTTPException) as err:
            check_safetensors(tmp_path / name)
        assert err.value.status_code == 415


def test_an_exported_voice_keeps_only_what_another_studio_can_use():
    stock = get_registry().model("eleven-v4").param("voice_id").options[0]
    assert _portable_voice({"model_id": "eleven-v4", "params": {"voice_id": stock}}).params == {
        "voice_id": stock
    }
    for own in ("mock-voice-0123456789ab", "a-cloned-voice-id"):
        assert _portable_voice({"model_id": "eleven-v4", "params": {"voice_id": own}}).params == {}
    assert _portable_voice({"model_id": "no-such-model", "params": {}}) is None
