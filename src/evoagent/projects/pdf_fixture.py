"""M5 F-03：手工构造最小 PDF，用于测试与 fixture，不引入"生成器"这类外部依赖。

只支持英文/ASCII 的 Type1 基础字体，够用来验证"文本型 PDF 能抽取、无文本 PDF 被识别为
扫描件"这两条契约；不支持中文编码，因为那需要嵌入 CID 字体，超出测试所需。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PdfPage:
    """一页内容：若干行文本。空行用空字符串表示。"""

    lines: tuple[str, ...]


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(page: PdfPage) -> bytes:
    parts = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for index, line in enumerate(page.lines):
        if index:
            parts.append("T*")
        parts.append(f"({_escape(line)}) Tj")
    parts.append("ET")
    return ("\n".join(parts) + "\n").encode("latin-1")


def build_pdf(pages: list[PdfPage]) -> bytes:
    """生成一个可被解析器正常读取的多页 PDF。"""

    objects: dict[int, bytes] = {}
    page_ids: list[int] = []
    # 1: Catalog，2: Pages，3: Font，之后每页两个对象（Page、Contents）。
    next_id = 4
    for page in pages:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)
        stream = _content_stream(page)
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("latin-1")
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"endstream"
        )

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{item} 0 R" for item in page_ids)
    objects[2] = (f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>").encode("latin-1")
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for object_id in sorted(objects):
        offsets[object_id] = len(out)
        out += f"{object_id} 0 obj\n".encode("latin-1")
        out += objects[object_id]
        out += b"\nendobj\n"

    xref_offset = len(out)
    count = max(objects) + 1
    out += f"xref\n0 {count}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for object_id in range(1, count):
        if object_id in offsets:
            out += f"{offsets[object_id]:010d} 00000 n \n".encode("latin-1")
        else:  # pragma: no cover - 本构造器不留空洞
            out += b"0000000000 65535 f \n"
    out += (f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").encode(
        "latin-1"
    )
    return bytes(out)


def text_pdf(*paragraphs: str) -> bytes:
    """把若干段落包成单页文本型 PDF。"""

    lines: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        if index:
            lines.append("")
        lines.extend(paragraph.splitlines() or [""])
    return build_pdf([PdfPage(lines=tuple(lines))])


def image_only_pdf() -> bytes:
    """一个没有可抽取文本的 PDF，用来模拟扫描/图片型文件。"""

    return build_pdf([PdfPage(lines=())])
