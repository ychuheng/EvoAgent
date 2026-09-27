"""读写代码文件时保留编码、BOM 与行尾（实施计划 §7 E-01）。

编辑必须"保留编码、行尾、权限位"，否则一次修改会把整个文件的行尾或 BOM 换掉，
在 diff 里表现为全文件重写。这里把"检测出的文本表示"和"字节"分开保存：

- `DetectedText`：检测到的编码、是否有 BOM、主导行尾、是否 UTF-8 BOM 等；
- `encode_like()`：按检测结果把编辑后的文本重新编码成字节，尽量逐字节还原未改动部分。
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from pathlib import Path

from evoagent.tools.base import ToolExecutionError

# 支持的编码按检测顺序排列：BOM 优先，其次是严格 UTF-8，最后是不带 BOM 的 UTF-16。
_BOMS: tuple[tuple[bytes, str, str], ...] = (
    (codecs.BOM_UTF8, "utf-8", "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le", "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16-be", "utf-16"),
)
# 探测行尾时只需看开头这么多字节。
SNIFF_BYTES = 8_192
LINE_ENDINGS = ("\r\n", "\n", "\r")


@dataclass(frozen=True, slots=True)
class DetectedText:
    """一个文件被检测出的文本表示。"""

    text: str
    encoding: str
    bom: bytes
    newline: str

    @property
    def encoding_label(self) -> str:
        if self.bom == codecs.BOM_UTF8:
            return "utf-8-sig"
        return self.encoding

    def encode_like(self, text: str) -> bytes:
        """按原文件的编码与 BOM 编码新文本。"""

        try:
            return self.bom + text.encode(self.encoding)
        except UnicodeEncodeError as error:
            raise ToolExecutionError(
                f"编辑结果无法用原编码 {self.encoding_label} 表示：{error.reason}"
            ) from error


def detect_line_ending(sample: str) -> str:
    """返回出现次数最多的行尾；没有换行时按平台习惯给 `\\n`。"""

    counts = {ending: sample.count(ending) for ending in LINE_ENDINGS}
    # `\r\n` 会被 `\n` 重复计数，比较时先把 CRLF 的量从 LF 里扣掉。
    counts["\n"] = max(0, counts["\n"] - counts["\r\n"])
    counts["\r"] = max(0, counts["\r"] - counts["\r\n"])
    best = max(LINE_ENDINGS, key=lambda item: counts[item])
    return best if counts[best] > 0 else "\n"


def decode_bytes(raw: bytes, *, display: str) -> DetectedText:
    """把字节解码成文本；不支持的编码直接拒绝，不做有损替换。"""

    if not raw:
        return DetectedText(text="", encoding="utf-8", bom=b"", newline="\n")
    for bom, encoding, _label in _BOMS:
        if raw.startswith(bom):
            body = raw[len(bom) :]
            try:
                text = body.decode(encoding)
            except UnicodeDecodeError as error:
                raise ToolExecutionError(f"{display} 的 {encoding} 内容损坏") from error
            return DetectedText(
                text=text,
                encoding=encoding,
                bom=bom,
                newline=detect_line_ending(text[:SNIFF_BYTES]),
            )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ToolExecutionError(
            f"{display} 不是受支持的文本编码（UTF-8/UTF-8 BOM/UTF-16）；"
            f"在偏移 {error.start} 处解码失败"
        ) from error
    if "\x00" in text:
        raise ToolExecutionError(f"{display} 含 NUL 字节，按二进制拒绝")
    return DetectedText(
        text=text,
        encoding="utf-8",
        bom=b"",
        newline=detect_line_ending(text[:SNIFF_BYTES]),
    )


def read_text(path: Path, *, display: str) -> DetectedText:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ToolExecutionError(f"{display} 无法读取") from error
    return decode_bytes(raw, display=display)


def count_line_endings(text: str) -> dict[str, int]:
    """统计行尾类型，用于在 diff 里报告"是否改动了行尾"。"""

    crlf = text.count("\r\n")
    return {
        "crlf": crlf,
        "lf": max(0, text.count("\n") - crlf),
        "cr": max(0, text.count("\r") - crlf),
    }
