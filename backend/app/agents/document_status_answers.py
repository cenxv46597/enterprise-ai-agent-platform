"""按查询快照展示处理进度；元数据不充当制度正文或引用。"""

from app.knowledge.answers import AnswerResult
from app.knowledge.document_status import DocumentProcessingStatus


STATUS_MESSAGES = {
    "uploaded": ("已上传", "原件已保存，尚未开始解析。"),
    "processing": ("解析中", "正在解析原件，当前状态不能保证已有可用正文。"),
    "parsed": ("已解析", "文字解析成功，但完整向量尚未保存；是否已切块需另行查询。"),
    "ready": ("可检索", "完整向量已保存，可以参与知识检索；能否回答具体问题仍取决于资料内容。"),
    "failed": ("解析失败", "原件解析失败。"),
}
ERROR_MESSAGES = {
    "EMPTY_TEXT": "文件没有有效文字。",
    "INVALID_UTF8": "文本文件不是有效的 UTF-8 编码。",
    "STORAGE_READ_FAILED": "读取已保存的原件失败。",
    "PDF_PASSWORD_REQUIRED": "PDF 需要密码，当前解析不支持加密文件。",
    "PDF_TOO_MANY_PAGES": "PDF 超过解析页数上限。",
    "PDF_TEXT_TOO_LONG": "PDF 提取文字超过字符上限。",
    "PDF_INVALID": "PDF 格式无效或文件已损坏。",
    "PDF_NO_TEXT": "PDF 中没有可提取的文字，当前不支持扫描件 OCR。",
}


def answer_from_document_status(result: DocumentProcessingStatus) -> AnswerResult:
    label, explanation = STATUS_MESSAGES[result.status]
    answer = f"查询时，文件“{result.original_filename}”的处理状态为：{label}（{result.status}）。{explanation}"
    if result.status == "failed":
        # 只展示已知安全错误的说明，不把任意数据库字段当成用户可见异常。
        answer += ERROR_MESSAGES.get(result.error_code, "未取得可识别的失败原因。")
    return AnswerResult(answer, False, [])
