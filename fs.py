"""Lab 4: file system driver. Knows the disk format; knows nothing about open files.

Disk layout (block = 64 bytes, a block number is a 2-byte integer):

    block 0             superblock: magic "FS4", block count, descriptor count
    bitmap blocks       1 bit per block, 1 = block is used
    descriptor blocks   file descriptors, 16 bytes each, 4 per block
    data blocks         file contents, directory contents, indirect blocks

File descriptor (16 bytes):
    type (1) | links (1) | size (2) | direct[4] (4 x 2) | indirect (2) | unused (2)

    direct[k] is the block with file bytes [k*64, (k+1)*64).
    indirect is a block holding 32 more block numbers (file blocks 4..35).
    Block number 0 means "no block": the offset is past the end of the file
    or the block contains only zero bytes. Block 0 is the superblock, so it
    can never be a data block.

Directory entry (16 bytes):
    used (1) | descriptor number (2) | name (13, padded with zero bytes)

    used = 0 marks a removed hard link; its slot is reused by the next link.
    The directory is descriptor 0 and stores its entries like file data,
    so it can hold at most 2304 / 16 = 144 entries.
"""

import struct
from dataclasses import dataclass

from disk import BLOCK_SIZE, Disk

DISK_SIZE = 64 * 1024                        # 1024 blocks
MAGIC = b"FS4"
FREE, REGULAR, DIRECTORY = 0, 1, 2           # file types
TYPE_NAMES = {FREE: "free", REGULAR: "regular file", DIRECTORY: "directory"}

N_DIRECT = 4                                 # direct block numbers in a descriptor
PTRS_PER_BLOCK = BLOCK_SIZE // 2             # 32 block numbers in the indirect block
MAX_FILE_BLOCKS = N_DIRECT + PTRS_PER_BLOCK  # 36
MAX_FILE_SIZE = MAX_FILE_BLOCKS * BLOCK_SIZE # 2304 bytes
BITS_PER_BLOCK = BLOCK_SIZE * 8

SUPER_FMT = "<3sHH"                          # magic, block count, descriptor count
INODE_FMT = "<BBH4HH2x"                      # see "File descriptor" above
INODE_SIZE = 16
INODES_PER_BLOCK = BLOCK_SIZE // INODE_SIZE  # 4
NAME_LEN = 13
DIRENT_FMT = "<BH13s"                        # see "Directory entry" above
DIRENT_SIZE = 16

ROOT = 0                                     # descriptor number of the directory
ZERO_BLOCK = bytes(BLOCK_SIZE)


class FSError(Exception):
    pass


@dataclass
class Inode:                                 # file descriptor as stored on the disk
    type: int
    links: int
    size: int
    direct: list
    indirect: int


@dataclass
class Stat:                                  # what stat / ls return
    name: str
    inode: int                               # file descriptor number
    type: int
    links: int
    size: int
    blocks: int                              # data blocks in use (zero blocks are not stored)
    direct: list
    indirect: int
    indirect_ptrs: list                      # block numbers inside the indirect block


def empty_inode(file_type, links):
    return Inode(file_type, links, 0, [0] * N_DIRECT, 0)


def check_name(name):
    if len(name) == 0 or len(name) > NAME_LEN:
        raise FSError("name must be 1.." + str(NAME_LEN) + " characters: " + repr(name))
    if name == "." or name == "..":
        raise FSError("reserved name: " + repr(name))
    for c in name:
        if not (c.isascii() and (c.isalnum() or c in "._-")):
            raise FSError("bad character in name: " + repr(name))


class Driver:
    def __init__(self, image_path):
        self.disk = Disk(image_path, DISK_SIZE)
        self.formatted = False
        if self.disk.block_count < 2:
            raise FSError("disk image is too small")
        # "mount": if the image already has our superblock, use its geometry
        magic, block_count, inode_count = struct.unpack_from(SUPER_FMT, self.disk.read_block(0))
        if magic == MAGIC and block_count == self.disk.block_count:
            self.set_layout(inode_count)
            # nothing is open right after mount, so a file without links is garbage
            # (it was open when the program stopped): free it
            for i in range(inode_count):
                inode = self.read_inode(i)
                if inode.type == REGULAR and inode.links == 0:
                    self.free_file(i)

    def unmount(self):
        self.disk.close()

    def set_layout(self, inode_count):
        bitmap_blocks = (self.disk.block_count + BITS_PER_BLOCK - 1) // BITS_PER_BLOCK
        inode_start = 1 + bitmap_blocks
        data_start = inode_start + (inode_count + INODES_PER_BLOCK - 1) // INODES_PER_BLOCK
        if inode_count < 1 or data_start >= self.disk.block_count:
            raise FSError("bad number of file descriptors: " + str(inode_count))
        self.inode_count = inode_count
        self.inode_start = inode_start
        self.data_start = data_start
        self.formatted = True

    def check_formatted(self):
        if not self.formatted:
            raise FSError("file system is not formatted, call mkfs first")

    # ---------------------------------------------------------------- bitmap

    def get_bit(self, b):
        block = self.disk.read_block(1 + b // BITS_PER_BLOCK)
        byte = block[(b % BITS_PER_BLOCK) // 8]
        return (byte >> (b % 8)) & 1

    def set_bit(self, b, value):
        n = 1 + b // BITS_PER_BLOCK
        block = bytearray(self.disk.read_block(n))
        i = (b % BITS_PER_BLOCK) // 8
        if value:
            block[i] = block[i] | (1 << (b % 8))
        else:
            block[i] = block[i] & ~(1 << (b % 8))
        self.disk.write_block(n, bytes(block))

    def alloc_block(self):
        """Take the first free data block and fill it with zeros."""
        for b in range(self.data_start, self.disk.block_count):
            if self.get_bit(b) == 0:
                self.set_bit(b, 1)
                self.disk.write_block(b, ZERO_BLOCK)
                return b
        raise FSError("no free blocks on the device")

    def free_block(self, b):
        self.set_bit(b, 0)

    def free_block_count(self):
        self.check_formatted()
        count = 0
        for b in range(self.disk.block_count):
            if self.get_bit(b) == 0:
                count += 1
        return count

    # ---------------------------------------------------------------- descriptors

    def inode_place(self, i):
        if i < 0 or i >= self.inode_count:
            raise FSError("bad file descriptor number: " + str(i))
        block = self.inode_start + i // INODES_PER_BLOCK
        offset = (i % INODES_PER_BLOCK) * INODE_SIZE
        return block, offset

    def read_inode(self, i):
        block, offset = self.inode_place(i)
        v = struct.unpack_from(INODE_FMT, self.disk.read_block(block), offset)
        return Inode(v[0], v[1], v[2], list(v[3:3 + N_DIRECT]), v[3 + N_DIRECT])

    def write_inode(self, i, inode):
        block, offset = self.inode_place(i)
        data = bytearray(self.disk.read_block(block))    # 3 other descriptors share the block
        data[offset:offset + INODE_SIZE] = struct.pack(
            INODE_FMT, inode.type, inode.links, inode.size, *inode.direct, inode.indirect)
        self.disk.write_block(block, bytes(data))

    # ---------------------------------------------------------------- block map

    def get_ptr(self, inode, k):
        """Disk block that holds file block k (0 = no block)."""
        if k < N_DIRECT:
            return inode.direct[k]
        if inode.indirect == 0:
            return 0
        data = self.disk.read_block(inode.indirect)
        i = (k - N_DIRECT) * 2
        return int.from_bytes(data[i:i + 2], "little")

    def set_ptr(self, inode, k, b):
        """Set file block k to disk block b. Changes the descriptor only in memory.
        For k >= N_DIRECT the indirect block must already exist; if it ends up
        holding no block numbers at all, it is freed."""
        if k < N_DIRECT:
            inode.direct[k] = b
            return
        data = bytearray(self.disk.read_block(inode.indirect))
        i = (k - N_DIRECT) * 2
        data[i:i + 2] = b.to_bytes(2, "little")
        if data == ZERO_BLOCK:
            self.free_block(inode.indirect)
            inode.indirect = 0
        else:
            self.disk.write_block(inode.indirect, bytes(data))

    def free_blocks_from(self, inode, first):
        """Free file blocks first, first+1, ... and the indirect block if it is not needed."""
        for k in range(first, MAX_FILE_BLOCKS):
            b = self.get_ptr(inode, k)
            if b != 0:
                self.set_ptr(inode, k, 0)
                self.free_block(b)
        if first <= N_DIRECT and inode.indirect != 0:    # can be left empty after "disk full"
            self.free_block(inode.indirect)
            inode.indirect = 0

    # ---------------------------------------------------------------- file data

    def read_data(self, inode, offset, size):
        end = min(offset + size, inode.size)
        result = b""
        while offset < end:
            k = offset // BLOCK_SIZE
            start = offset % BLOCK_SIZE
            n = min(BLOCK_SIZE - start, end - offset)
            b = self.get_ptr(inode, k)
            if b == 0:
                result += bytes(n)               # no block = zeros
            else:
                result += self.disk.read_block(b)[start:start + n]
            offset += n
        return result

    def write_data(self, inode, offset, data):
        """Write data at offset. A block of only zeros is never kept on the disk.
        Changes the descriptor only in memory: the caller must write it back,
        also when this raises (some blocks may already be written)."""
        end = offset + len(data)
        if end > MAX_FILE_SIZE:
            raise FSError("file size limit is " + str(MAX_FILE_SIZE) + " bytes")
        pos = offset
        try:
            while pos < end:
                k = pos // BLOCK_SIZE
                start = pos % BLOCK_SIZE
                n = min(BLOCK_SIZE - start, end - pos)
                b = self.get_ptr(inode, k)
                if b == 0:
                    block = bytearray(BLOCK_SIZE)
                else:
                    block = bytearray(self.disk.read_block(b))
                block[start:start + n] = data[pos - offset:pos - offset + n]
                if block == ZERO_BLOCK:
                    if b != 0:                   # block became all zeros: free it
                        self.set_ptr(inode, k, 0)
                        self.free_block(b)
                else:
                    if b == 0:
                        if k >= N_DIRECT and inode.indirect == 0:
                            inode.indirect = self.alloc_block()
                        b = self.alloc_block()
                        self.set_ptr(inode, k, b)
                    self.disk.write_block(b, bytes(block))
                pos += n
        finally:
            if pos > inode.size:                 # count only the bytes really written
                inode.size = pos

    # ---------------------------------------------------------------- directory

    def read_dir(self):
        """All entry slots of the directory: list of (slot, used, inode, name)."""
        root = self.read_inode(ROOT)
        data = self.read_data(root, 0, root.size)
        entries = []
        for slot in range(len(data) // DIRENT_SIZE):
            used, ino, name = struct.unpack_from(DIRENT_FMT, data, slot * DIRENT_SIZE)
            entries.append((slot, used, ino, name.rstrip(b"\0").decode()))
        return entries

    def write_dirent(self, slot, used, ino, name):
        root = self.read_inode(ROOT)
        try:
            self.write_data(root, slot * DIRENT_SIZE, struct.pack(DIRENT_FMT, used, ino, name.encode()))
        finally:
            self.write_inode(ROOT, root)

    def find(self, name):
        """Return (slot, descriptor number) of the hard link `name`."""
        for slot, used, ino, entry_name in self.read_dir():
            if used and entry_name == name:
                return slot, ino
        raise FSError("no such file: " + repr(name))

    def add_link(self, name, ino):
        check_name(name)
        entries = self.read_dir()
        free_slot = -1
        for slot, used, _, entry_name in entries:
            if used and entry_name == name:
                raise FSError("file exists: " + repr(name))
            if not used and free_slot == -1:
                free_slot = slot                 # reuse the first removed entry
        if free_slot == -1:
            free_slot = len(entries)             # no removed entry: append
        if (free_slot + 1) * DIRENT_SIZE > MAX_FILE_SIZE:
            raise FSError("directory is full")
        self.write_dirent(free_slot, 1, ino, name)

    def make_stat(self, name, ino):
        inode = self.read_inode(ino)
        indirect_ptrs = []
        if inode.indirect != 0:
            for k in range(N_DIRECT, MAX_FILE_BLOCKS):
                indirect_ptrs.append(self.get_ptr(inode, k))
        blocks = 0
        for b in inode.direct + indirect_ptrs:
            if b != 0:
                blocks += 1
        return Stat(name, ino, inode.type, inode.links, inode.size, blocks,
                    inode.direct, inode.indirect, indirect_ptrs)

    # ---------------------------------------------------------------- used by the kernel

    def lookup(self, name):
        """Descriptor number of the file the hard link `name` points to."""
        self.check_formatted()
        _, ino = self.find(name)
        return ino

    def read_file(self, ino, offset, size):
        return self.read_data(self.read_inode(ino), offset, size)

    def write_file(self, ino, offset, data):
        inode = self.read_inode(ino)
        try:
            self.write_data(inode, offset, data)
        finally:
            self.write_inode(ino, inode)

    def free_file(self, ino):
        """Free all blocks of the file and its descriptor."""
        inode = self.read_inode(ino)
        self.free_blocks_from(inode, 0)
        self.write_inode(ino, empty_inode(FREE, 0))

    def free_if_no_links(self, ino):
        """Called by the kernel when the file is not open."""
        if self.read_inode(ino).links == 0:
            self.free_file(ino)

    # ---------------------------------------------------------------- commands

    def mkfs(self, n):
        self.set_layout(n)
        for b in range(1, self.data_start):      # empty bitmap, all descriptors free
            self.disk.write_block(b, ZERO_BLOCK)
        sb = struct.pack(SUPER_FMT, MAGIC, self.disk.block_count, n)
        self.disk.write_block(0, sb + bytes(BLOCK_SIZE - len(sb)))
        for b in range(self.data_start):         # metadata blocks are always "used"
            self.set_bit(b, 1)
        self.write_inode(ROOT, empty_inode(DIRECTORY, 1))

    def stat(self, name):
        """Information about a file; "." means the directory itself."""
        self.check_formatted()
        if name == ".":
            return self.make_stat(".", ROOT)
        _, ino = self.find(name)
        return self.make_stat(name, ino)

    def ls(self):
        self.check_formatted()
        result = []
        for _, used, ino, name in self.read_dir():
            if used:
                result.append(self.make_stat(name, ino))
        return result

    def create(self, name):
        self.check_formatted()
        ino = -1
        for i in range(self.inode_count):
            if self.read_inode(i).type == FREE:
                ino = i
                break
        if ino == -1:
            raise FSError("no free file descriptors")
        self.write_inode(ino, empty_inode(REGULAR, 1))
        try:
            self.add_link(name, ino)
        except FSError:
            self.write_inode(ino, empty_inode(FREE, 0))   # undo
            raise
        return ino

    def link(self, name1, name2):
        # no check for the links limit (255): the directory holds at most 144 entries
        self.check_formatted()
        _, ino = self.find(name1)
        self.add_link(name2, ino)
        inode = self.read_inode(ino)
        inode.links += 1
        self.write_inode(ino, inode)

    def unlink(self, name):
        """Remove the hard link. The file itself is freed by the kernel (free_if_no_links)."""
        self.check_formatted()
        slot, ino = self.find(name)
        self.write_dirent(slot, 0, ino, name)    # mark the hard link invalid
        inode = self.read_inode(ino)
        inode.links -= 1
        self.write_inode(ino, inode)
        return ino

    def truncate(self, name, size):
        self.check_formatted()
        if size < 0 or size > MAX_FILE_SIZE:
            raise FSError("size must be 0.." + str(MAX_FILE_SIZE))
        _, ino = self.find(name)
        inode = self.read_inode(ino)
        if size < inode.size:
            self.free_blocks_from(inode, (size + BLOCK_SIZE - 1) // BLOCK_SIZE)
            if size % BLOCK_SIZE != 0:           # zero the old bytes after the new end
                self.write_data(inode, size, bytes(BLOCK_SIZE - size % BLOCK_SIZE))
        inode.size = size                        # growing adds no blocks: the new part reads as zeros
        self.write_inode(ino, inode)
