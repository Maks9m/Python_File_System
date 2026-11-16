"""Shell-like helpers for the demos: every do_* prints the command and its output
(like a real shell session) and keeps the log so it can be saved to reports/."""

import os

import fs
from fs import BLOCK_SIZE, TYPE_NAMES, FSError
from kernel import Kernel

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = os.path.join(HERE, "disk.img")

k = None                                     # the kernel the commands go to
lines = []


def start():
    """Fresh disk image, new kernel."""
    global k
    if os.path.exists(IMAGE):
        os.remove(IMAGE)
    k = Kernel(IMAGE)


def remount():
    global k
    k.unmount()
    k = Kernel(IMAGE)
    log("(unmounted and mounted " + os.path.basename(IMAGE) + " again)")


def save(report_name):
    path = os.path.join(HERE, "reports", report_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print()
    print("log saved to " + path)


def log(text=""):
    print(text)
    lines.append(text)


def note(text):
    log()
    log("// " + text)


def section(title):
    log()
    log("=" * 70)
    log(title)
    log("=" * 70)


def log_format():
    section("FS data format")
    for line in fs.__doc__.strip().splitlines()[3:]:
        log(line)


def pattern(size):
    """size bytes of ABC...ZABC... (easy to recognise when read back)."""
    result = b""
    for i in range(size):
        result += bytes([ord("A") + i % 26])
    return result


def show_bytes(data):
    """Bytes as text; zero bytes as \\0, long runs of zeros shortened."""
    text = ""
    i = 0
    while i < len(data):
        if data[i] == 0:
            j = i
            while j < len(data) and data[j] == 0:
                j += 1
            if j - i > 6:
                text += "\\0\\0...(" + str(j - i) + " zero bytes)...\\0\\0"
            else:
                text += "\\0" * (j - i)
            i = j
        else:
            text += chr(data[i])
            i += 1
    if len(text) > 120:
        text = text[:60] + " ... " + text[-30:] + "   (" + str(len(data)) + " bytes)"
    return text


def show_ptrs(ptrs):
    """Block numbers like [8, 9, -, 11]; '-' = no block. Trailing '-' are dropped."""
    last = len(ptrs)
    while last > 0 and ptrs[last - 1] == 0:
        last -= 1
    items = []
    for b in ptrs[:last]:
        if b == 0:
            items.append("-")
        else:
            items.append(str(b))
    return "[" + ", ".join(items) + "]"


def show_free():
    log("(free blocks: " + str(k.free_blocks()) + ")")


def error(e):
    log("error: " + str(e))


# ------------------------------------------------------------------ commands

def do_mkfs(n):
    log("> mkfs " + str(n))
    try:
        k.mkfs(n)
        log("formatted: " + str(k.fs.disk.block_count) + " blocks of " + str(BLOCK_SIZE)
            + " bytes, " + str(n) + " file descriptors, " + str(k.free_blocks()) + " free blocks")
    except FSError as e:
        error(e)


def do_stat(path):
    log("> stat " + path)
    try:
        s = k.stat(path)
    except FSError as e:
        error(e)
        return
    name = s.name
    if s.type == fs.SYMLINK:
        name += " -> " + s.target
    indirect = "-"
    if s.indirect != 0:
        indirect = str(s.indirect) + " -> " + show_ptrs(s.indirect_ptrs)
    log("  File: " + name)
    log("  Size: " + str(s.size).ljust(10) + "Blocks: " + str(s.blocks).ljust(8)
        + "IO Block: " + str(BLOCK_SIZE).ljust(6) + TYPE_NAMES[s.type])
    log(" Inode: " + str(s.inode).ljust(10) + "Links: " + str(s.links))
    log("   Map: direct " + show_ptrs(s.direct) + "   indirect " + indirect)


def do_ls(path=None):
    if path is None:
        log("> ls -l")
    else:
        log("> ls -l " + path)
    try:
        files = k.ls(path)
    except FSError as e:
        error(e)
        return
    total = 0
    for s in files:
        total += s.blocks
    log("total " + str(total) + " blocks")
    if len(files) > 0:
        log("inode  type  links   size  blocks  name")
    for s in files:
        t = "-"                                  # the type letters of ls -l
        name = s.name
        if s.type == fs.DIRECTORY:
            t = "d"
        if s.type == fs.SYMLINK:
            t = "l"
            name += " -> " + s.target
        log(str(s.inode).rjust(5) + "  " + t.rjust(4) + "  " + str(s.links).rjust(5) + "  "
            + str(s.size).rjust(5) + "  " + str(s.blocks).rjust(6) + "  " + name)


def do_create(path):
    log("> create " + path)
    try:
        log("descriptor = " + str(k.create(path)))
    except FSError as e:
        error(e)


def do_mkdir(path):
    log("> mkdir " + path)
    try:
        log("descriptor = " + str(k.mkdir(path)))
    except FSError as e:
        error(e)


def do_rmdir(path):
    log("> rmdir " + path)
    try:
        k.rmdir(path)
    except FSError as e:
        error(e)


def do_cd(path):
    log("> cd " + path)
    try:
        k.cd(path)
    except FSError as e:
        error(e)


def do_symlink(target, path):
    log("> symlink " + target + " " + path)
    try:
        log("descriptor = " + str(k.symlink(target, path)))
    except FSError as e:
        error(e)


def do_open(path):
    log("> open " + path)
    try:
        fd = k.open(path)
        log("fd = " + str(fd))
        return fd
    except FSError as e:
        error(e)
        return -1


def do_close(fd):
    log("> close " + str(fd))
    try:
        k.close(fd)
    except FSError as e:
        error(e)


def do_seek(fd, offset):
    log("> seek " + str(fd) + " " + str(offset))
    try:
        k.seek(fd, offset)
    except FSError as e:
        error(e)


def do_read(fd, size):
    log("> read " + str(fd) + " " + str(size))
    try:
        data = k.read(fd, size)
        log(show_bytes(data) + "   [" + str(len(data)) + " bytes read]")
        return data
    except FSError as e:
        error(e)
        return b""


def do_write(fd, data):
    log("> write " + str(fd) + " " + str(len(data)))
    try:
        k.write(fd, data)
        log(show_bytes(data))
    except FSError as e:
        error(e)


def do_link(path1, path2):
    log("> link " + path1 + " " + path2)
    try:
        k.link(path1, path2)
    except FSError as e:
        error(e)


def do_unlink(path):
    log("> unlink " + path)
    try:
        k.unlink(path)
    except FSError as e:
        error(e)


def do_truncate(path, size):
    log("> truncate " + path + " " + str(size))
    try:
        k.truncate(path, size)
    except FSError as e:
        error(e)
