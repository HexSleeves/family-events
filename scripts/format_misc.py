from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

TARGET_PATTERNS = ("Makefile", "*.mako")


def tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--", *TARGET_PATTERNS],
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


def normalize_text(text: str) -> str:
    stripped_lines = [
        line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    normalized = "\n".join(stripped_lines).rstrip("\n")
    return f"{normalized}\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Format misc text files not handled by other tools."
    )
    parser.add_argument("--check", action="store_true", help="Report files that need formatting.")
    args = parser.parse_args()

    changed: list[str] = []
    for path in tracked_paths():
        original = path.read_text()
        normalized = normalize_text(original)
        if normalized == original:
            continue
        changed.append(str(path))
        if not args.check:
            path.write_text(normalized)

    if args.check and changed:
        for path in changed:
            print(path)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
