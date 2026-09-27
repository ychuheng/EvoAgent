"""M5 F-03：格式处理——UTF-8 以外编码与文本型 PDF 的**明确解析器**。

计划要求：

- 「UTF-8 以外编码和文本型 PDF 各用**明确解析器**」；
- 「扫描/图片型 PDF 首版不支持并提示需单独配置 OCR」；
- 「不把二进制当文本送入模型」；
- 「解析器缺失或文件损坏时保留原文件与错误……依赖它的任务判为不完整，不静默猜测内容」。

因此这里的原则是**宁可报错也不猜**：

- 编码用"严格解码候选阶梯"，任何一个候选解出控制字符过多的文本都判为"无法确定为文本"；
- PDF 只在 `pypdf` 可用且能解析时抽取文本；抽取出的文本过少即判定为扫描/图片型 PDF 并明确拒绝；
- 所有失败都带稳定的错误码，便于上层把任务标为不完整而不是编造内容。
"""

from __future__ import annotations

import codecs
import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path

from evoagent.tools.base import ToolExecutionError

# 内置解析器版本进入结果，便于报告里说明"用哪个解析器得到这段文本"。
TEXT_EXTRACTOR = "text-strict-v1"
PDF_EXTRACTOR = "pypdf"
PDF_MIN_TEXT_CHARS = 20
MAX_EXTRACT_CHARS = 200_000

# **可确定才自动**：UTF-8 家族与带 BOM 的 UTF-16/32 能可靠判定；其余编码的字节范围互相重叠
# （实测：gb18030 会把 Big5 与 Shift_JIS 的字节"成功"解成乱码），因此不自动猜——
# 由调用方用显式 `encoding` 声明，或报告"无法确定"让上层把任务标为不完整。
AUTO_ENCODINGS: tuple[str, ...] = ("utf-8",)
# 显式声明时允许使用的编码（计划要求"各用明确解析器"，这就是那份名单）。
# 名单用**可读别名**书写；`codecs.lookup` 的规范名（如 latin-1 → iso8859-1）由
# `_canonical_declared_encoding` 折回名单，因此 `Shift_JIS`、`latin1` 这类写法同样可用。
SUPPORTED_ENCODINGS: tuple[str, ...] = (
    "utf-8",
    "utf-8-sig",
    "utf-16",
    "utf-32",
    "gb18030",
    "big5",
    "shift_jis",
    "euc-kr",
    "cp1252",
    "latin-1",
)
# 规范名 → 名单里的可读写法。
_CANONICAL_ALIASES: dict[str, str] = {
    "iso8859-1": "latin-1",
    "euc_kr": "euc-kr",
}


def _canonical_declared_encoding(encoding: str) -> str | None:
    """把声明归一成名单里的写法；不在名单内返回 None。"""

    try:
        canonical = codecs.lookup(encoding.strip()).name
    except (LookupError, AttributeError):
        return None
    candidate = _CANONICAL_ALIASES.get(canonical, canonical)
    return candidate if candidate in SUPPORTED_ENCODINGS else None


# 这些编码只在 BOM 出现时自动判定（顺序敏感：先长 BOM，避免 utf-16 的 BOM 抢先匹配 utf-32）。
BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)
# `latin-1` 能解码任意字节，因此必须靠"控制字符比例"兜底，否则任何二进制都会被当成文本。
CONTROL_CHAR_RATIO_LIMIT = 0.02


class ExtractionError(ToolExecutionError):
    """提取失败，带稳定错误码。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Extraction:
    """一次成功提取的结果。"""

    text: str
    format: str
    extractor: str
    encoding: str | None = None
    pages: int | None = None
    truncated: bool = False
    warnings: tuple[str, ...] = field(default=())


def has_pdf_signature(head: bytes) -> bool:
    """PDF 头可能在文件很前面（允许少量前导字节）。"""

    return b"%PDF-" in head[:1024]


def pdf_parser_available() -> bool:
    return importlib.util.find_spec("pypdf") is not None


def _control_ratio(text: str) -> float:
    if not text:
        return 0.0
    suspicious = sum(
        1 for char in text if (ord(char) < 32 and char not in "\t\n\r") or ord(char) == 0x7F
    )
    return suspicious / len(text)


def _detect_with_bom(raw: bytes) -> tuple[str, str] | None:
    for bom, encoding in BOM_ENCODINGS:
        if not raw.startswith(bom):
            continue
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if _control_ratio(text) > CONTROL_CHAR_RATIO_LIMIT:
            continue
        return encoding, text
    return None


def detect_text_encoding(raw: bytes) -> tuple[str, str] | None:
    """只在**可确定**时给出编码与文本；否则返回 None。

    判定顺序：BOM（UTF-16/32）→ 严格 UTF-8。UTF-8 的字节结构自带冗余校验，
    因此"能严格解出且控制字符不多"就足以确定；其它多字节编码做不到这一点，不猜。
    """

    with_bom = _detect_with_bom(raw)
    if with_bom is not None:
        return with_bom

    # 无 BOM 时含 NUL 的一律按二进制拒绝：这类字节无法可靠区分编码。
    if b"\x00" in raw:
        return None

    for encoding in AUTO_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if _control_ratio(text) > CONTROL_CHAR_RATIO_LIMIT:
            continue
        return encoding, text
    return None


def decode_with_declared_encoding(raw: bytes, encoding: str) -> tuple[str, str]:
    """按调用方**显式声明**的编码解码；编码不在名单内或解不出即报错。

    编码名按 Python 的规范名归一（`Shift_JIS`、`shift-jis` 都指向 `shift_jis`），
    但**必须落在 `SUPPORTED_ENCODINGS` 名单内**——名单是"明确解析器"的清单，
    不能让任意别名绕过它。
    """

    canonical = _canonical_declared_encoding(encoding)
    if canonical is None:
        raise ExtractionError(
            "unsupported_encoding_declaration",
            f"不支持的编码声明 {encoding!r}；可用：{', '.join(SUPPORTED_ENCODINGS)}",
        )
    try:
        text = raw.decode(canonical)
    except (UnicodeDecodeError, LookupError) as error:
        raise ExtractionError(
            "encoding_mismatch",
            f"按声明的 {canonical} 解码失败（{type(error).__name__}）；"
            "请确认编码是否正确，不要按猜测内容继续",
        ) from error
    if _control_ratio(text) > CONTROL_CHAR_RATIO_LIMIT:
        raise ExtractionError(
            "encoding_mismatch",
            f"按声明的 {canonical} 解出的文本含过多控制字符；该声明很可能不正确",
        )
    return canonical, text


def extract_text(raw: bytes, *, display: str, encoding: str | None = None) -> Extraction:
    """从字节里提取可送入模型的文本；不支持或不确定时抛 `ExtractionError`。

    `encoding` 给出时按**显式声明**解码（F-03 的"明确解析器"）；未给出时只接受可确定判定的
    UTF-8 家族与带 BOM 的 UTF-16/32。
    """

    if has_pdf_signature(raw):
        return extract_pdf(raw, display=display)
    if encoding is not None:
        declared, text = decode_with_declared_encoding(raw, encoding)
        return _text_extraction(text, encoding=declared, declared=True)
    detected = detect_text_encoding(raw)
    if detected is None:
        raise ExtractionError(
            "unsupported_text_encoding",
            f"{display} 的编码无法确定（自动判定只覆盖 UTF-8 与带 BOM 的 UTF-16/32，"
            "因为其它编码的字节范围互相重叠、猜错会得到乱码）；"
            f"请用 encoding= 显式声明，可选：{', '.join(SUPPORTED_ENCODINGS)}",
        )
    detected_encoding, text = detected
    return _text_extraction(text, encoding=detected_encoding, declared=False)


def _text_extraction(text: str, *, encoding: str, declared: bool) -> Extraction:
    truncated = len(text) > MAX_EXTRACT_CHARS
    if truncated:
        text = text[:MAX_EXTRACT_CHARS]
    warnings: tuple[str, ...] = ()
    if encoding not in {"utf-8", "utf-8-sig"}:
        source = "显式声明" if declared else "BOM"
        warnings = (f"编码不是 UTF-8（按{source}判定为 {encoding}）",)
    return Extraction(
        text=text,
        format="text",
        extractor=TEXT_EXTRACTOR,
        encoding=encoding,
        truncated=truncated,
        warnings=warnings,
    )


def extract_pdf(raw: bytes, *, display: str) -> Extraction:
    """用 `pypdf` 抽取文本型 PDF；扫描/图片型与损坏文件分别拒绝。"""

    if not pdf_parser_available():
        raise ExtractionError(
            "pdf_parser_missing",
            "PDF 解析器未安装（需要 pypdf）；该任务应判为不完整，不要猜测文件内容",
        )
    import io

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionError("pdf_encrypted", f"{display} 是加密 PDF，无法抽取文本")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ExtractionError:
        raise
    except (PdfReadError, OSError, ValueError, KeyError, TypeError) as error:
        # 保留原文件与错误，不输出半成品内容。
        raise ExtractionError(
            "pdf_unreadable", f"{display} 无法解析（{type(error).__name__}）；请人工检查文件"
        ) from error

    text = "\n".join(item.strip() for item in pages)
    if len(text.strip()) < PDF_MIN_TEXT_CHARS:
        raise ExtractionError(
            "pdf_scanned_or_empty",
            f"{display} 抽取不到文本（{len(pages)} 页、{len(text.strip())} 个字符）；"
            "首版不支持扫描/图片型 PDF，需单独配置 OCR 后再处理",
        )
    truncated = len(text) > MAX_EXTRACT_CHARS
    if truncated:
        text = text[:MAX_EXTRACT_CHARS]
    return Extraction(
        text=text,
        format="pdf",
        extractor=PDF_EXTRACTOR,
        pages=len(pages),
        truncated=truncated,
        warnings=("已截断",) if truncated else (),
    )


def read_file_text(path: Path, *, display: str, encoding: str | None = None) -> Extraction:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ExtractionError("file_unreadable", f"{display} 无法读取") from error
    return extract_text(raw, display=display, encoding=encoding)


def normalize_extracted_text(text: str) -> str:
    """折叠多余空行与行尾空白，避免 PDF 抽取结果里的大段空隙浪费上下文。"""

    collapsed = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", collapsed).strip()
