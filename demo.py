"""Runs every Lab 4 command like a shell session and saves the log to reports/lab4_output.txt."""

import os

import fs
from fs import BLOCK_SIZE, MAX_FILE_SIZE, TYPE_NAMES, FSError
from kernel import MAX_OPEN, Kernel

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = os.path.join(HERE, "disk.img")
REPORT = os.path.join(HERE, "reports", "lab4_output.txt")

lines = []


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


def error(e):
    log("error: " + str(e))


# ------------------------------------------------------------------ shell commands

def do_mkfs(n):
    log("> mkfs " + str(n))
    try:
        k.mkfs(n)
        log("formatted: " + str(k.fs.disk.block_count) + " blocks of " + str(BLOCK_SIZE)
            + " bytes, " + str(n) + " file descriptors, " + str(k.free_blocks()) + " free blocks")
    except FSError as e:
        error(e)


def do_stat(name):
    log("> stat " + name)
    try:
        s = k.stat(name)
    except FSError as e:
        error(e)
        return
    indirect = "-"
    if s.indirect != 0:
        indirect = str(s.indirect) + " -> " + show_ptrs(s.indirect_ptrs)
    log("  File: " + s.name)
    log("  Size: " + str(s.size).ljust(10) + "Blocks: " + str(s.blocks).ljust(8)
        + "IO Block: " + str(BLOCK_SIZE).ljust(6) + TYPE_NAMES[s.type])
    log(" Inode: " + str(s.inode).ljust(10) + "Links: " + str(s.links))
    log("   Map: direct " + show_ptrs(s.direct) + "   indirect " + indirect)


def do_ls():
    log("> ls -l")
    try:
        files = k.ls()
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
        t = "-"                                   # "-" is a regular file in ls -l
        if s.type == fs.DIRECTORY:
            t = "d"
        log(str(s.inode).rjust(5) + "  " + t.rjust(4) + "  " + str(s.links).rjust(5) + "  "
            + str(s.size).rjust(5) + "  " + str(s.blocks).rjust(6) + "  " + s.name)


def do_create(name):
    log("> create " + name)
    try:
        log("descriptor = " + str(k.create(name)))
    except FSError as e:
        error(e)


def do_open(name):
    log("> open " + name)
    try:
        fd = k.open(name)
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


def do_link(name1, name2):
    log("> link " + name1 + " " + name2)
    try:
        k.link(name1, name2)
    except FSError as e:
        error(e)


def do_unlink(name):
    log("> unlink " + name)
    try:
        k.unlink(name)
    except FSError as e:
        error(e)


def do_truncate(name, size):
    log("> truncate " + name + " " + str(size))
    try:
        k.truncate(name, size)
    except FSError as e:
        error(e)


def show_free():
    log("(free blocks: " + str(k.free_blocks()) + ")")


# ------------------------------------------------------------------ the session

if os.path.exists(IMAGE):
    os.remove(IMAGE)
k = Kernel(IMAGE)

section("FS data format")
for line in fs.__doc__.strip().splitlines()[2:]:
    log(line)

section("1. Formatting")
note("The disk image is new, nothing works before mkfs.")
do_ls()
do_mkfs(16)
note("The directory is a file too (descriptor 0); '.' means the directory.")
do_stat(".")

section("2. The example from the task (block size here is 64, not 128)")
note("Create a regular file, output information about it.")
do_create("file.txt")
do_stat("file.txt")
note("Output the contents of the directory.")
do_ls()
note("Create another name for the file: the number of hard links becomes 2.")
do_link("file.txt", "document.txt")
do_ls()
do_stat("document.txt")
note("Create a file and make it 1024 bytes long: no blocks are used.")
do_create("some.dat")
do_ls()
do_truncate("some.dat", 1024)
do_stat("some.dat")
note("Open the file, write 10 bytes: 1 block is used.")
fd = do_open("some.dat")
do_write(fd, b"0123456789")
do_stat("some.dat")
note("The offset can be set for the file.")
do_seek(fd, 7)
do_read(fd, 2)
note("Write into the third block (offset 2 * 64 = 128), read the first three blocks.")
do_seek(fd, 2 * BLOCK_SIZE)
do_write(fd, b"abcdefg")
do_seek(fd, 0)
do_read(fd, 3 * BLOCK_SIZE)
do_stat("some.dat")
note("Remove the only hard link: the open file still works.")
show_free()
do_unlink("some.dat")
do_ls()
do_seek(fd, 0)
do_read(fd, 10)
note("Closing the last fd frees the file (2 blocks).")
do_close(fd)
show_free()

section("3. Numerical file descriptors and offsets")
note("open gives the smallest free number; one file can be opened several times.")
fd0 = do_open("file.txt")
fd1 = do_open("file.txt")
fd2 = do_open("document.txt")
do_close(fd1)
fd1 = do_open("file.txt")
do_close(9)
note("Every fd has its own offset.")
do_write(fd0, b"hello world")
do_read(fd1, 5)
do_read(fd1, 100)
do_read(fd1, 10)
do_seek(fd0, 6)
do_write(fd0, b"WORLD")
do_seek(fd2, 0)
do_read(fd2, 100)
do_seek(fd0, -1)
do_read(fd0, -1)
do_close(fd0)
do_close(fd1)
do_close(fd2)

section("4. Big file: direct blocks and the indirect block")
note("400 bytes = 7 blocks: 4 direct + 3 whose numbers are in the indirect block.")
do_create("big")
fd = do_open("big")
do_write(fd, pattern(400))
do_stat("big")
note("A read across the direct/indirect border (offset 256).")
do_seek(fd, 250)
do_read(fd, 12)
do_seek(fd, 0)
data = do_read(fd, 400)
log("(equal to what was written: " + str(data == pattern(400)) + ")")
note("The file cannot grow past " + str(MAX_FILE_SIZE) + " bytes.")
do_seek(fd, MAX_FILE_SIZE)
do_write(fd, b"x")
do_close(fd)

section("5. Hard links and names")
do_create("notes")
do_link("notes", "memo")
do_unlink("notes")
do_stat("memo")
note("The removed entry's slot is reused by the next link (memo stays after it).")
do_create("todo")
do_ls()
note("Errors.")
do_create("memo")
do_create("a/b")
do_create("abcdefghijklmn")
do_create(".")
do_link("missing", "x")
do_link("memo", "todo")
do_unlink("missing")
do_stat("missing")

section("6. A file open twice after its last hard link is removed")
do_create("tmp")
fa = do_open("tmp")
fb = do_open("tmp")
do_write(fa, pattern(100))
show_free()
do_unlink("tmp")
note("Closing the first fd frees nothing, the file is still open.")
do_close(fa)
show_free()
do_seek(fb, 90)
do_read(fb, 20)
note("Closing the second fd frees the 2 data blocks.")
do_close(fb)
show_free()

section("7. Blocks of zeros are not stored")
do_create("sparse")
fd = do_open("sparse")
note("Writing 128 zero bytes uses no blocks.")
do_write(fd, bytes(128))
do_stat("sparse")
note("Writing in the middle uses only the block that was written (file block 10).")
do_seek(fd, 10 * BLOCK_SIZE)
do_write(fd, b"END")
do_stat("sparse")
note("When the block becomes all zeros again, it is freed (and the indirect block too).")
do_seek(fd, 10 * BLOCK_SIZE)
do_write(fd, bytes(3))
do_stat("sparse")
do_close(fd)

section("8. truncate down")
note("Shrinking frees the blocks after the new end and the unused indirect block.")
fd = do_open("big")
do_seek(fd, 400)
show_free()
do_truncate("big", 70)
do_stat("big")
show_free()
note("The fd offset (400) is now past the end: read returns nothing.")
do_read(fd, 10)
note("Growing again: the bytes after the old end (70) read as zeros, not old data.")
do_truncate("big", 200)
do_seek(fd, 64)
do_read(fd, 12)
do_close(fd)
do_truncate("big", 5000)

section("9. The directory grows into its indirect block")
note("16 entries fit in the 4 direct blocks of the directory; add 20 more links.")
do_stat(".")
for i in range(20):
    k.link("memo", "m" + str(i))
log("(created hard links m0 .. m19 to memo)")
do_stat(".")
do_stat("memo")
for i in range(20):
    k.unlink("m" + str(i))
log("(removed m0 .. m19; the entries are marked invalid, the directory does not shrink)")

section("10. Limits")
for i in range(MAX_OPEN):
    k.open("memo")
log("(opened memo " + str(MAX_OPEN) + " times)")
do_open("memo")
for i in range(MAX_OPEN):
    k.close(i)
note("16 descriptors in total, one is the directory.")
count = 0
while True:
    try:
        k.create("f" + str(count))
        count += 1
    except FSError:
        break
log("(created " + str(count) + " more files f0 ..)")
do_create("one_more")

section("11. Unmount and mount again")
note("Unmount while an unlinked file is still open: unmount closes it and frees it.")
fd = do_open("todo")
do_write(fd, pattern(100))
do_unlink("todo")
show_free()
k.unmount()
k = Kernel(IMAGE)
log("(unmounted and mounted " + os.path.basename(IMAGE) + " again)")
show_free()
note("The data is still on the disk.")
do_ls()
fd = do_open("file.txt")
do_read(fd, 100)
do_close(fd)

section("12. mkfs again, then fill the whole disk")
note("mkfs on a formatted disk starts a new empty file system.")
do_mkfs(40)
do_ls()
start_free = k.free_blocks()
note("Create files of " + str(MAX_FILE_SIZE) + " bytes until the disk is full.")
n = 0
while True:
    name = "full" + str(n)
    k.create(name)
    fd = k.open(name)
    try:
        k.write(fd, pattern(MAX_FILE_SIZE))
    except FSError:
        k.close(fd)
        break
    k.close(fd)
    n += 1
log("(" + str(n) + " full files written, then the disk ran out)")
do_stat(name)
show_free()
note("Remove all files: every data block comes back, nothing is lost.")
for i in range(n + 1):
    k.unlink("full" + str(i))
log("(removed full0 .. full" + str(n) + ")")
show_free()
log("(free blocks right after mkfs: " + str(start_free) + "; the difference is the directory itself:)")
do_stat(".")
k.unmount()

os.makedirs(os.path.dirname(REPORT), exist_ok=True)
with open(REPORT, "w") as f:
    f.write("\n".join(lines) + "\n")
print()
print("log saved to " + REPORT)
