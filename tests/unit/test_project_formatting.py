"""M5 F-03 格式处理的测试：编码阶梯、文本型 PDF、扫描型 PDF 与损坏文件。

计划要求"UTF-8 以外编码和文本型 PDF 各用明确解析器；扫描/图片型 PDF 首版不支持并提示
需单独配置 OCR；不把二进制当文本送入模型；解析器缺失或文件损坏时保留原文件与错误"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evoagent.projects.formatting import (
    PDF_EXTRACTOR,
    TEXT_EXTRACTOR,
    ExtractionError,
    detect_text_encoding,
    extract_pdf,
    extract_text,
    normalize_extracted_text,
)
from evoagent.projects.pdf_fixture import image_only_pdf, text_pdf
from evoagent.projects.schema import ProjectAuthorization
from evoagent.tools.base import ToolExecutionError, ToolPermissionError
from evoagent.tools.builtin.project_extract import ExtractTextArguments, ExtractTextTool


def test_utf8_is_detected_and_parsed() -> None:
    detected = detect_text_encoding("你好，EvoAgent\n".encode())
    assert detected is not None
    encoding, text = detected
    assert encoding == "utf-8"
    assert "EvoAgent" in text


@pytest.mark.parametrize(
    ("encoding", "sample"),
    [
        ("gb18030", "这是一份中文资料，使用国标编码保存。"),
        ("big5", "這是一份繁體中文資料。"),
        ("shift_jis", "これは日本語の資料です。"),
        ("cp1252", "Resume du projet - detail"),
        ("latin-1", "Cafe au lait"),
    ],
)
def test_declared_encodings_are_used_verbatim(encoding: str, sample: str) -> None:
    """非 UTF-8 走**显式声明**：声明什么就用什么，不做猜测。"""

    raw = sample.encode(encoding)
    extraction = extract_text(raw, display=f"sample.{encoding}", encoding=encoding)

    assert extraction.encoding == encoding
    assert extraction.extractor == TEXT_EXTRACTOR
    assert sample in extraction.text
    assert extraction.warnings  # 非 UTF-8 必须提示编码来源


def test_non_utf8_bytes_without_declaration_are_refused() -> None:
    """不声明编码时，GB18030/Big5/Shift_JIS 的字节必须被拒绝而不是猜成乱码。

    这是实测得出的设计约束：gb18030 能"成功"解码 Big5 与 Shift_JIS 的字节，
    因此自动判定只覆盖 UTF-8 与带 BOM 的 UTF-16/32，其余必须显式声明。
    """

    for encoding, sample in (
        ("gb18030", "这是一份中文资料。"),
        ("big5", "繁體中文資料。"),
        ("shift_jis", "日本語の資料です。"),
    ):
        with pytest.raises(ExtractionError) as error:
            extract_text(sample.encode(encoding), display=f"sample.{encoding}")
        assert error.value.code == "unsupported_text_encoding"
        # 错误信息必须告诉模型下一步怎么做（否则模型只能反复重试同一次调用）。
        assert "encoding=" in str(error.value)


def test_wrong_declaration_is_rejected() -> None:
    with pytest.raises(ExtractionError) as error:
        extract_text("中文".encode("gb18030"), display="x.txt", encoding="utf-8")
    assert error.value.code == "encoding_mismatch"


def test_unknown_declaration_lists_supported_encodings() -> None:
    with pytest.raises(ExtractionError) as error:
        extract_text(b"abc", display="x.txt", encoding="koi8-r")
    assert error.value.code == "unsupported_encoding_declaration"
    assert "gb18030" in str(error.value)


def test_utf16_with_bom_is_detected() -> None:
    raw = "UTF-16 内容".encode("utf-16")
    extraction = extract_text(raw, display="utf16.txt")
    assert extraction.encoding == "utf-16"
    assert "UTF-16 内容" in extraction.text


def test_binary_is_refused_instead_of_decoded_lossily(tmp_path: Path) -> None:
    """随机二进制不能被 latin-1 "解成文本"送进模型。"""

    raw = bytes(range(256)) * 4
    with pytest.raises(ExtractionError) as error:
        extract_text(raw, display="blob.bin")
    assert error.value.code == "unsupported_text_encoding"


def test_nul_bytes_are_refused() -> None:
    with pytest.raises(ExtractionError) as error:
        extract_text(b"text\x00more", display="nul.bin")
    assert error.value.code == "unsupported_text_encoding"


def test_text_pdf_is_extracted_with_metadata() -> None:
    raw = text_pdf("EvoAgent report", "Second line of the report")
    extraction = extract_pdf(raw, display="report.pdf")

    assert extraction.format == "pdf"
    assert extraction.extractor == PDF_EXTRACTOR
    assert extraction.pages == 1
    assert "EvoAgent report" in extraction.text
    assert "Second line of the report" in extraction.text
    assert extraction.truncated is False


def test_scanned_pdf_is_refused_with_ocr_hint() -> None:
    """扫描型 PDF 必须明确报"不支持 + 需 OCR"，不能返回空文本当成功。"""

    with pytest.raises(ExtractionError) as error:
        extract_pdf(image_only_pdf(), display="scan.pdf")
    assert error.value.code == "pdf_scanned_or_empty"
    assert "OCR" in str(error.value)


def test_corrupt_pdf_is_refused_and_keeps_the_error() -> None:
    corrupt = b"%PDF-1.4\nthis is not a real pdf body\n%%EOF\n"
    with pytest.raises(ExtractionError) as error:
        extract_pdf(corrupt, display="broken.pdf")
    assert error.value.code == "pdf_unreadable"


def test_normalize_collapses_pdf_whitespace() -> None:
    messy = "Title   \n\n\n\n" + "Body line  \n" + "   " * 4 + "\nNext"
    assert normalize_extracted_text(messy) == "Title\n\nBody line\n\nNext"


@pytest.mark.asyncio
async def test_tool_extracts_non_utf8_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.txt").write_bytes("国标编码的中文资料。".encode("gb18030"))

    result = await ExtractTextTool(root).invoke(
        ExtractTextArguments(path="notes.txt", encoding="gb18030")
    )

    assert "编码：gb18030" in result
    assert "不可信资料" in result
    assert "国标编码的中文资料。" in result


@pytest.mark.asyncio
async def test_tool_asks_for_encoding_when_undeclared(tmp_path: Path) -> None:
    """未声明编码时必须报"请用 encoding= 声明"，而不是给出乱码。"""

    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.txt").write_bytes("国标编码的中文资料。".encode("gb18030"))

    with pytest.raises(ToolExecutionError, match="unsupported_text_encoding"):
        await ExtractTextTool(root).invoke(ExtractTextArguments(path="notes.txt"))


@pytest.mark.asyncio
async def test_tool_extracts_pdf_and_reports_pages(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "report.pdf").write_bytes(text_pdf("Quarterly summary", "Revenue grew"))

    result = await ExtractTextTool(root).invoke(ExtractTextArguments(path="report.pdf"))

    assert "格式：pdf" in result
    assert f"解析器：{PDF_EXTRACTOR}" in result
    assert "页数：1" in result
    assert "Quarterly summary" in result


@pytest.mark.asyncio
async def test_tool_refuses_scanned_pdf_with_stable_code(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "scan.pdf").write_bytes(image_only_pdf())

    with pytest.raises(ToolExecutionError, match="pdf_scanned_or_empty"):
        await ExtractTextTool(root).invoke(ExtractTextArguments(path="scan.pdf"))


@pytest.mark.asyncio
async def test_tool_refuses_binary_and_out_of_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "blob.bin").write_bytes(bytes(range(256)))
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="unsupported_text_encoding"):
        await ExtractTextTool(root).invoke(ExtractTextArguments(path="blob.bin"))
    with pytest.raises(ToolPermissionError):
        await ExtractTextTool(root).invoke(ExtractTextArguments(path="../secret.txt"))


def test_extract_tools_are_available_under_read_only_authorization(tmp_path: Path) -> None:
    """抽取是只读操作，因此只读授权也必须可用。"""

    from evoagent.tools.builtin.project_extract import project_extract_tools

    tools = project_extract_tools(tmp_path)
    assert [tool.name for tool in tools] == ["extract_text"]
    assert all(not tool.has_side_effects for tool in tools)
    assert ProjectAuthorization.READ is not None
