"""Restore additional installed packages after relocation rebuilt the venv.

The installer first installs the current OpenHop dependency set. Never downgrade
those packages to old versions; only restore missing extras (e.g. plugin deps).
"""

from importlib.metadata import distributions
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, unquote, urlparse


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def archive_hash(archive):
    """Keep a recorded digest that pip's URL parser can actually enforce."""
    if not isinstance(archive, dict):
        raise ValueError("Invalid archive hash metadata in recovery inventory")
    hashes = archive.get("hashes")
    if hashes is None:
        legacy = archive.get("hash")
        if legacy is None:
            return None
        if not isinstance(legacy, str) or "=" not in legacy:
            raise ValueError("Invalid archive hash in recovery inventory")
        algorithm, digest = legacy.split("=", 1)
        hashes = {algorithm: digest}
    if not isinstance(hashes, dict):
        raise ValueError("Invalid archive hash map in recovery inventory")
    if not hashes:
        return None
    # Prefer SHA-256; older pip versions also support these URL fragment hashes.
    for algorithm in ("sha256", "sha512", "sha384", "sha224", "sha1", "md5"):
        if algorithm not in hashes:
            continue
        digest = hashes[algorithm]
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]+", digest)
                or len(digest) != hashlib.new(algorithm).digest_size * 2):
            raise ValueError("Invalid archive hash digest in recovery inventory")
        return f"{algorithm}={digest}"
    raise ValueError("Cannot enforce recorded archive hash with pip")


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
                url = parsed._replace(netloc="", path=quote(relocated)).geturl()
        if direct.get("dir_info", {}).get("editable"):
            parsed = urlparse(url)
            if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
                raise ValueError(f"Cannot restore editable package {name}: {url}")
            path = unquote(parsed.path)
            if path == "/opt/pymc_repeater" or path.startswith("/opt/pymc_repeater/"):
                path = path.replace("/opt/pymc_repeater", "/opt/openhop_repeater", 1)
            yield ["-e", path]
        elif direct:
            parsed = urlparse(url)
            url = parsed._replace(fragment="").geturl()
            fragments = []
            vcs = direct.get("vcs_info")
            if vcs:
                url = f"{vcs['vcs']}+{url}@{vcs['commit_id']}"
            elif "archive_info" in direct:
                digest = archive_hash(direct["archive_info"])
                if digest:
                    fragments.append(digest)
            if direct.get("subdirectory"):
                fragments.append("subdirectory=" + quote(direct["subdirectory"], safe="/"))
            if parsed.fragment:
                fragments.append(parsed.fragment)
            if fragments:
                url += "#" + "&".join(fragments)
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
