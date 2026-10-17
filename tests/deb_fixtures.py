"""Small, real Debian packages for identity tests (an `ar` archive with control and data)."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

COMMIT = "de31c94dceb290f98bc8b0784ac17d3fd3d78727"


def _tar(files: dict[str, bytes], mode: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:  # type: ignore[call-overload]
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _ar(members: list[tuple[str, bytes]]) -> bytes:
    out = bytearray(b"!<arch>\n")
    for name, content in members:
        header = (f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(content):<10}`\n").encode(
            "ascii"
        )
        out += header + content
        if len(content) % 2:
            out += b"\n"
    return bytes(out)


def make_deb(
    path: Path,
    *,
    binary: bytes = b"control-tv binary",
    version: str = "0.1.0",
    architecture: str = "amd64",
    compression: str = "gz",
) -> Path:
    control = (
        f"Package: control-tv\nVersion: {version}\nArchitecture: {architecture}\n"
        "Depends: libwebkit2gtk-4.1-0\nDescription: test\n multi-line\n"
    ).encode()
    data = _tar({"./usr/bin/control-tv": binary, "./usr/share/doc/x": b"doc"}, f"w:{compression}")
    path.write_bytes(
        _ar(
            [
                ("debian-binary", b"2.0\n"),
                ("control.tar.gz", _tar({"./control": control}, "w:gz")),
                (f"data.tar.{compression}", data),
            ]
        )
    )
    return path


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
