"""Lab 4 session: every Lab 4 command, run like a shell; log goes to reports/lab4_output.txt."""

import shell
from fs import BLOCK_SIZE, MAX_FILE_SIZE, FSError
from kernel import MAX_OPEN
from shell import (
    do_close,
    do_create,
    do_link,
    do_ls,
    do_mkfs,
    do_open,
    do_read,
    do_seek,
    do_stat,
    do_truncate,
    do_unlink,
    do_write,
    log,
    log_format,
    note,
    pattern,
    remount,
    save,
    section,
    show_free,
    start,
)

start()
log_format()

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
    shell.k.link("memo", "m" + str(i))
log("(created hard links m0 .. m19 to memo)")
do_stat(".")
do_stat("memo")
for i in range(20):
    shell.k.unlink("m" + str(i))
log("(removed m0 .. m19; the entries are marked invalid, the directory does not shrink)")

section("10. Limits")
for i in range(MAX_OPEN):
    shell.k.open("memo")
log("(opened memo " + str(MAX_OPEN) + " times)")
do_open("memo")
for i in range(MAX_OPEN):
    shell.k.close(i)
note("16 descriptors in total, one is the directory.")
count = 0
while True:
    try:
        shell.k.create("f" + str(count))
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
remount()
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
start_free = shell.k.free_blocks()
note("Create files of " + str(MAX_FILE_SIZE) + " bytes until the disk is full.")
n = 0
while True:
    name = "full" + str(n)
    shell.k.create(name)
    fd = shell.k.open(name)
    try:
        shell.k.write(fd, pattern(MAX_FILE_SIZE))
    except FSError:
        shell.k.close(fd)
        break
    shell.k.close(fd)
    n += 1
log("(" + str(n) + " full files written, then the disk ran out)")
do_stat(name)
show_free()
note("Remove all files: every data block comes back, nothing is lost.")
for i in range(n + 1):
    shell.k.unlink("full" + str(i))
log("(removed full0 .. full" + str(n) + ")")
show_free()
log("(free blocks right after mkfs: " + str(start_free) + "; the difference is the directory itself:)")
do_stat(".")
shell.k.unmount()
save("lab4_output.txt")

