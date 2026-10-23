"""Check the Pages artifact and links without installing website dependencies."""

from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

from scripts.build_site import ROOT, build


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        for key in ("href", "src"):
            if key in attrs:
                self.references.append(attrs[key])


class SiteTests(unittest.TestCase):
    def test_public_artifact_and_project_relative_links(self):
        with tempfile.TemporaryDirectory() as directory:
            output = build(Path(directory) / "public")
            (output / "stale-file.txt").write_text("Must not be redeployed")
            build(output)
            expected = {"index.html", "styles.css", ".nojekyll", "assets/favicon.svg"}
            self.assertEqual(expected, {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()})
            parser = PageParser()
            parser.feed((output / "index.html").read_text())
            self.assertEqual(len(parser.ids), len(set(parser.ids)), "Duplicate HTML IDs")
            for reference in parser.references:
                url = urlsplit(reference)
                if url.scheme or url.netloc:
                    # Repository documentation links must point to tracked sources.
                    prefix = "/asamonik/openhop-wm1303/"
                    if url.netloc == "github.com" and url.path.startswith(prefix):
                        for kind in ("blob/main/", "tree/main/"):
                            if url.path.startswith(prefix + kind):
                                self.assertTrue((ROOT / unquote(url.path[len(prefix + kind):])).exists(), reference)
                    continue
                self.assertFalse(url.path.startswith("/"), f"Breaks project Pages path: {reference}")
                if url.path:
                    self.assertTrue((output / unquote(url.path)).is_file(), reference)
                elif url.fragment:
                    self.assertIn(unquote(url.fragment), parser.ids, reference)

    def test_install_command_matches_readme(self):
        readme = (ROOT / "README.md").read_text()
        html = (ROOT / "site/index.html").read_text()
        command = re.search(r'id="install-command">([^<]+)</code>', html).group(1)
        self.assertIn(command, readme)


if __name__ == "__main__":
    unittest.main()
