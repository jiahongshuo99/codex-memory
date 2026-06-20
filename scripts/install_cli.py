#!/usr/bin/env python3
"""Install the codex-memory CLI launcher from this plugin."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_target_dir() -> Path:
    return Path.home() / ".local" / "bin"


def install(target_dir: Path, copy: bool, force: bool) -> Path:
    root = plugin_root()
    source = root / "bin" / "codex-memory"
    if not source.exists():
        raise RuntimeError(f"missing launcher: {source}")

    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "codex-memory"
    if target.exists() or target.is_symlink():
        if not force:
            raise RuntimeError(f"{target} already exists; re-run with --force to replace it")
        target.unlink()

    if copy:
        target.write_text(
            "#!/usr/bin/env sh\n"
            "set -eu\n"
            f"PLUGIN_ROOT={sh_quote(str(root))}\n"
            'exec python3 "$PLUGIN_ROOT/scripts/codex_memory.py" "$@"\n',
            encoding="utf-8",
        )
    else:
        os.symlink(source, target)
    target.chmod(0o755)
    return target


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install codex-memory CLI launcher.")
    parser.add_argument("--target-dir", default=str(default_target_dir()))
    parser.add_argument("--copy", action="store_true", help="Copy instead of symlinking.")
    parser.add_argument("--force", action="store_true", help="Replace an existing launcher.")
    parser.add_argument("--print-path", action="store_true", help="Print the installed launcher path only.")
    args = parser.parse_args(argv)

    try:
        target = install(Path(args.target_dir).expanduser(), args.copy, args.force)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.print_path:
        print(target)
    else:
        print(f"Installed codex-memory -> {target}")
        print(f"Make sure {target.parent} is on PATH.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
