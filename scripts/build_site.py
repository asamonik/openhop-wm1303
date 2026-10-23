"""Stage only the public landing page and its selected documentation images."""

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = ("status", "channels", "bridge-rules", "spectrum")


def build(output=None):
    output = Path(output) if output is not None else ROOT / "_site"
    # A fixed, dedicated output keeps runtime code and configuration off Pages.
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(ROOT / "site", output)
    for name in SCREENSHOTS:
        shutil.copy2(ROOT / "screenshots" / f"{name}.png", output / "assets" / f"{name}.png")
    (output / ".nojekyll").touch()
    return output


if __name__ == "__main__":
    print(f"Website ready: {build()}")
