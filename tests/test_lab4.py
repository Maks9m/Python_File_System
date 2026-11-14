"""Lab 4: flat block filesystem."""

import os

import pytest

from cli import Shell, pattern_bytes
from fs import FileSystem, FSError
from layout import (
    BLOCK_SIZE, DEFAULT_DISK_SIZE, MAX_FILE_SIZE, NUM_DIRECT, ROOT_INODE, FileType,
    Superblock,
)


@pytest.fixture
def fs(tmp_path):
    f = FileSystem(str(tmp_path / "disk.img"), max_open=8)
    f.mkfs(32)
    yield f
    f.close()


def used_blocks(fs):
    return fs.sb.total_blocks - fs.free_block_count()


def entries(fs):
    """(name, inode) pairs of the root directory, without '.' and '..'."""
    return [(n, i) for n, i, _ in fs.ls() if n not in (".", "..")]


def names(fs):
    return [n for n, _ in entries(fs)]


# ---------------------------------------------------------------- formatting

def test_mkfs_layout(tmp_path):
    path = str(tmp_path / "d.img")
    fs = FileSystem(path)
    sb = fs.mkfs(20)
    assert os.path.getsize(path) == DEFAULT_DISK_SIZE
    assert sb.total_blocks == DEFAULT_DISK_SIZE // BLOCK_SIZE
    assert (sb.bitmap_start, sb.inode_table_start, sb.data_start) == (1, 2, 5)  # 20 inodes -> 3 blocks
    root = fs.stat_inode(ROOT_INODE)
    assert root.file_type == FileType.DIRECTORY
    assert entries(fs) == []
    fs.close()
    # superblock really is on disk
    with open(path, "rb") as f:
        assert Superblock.unpack(f.read(BLOCK_SIZE)).inode_count == 20


def test_persistence_across_remount(tmp_path):
    path = str(tmp_path / "d.img")
    fs = FileSystem(path)
    fs.mkfs(8)
    fs.create("a")
    fd = fs.open("a")
    fs.write(fd, b"persistent")
    fs.close()
    fs2 = FileSystem(path)
    fd = fs2.open("a")
    assert fs2.read(fd, 100) == b"persistent"
    fs2.close()


def test_unformatted_disk_is_rejected(tmp_path):
    path = tmp_path / "raw.img"
    path.write_bytes(bytes(4096))
    fs = FileSystem(str(path))
    with pytest.raises(FSError, match="not formatted"):
        fs.ls()
    fs.close()


# ---------------------------------------------------------------- CRUD

def test_create_ls_stat(fs):
    ino = fs.create("hello.txt")
    assert ino == 1
    _, inode = fs.stat("hello.txt")
    assert inode.file_type == FileType.REGULAR
    assert (inode.hard_links, inode.size) == (1, 0)
    assert entries(fs) == [("hello.txt", 1)]


def test_create_errors(fs):
    fs.create("x")
    with pytest.raises(FSError, match="exists"):
        fs.create("x")
    for bad in ["", "a/b", "x" * 29, "tab\there"]:
        with pytest.raises(FSError):
            fs.create(bad)
    fs.create("ж" * 14)                               # 28 UTF-8 bytes is fine
    with pytest.raises(FSError):
        fs.create("ж" * 15)


def test_inode_exhaustion(tmp_path):
    fs = FileSystem(str(tmp_path / "d.img"))
    fs.mkfs(3)                                        # root + 2 files
    fs.create("a")
    fs.create("b")
    with pytest.raises(FSError, match="no free inodes"):
        fs.create("c")
    assert names(fs) == ["a", "b"]
    fs.close()


def test_fd_allocation_lowest_free(fs):
    fs.create("a")
    assert [fs.open("a") for _ in range(3)] == [0, 1, 2]
    fs.close_fd(1)
    assert fs.open("a") == 1
    with pytest.raises(FSError, match="bad file descriptor"):
        fs.close_fd(7)


def test_open_file_limit(fs):
    fs.create("a")
    for _ in range(fs.max_open):
        fs.open("a")
    with pytest.raises(FSError, match="too many open files"):
        fs.open("a")


def test_independent_offsets(fs):
    fs.create("a")
    fd1, fd2 = fs.open("a"), fs.open("a")
    fs.write(fd1, b"hello world")
    assert fs.read(fd2, 5) == b"hello"
    assert fs.read(fd2, 100) == b" world"
    assert fs.read(fd2, 10) == b""                    # at EOF
    fs.seek(fd1, 6)
    fs.write(fd1, b"WORLD")
    fs.seek(fd2, 0)
    assert fs.read(fd2, 100) == b"hello WORLD"


# ---------------------------------------------------------------- multi-block / indirect

def test_multi_block_and_indirect_boundary(fs):
    fs.create("big")
    fd = fs.open("big")
    size = (NUM_DIRECT + 2) * BLOCK_SIZE + 17         # crosses into the indirect block
    data = pattern_bytes(size)
    fs.write(fd, data)
    _, inode = fs.stat("big")
    assert inode.size == size
    assert all(inode.direct) and inode.indirect != 0
    assert len(fs.block_map(inode)) == NUM_DIRECT + 3
    fs.seek(fd, 0)
    assert fs.read(fd, size) == data
    # read straddling the direct/indirect boundary
    boundary = NUM_DIRECT * BLOCK_SIZE
    fs.seek(fd, boundary - 10)
    assert fs.read(fd, 20) == data[boundary - 10:boundary + 10]


def test_max_file_size(fs):
    fs.create("big")
    fd = fs.open("big")
    fs.write(fd, b"x" * MAX_FILE_SIZE)
    with pytest.raises(FSError, match="limit"):
        fs.write(fd, b"y")
    with pytest.raises(FSError, match="limit"):
        fs.truncate("big", MAX_FILE_SIZE + 1)


# ---------------------------------------------------------------- hard links

def test_hard_links(fs):
    fs.create("a")
    fs.link("a", "b")
    ia, a = fs.stat("a")
    ib, b = fs.stat("b")
    assert ia == ib and a.hard_links == 2
    fd = fs.open("a")
    fs.write(fd, b"shared")
    fd2 = fs.open("b")
    assert fs.read(fd2, 10) == b"shared"
    fs.unlink("a")
    _, b = fs.stat("b")
    assert b.hard_links == 1
    with pytest.raises(FSError):
        fs.stat("a")
    with pytest.raises(FSError, match="exists"):
        fs.link("b", "b")
    with pytest.raises(FSError, match="no such"):
        fs.link("missing", "c")


def test_unlinked_slot_is_reused(fs):
    fs.create("a")
    fs.create("b")
    fs.unlink("a")
    fs.create("c")
    assert names(fs) == ["c", "b"]   # 'c' took a's slot, no shifting


def test_unlink_frees_everything(fs):
    base = used_blocks(fs)
    fs.create("a")
    fd = fs.open("a")
    fs.write(fd, b"z" * (BLOCK_SIZE * 12))
    fs.close_fd(fd)
    root_blocks = used_blocks(fs) - base - 13         # 12 data + 1 indirect
    fs.unlink("a")
    assert used_blocks(fs) == base + root_blocks      # only the root dir block remains
    assert fs.stat_inode(1).file_type == FileType.FREE


# ---------------------------------------------------------------- deferred deletion

def test_unlinked_but_open_lifecycle(fs):
    fs.create("tmp")
    fd = fs.open("tmp")
    fs.write(fd, b"still here")
    fd2 = fs.open("tmp")
    before = used_blocks(fs)
    fs.unlink("tmp")
    assert entries(fs) == []
    inode = fs.stat_inode(1)
    assert inode.file_type == FileType.REGULAR and inode.hard_links == 0
    assert used_blocks(fs) == before                  # nothing freed yet
    # seek / read / write keep working
    fs.seek(fd, 0)
    assert fs.read(fd, 100) == b"still here"
    fs.write(fd, b"!")
    fs.close_fd(fd)
    assert fs.stat_inode(1).file_type == FileType.REGULAR   # fd2 still open
    fs.seek(fd2, 0)
    assert fs.read(fd2, 100) == b"still here!"
    fs.close_fd(fd2)
    assert fs.stat_inode(1).file_type == FileType.FREE
    assert used_blocks(fs) == before - 1              # data block released


# ---------------------------------------------------------------- sparse files

def test_truncate_grow_is_sparse(fs):
    fs.create("s")
    before = used_blocks(fs)
    fs.truncate("s", 20 * BLOCK_SIZE)
    _, inode = fs.stat("s")
    assert inode.size == 20 * BLOCK_SIZE
    assert fs.block_map(inode) == {} and inode.indirect == 0
    assert used_blocks(fs) == before
    fd = fs.open("s")
    assert fs.read(fd, 1000) == bytes(1000)


def test_write_past_eof_leaves_hole(fs):
    fs.create("s")
    fd = fs.open("s")
    fs.seek(fd, 10 * BLOCK_SIZE + 5)
    fs.write(fd, b"END")
    _, inode = fs.stat("s")
    assert inode.size == 10 * BLOCK_SIZE + 8
    assert list(fs.block_map(inode)) == [10]           # only the written block
    fs.seek(fd, 0)
    data = fs.read(fd, inode.size)
    assert data == bytes(10 * BLOCK_SIZE + 5) + b"END"


def test_writing_zeros_does_not_allocate(fs):
    fs.create("z")
    fd = fs.open("z")
    before = used_blocks(fs)
    fs.write(fd, bytes(3 * BLOCK_SIZE))
    assert used_blocks(fs) == before
    fs.seek(fd, 0)
    fs.write(fd, b"a")
    assert used_blocks(fs) == before + 1
    fs.seek(fd, 0)
    fs.write(fd, b"\x00")                             # block is all zeros again
    assert used_blocks(fs) == before


def test_truncate_shrink_frees_and_zeroes_tail(fs):
    fs.create("t")
    fd = fs.open("t")
    fs.write(fd, pattern_bytes(12 * BLOCK_SIZE))
    base = used_blocks(fs)
    fs.truncate("t", BLOCK_SIZE + 10)
    _, inode = fs.stat("t")
    assert inode.size == BLOCK_SIZE + 10
    assert list(fs.block_map(inode)) == [0, 1] and inode.indirect == 0
    assert used_blocks(fs) == base - 11               # 10 data blocks + indirect
    fs.truncate("t", 3 * BLOCK_SIZE)                  # grow again: old bytes must be gone
    fs.seek(fd, 0)
    data = fs.read(fd, 3 * BLOCK_SIZE)
    assert data[:BLOCK_SIZE + 10] == pattern_bytes(BLOCK_SIZE + 10)
    assert data[BLOCK_SIZE + 10:] == bytes(2 * BLOCK_SIZE - 10)


# ---------------------------------------------------------------- CLI

def test_cli_script(tmp_path):
    out = []
    fs = FileSystem(str(tmp_path / "d.img"))
    sh = Shell(fs, out.append)
    sh.run_script(["mkfs 8", "create f", "open f", "write 0 5", "seek 0 0",
                   "read 0 10", "write 0 4 xy", "seek 0 0", "read 0 10",
                   "bogus", "close 9", "exit", "ls"])
    text = "\n".join(out)
    assert 'fd 0: read 5 bytes: "ABCDE"' in text
    assert 'fd 0: read 9 bytes: "ABCDExyxy"' in text
    assert "unknown command" in text and "bad file descriptor" in text
    assert not any(line.endswith("> ls") for line in out)   # stopped at exit
    fs.close()
