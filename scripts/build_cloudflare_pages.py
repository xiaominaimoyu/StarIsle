"""Build the public-only Pages artifact, then run the existing release gate.

Requires an assigned HTTPS origin in PUBLIC_SITE_URL. Does not authenticate,
create cloud resources, upload files, or read dotenv/private runtime data.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from harness_safety import has_link_or_reparse_component, is_link_or_reparse

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "apps" / "web" / "dist"
GATE = ROOT / "scripts" / "release_artifact_gate.py"
REDIRECTS = "/console /login/ 302\n/console/* /login/ 302\n"
HEADERS = (
    "/*\n"
    "  X-Content-Type-Options: nosniff\n"
    "  Referrer-Policy: strict-origin-when-cross-origin\n"
    "  X-Frame-Options: DENY\n"
    "/login\n"
    "  X-Robots-Tag: noindex, nofollow\n"
    "/login/*\n"
    "  X-Robots-Tag: noindex, nofollow\n"
)


def main() -> int:
    env = dict(os.environ, PUBLIC_PRIVATE_AREA_ENABLED="false")
    gate_command = [sys.executable, "-B", str(GATE)]
    result = subprocess.run([*gate_command, "--config-only"], cwd=ROOT, env=env, check=False)
    if result.returncode:
        return result.returncode

    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print("PAGES_BUILD: pnpm is required", file=sys.stderr)
        return 1
    result = subprocess.run([pnpm, "build"], cwd=ROOT, env=env, check=False)
    if result.returncode:
        return result.returncode

    if has_link_or_reparse_component(DIST) or not DIST.is_dir():
        print("PAGES_BUILD: unsafe output directory", file=sys.stderr)
        return 1
    for name, contents in (("_redirects", REDIRECTS), ("_headers", HEADERS)):
        path = DIST / name
        if is_link_or_reparse(path):
            print("PAGES_BUILD: unsafe output file", file=sys.stderr)
            return 1
        path.write_text(contents, encoding="utf-8", newline="\n")

    login_html = (DIST / "login" / "index.html").read_text(encoding="utf-8")
    if "私人空间暂未开放" not in login_html or "<form" in login_html.casefold():
        print("PAGES_BUILD: private login must be disabled", file=sys.stderr)
        return 1
    return subprocess.run(gate_command, cwd=ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
