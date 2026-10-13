"""从已保存的受限格式原文件提取可切块的确定性文本。"""

from pypdf import PdfReader

from app.knowledge.storage import LocalDocumentStore


MAX_PDF_PAGES = 100
MAX_PDF_TEXT_CHARACTERS = 1_000_000


class PdfTextError(ValueError):
    """对外仅暴露固定、安全的 PDF 解析失败代码。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def read_document_text(
    store: LocalDocumentStore, storage_key: str, media_type: str
) -> str:
    """上传校验与首次切块共用格式分派；偏移对应本函数返回的文本。"""

    if media_type in {"text/plain", "text/markdown"}:
        return store.read_text_utf8(storage_key)
    if media_type != "application/pdf":
        raise ValueError("不支持的文档媒体类型")

    # 保持受限存储路径检查，原 PDF 不转换或另存为派生文件。
    with store.open_binary(storage_key) as source:
        try:
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted:
                raise PdfTextError("PDF_PASSWORD_REQUIRED")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise PdfTextError("PDF_TOO_MANY_PAGES")

            pages: list[str] = []
            character_count = 0
            for page in reader.pages:
                page_text = page.extract_text() or ""
                character_count += len(page_text) + (1 if pages else 0)
                if character_count > MAX_PDF_TEXT_CHARACTERS:
                    raise PdfTextError("PDF_TEXT_TOO_LONG")
                pages.append(page_text)
        except PdfTextError:
            raise
        except Exception as exc:
            # 第三方解析异常可能包含文件内容；只向外传固定错误代码。
            raise PdfTextError("PDF_INVALID") from exc

    text = "\n".join(pages)
    if "\x00" in text:
        raise PdfTextError("PDF_INVALID")
    if not text.strip():
        raise PdfTextError("PDF_NO_TEXT")
    return text
