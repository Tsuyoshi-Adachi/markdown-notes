#!/usr/bin/env python3
"""Export only explicitly public Markdown and raster images to GitHub Pages.

The repository must be private. This script does not change repository visibility.
The viewer template is trusted application code; document sources are copied only
from 公開/. Drafts, repository-root documents, hidden paths, and active image
formats such as SVG are never included in the generated site.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat


DOCUMENT_EXTENSION = ".md"
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"})
DOCUMENT_MARKER = "__DOCUMENT_PATHS_JSON__"


class BuildError(ValueError):
    """A source path or template would make the publication unsafe or invalid."""


def _safe_relative_path(path: str) -> PurePosixPath:
    """Validate a relative public path before using it as an output path."""
    parts = path.split("/")
    if (
        not path
        or "\\" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or any(part in {"", ".", ".."} or part.startswith(".") for part in parts)
    ):
        raise BuildError("Unsafe relative publication path")
    return PurePosixPath(*parts)


def _read_regular_file(path: Path) -> bytes:
    """Read one regular, unlinked file without following a final symlink."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise BuildError(f"Only regular files without hard links are allowed: {path.name}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        after = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise BuildError("A source file changed while it was being read")
        return source.read()


def _collect_public_files(directory: Path, prefix: str = "") -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for item in sorted(directory.iterdir(), key=lambda entry: entry.name):
        information = item.lstat()
        if stat.S_ISLNK(information.st_mode):
            raise BuildError(f"Symbolic links are not allowed in 公開: {item.name}")
        if item.name.startswith("."):
            continue
        relative = f"{prefix}/{item.name}" if prefix else item.name
        _safe_relative_path(relative)
        if stat.S_ISDIR(information.st_mode):
            files.extend(_collect_public_files(item, relative))
        elif stat.S_ISREG(information.st_mode):
            if information.st_nlink != 1:
                raise BuildError(f"Hard links are not allowed in 公開: {item.name}")
            if item.suffix.lower() in IMAGE_EXTENSIONS | {DOCUMENT_EXTENSION}:
                files.append((relative, _read_regular_file(item)))
        else:
            raise BuildError(f"Unsupported filesystem entry in 公開: {item.name}")
    return files


def _script_safe_json(paths: list[str]) -> str:
    """Keep file names as JSON data, including inside an HTML script element."""
    encoded = json.dumps(paths, ensure_ascii=False)
    for character, replacement in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        encoded = encoded.replace(character, replacement)
    return encoded


def build(project_root: Path) -> list[str]:
    """Create a complete new _site and return its public document paths."""
    root = project_root.resolve(strict=True)
    output = root / "_site"
    # Remove the preceding output before validation: a failed build must not leave
    # an old export that still contains a document whose sharing was withdrawn.
    if output.is_symlink():
        raise BuildError("_site must not be a symbolic link")
    if output.exists():
        if not output.is_dir():
            raise BuildError("_site must be a directory")
        shutil.rmtree(output)

    template = _read_regular_file(root / "viewer.html").decode("utf-8")
    if template.count(DOCUMENT_MARKER) != 1:
        raise BuildError("viewer.html must contain exactly one document-path marker")

    public = root / "公開"
    try:
        public_information = public.lstat()
    except FileNotFoundError:
        files: list[tuple[str, bytes]] = []
    else:
        if not stat.S_ISDIR(public_information.st_mode):
            raise BuildError("公開 must be a directory, not a link or a file")
        files = _collect_public_files(public)

    documents = sorted(path for path, _ in files if Path(path).suffix.lower() == DOCUMENT_EXTENSION)
    rendered_viewer = template.replace(DOCUMENT_MARKER, _script_safe_json(documents))
    output.mkdir()
    for relative, data in files:
        destination = output / "documents" / Path(*_safe_relative_path(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (output / "index.html").write_text(rendered_viewer, encoding="utf-8")
    return documents


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    try:
        documents = build(arguments.root)
    except (BuildError, OSError, UnicodeError) as error:
        parser.exit(1, f"Publication build failed: {error}\n")
    print(f"Built {len(documents)} public document(s).")


if __name__ == "__main__":
    main()
