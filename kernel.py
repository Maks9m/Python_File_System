"""Lab 5: the kernel part of the file system subsystem.

The kernel owns the open file table (numerical file descriptors and their offsets)
and the current working directory (CWD) of the process, and passes every request
to the file system driver. It decides when a file can be freed: only when no hard
link points to it, no fd has it open and it is not the CWD.
"""

from dataclasses import dataclass

from fs import ROOT, Driver, FSError

MAX_OPEN = 16                                # size of the open file table


@dataclass
class OpenFile:                              # entry of the open file table
    inode: int                               # file descriptor number in the FS
    offset: int


class Kernel:
    def __init__(self, image_path):
        self.fs = Driver(image_path)
        self.open_files = [None] * MAX_OPEN
        self.cwd = ROOT                          # descriptor number of the CWD

    def unmount(self):
        for fd in range(MAX_OPEN):               # closing frees files that have no links
            if self.open_files[fd] is not None:
                self.close(fd)
        old = self.cwd
        self.cwd = ROOT
        self.release(old)
        self.fs.unmount()

    def get_open(self, fd):
        if fd < 0 or fd >= MAX_OPEN or self.open_files[fd] is None:
            raise FSError("bad numerical file descriptor: " + str(fd))
        return self.open_files[fd]

    def release(self, ino):
        """Free the file if nothing in the kernel uses it (the driver checks it has no links)."""
        if ino == self.cwd:
            return
        for f in self.open_files:
            if f is not None and f.inode == ino:
                return
        self.fs.free_if_no_links(ino)

    # ---------------------------------------------------------------- commands

    def mkfs(self, n):
        self.fs.mkfs(n)
        self.open_files = [None] * MAX_OPEN
        self.cwd = ROOT

    def stat(self, path):
        return self.fs.stat(path, self.cwd)

    def ls(self, path=None):
        return self.fs.ls(path, self.cwd)

    def create(self, path):
        return self.fs.create(path, self.cwd)

    def mkdir(self, path):
        return self.fs.mkdir(path, self.cwd)

    def symlink(self, target, path):
        return self.fs.symlink(target, path, self.cwd)

    def link(self, path1, path2):
        self.fs.link(path1, path2, self.cwd)

    def unlink(self, path):
        ino = self.fs.unlink(path, self.cwd)
        self.release(ino)

    def rmdir(self, path):
        ino = self.fs.rmdir(path, self.cwd)
        self.release(ino)                        # stays while it is the CWD

    def cd(self, path):
        ino = self.fs.find_dir(path, self.cwd)
        old = self.cwd
        self.cwd = ino
        self.release(old)                        # frees the old CWD if it was removed

    def truncate(self, path, size):
        self.fs.truncate(path, size, self.cwd)

    def open(self, path):
        ino = self.fs.find_regular(path, self.cwd)
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
