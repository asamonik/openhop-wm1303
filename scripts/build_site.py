"""Stage only the public static setup guide and its assets."""

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]


def build(output=None):
    output = Path(output) if output is not None else ROOT / "_site"
    # A fixed, dedicated output keeps runtime code and configuration off Pages.
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(ROOT / "site", output)
    (output / ".nojekyll").touch()
    return output


if __name__ == "__main__":
    print(f"Website ready: {build()}")
