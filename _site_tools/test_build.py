"""Security-boundary and withdrawal tests for the selective Pages export."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from build import BuildError, DOCUMENT_MARKER, _safe_relative_path, build


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "viewer.html").write_text(
            '<!doctype html><script type="application/json" id="document-paths">'
            + DOCUMENT_MARKER
            + "</script>",
            encoding="utf-8",
        )
        (self.root / "公開").mkdir()
        (self.root / "下書き").mkdir()

    def write(self, relative: str, contents: bytes = b"example") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def listed_paths(self) -> list[str]:
        html = (self.root / "_site/index.html").read_text(encoding="utf-8")
        return json.loads(html.split('id="document-paths">', 1)[1].split("</script>", 1)[0])

    def test_withdraw_one_document_preserves_other_and_private_source(self) -> None:
        withdrawn = self.write("公開/README.md", b"# Document to withdraw")
        remaining = self.write("公開/授業/other.md", b"# Keep this public")
        self.assertEqual(build(self.root), ["README.md", "授業/other.md"])
        self.assertTrue((self.root / "_site/documents/README.md").exists())
        before = (self.root / "_site/documents/授業/other.md").read_bytes()

        retained_source = self.root / "下書き/README.md"
        withdrawn.rename(retained_source)
        self.write("_site/obsolete-output.html", b"obsolete")
        self.assertEqual(build(self.root), ["授業/other.md"])

        self.assertFalse((self.root / "_site/documents/README.md").exists())
        self.assertFalse((self.root / "_site/obsolete-output.html").exists())
        self.assertEqual(retained_source.read_bytes(), b"# Document to withdraw")
        self.assertEqual((self.root / "_site/documents/授業/other.md").read_bytes(), before)
        self.assertEqual(remaining.read_bytes(), before)
        self.assertEqual(self.listed_paths(), ["授業/other.md"])

    def test_export_allowlist_excludes_private_hidden_and_active_content(self) -> None:
        self.write("README.md", b"private root document")
        self.write("下書き/secret.md", b"private draft")
        self.write("公開/.hidden.md", b"hidden")
        self.write("公開/.private/nested.md", b"hidden directory")
        self.write("公開/visible.md", b"# Public")
        self.write("公開/images/picture.PNG", b"raster bytes")
        self.write("公開/images/vector.svg", b"<svg onload='alert(1)'/>")
        self.write("公開/script.js", b"alert(1)")
        self.write("公開/style.html", b"<script>alert(1)</script>")
        self.write("公開/secret.json", b'{"secret":true}')

        build(self.root)
        generated = {
            path.relative_to(self.root / "_site").as_posix()
            for path in (self.root / "_site").rglob("*")
            if path.is_file()
        }
        self.assertEqual(generated, {"index.html", "documents/visible.md", "documents/images/picture.PNG"})
        self.assertEqual(self.listed_paths(), ["visible.md"])

    def test_symlink_to_draft_is_rejected_and_old_output_is_cleared(self) -> None:
        draft = self.write("下書き/private.md", b"draft")
        self.write("_site/documents/previous.md", b"obsolete")
        (self.root / "公開/borrowed.md").symlink_to(draft)
        with self.assertRaises(BuildError):
            build(self.root)
        self.assertFalse((self.root / "_site").exists())
        self.assertEqual(draft.read_bytes(), b"draft")

    def test_directory_symlink_is_rejected(self) -> None:
        self.write("下書き/private.md", b"draft")
        (self.root / "公開/borrowed-folder").symlink_to(self.root / "下書き", target_is_directory=True)
        with self.assertRaises(BuildError):
            build(self.root)

    def test_hardlink_to_draft_is_rejected(self) -> None:
        draft = self.write("下書き/private.md", b"draft")
        os.link(draft, self.root / "公開/borrowed.md")
        with self.assertRaises(BuildError):
            build(self.root)

    def test_public_root_symlink_is_rejected(self) -> None:
        (self.root / "公開").rmdir()
        (self.root / "公開").symlink_to(self.root / "下書き", target_is_directory=True)
        with self.assertRaises(BuildError):
            build(self.root)

    def test_json_keeps_japanese_and_html_like_filenames_as_data(self) -> None:
        paths = [
            '日本語/引用"と&記号.md',
            "<script>驚き.md",
            "</script><script>見出し.md",
            "授業\u2028資料\u2029.MD",
        ]
        for name in paths:
            self.write("公開/" + name, b"# Fine")
        self.assertEqual(build(self.root), sorted(paths))
        self.assertEqual(self.listed_paths(), sorted(paths))
        html = (self.root / "_site/index.html").read_text(encoding="utf-8")
        embedded = html.split('id="document-paths">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn("<", embedded)
        self.assertNotIn(">", embedded)
        self.assertNotIn("&", embedded)
        self.assertNotIn("\u2028", embedded)
        self.assertNotIn("\u2029", embedded)
        for name in paths:
            self.assertTrue((self.root / "_site/documents" / name).is_file())

    def test_empty_or_absent_public_folder_clears_all_previous_documents(self) -> None:
        self.write("_site/documents/withdrawn.md", b"withdrawn")
        self.assertEqual(build(self.root), [])
        self.assertEqual(self.listed_paths(), [])
        self.assertFalse((self.root / "_site/documents").exists())
        (self.root / "公開").rmdir()
        self.assertEqual(build(self.root), [])
        self.assertEqual(self.listed_paths(), [])

    def test_traversal_hidden_and_ambiguous_paths_are_rejected(self) -> None:
        for path in ["", "../secret.md", "/secret.md", "folder/../secret.md", "folder//x.md", ".hidden/x.md", "folder/.x.md", "folder\\x.md", "file\n.md"]:
            with self.subTest(path=path), self.assertRaises(BuildError):
                _safe_relative_path(path)

    def test_output_symlink_cannot_delete_or_write_outside_output(self) -> None:
        draft = self.write("下書き/private.md", b"draft")
        (self.root / "_site").symlink_to(self.root / "下書き", target_is_directory=True)
        with self.assertRaises(BuildError):
            build(self.root)
        self.assertEqual(draft.read_bytes(), b"draft")

    def test_template_must_have_exactly_one_marker(self) -> None:
        for template in ["missing", DOCUMENT_MARKER * 2]:
            with self.subTest(template=template):
                (self.root / "viewer.html").write_text(template, encoding="utf-8")
                with self.assertRaises(BuildError):
                    build(self.root)


if __name__ == "__main__":
    unittest.main()
