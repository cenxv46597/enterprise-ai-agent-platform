"""保存展示时的候选顺序；用户编号只绑定这份快照，不重新搜索或猜测。"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.knowledge.document_candidates import MAX_DOCUMENT_CANDIDATES, DocumentCandidate, DocumentCandidateList


class DocumentSelectionTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kind: Literal["document_selection"] = "document_selection"
    question: str = Field(min_length=1, max_length=2000)
    filename_query: str = Field(min_length=1, max_length=255)
    candidates: DocumentCandidateList

    @model_validator(mode="after")
    def bounded_candidates(self) -> Self:
        items = self.candidates.candidates
        if not 1 <= len(items) <= MAX_DOCUMENT_CANDIDATES or len({item.document_id for item in items}) != len(items):
            raise ValueError("选择候选数量或编号无效")
        if len(items) == 1 and not self.candidates.has_more:
            raise ValueError("唯一完整候选无需等待选择")
        return self


def selected_candidate(task: DocumentSelectionTask, content: str) -> DocumentCandidate | None:
    value = content.strip()
    if value not in {str(index) for index in range(1, len(task.candidates.candidates) + 1)}:
        return None
    return task.candidates.candidates[int(value) - 1]


def selection_reply(task: DocumentSelectionTask) -> str:
    lines = [f"“{task.filename_query}”匹配到多份文件，请选择要查询状态的一份："]
    for index, item in enumerate(task.candidates.candidates, 1):
        lines.append(f"{index}. {item.original_filename}；上传时间：{item.created_at.isoformat()}；大小：{item.byte_size} 字节。")
    if task.candidates.has_more:
        lines.append("当前列表未展示全部匹配文件；可以选择其中一份，或用 new_task 提供更具体的文件名线索。")
    lines.append(f"请只回复 1 到 {len(task.candidates.candidates)} 的编号；不会自动选第一份。")
    return "\n".join(lines)
