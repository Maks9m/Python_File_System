"""Storage device: a disk image file that is read and written only in whole blocks."""

import os

BLOCK_SIZE = 64 # bytes per block


class Disk:
    def __init__(self, path, size):
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(bytes(size))
        # os.pread / os.pwrite work on the raw OS file: no buffering in between
        self.fd = os.open(path, os.O_RDWR)
        self.block_count = os.path.getsize(path) // BLOCK_SIZE

    def read_block(self, n):
        if n < 0 or n >= self.block_count:
            raise IndexError("block number out of range: " + str(n))
        return os.pread(self.fd, BLOCK_SIZE, n * BLOCK_SIZE)

    def write_block(self, n, data):
        if n < 0 or n >= self.block_count:
            raise IndexError("block number out of range: " + str(n))
        if len(data) != BLOCK_SIZE:
            raise ValueError("a block write must be exactly BLOCK_SIZE bytes")
        os.pwrite(self.fd, data, n * BLOCK_SIZE)

    def close(self):
        os.close(self.fd)
