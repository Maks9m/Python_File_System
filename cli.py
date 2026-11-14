"""Command interpreter: interactive REPL and script/stdin runner."""

from __future__ import annotations

import shlex
import string
import sys
from typing import Callable, Iterable, TextIO

from fs import FileSystem, FSError
from layout import NUM_DIRECT, FileType, Inode

PATTERN = string.ascii_uppercase.encode()   # default write pattern A..Z
_PRINTABLE = set(range(0x20, 0x7F)) - {ord('"'), ord("\\")}


def pattern_bytes(size: int, start: int = 0) -> bytes:
    """Deterministic ``size``-byte pattern 'ABC...ZAB...'."""
    return bytes(PATTERN[(start + i) % len(PATTERN)] for i in range(size))


def fill_bytes(data: bytes, size: int) -> bytes:
    """Repeat ``data`` cyclically / truncate it to exactly ``size`` bytes."""
    if not data:
        return bytes(size)
    reps = size // len(data) + 1
    return (data * reps)[:size]


def escape_bytes(data: bytes) -> str:
    """Render bytes as a readable, unambiguous string (non-printables as \\xNN)."""
    out = []
    for b in data:
        if b in _PRINTABLE:
            out.append(chr(b))
        elif b == 0x0A:
            out.append("\\n")
        elif b == 0x09:
            out.append("\\t")
        elif b in (ord('"'), ord("\\")):
            out.append("\\" + chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return '"' + "".join(out) + '"'


def _int(s: str, what: str) -> int:
    try:
        return int(s, 0)
    except ValueError:
        raise FSError(f"invalid {what}: {s!r}") from None


class UsageError(Exception):
    pass


class Shell:
    COMMANDS = {
        "mkfs": "mkfs <n>",
        "stat": "stat <name>",
        "ls": "ls",
        "create": "create <name>",
        "open": "open <name>",
        "close": "close <fd>",
        "seek": "seek <fd> <offset>",
        "read": "read <fd> <size>",
        "write": "write <fd> <size> [data]",
        "link": "link <name1> <name2>",
        "unlink": "unlink <name>",
        "truncate": "truncate <name> <size>",
        "help": "help",
        "exit": "exit | quit",
    }

    def __init__(self, fs: FileSystem, out: Callable[[str], None] = print) -> None:
        self.fs = fs
        self.out = out

    @property
    def prompt(self) -> str:
        return "fs> "

    # ------------------------------------------------------------------ dispatch

    def execute(self, line: str) -> bool:
        """Execute one command line.  Returns ``False`` when the shell should exit."""
        try:
            argv = shlex.split(line, comments=True)
        except ValueError as e:
            self.out(f"error: {e}")
            return True
        if not argv:
            return True
        cmd, args = argv[0], argv[1:]
        if cmd in ("exit", "quit"):
            return False
        handler = getattr(self, f"do_{cmd}", None)
        if handler is None:
            self.out(f"error: unknown command {cmd!r} (try 'help')")
            return True
        try:
            handler(args)
        except UsageError:
            self.out(f"usage: {self.COMMANDS.get(cmd, cmd)}")
        except FSError as e:
            self.out(f"error: {e}")
        return True

    @staticmethod
    def _nargs(args: list[str], lo: int, hi: int | None = None) -> None:
        hi = lo if hi is None else hi
        if not lo <= len(args) <= hi:
            raise UsageError

    # ------------------------------------------------------------------ formatting

    def _print_stat(self, ino: int, inode: Inode) -> None:
        bmap = self.fs.block_map(inode)
        data_blocks = len(bmap)
        ind = 1 if inode.indirect else 0
        self.out(f"  inode:      {ino}")
        self.out(f"  type:       {inode.file_type.name} ({int(inode.file_type)})")
        self.out(f"  hard_links: {inode.hard_links}")
        self.out(f"  size:       {inode.size} bytes")
        self.out(f"  blocks:     {data_blocks} data + {ind} indirect")
        self.out(f"  direct:     {inode.direct}")
        self.out(f"  indirect:   {inode.indirect}")
        ind_map = {i: b for i, b in bmap.items() if i >= NUM_DIRECT}
        if ind_map:
            self.out("  indirect map: " + " ".join(f"[{i}]={b}" for i, b in ind_map.items()))

    # ------------------------------------------------------------------ commands

    def do_help(self, args: list[str]) -> None:
        for usage in self.COMMANDS.values():
            self.out(f"  {usage}")

    def do_mkfs(self, args: list[str]) -> None:
        self._nargs(args, 1)
        sb = self.fs.mkfs(_int(args[0], "inode count"))
        self.out(f"formatted {self.fs.disk_path}: {sb.total_blocks} blocks x {sb.block_size} B, "
                 f"{sb.inode_count} inodes, bitmap@{sb.bitmap_start}, "
                 f"inodes@{sb.inode_table_start}, data@{sb.data_start}")

    def do_stat(self, args: list[str]) -> None:
        self._nargs(args, 1)
        ino, inode = self.fs.stat(args[0])
        self._print_stat(ino, inode)

    def do_ls(self, args: list[str]) -> None:
        self._nargs(args, 0)
        entries = self.fs.ls()
        for name, ino, inode in entries:
            self.out(f"  {ino:>4}  {inode.file_type.name:<9} {inode.size:>6}  {name}")
        self.out(f"  ({len(entries)} entries)")

    def do_create(self, args: list[str]) -> None:
        self._nargs(args, 1)
        ino = self.fs.create(args[0])
        self.out(f"created {args[0]!r} (inode {ino})")

    def do_open(self, args: list[str]) -> None:
        self._nargs(args, 1)
        fd = self.fs.open(args[0])
        self.out(f"fd = {fd}")

    def do_close(self, args: list[str]) -> None:
        self._nargs(args, 1)
        fd = _int(args[0], "fd")
        self.fs.close_fd(fd)
        self.out(f"closed fd {fd}")

    def do_seek(self, args: list[str]) -> None:
        self._nargs(args, 2)
        fd, off = _int(args[0], "fd"), _int(args[1], "offset")
        self.fs.seek(fd, off)
        self.out(f"fd {fd}: offset = {off}")

    def do_read(self, args: list[str]) -> None:
        self._nargs(args, 2)
        fd, size = _int(args[0], "fd"), _int(args[1], "size")
        data = self.fs.read(fd, size)
        self.out(f"fd {fd}: read {len(data)} bytes: {escape_bytes(data)}")

    def do_write(self, args: list[str]) -> None:
        self._nargs(args, 2, 3)
        fd, size = _int(args[0], "fd"), _int(args[1], "size")
        if size < 0:
            raise FSError("negative size")
        if len(args) == 3:
            data = fill_bytes(args[2].encode("utf-8"), size)
        else:
            data = pattern_bytes(size)
        n = self.fs.write(fd, data)
        self.out(f"fd {fd}: wrote {n} bytes, offset = {self.fs.open_files[fd].offset}")

    def do_link(self, args: list[str]) -> None:
        self._nargs(args, 2)
        self.fs.link(args[0], args[1])
        self.out(f"linked {args[1]!r} -> {args[0]!r}")

    def do_unlink(self, args: list[str]) -> None:
        self._nargs(args, 1)
        self.fs.unlink(args[0])
        self.out(f"unlinked {args[0]!r}")

    def do_truncate(self, args: list[str]) -> None:
        self._nargs(args, 2)
        size = _int(args[1], "size")
        self.fs.truncate(args[0], size)
        self.out(f"truncated {args[0]!r} to {size} bytes")

    # ------------------------------------------------------------------ loops

    def run_script(self, lines: Iterable[str], echo: bool = True) -> None:
        """Run commands from a script, echoing each command (for report logs)."""
        for raw in lines:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped:
                if echo:
                    self.out("")
                continue
            if stripped.startswith("#"):
                if echo:
                    self.out(stripped)
                continue
            if echo:
                self.out(f"{self.prompt}{stripped}")
            if not self.execute(stripped):
                break

    def run_interactive(self, stdin: TextIO = sys.stdin) -> None:
        while True:
            try:
                line = input(self.prompt)
            except EOFError:
                self.out("")
                break
            except KeyboardInterrupt:
                self.out("^C")
                continue
            if not self.execute(line):
                break
