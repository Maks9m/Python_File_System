"""Lab 4: the kernel part of the file system subsystem.

The kernel owns the open file table (numerical file descriptors and their offsets)
and passes every request to the file system driver. It decides when a file can be
freed: only when no hard link points to it and no fd has it open.
"""

from dataclasses import dataclass

from fs import Driver, FSError

MAX_OPEN = 16                                # size of the open file table


@dataclass
class OpenFile:                              # entry of the open file table
    inode: int                               # file descriptor number in the FS
    offset: int


class Kernel:
    def __init__(self, image_path):
        self.fs = Driver(image_path)
        self.open_files = [None] * MAX_OPEN

    def unmount(self):
        for fd in range(MAX_OPEN):               # closing frees files that have no links
            if self.open_files[fd] is not None:
                self.close(fd)
        self.fs.unmount()

    def get_open(self, fd):
        if fd < 0 or fd >= MAX_OPEN or self.open_files[fd] is None:
            raise FSError("bad numerical file descriptor: " + str(fd))
        return self.open_files[fd]

    def release(self, ino):
        """Free the file if it is not open (the driver checks that it has no links)."""
        for f in self.open_files:
            if f is not None and f.inode == ino:
                return
        self.fs.free_if_no_links(ino)

    # ---------------------------------------------------------------- commands

    def mkfs(self, n):
        self.fs.mkfs(n)
        self.open_files = [None] * MAX_OPEN

    def stat(self, name):
        return self.fs.stat(name)

    def ls(self):
        return self.fs.ls()

    def create(self, name):
        return self.fs.create(name)

    def link(self, name1, name2):
        self.fs.link(name1, name2)

    def unlink(self, name):
        ino = self.fs.unlink(name)
        self.release(ino)

    def truncate(self, name, size):
        self.fs.truncate(name, size)

    def open(self, name):
        ino = self.fs.lookup(name)
        for fd in range(MAX_OPEN):
            if self.open_files[fd] is None:      # smallest free number
                self.open_files[fd] = OpenFile(ino, 0)
                return fd
        raise FSError("too many open files")

    def close(self, fd):
        f = self.get_open(fd)
        self.open_files[fd] = None
        self.release(f.inode)

    def seek(self, fd, offset):
        if offset < 0:
            raise FSError("negative offset")
        self.get_open(fd).offset = offset

    def read(self, fd, size):
        if size < 0:
            raise FSError("negative size")
        f = self.get_open(fd)
        data = self.fs.read_file(f.inode, f.offset, size)
        f.offset += len(data)
        return data

    def write(self, fd, data):
        f = self.get_open(fd)
        if len(data) > 0:                        # an empty write changes nothing
            self.fs.write_file(f.inode, f.offset, data)
            f.offset += len(data)
        return len(data)

    def free_blocks(self):
        return self.fs.free_block_count()
