"""Filesystem driver + the kernel part of the FS subsystem (open file table).

Every operation reads and writes the on-disk structures directly through the
:class:`~block_device.BlockDevice`; nothing except the immutable superblock
geometry (read at mount time) and the open file table is kept in memory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from block_device import BlockDevice
from layout import (
    BLOCK_SIZE, DEFAULT_DISK_SIZE, DIRENT_SIZE, INODE_SIZE, INODES_PER_BLOCK,
    MAX_FILE_BLOCKS, MAX_FILE_SIZE, MAX_TOTAL_BLOCKS, NUM_DIRECT, PTR_SIZE,
    PTRS_PER_BLOCK, ROOT_INODE, DirEntry, FileType, Inode, Superblock,
    bitmap_blocks_for, inode_blocks_for, validate_name,
)

DEFAULT_MAX_OPEN = 64
ZERO_BLOCK = bytes(BLOCK_SIZE)


class FSError(Exception):
    """Any error reported to the user by a filesystem operation."""


@dataclass
class OpenFile:
    """Entry of the kernel open file table."""

    inode: int
    offset: int = 0


class FileSystem:
    def __init__(self, disk_path: str, max_open: int = DEFAULT_MAX_OPEN,
                 disk_size: int = DEFAULT_DISK_SIZE) -> None:
        self.disk_path = disk_path
        self.disk_size = disk_size
        self.max_open = max_open
        self.open_files: list[OpenFile | None] = [None] * max_open
        self.dev: BlockDevice | None = None
        self.sb: Superblock | None = None
        if os.path.exists(disk_path):
            self.dev = BlockDevice(disk_path)
            self._mount()

    # ------------------------------------------------------------------ mount / mkfs

    def _mount(self) -> None:
        self.sb = None
        if self.dev is None or self.dev.total_blocks == 0:
            return
        sb = Superblock.unpack(self.dev.read_block(0))
        if sb.valid and sb.total_blocks == self.dev.total_blocks:
            self.sb = sb

    def _require_mounted(self) -> None:
        if self.sb is None:
            raise FSError("filesystem is not formatted (run 'mkfs <n>' first)")

    def close(self) -> None:
        if self.dev is not None:
            self.dev.close()
            self.dev = None

    def mkfs(self, inode_count: int) -> Superblock:
        """Format the disk image with ``inode_count`` inodes."""
        if inode_count < 1:
            raise FSError("inode count must be >= 1")
        if self.dev is None:
            BlockDevice.create_image(self.disk_path, self.disk_size)
            self.dev = BlockDevice(self.disk_path)
        total = self.dev.total_blocks
        if total > MAX_TOTAL_BLOCKS:
            raise FSError(f"disk too large: {total} blocks > {MAX_TOTAL_BLOCKS}")
        bmp_blocks = bitmap_blocks_for(total)
        ino_blocks = inode_blocks_for(inode_count)
        data_start = 1 + bmp_blocks + ino_blocks
        if data_start >= total:
            raise FSError(f"too many inodes for a disk of {total} blocks")

        sb = Superblock(BLOCK_SIZE, total, inode_count, 1, 1 + bmp_blocks, data_start)
        for b in range(1, data_start):
            self.dev.write_block(b, ZERO_BLOCK)
        self.dev.write_block(0, sb.pack())
        self.sb = sb
        self.open_files = [None] * self.max_open
        for b in range(data_start):          # metadata blocks are always "allocated"
            self._set_bit(b, True)
        self._init_root()
        return sb

    def _init_root(self) -> None:
        self._write_inode(ROOT_INODE, Inode(FileType.DIRECTORY, hard_links=1))

    # ------------------------------------------------------------------ bitmap

    def _bit_location(self, block: int) -> tuple[int, int, int]:
        bits_per_block = BLOCK_SIZE * 8
        bmp_block = self.sb.bitmap_start + block // bits_per_block
        bit = block % bits_per_block
        return bmp_block, bit // 8, bit % 8

    def _set_bit(self, block: int, value: bool) -> None:
        bmp_block, byte, bit = self._bit_location(block)
        raw = bytearray(self.dev.read_block(bmp_block))
        if value:
            raw[byte] |= 1 << bit
        else:
            raw[byte] &= ~(1 << bit) & 0xFF
        self.dev.write_block(bmp_block, bytes(raw))

    def _alloc_block(self) -> int:
        """Find the first free data block, mark it allocated and zero it."""
        sb = self.sb
        bits_per_block = BLOCK_SIZE * 8
        for i in range(sb.inode_table_start - sb.bitmap_start):
            raw = self.dev.read_block(sb.bitmap_start + i)
            for byte_idx, byte in enumerate(raw):
                if byte == 0xFF:
                    continue
                for bit in range(8):
                    block = i * bits_per_block + byte_idx * 8 + bit
                    if block >= sb.total_blocks:
                        raise FSError("no free blocks left on device")
                    if not byte & (1 << bit) and block >= sb.data_start:
                        self._set_bit(block, True)
                        self.dev.write_block(block, ZERO_BLOCK)
                        return block
        raise FSError("no free blocks left on device")

    def _free_block(self, block: int) -> None:
        if block >= self.sb.data_start:
            self._set_bit(block, False)

    def free_block_count(self) -> int:
        sb = self.sb
        used = 0
        for i in range(sb.inode_table_start - sb.bitmap_start):
            raw = self.dev.read_block(sb.bitmap_start + i)
            used += sum(bin(b).count("1") for b in raw)
        return sb.total_blocks - used

    # ------------------------------------------------------------------ inodes

    def _inode_location(self, ino: int) -> tuple[int, int]:
        if not 0 <= ino < self.sb.inode_count:
            raise FSError(f"inode {ino} out of range")
        return (self.sb.inode_table_start + ino // INODES_PER_BLOCK,
                (ino % INODES_PER_BLOCK) * INODE_SIZE)

    def _read_inode(self, ino: int) -> Inode:
        block, off = self._inode_location(ino)
        return Inode.unpack(self.dev.read_block(block), off)

    def _write_inode(self, ino: int, inode: Inode) -> None:
        block, off = self._inode_location(ino)
        raw = bytearray(self.dev.read_block(block))
        raw[off:off + INODE_SIZE] = inode.pack()
        self.dev.write_block(block, bytes(raw))

    def _alloc_inode(self, ftype: FileType) -> int:
        for ino in range(self.sb.inode_count):
            if self._read_inode(ino).file_type == FileType.FREE:
                self._write_inode(ino, Inode(ftype, hard_links=0))
                return ino
        raise FSError("no free inodes left")

    def _free_inode(self, ino: int) -> None:
        """Release all data blocks (and the indirect block) and the inode itself."""
        inode = self._read_inode(ino)
        self._truncate_blocks(inode, 0)
        self._write_inode(ino, Inode())

    # ------------------------------------------------------------------ block map

    def _get_ptr(self, inode: Inode, idx: int) -> int:
        """Block number for file block ``idx`` (0 = none / sparse)."""
        if idx < NUM_DIRECT:
            return inode.direct[idx]
        if inode.indirect == 0:
            return 0
        raw = self.dev.read_block(inode.indirect)
        off = (idx - NUM_DIRECT) * PTR_SIZE
        return int.from_bytes(raw[off:off + PTR_SIZE], "little")

    def _set_ptr(self, inode: Inode, idx: int, block: int) -> None:
        """Set the pointer for file block ``idx`` (inode is modified, caller writes it)."""
        if idx < NUM_DIRECT:
            inode.direct[idx] = block
            return
        if inode.indirect == 0:
            if block == 0:
                return
            inode.indirect = self._alloc_block()
        raw = bytearray(self.dev.read_block(inode.indirect))
        off = (idx - NUM_DIRECT) * PTR_SIZE
        raw[off:off + PTR_SIZE] = block.to_bytes(PTR_SIZE, "little")
        if not any(raw):                      # indirect block no longer needed
            self._free_block(inode.indirect)
            inode.indirect = 0
        else:
            self.dev.write_block(inode.indirect, bytes(raw))

    def _indirect_ptrs(self, inode: Inode) -> list[int]:
        if inode.indirect == 0:
            return [0] * PTRS_PER_BLOCK
        raw = self.dev.read_block(inode.indirect)
        return [int.from_bytes(raw[i:i + PTR_SIZE], "little")
                for i in range(0, BLOCK_SIZE, PTR_SIZE)]

    # ------------------------------------------------------------------ file data

    def _read_data(self, inode: Inode, offset: int, size: int) -> bytes:
        end = min(offset + size, inode.size)
        if offset >= end:
            return b""
        out = bytearray()
        pos = offset
        while pos < end:
            idx, boff = divmod(pos, BLOCK_SIZE)
            n = min(BLOCK_SIZE - boff, end - pos)
            block = self._get_ptr(inode, idx)
            if block == 0:
                out += bytes(n)               # sparse hole reads as zeros
            else:
                out += self.dev.read_block(block)[boff:boff + n]
            pos += n
        return bytes(out)

    def _write_data(self, inode: Inode, offset: int, data: bytes) -> None:
        """Write ``data`` at ``offset``; all-zero blocks are never stored."""
        end = offset + len(data)
        if end > MAX_FILE_SIZE:
            raise FSError(f"file size limit exceeded ({MAX_FILE_SIZE} bytes)")
        pos = offset
        while pos < end:
            idx, boff = divmod(pos, BLOCK_SIZE)
            n = min(BLOCK_SIZE - boff, end - pos)
            chunk = data[pos - offset:pos - offset + n]
            block = self._get_ptr(inode, idx)
            if block == 0:
                if any(chunk):
                    block = self._alloc_block()
                    raw = bytearray(ZERO_BLOCK)
                    raw[boff:boff + n] = chunk
                    self.dev.write_block(block, bytes(raw))
                    self._set_ptr(inode, idx, block)
                # else: writing zeros into a hole keeps it a hole
            else:
                raw = bytearray(self.dev.read_block(block))
                raw[boff:boff + n] = chunk
                if any(raw):
                    self.dev.write_block(block, bytes(raw))
                else:                         # block became all zeros -> make sparse
                    self._set_ptr(inode, idx, 0)
                    self._free_block(block)
            pos += n
        inode.size = max(inode.size, end)

    def _truncate_blocks(self, inode: Inode, new_size: int) -> None:
        """Resize ``inode`` to ``new_size``, freeing blocks past the new EOF."""
        if new_size > MAX_FILE_SIZE:
            raise FSError(f"file size limit exceeded ({MAX_FILE_SIZE} bytes)")
        if new_size < inode.size:
            keep = (new_size + BLOCK_SIZE - 1) // BLOCK_SIZE
            for idx in range(keep, MAX_FILE_BLOCKS):
                block = self._get_ptr(inode, idx)
                if block:
                    self._set_ptr(inode, idx, 0)   # frees the indirect block when empty
                    self._free_block(block)
            tail = new_size % BLOCK_SIZE
            if tail:
                # zero the bytes past the new EOF in the last kept block, so that a
                # later growth reads zeros there (and the block may become sparse)
                self._write_data(inode, new_size, bytes(BLOCK_SIZE - tail))
        inode.size = new_size                 # growing: new region stays sparse

    # ------------------------------------------------------------------ directories

    def _dir_entries(self, dir_ino: int) -> list[tuple[int, DirEntry]]:
        """All entry slots of a directory as ``(slot_index, entry)``."""
        inode = self._read_inode(dir_ino)
        raw = self._read_data(inode, 0, inode.size)
        return [(i // DIRENT_SIZE, DirEntry.unpack(raw, i))
                for i in range(0, len(raw), DIRENT_SIZE)]

    def _lookup(self, dir_ino: int, name: str) -> int | None:
        for _, e in self._dir_entries(dir_ino):
            if e.valid and e.name == name:
                return e.inode
        return None

    def _add_entry(self, dir_ino: int, name: str, ino: int) -> None:
        validate_name_or_raise(name)
        if self._lookup(dir_ino, name) is not None:
            raise FSError(f"file exists: {name!r}")
        slot = None
        for i, e in self._dir_entries(dir_ino):
            if not e.valid:
                slot = i                      # reuse the first invalid slot
                break
        dinode = self._read_inode(dir_ino)
        if slot is None:
            slot = dinode.size // DIRENT_SIZE
        self._write_data(dinode, slot * DIRENT_SIZE, DirEntry(1, ino, name).pack())
        self._write_inode(dir_ino, dinode)

    def _remove_entry(self, dir_ino: int, name: str) -> int:
        """Mark the entry ``name`` invalid (no shifting) and return its inode."""
        for i, e in self._dir_entries(dir_ino):
            if e.valid and e.name == name:
                e.valid = 0
                dinode = self._read_inode(dir_ino)
                self._write_data(dinode, i * DIRENT_SIZE, e.pack())
                self._write_inode(dir_ino, dinode)
                return e.inode
        raise FSError(f"no such file: {name!r}")

    # ------------------------------------------------------------------ name helpers

    def _resolve(self, name: str) -> int:
        self._require_mounted()
        ino = self._lookup(ROOT_INODE, name)
        if ino is None:
            raise FSError(f"no such file: {name!r}")
        return ino

    def _is_open(self, ino: int) -> bool:
        return any(f is not None and f.inode == ino for f in self.open_files)

    def _maybe_release(self, ino: int) -> None:
        """Deferred deletion: free the file once no link and no fd refer to it."""
        inode = self._read_inode(ino)
        if inode.file_type != FileType.FREE and inode.hard_links == 0 and not self._is_open(ino):
            self._free_inode(ino)

    def _get_open(self, fd: int) -> OpenFile:
        self._require_mounted()
        if not 0 <= fd < self.max_open or self.open_files[fd] is None:
            raise FSError(f"bad file descriptor: {fd}")
        return self.open_files[fd]

    # ------------------------------------------------------------------ public API

    def stat(self, name: str) -> tuple[int, Inode]:
        ino = self._resolve(name)
        return ino, self._read_inode(ino)

    def stat_inode(self, ino: int) -> Inode:
        self._require_mounted()
        return self._read_inode(ino)

    def block_map(self, inode: Inode) -> dict[int, int]:
        """Allocated file blocks as ``{file_block_index: disk_block}``."""
        result = {i: b for i, b in enumerate(inode.direct) if b}
        for i, b in enumerate(self._indirect_ptrs(inode)):
            if b:
                result[NUM_DIRECT + i] = b
        return result

    def ls(self) -> list[tuple[str, int, Inode]]:
        self._require_mounted()
        return [(e.name, e.inode, self._read_inode(e.inode))
                for _, e in self._dir_entries(ROOT_INODE) if e.valid]

    def create(self, name: str) -> int:
        self._require_mounted()
        validate_name_or_raise(name)
        if self._lookup(ROOT_INODE, name) is not None:
            raise FSError(f"file exists: {name!r}")
        ino = self._alloc_inode(FileType.REGULAR)
        try:
            self._add_entry(ROOT_INODE, name, ino)
        except FSError:
            self._write_inode(ino, Inode())
            raise
        inode = self._read_inode(ino)
        inode.hard_links = 1
        self._write_inode(ino, inode)
        return ino

    def open(self, name: str) -> int:
        ino = self._resolve(name)
        if self._read_inode(ino).file_type != FileType.REGULAR:
            raise FSError(f"not a regular file: {name!r}")
        return self._open_inode(ino)

    def _open_inode(self, ino: int) -> int:
        for fd, f in enumerate(self.open_files):
            if f is None:
                self.open_files[fd] = OpenFile(ino)
                return fd
        raise FSError(f"too many open files (limit {self.max_open})")

    def close_fd(self, fd: int) -> None:
        f = self._get_open(fd)
        self.open_files[fd] = None
        self._maybe_release(f.inode)

    def seek(self, fd: int, offset: int) -> None:
        f = self._get_open(fd)
        if offset < 0:
            raise FSError("negative offset")
        f.offset = offset

    def read(self, fd: int, size: int) -> bytes:
        f = self._get_open(fd)
        if size < 0:
            raise FSError("negative size")
        data = self._read_data(self._read_inode(f.inode), f.offset, size)
        f.offset += len(data)
        return data

    def write(self, fd: int, data: bytes) -> int:
        f = self._get_open(fd)
        inode = self._read_inode(f.inode)
        self._write_data(inode, f.offset, data)
        self._write_inode(f.inode, inode)
        f.offset += len(data)
        return len(data)

    def link(self, src: str, dst: str) -> None:
        ino = self._resolve(src)
        self._add_entry(ROOT_INODE, dst, ino)
        inode = self._read_inode(ino)
        inode.hard_links += 1
        self._write_inode(ino, inode)

    def unlink(self, name: str) -> None:
        self._require_mounted()
        ino = self._remove_entry(ROOT_INODE, name)
        inode = self._read_inode(ino)
        inode.hard_links -= 1
        self._write_inode(ino, inode)
        self._maybe_release(ino)

    def truncate(self, name: str, size: int) -> None:
        ino = self._resolve(name)
        if size < 0:
            raise FSError("negative size")
        inode = self._read_inode(ino)
        if inode.file_type != FileType.REGULAR:
            raise FSError(f"not a regular file: {name!r}")
        self._truncate_blocks(inode, size)
        self._write_inode(ino, inode)


def validate_name_or_raise(name: str) -> None:
    try:
        validate_name(name)
    except ValueError as e:
        raise FSError(str(e)) from None
