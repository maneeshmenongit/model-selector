"""Supply-chain checks for the environment (the Mar 2026 LiteLLM compromise used a .pth file)."""

from __future__ import annotations

import site
import sys
from importlib import metadata
from pathlib import Path

PINNED = {"litellm": "1.103.0"}
KNOWN_BAD = {"litellm": {"1.82.7", "1.82.8"}}
# .pth files that normal tooling creates.
EXPECTED_PTH_PREFIXES = ("_virtualenv", "_editable_impl_", "__editable__", "distutils-precedence")


def site_dirs() -> list[Path]:
    dirs = list(site.getsitepackages()) if hasattr(site, "getsitepackages") else []
    dirs += [p for p in sys.path if p.endswith("site-packages")]
    return sorted({Path(d) for d in dirs if Path(d).is_dir()})


def audit() -> list[str]:
    """Return a list of problems; empty means clean."""
    problems = []
    for pkg, want in PINNED.items():
        try:
            got = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            problems.append(f"{pkg} is not installed")
            continue
        if got in KNOWN_BAD.get(pkg, ()):
            problems.append(f"{pkg}=={got} is a KNOWN COMPROMISED release - rotate keys now")
        elif got != want:
            problems.append(f"{pkg}=={got} differs from pinned {want}")
    for d in site_dirs():
        for pth in d.glob("*.pth"):
            if not pth.name.startswith(EXPECTED_PTH_PREFIXES):
                problems.append(f"unexpected .pth file: {pth}")
            elif any(line.lstrip().startswith("import ") for line in pth.read_text(errors="replace").splitlines()
                     if not pth.name.startswith(("_virtualenv", "distutils-precedence"))):
                problems.append(f".pth file executes code: {pth}")
    return problems
