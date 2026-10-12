"""Restore additional installed packages after relocation rebuilt the venv.

The installer first installs the current OpenHop dependency set. Never downgrade
those packages to old versions; only restore missing extras (e.g. plugin deps).
"""

from importlib.metadata import distributions
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, unquote, urlparse


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def missing_requirements(inventory, installed):
    excluded = {"openhop-core", "openhop-repeater", "pymc-core", "pymc-repeater", "pip", "setuptools", "wheel"}
    for item in inventory:
        name, version = item["name"], item["version"]
        if normalized(name) in installed | excluded:
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version):
            raise ValueError("Invalid distribution name/version in recovery inventory")
        direct = item.get("direct_url") or {}
        url = direct.get("url", "")
        parsed = urlparse(url)
        if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
            relocated = unquote(parsed.path)
            if relocated == "/opt/pymc_repeater" or relocated.startswith("/opt/pymc_repeater/"):
                relocated = relocated.replace("/opt/pymc_repeater", "/opt/openhop_repeater", 1)
                url = "file://" + quote(relocated)
        if direct.get("dir_info", {}).get("editable"):
            parsed = urlparse(url)
            if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
                raise ValueError(f"Cannot restore editable package {name}: {url}")
            path = unquote(parsed.path)
            if path == "/opt/pymc_repeater" or path.startswith("/opt/pymc_repeater/"):
                path = path.replace("/opt/pymc_repeater", "/opt/openhop_repeater", 1)
            yield ["-e", path]
        elif direct:
            vcs = direct.get("vcs_info")
            if vcs:
                url = f"{vcs['vcs']}+{url}@{vcs['commit_id']}"
            if direct.get("subdirectory"):
                url += "#subdirectory=" + quote(direct["subdirectory"], safe="/")
            yield [f"{name} @ {url}"]
        else:
            yield [f"{name}=={version}"]


def main():
    path = Path(sys.argv[1])
    if not path.exists():
        return
    installed = {normalized(dist.metadata["Name"]) for dist in distributions() if dist.metadata["Name"]}
    for requirement in missing_requirements(json.loads(path.read_text()), installed):
        # Missing extras must be compatible with the already installed release.
        # --no-deps avoids silently changing the tested OpenHop package set.
        subprocess.run([sys.executable, "-m", "pip", "--no-input", "install", "--no-deps", *requirement], check=True)
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)


if __name__ == "__main__":
    main()
