"""极简 OLE 复合文档 (Compound File Binary) 读取器。

Altium 的 .PcbDoc / .SchDoc 都是 CFB 容器，这里只需要按名字取出流内容，
不依赖任何第三方库。
"""

from __future__ import annotations

import struct

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
MAXREGSECT = 0xFFFFFFFA


class CFBError(Exception):
    pass


class CFBFile:
    def __init__(self, data: bytes):
        if len(data) < 512 or not data.startswith(MAGIC):
            raise CFBError("不是有效的 OLE 复合文档")
        self.data = data
        self.sector_size = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_sector_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
        if self.sector_size not in (512, 4096):
            raise CFBError(f"异常的扇区大小: {self.sector_size}")
        (
            self.num_fat_sectors,
            self.first_dir_sector,
            self.mini_cutoff,
            self.first_mini_fat_sector,
            self.num_mini_fat_sectors,
            self.first_difat_sector,
            self.num_difat_sectors,
        ) = struct.unpack_from("<IIIIIII", data, 0x2C)

        self._fat = self._read_fat()
        self._entries = self._read_directory()
        self._mini_stream = b""
        self._mini_fat: list[int] = []
        root = self._root_entry()
        if root:
            self._mini_stream = self._read_chain(root[1], root[2], mini=False)
            self._mini_fat = self._read_mini_fat(root[1])

    # ---- 结构读取 -------------------------------------------------
    def _sector_offset(self, sector: int) -> int:
        return 512 + sector * self.sector_size if self.sector_size == 512 else (sector + 1) * self.sector_size

    def _read_fat(self) -> list[int]:
        difat: list[int] = list(struct.unpack_from("<109I", self.data, 0x4C))
        sector = self.first_difat_sector
        per = self.sector_size // 4 - 1
        count = 0
        while sector not in (ENDOFCHAIN, FREESECT) and sector <= MAXREGSECT and count < self.num_difat_sectors:
            off = self._sector_offset(sector)
            chunk = self.data[off: off + self.sector_size]
            if len(chunk) < self.sector_size:
                break
            values = struct.unpack("<%dI" % (self.sector_size // 4), chunk)
            difat.extend(values[:per])
            sector = values[-1]
            count += 1

        fat: list[int] = []
        for fat_sector in difat[: self.num_fat_sectors]:
            if fat_sector > MAXREGSECT:
                continue
            off = self._sector_offset(fat_sector)
            chunk = self.data[off: off + self.sector_size]
            if len(chunk) < self.sector_size:
                break
            fat.extend(struct.unpack("<%dI" % (self.sector_size // 4), chunk))
        return fat

    def _read_mini_fat(self, _root_sector: int) -> list[int]:
        if self.first_mini_fat_sector > MAXREGSECT:
            return []
        raw = self._read_chain(self.first_mini_fat_sector, self.num_mini_fat_sectors * self.sector_size, mini=False)
        count = len(raw) // 4
        return list(struct.unpack("<%dI" % count, raw[: count * 4]))

    def _read_chain(self, start: int, size: int | None = None, mini: bool = False) -> bytes:
        out = bytearray()
        sector = start
        seen = 0
        if mini:
            while sector <= MAXREGSECT:
                off = sector * self.mini_sector_size
                out += self._mini_stream[off: off + self.mini_sector_size]
                if sector >= len(self._mini_fat):
                    break
                sector = self._mini_fat[sector]
                seen += 1
                if seen > 1_000_000:
                    break
        else:
            while sector <= MAXREGSECT:
                off = self._sector_offset(sector)
                out += self.data[off: off + self.sector_size]
                if sector >= len(self._fat):
                    break
                sector = self._fat[sector]
                seen += 1
                if seen > 1_000_000:
                    break
        return bytes(out[:size]) if size is not None else bytes(out)

    def _read_directory(self) -> list[tuple[str, int, int, int]]:
        raw = self._read_chain(self.first_dir_sector)
        entries: list[tuple[str, int, int, int]] = []
        for off in range(0, len(raw) - 127, 128):
            entry = raw[off: off + 128]
            name_len = struct.unpack_from("<H", entry, 0x40)[0]
            obj_type = entry[0x42]
            if obj_type == 0 or name_len < 2:
                continue
            name = entry[: max(0, name_len - 2)].decode("utf-16-le", errors="ignore")
            start_sector = struct.unpack_from("<I", entry, 0x74)[0]
            size = struct.unpack_from("<Q", entry, 0x78)[0]
            entries.append((name, start_sector, size, obj_type))
        return entries

    def _root_entry(self):
        for entry in self._entries:
            if entry[3] == 5:  # root storage
                return entry
        return None

    # ---- 对外接口 -------------------------------------------------
    def list_streams(self) -> list[str]:
        return [e[0] for e in self._entries if e[3] == 2]

    def has_stream(self, name: str) -> bool:
        return any(e[0] == name for e in self._entries)

    def get_stream(self, name: str) -> bytes | None:
        for entry_name, start, size, obj_type in self._entries:
            if obj_type != 2 or entry_name != name:
                continue
            if size < self.mini_cutoff:
                return self._read_chain(start, size, mini=True)
            return self._read_chain(start, size, mini=False)
        return None
