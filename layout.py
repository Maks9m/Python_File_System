"""On-disk binary layout of the filesystem (all integers little-endian).

Disk image = array of ``BLOCK_SIZE``-byte blocks::

    block 0                      superblock
    [bitmap_start, ...)          free-block bitmap (1 bit per block, 1 = allocated)
    [inode_table_start, ...)     inode (file descriptor) table, INODES_PER_BLOCK per block
    [data_start, total_blocks)   data blocks (file contents, directories, indirect blocks)

Block number ``0`` is always the superblock, so ``0`` is used as the
"no block" (sparse / unallocated) value in inode block maps.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

# --------------------------------------------------------------------------- constants

BLOCK_SIZE = 256                       # bytes per block
DEFAULT_DISK_SIZE = 512 * 1024         # default disk image size (2048 blocks)
MAGIC = b"PYFS"

NUM_DIRECT = 8                         # direct block pointers per inode
PTR_SIZE = 2                           # block pointers are uint16
PTRS_PER_BLOCK = BLOCK_SIZE // PTR_SIZE  # 128 pointers in an indirect block
MAX_FILE_BLOCKS = NUM_DIRECT + PTRS_PER_BLOCK
MAX_FILE_SIZE = MAX_FILE_BLOCKS * BLOCK_SIZE  # 34816 bytes
MAX_TOTAL_BLOCKS = 0xFFFF              # must be addressable with uint16 pointers

MAX_NAME_LEN = 28                      # max filename length in UTF-8 bytes
ROOT_INODE = 0


class FileType(IntEnum):
    FREE = 0
    REGULAR = 1
    DIRECTORY = 2


# --------------------------------------------------------------------------- superblock

# magic, block_size, total_blocks, inode_count, bitmap_start, inode_table_start, data_start
SUPERBLOCK_FMT = "<4sIIIIII"
SUPERBLOCK_SIZE = struct.calcsize(SUPERBLOCK_FMT)  # 28 bytes, rest of block 0 is zero


@dataclass
class Superblock:
    """Block 0: filesystem geometry, written once by ``mkfs``."""

    block_size: int
    total_blocks: int
    inode_count: int
    bitmap_start: int
    inode_table_start: int
    data_start: int
    magic: bytes = MAGIC

    def pack(self) -> bytes:
        raw = struct.pack(
            SUPERBLOCK_FMT, self.magic, self.block_size, self.total_blocks,
            self.inode_count, self.bitmap_start, self.inode_table_start, self.data_start,
        )
        return raw.ljust(BLOCK_SIZE, b"\x00")

    @classmethod
    def unpack(cls, block: bytes) -> "Superblock":
        magic, bs, total, n, bmp, itab, data = struct.unpack_from(SUPERBLOCK_FMT, block)
        return cls(bs, total, n, bmp, itab, data, magic)

    @property
    def valid(self) -> bool:
        return self.magic == MAGIC and self.block_size == BLOCK_SIZE


def bitmap_blocks_for(total_blocks: int) -> int:
    """Number of blocks needed for a bitmap covering ``total_blocks`` blocks."""
    bits_per_block = BLOCK_SIZE * 8
    return (total_blocks + bits_per_block - 1) // bits_per_block


# --------------------------------------------------------------------------- inode

# file_type:u8, reserved:u8, hard_links:u16, size:u32, direct:8*u16, indirect:u16, pad:6
INODE_FMT = f"<BBHI{NUM_DIRECT}HH6x"
INODE_SIZE = struct.calcsize(INODE_FMT)            # 32 bytes
INODES_PER_BLOCK = BLOCK_SIZE // INODE_SIZE        # 8


def inode_blocks_for(inode_count: int) -> int:
    return (inode_count + INODES_PER_BLOCK - 1) // INODES_PER_BLOCK


@dataclass
class Inode:
    """File descriptor (inode) as stored in the inode table.

    ``direct[i]`` holds the block with file bytes ``[i*BS, (i+1)*BS)``;
    ``indirect`` points to a block of ``PTRS_PER_BLOCK`` uint16 pointers for
    file blocks ``NUM_DIRECT..MAX_FILE_BLOCKS-1``.  A pointer of ``0`` means the
    block is beyond EOF or consists entirely of zero bytes (sparse).
    """

    file_type: FileType = FileType.FREE
    hard_links: int = 0
    size: int = 0
    direct: list[int] = field(default_factory=lambda: [0] * NUM_DIRECT)
    indirect: int = 0

    def pack(self) -> bytes:
        return struct.pack(INODE_FMT, int(self.file_type), 0, self.hard_links,
                           self.size, *self.direct, self.indirect)

    @classmethod
    def unpack(cls, raw: bytes, offset: int = 0) -> "Inode":
        vals = struct.unpack_from(INODE_FMT, raw, offset)
        ftype, _reserved, links, size = vals[:4]
        direct = list(vals[4:4 + NUM_DIRECT])
        indirect = vals[4 + NUM_DIRECT]
        return cls(FileType(ftype), links, size, direct, indirect)


# --------------------------------------------------------------------------- directory entry

# valid:u8, inode:u16, name:28s (UTF-8, NUL-padded), reserved:1
DIRENT_FMT = f"<BH{MAX_NAME_LEN}sx"
DIRENT_SIZE = struct.calcsize(DIRENT_FMT)          # 32 bytes
DIRENTS_PER_BLOCK = BLOCK_SIZE // DIRENT_SIZE      # 8


@dataclass
class DirEntry:
    """A hard link: ``name`` -> ``inode``.  ``valid == 0`` marks a removed link."""

    valid: int
    inode: int
    name: str

    def pack(self) -> bytes:
        return struct.pack(DIRENT_FMT, self.valid, self.inode, self.name.encode("utf-8"))

    @classmethod
    def unpack(cls, raw: bytes, offset: int = 0) -> "DirEntry":
        valid, ino, name = struct.unpack_from(DIRENT_FMT, raw, offset)
        return cls(valid, ino, name.rstrip(b"\x00").decode("utf-8", errors="replace"))


def validate_name(name: str) -> None:
    """Raise ``ValueError`` if ``name`` is not a legal filename."""
    if not name:
        raise ValueError("empty filename")
    if "/" in name or "\x00" in name or not name.isprintable():
        raise ValueError(f"invalid character in filename {name!r}")
    if len(name.encode("utf-8")) > MAX_NAME_LEN:
        raise ValueError(f"filename {name!r} longer than {MAX_NAME_LEN} bytes")


# sanity checks on the format
assert SUPERBLOCK_SIZE <= BLOCK_SIZE
assert INODE_SIZE == 32 and DIRENT_SIZE == 32
