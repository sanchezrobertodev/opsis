"""A character (or a location, prop or style) as a zip to keep or take to another studio (`GET /elements/{id}/export`,
`POST /elements/import`): `character.json`, the manifest, and the files it lists under `files/`: its pictures and the
LoRA weights of each trained identity. Never an id, an owner, a cloned voice or a run: an import makes a new element.

Reading one trusts nothing in it: the zip's list of entries is sized from its end record before zipfile reads it, and
only the members the manifest names are read, each to no more than the size the list declares."""

from __future__ import annotations

import hashlib
import json
import stat
import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from studio.characters.profile import ElementProfile
from studio.director.elements import LOOK_SLUG, SLUG, TRIGGER, ElementLook, View

FORMAT = 1
MANIFEST = "character.json"
MAX_BYTES = 2 << 30  # the zip as sent
# what its members unpack to, as declared and as read; under 2 GiB, so an export never needs ZIP64 records
MAX_UNPACKED = 2000 << 20
MAX_ENTRIES = 500
MAX_MANIFEST = 1 << 20
MEMBER = r"^files/[A-Za-z0-9][A-Za-z0-9._-]{0,119}$"  # one level under files/: no "..", no absolute path
CHUNK = 1 << 20
NOT_A_ZIP = "this isn't a zip the studio can read"


class BundleError(ValueError):
    """Why a zip can't be imported, said to the person importing it."""


class BundleImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str = Field(pattern=MEMBER)
    type: str | None = Field(
        None,
        max_length=100,
        description="Its media type, as an upload sends it: a picture kept as .bin has no name that says",
    )
    view: View = "front"
    primary: bool = Field(False, description="The first of its pictures (the main ones, or its look's)")
    sort: int = Field(0, ge=0, le=100_000)
    look: str | None = Field(None, pattern=LOOK_SLUG)


class BundleVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] = {}


class BundleIdentity(BaseModel):
    """A trained identity: its trigger word, how long it trained and its LoRA files (`high`: the high-noise expert's,
    on two-expert models)."""

    model_config = ConfigDict(extra="forbid")
    trigger: str = Field(
        pattern=TRIGGER, description="A word made up for it, as training makes; it goes into prompts"
    )
    steps: int | None = Field(None, ge=1)
    trained_at: str | None = Field(None, max_length=40)
    low: str = Field(pattern=MEMBER)
    high: str | None = Field(None, pattern=MEMBER)


Base = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")]
LookKey = Annotated[str, StringConstraints(pattern=LOOK_SLUG)]


class Manifest(BaseModel):
    """`character.json`: what an export keeps of an element."""

    model_config = ConfigDict(extra="forbid")
    format: Literal[1] = FORMAT
    kind: Literal["character", "location", "prop", "style"]
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(pattern=SLUG, description="The handle it had; an import keeps it when it's free")
    description: str = Field("", max_length=600)
    attributes: dict[str, Any] = {}
    profile: ElementProfile = ElementProfile()
    looks: dict[LookKey, ElementLook] = {}
    voice: BundleVoice | None = None
    lora: dict[Base, BundleIdentity] = Field({}, description="Trained identities by LoRA base")
    images: list[BundleImage] = Field([], max_length=MAX_ENTRIES)
    cover: str | None = Field(None, pattern=MEMBER)

    @model_validator(mode="after")
    def _consistent(self) -> Manifest:
        files = self.files()
        if len(set(files)) != len(files):
            raise ValueError("a file is listed twice")
        if unknown := sorted({i.look for i in self.images if i.look and i.look not in self.looks}):
            raise ValueError(f"pictures of looks it doesn't have: {', '.join(unknown)}")
        if self.cover and self.cover not in {i.file for i in self.images}:
            raise ValueError("the cover must be one of its pictures")
        return self

    def files(self) -> list[str]:
        """Every member it lists: its pictures, then its LoRA files."""
        return [
            *(i.file for i in self.images),
            *(f for x in self.lora.values() for f in (x.low, x.high) if f),
        ]


@dataclass(frozen=True)
class Unpacked:
    path: Path
    size: int
    sha256: str


def pack(dest: Path, manifest: Manifest, files: dict[str, Path]) -> None:
    """Write a character zip: the manifest, then each listed member from where it's kept, stored as it is (pictures
    and weights don't compress)."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as z:
        z.writestr(MANIFEST, manifest.model_dump_json(indent=2))
        for name in manifest.files():
            z.write(files[name], name)


def unpack(src: Path, work: Path) -> tuple[Manifest, dict[str, Unpacked]]:
    """The manifest of the character zip at `src` and the members it lists, each written into `work` (an empty
    folder) under a name of our own. Raises BundleError for anything that isn't a character zip within the limits;
    members it doesn't list are never read."""
    entries, directory = _directory(src)
    if entries > MAX_ENTRIES or directory > MAX_ENTRIES << 10:
        raise BundleError(f"a character zip holds at most {MAX_ENTRIES} files")
    try:
        with zipfile.ZipFile(src) as z:
            infos = z.infolist()
            if len(infos) > MAX_ENTRIES:
                raise BundleError(f"a character zip holds at most {MAX_ENTRIES} files")
            names = [i.filename for i in infos]
            by_name = {i.filename: i for i in infos}

            def member(name: str) -> zipfile.ZipInfo:
                info = by_name.get(name)
                if info is None:
                    raise BundleError(
                        f"there's no {MANIFEST} in the zip: is it a character the studio exported?"
                        if name == MANIFEST
                        else f"{name} is listed in {MANIFEST} but isn't in the zip"
                    )
                if names.count(name) > 1:
                    raise BundleError(f"{name} is in the zip twice")
                if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 0x1:
                    raise BundleError(f"{name} isn't a plain file (a folder, a link or encrypted)")
                # stored or deflated, as zip tools write by default; an offset before the start is a broken zip
                if (
                    info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or info.header_offset < 0
                ):
                    raise BundleError(NOT_A_ZIP)
                return info

            manifest = _manifest(z, member(MANIFEST))
            wanted = [member(name) for name in manifest.files()]
            if sum(i.file_size for i in wanted) > MAX_UNPACKED:
                raise BundleError(f"a character's files can come to {MAX_UNPACKED >> 20} MB at most")
            out = {
                i.filename: _extract(z, i, work / f"{n:03d}-{Path(i.filename).name}")
                for n, i in enumerate(wanted)
            }
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        NotImplementedError,
        EOFError,
        zlib.error,
        UnicodeDecodeError,
    ) as exc:  # a broken member, or a name flagged UTF-8 that isn't
        raise BundleError(NOT_A_ZIP) from exc
    return manifest, out


def _directory(src: Path) -> tuple[int, int]:
    """(entries, bytes) of the zip's list of entries, from its end record (or the ZIP64 one right before it, as
    zipfile reads them), so a list too long to read is refused before zipfile makes an object per entry."""
    with src.open("rb") as fh:
        size = fh.seek(0, 2)
        # the end record, its longest comment, and the ZIP64 locator and record before it
        fh.seek(max(0, size - (22 + 0xFFFF + 20 + 56)))
        tail = fh.read()
    at = tail.rfind(b"PK\x05\x06")
    if at < 0 or len(tail) - at < 22:
        raise BundleError(NOT_A_ZIP)
    entries, length = struct.unpack("<HI", tail[at + 10 : at + 16])
    if at >= 76 and tail[at - 20 : at - 16] == b"PK\x06\x07" and tail[at - 76 : at - 72] == b"PK\x06\x06":
        entries, length = struct.unpack("<QQ", tail[at - 44 : at - 28])
    return entries, length


def _manifest(z: zipfile.ZipFile, info: zipfile.ZipInfo) -> Manifest:
    if info.file_size > MAX_MANIFEST:
        raise BundleError(f"{MANIFEST} is too large")
    with z.open(info) as fh:
        raw = fh.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise BundleError(f"{MANIFEST} is too large")
    try:
        return Manifest.model_validate(json.loads(raw))
    except (ValueError, RecursionError) as exc:  # bad JSON (or nested too deep to read), or a ValidationError
        raise BundleError(f"{MANIFEST} isn't a character the studio can read: {_reason(exc)}") from None


def _reason(exc: ValueError | RecursionError) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'it'}: {e['msg']}" for e in exc.errors()[:5])
    return "nested too deep" if isinstance(exc, RecursionError) else str(exc)


def _extract(z: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path) -> Unpacked:
    digest, size = hashlib.sha256(), 0
    with z.open(info) as src, dest.open("wb") as out:
        while chunk := src.read(CHUNK):
            size += len(chunk)
            if size > info.file_size:  # unpacks to more than the list says
                raise BundleError(f"{info.filename} is larger than the zip says")
            digest.update(chunk)
            out.write(chunk)
    return Unpacked(dest, size, digest.hexdigest())
