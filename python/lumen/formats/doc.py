"""Legacy Word 97-2003 .doc (standard library only).

Best quality first:
  1. Microsoft Word installed  → converted to .docx through Word's COM interface (via PowerShell)
  2. LibreOffice installed     → converted to .docx in headless mode
  3. Built-in fallback         → plain text read from the binary file (own Compound File reader +
                                 [MS-DOC] piece table), with cycle protection against crafted files
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from typing import Optional

from .common import DocumentError

ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF


# ---------------------------------------------------------------------------
#  1-2. Conversion with installed office software
# ---------------------------------------------------------------------------

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_PS_WORD = r"""
$ErrorActionPreference = 'Stop'
$word = New-Object -ComObject Word.Application
try {
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $doc = $word.Documents.Open($env:LUMEN_IN, $false, $true, $false, 'lumen-no-password')
  $doc.SaveAs2($env:LUMEN_OUT, 16)
  $doc.Close($false)
} finally {
  $word.Quit()
}
"""


def convert_with_word(src: str, out_dir: str) -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"))
    except OSError:
        return None  # Word is not installed
    out = os.path.join(out_dir, "converted.docx")
    env = dict(os.environ, LUMEN_IN=os.path.abspath(src), LUMEN_OUT=out)
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_WORD],
                       env=env, timeout=90, capture_output=True, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out if os.path.isfile(out) else None


def convert_with_libreoffice(src: str, out_dir: str) -> Optional[str]:
    candidates = [shutil.which("soffice")]
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
    exe = next((c for c in candidates if c and os.path.isfile(c)), None)
    if not exe:
        return None
    profile = "file:///" + os.path.join(out_dir, "lo-profile").replace("\\", "/").lstrip("/")
    try:
        subprocess.run([exe, f"-env:UserInstallation={profile}", "--headless", "--norestore",
                        "--convert-to", "docx", "--outdir", out_dir, src],
                       timeout=120, capture_output=True, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + ".docx")
    return out if os.path.isfile(out) else None


def convert_to_docx(src: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (docx path, temp dir to delete) or (None, None)."""
    tmp = tempfile.mkdtemp(prefix="lumen-doc-")
    for fn in (convert_with_word, convert_with_libreoffice):
        try:
            out = fn(src, tmp)
        except Exception:
            out = None
        if out:
            return out, tmp
    shutil.rmtree(tmp, ignore_errors=True)
    return None, None


# ---------------------------------------------------------------------------
#  3. Built-in text extraction
# ---------------------------------------------------------------------------

class CompoundFile:
    """Minimal read-only reader for the OLE Compound File Binary format ([MS-CFB])."""

    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise DocumentError("ErrorTitle")
        self.data = data
        self.sector_size = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
        if self.sector_size not in (512, 4096) or self.mini_size != 64:
            raise DocumentError("ErrorTitle")
        first_dir = struct.unpack_from("<I", data, 0x30)[0]
        self.mini_cutoff = struct.unpack_from("<I", data, 0x38)[0]
        first_minifat = struct.unpack_from("<I", data, 0x3C)[0]
        first_difat = struct.unpack_from("<I", data, 0x44)[0]
        num_difat = struct.unpack_from("<I", data, 0x48)[0]

        # DIFAT → list of FAT sectors
        fat_sectors = [s for s in struct.unpack_from("<109I", data, 0x4C) if s < 0xFFFFFFFA]
        per = self.sector_size // 4 - 1
        sid, seen = first_difat, set()
        for _ in range(num_difat):
            if sid >= 0xFFFFFFFA or sid in seen:
                break
            seen.add(sid)
            vals = struct.unpack_from(f"<{per + 1}I", self._sector(sid))
            fat_sectors += [s for s in vals[:per] if s < 0xFFFFFFFA]
            sid = vals[per]

        fat_bytes = b"".join(self._sector(s) for s in fat_sectors)
        self.fat = struct.unpack(f"<{len(fat_bytes) // 4}I", fat_bytes)

        dir_data = self._chain_data(first_dir, self.fat, self._sector)
        self.entries = []
        for off in range(0, len(dir_data) - 127, 128):
            name_len = struct.unpack_from("<H", dir_data, off + 0x40)[0]
            name = dir_data[off:off + max(0, min(name_len, 64) - 2)].decode("utf-16-le", errors="replace")
            etype = dir_data[off + 0x42]
            start = struct.unpack_from("<I", dir_data, off + 0x74)[0]
            size = struct.unpack_from("<Q", dir_data, off + 0x78)[0]
            if self.sector_size == 512:
                size &= 0xFFFFFFFF
            self.entries.append((name, etype, start, size))

        root = next((e for e in self.entries if e[1] == 5), None)
        if root is None:
            raise DocumentError("ErrorTitle")
        self.mini_stream = self._chain_data(root[2], self.fat, self._sector)[:root[3]]
        minifat_bytes = self._chain_data(first_minifat, self.fat, self._sector) if first_minifat < 0xFFFFFFFA else b""
        self.minifat = struct.unpack(f"<{len(minifat_bytes) // 4}I", minifat_bytes) if minifat_bytes else ()

    def _sector(self, sid: int) -> bytes:
        off = (sid + 1) * self.sector_size
        if off + self.sector_size > len(self.data):
            raise DocumentError("ErrorTitle")
        return self.data[off:off + self.sector_size]

    def _mini_sector(self, sid: int) -> bytes:
        off = sid * self.mini_size
        return self.mini_stream[off:off + self.mini_size]

    @staticmethod
    def _chain_data(start: int, table, reader) -> bytes:
        parts, sid, seen = [], start, set()
        while sid < 0xFFFFFFFA:
            if sid in seen or sid >= len(table):   # cycle or corrupt chain
                break
            seen.add(sid)
            parts.append(reader(sid))
            sid = table[sid]
        return b"".join(parts)

    def stream(self, name: str) -> bytes:
        for ename, etype, start, size in self.entries:
            if etype == 2 and ename == name:
                if size < self.mini_cutoff:
                    data = self._chain_data(start, self.minifat, self._mini_sector)
                else:
                    data = self._chain_data(start, self.fat, self._sector)
                return data[:size]
        raise DocumentError("ErrorTitle")


def extract_text(path: str) -> str:
    with open(path, "rb") as f:
        cf = CompoundFile(f.read())
    wd = cf.stream("WordDocument")
    if len(wd) < 0x1AA or struct.unpack_from("<H", wd, 0)[0] != 0xA5EC:
        raise DocumentError("ErrorTitle")
    flags = struct.unpack_from("<H", wd, 0x0A)[0]
    if flags & 0x0100:
        raise DocumentError("PasswordProtected")
    table = cf.stream("1Table" if flags & 0x0200 else "0Table")

    ccp_text = struct.unpack_from("<i", wd, 0x4C)[0]
    fc_clx, lcb_clx = struct.unpack_from("<iI", wd, 0x1A2)
    pos, end = fc_clx, fc_clx + lcb_clx
    if end > len(table):
        raise DocumentError("ErrorTitle")
    while pos < end and table[pos] == 0x01:              # Prc entries
        pos += 3 + struct.unpack_from("<h", table, pos + 1)[0]
    if pos >= end or table[pos] != 0x02:                  # Pcdt
        raise DocumentError("ErrorTitle")
    lcb = struct.unpack_from("<I", table, pos + 1)[0]
    plc = pos + 5
    n = (lcb - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}i", table, plc)

    out = []
    for i in range(n):
        cp_start, cp_end = cps[i], min(cps[i + 1], ccp_text)
        if cp_start >= ccp_text:
            break
        count = cp_end - cp_start
        if count <= 0:
            continue
        fc_raw = struct.unpack_from("<I", table, plc + (n + 1) * 4 + i * 8 + 2)[0]
        fc = fc_raw & 0x3FFFFFFF
        if fc_raw & 0x40000000:
            off = fc // 2
            out.append(wd[off:off + count].decode("cp1252", errors="replace"))
        else:
            out.append(wd[fc:fc + count * 2].decode("utf-16-le", errors="replace"))
    return _clean("".join(out))


def _clean(s: str) -> str:
    """Hides field codes (0x13 code 0x14 result 0x15) and maps Word control characters."""
    res, fields = [], []
    for ch in s:
        if ch == "\x13":
            fields.append(True)
            continue
        if ch == "\x14":
            if fields:
                fields[-1] = False
            continue
        if ch == "\x15":
            if fields:
                fields.pop()
            continue
        if True in fields:
            continue
        if ch in "\r\x0b\x0c\x07":
            res.append("\n")
        elif ch == "\x1e":
            res.append("-")
        elif ch == "\t" or ch >= " ":
            res.append(ch)
    return "".join(res)


def sniff(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"
    if head.startswith(b"PK"):
        return "zip"
    start = head.lstrip(b"\xef\xbb\xbf \r\n\t")
    if start.startswith(b"{\\rtf"):
        return "rtf"
    if start.startswith(b"<"):
        return "html"
    return "ole"
