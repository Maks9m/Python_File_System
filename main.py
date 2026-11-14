"""Entry point: ``python main.py --disk disk.img [script.txt]``.

Without a script argument the commands are read from stdin: interactively when
stdin is a terminal, otherwise in script mode (each command is echoed before
its output, which makes the output directly usable as a report log).
"""

from __future__ import annotations

import argparse
import sys

from cli import Shell
from fs import DEFAULT_MAX_OPEN, FileSystem
from layout import DEFAULT_DISK_SIZE


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="User-space block filesystem simulator")
    p.add_argument("--disk", default="disk.img", help="disk image path (default: disk.img)")
    p.add_argument("--size", type=int, default=DEFAULT_DISK_SIZE,
                   help="image size in bytes when mkfs creates a new image (default: 524288)")
    p.add_argument("--max-open", type=int, default=DEFAULT_MAX_OPEN,
                   help="open file table size (default: 64)")
    p.add_argument("--no-echo", action="store_true", help="do not echo commands in script mode")
    p.add_argument("script", nargs="?", help="command file to execute (default: stdin)")
    args = p.parse_args(argv)

    fs = FileSystem(args.disk, max_open=args.max_open, disk_size=args.size)
    shell = Shell(fs)
    try:
        if args.script:
            with open(args.script, encoding="utf-8") as f:
                shell.run_script(f, echo=not args.no_echo)
        elif sys.stdin.isatty():
            shell.run_interactive()
        else:
            shell.run_script(sys.stdin, echo=not args.no_echo)
    finally:
        fs.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
