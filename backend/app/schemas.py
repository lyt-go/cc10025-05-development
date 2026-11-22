"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class DirectLabelItem(BaseModel):
    """批量登记中的一条直接标签。"""

    asset: str
    dimension: str
    value: str
    source: str | None = None


class LabelBatchPayload(BaseModel):
    direct_labels: list[DirectLabelItem] = Field(default_factory=list)
    remark: str | None = None
    batch_no: str | None = None


class ConflictResolvePayload(BaseModel):
    chosen_value: str


class LineageCorrectionPayload(BaseModel):
    new_upstream: str | None = None
    new_downstream: str | None = None
    invalidate: bool = False
