# Python_File_System

Python File System: Operating Systems labs, 5th semester.

This is a user-space simulation of a Unix-like block filesystem driver and the
kernel FS subsystem (open file table), written in pure Python 3.11+ (standard
library only; `pytest` for tests). It does not use FUSE or pass-through files:
every operation reads and writes fixed-size blocks of a disk image file
(`disk.img`) through `os.pread`/`os.pwrite`, with **no buffer cache and no name
cache**.

| Module | Responsibility |
|---|---|
| `block_device.py` | `read_block(i)` / `write_block(i, data)` over the image file |
| `layout.py` | constants and `struct` formats: superblock, bitmap, inode, directory entry |
| `fs.py` | FS driver: block allocation, block maps, sparse files, directories, open file table |
| `cli.py` | command interpreter (interactive REPL + script/stdin mode) |
| `main.py` | entry point (`--disk <path>`) |

## On-disk layout

All integers are little-endian. `BLOCK_SIZE = 256`. The default image is 512 KB (2048 blocks).

```
block 0                         superblock
block 1 .. bitmap_end           free-block bitmap        (1 bit / block, 1 = allocated)
inode_table_start ..            inode table              (8 inodes / block)
data_start .. total_blocks-1    data blocks              (file data, directories, indirect blocks)
```

With `mkfs 32` on a 512 KB image: bitmap = block 1, inodes = blocks 2–5, data starts at block 6.
Block `0` is always the superblock, so a block pointer of `0` means "no block".

### Superblock (block 0, `<4sIIIIII`, 28 bytes, rest zero)

| Offset | Type | Field |
|---|---|---|
| 0 | `4s` | magic `b"PYFS"` |
| 4 | `u32` | `block_size` (256) |
| 8 | `u32` | `total_blocks` |
| 12 | `u32` | `inode_count` (n) |
| 16 | `u32` | `bitmap_start_block` |
| 20 | `u32` | `inode_table_start_block` |
| 24 | `u32` | `data_start_block` |

### Free-block bitmap

`ceil(total_blocks / 2048)` blocks. Bit `b % 8` of byte `b // 8` describes block `b`.
Metadata blocks (`0 .. data_start-1`) are marked allocated by `mkfs`.

### Inode (`<BBHI8HH6x`, 32 bytes, 8 per block)

| Offset | Type | Field |
|---|---|---|
| 0 | `u8` | `file_type`: `FREE=0`, `REGULAR=1`, `DIRECTORY=2` |
| 1 | `u8` | reserved |
| 2 | `u16` | `hard_links` |
| 4 | `u32` | `size` in bytes |
| 8 | `8 × u16` | `direct_blocks[0..7]` |
| 24 | `u16` | `indirect_block`: a block of 128 `u16` pointers for file blocks 8..135 |
| 26 | 6 bytes | padding |

Inode `i` is stored in block `inode_table_start + i // 8` at offset `(i % 8) * 32`.
The index of a pointer in the block map gives the file offset (`index * 256`). A pointer
is `0` when that offset is beyond EOF **or the block contains only zero bytes**
(sparse block). The maximum file size is `(8 + 128) * 256 = 34816` bytes.

### Directory entry (`<BH28sx`, 32 bytes, 8 per block)

| Offset | Type | Field |
|---|---|---|
| 0 | `u8` | `valid` (1 = live hard link, 0 = removed) |
| 1 | `u16` | inode number |
| 3 | `28s` | name, UTF-8, NUL-padded |
| 31 | 1 byte | reserved |

A directory's contents are an array of entries stored in its data blocks, like a regular
file. `unlink` only clears `valid`; it never shifts entries, and `create`/`link` reuse the
first invalid slot. Filenames must be non-empty, printable, contain no `/` or NUL, and
be at most 28 UTF-8 bytes long. The root directory is inode `0`.

## Semantics

* **Sparse files.** `truncate` to a larger size and `write` after a `seek` past EOF do not
  allocate blocks for the gap. Writing zeros into a hole keeps it a hole, and a block that
  becomes all zeros is freed. Holes read back as `\x00`. Shrinking frees blocks past
  the new EOF (and the indirect block when it is no longer needed), and zeroes the tail
  of the last kept block.
* **Open file table.** Up to 64 entries by default (`--max-open`). `open` returns the
  lowest free fd (`0, 1, 2, …`), which is independent of the inode number. Each fd has
  its own offset, starting at 0.
* **Deferred deletion.** A file's blocks and inode are freed only when `hard_links == 0`
  **and** no fd refers to it. An unlinked file that is still open can still be
  `seek`/`read`/`write`-ed until its last fd is closed.

## Usage

```bash
python3 main.py --disk disk.img                         # interactive REPL
```
```bash
python3 main.py --disk disk.img < scripts/demo_lab4.txt  # script mode (commands echoed)
```
```bash
python3 main.py --disk disk.img scripts/demo_lab4.txt    # same, script as an argument
```

`mkfs` creates the image (`--size`, default 524288 bytes) if it does not exist yet.

| Command | Description |
|---|---|
| `mkfs <n>` | format the disk with `n` inodes and create the root directory (inode 0) |
| `stat <name>` | inode #, type, hard links, size, direct/indirect block map |
| `ls` | hard links in the directory with inode numbers |
| `create <name>` | create an empty regular file |
| `open <name>` | open a regular file, print the fd |
| `close <fd>` | close the fd (may trigger deferred deallocation) |
| `seek <fd> <offset>` | set the fd's offset |
| `read <fd> <size>` | read up to `size` bytes (non-printables shown as `\xNN`) |
| `write <fd> <size> [data]` | write `size` bytes: `data` repeated/truncated to `size`, or the pattern `ABC…Z` when omitted |
| `link <name1> <name2>` | create hard link `name2` to the file `name1` |
| `unlink <name>` | remove the hard link |
| `truncate <name> <size>` | resize the file (growth stays sparse) |
| `help`, `exit`, `quit` | |

Arguments are parsed with `shlex`, so quote data that contains spaces: `write 0 6 " world"`.

## Tests and report logs

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
```
```bash
.venv/bin/python -m pytest
```
```bash
rm -f disk.img && .venv/bin/python main.py --disk disk.img < scripts/demo_lab4.txt > reports/lab4_output.txt
```

`reports/lab4_output.txt` contains the full command and output log of the Lab 4 demo.
