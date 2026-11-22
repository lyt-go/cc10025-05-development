"""标签派生批次专用接口：批量登记、冲突、撤销与血缘修正重算。

路径统一挂在 ``/api/label-batches`` 下，与通用资源路由
``/api/{module}`` 互不冲突；路由层不做业务判断，全部委托
``LabelBatchService``。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas import (
    ConflictResolvePayload,
    LabelBatchPayload,
    LineageCorrectionPayload,
)
from app.services.label_batch import LabelBatchService

router = APIRouter(prefix="/api/label-batches", tags=["标签派生批次"])


def _service() -> LabelBatchService:
    return LabelBatchService()


@router.post("")
def register_batch(payload: LabelBatchPayload) -> dict[str, Any]:
    """批量登记直接标签，并沿已核实血缘边计算派生标签批次。"""
    service = _service()
    direct_labels = [item.model_dump() for item in payload.direct_labels]
    batch, message = service.register_batch(
        direct_labels,
        remark=payload.remark or "",
        batch_no=payload.batch_no,
    )
    if batch is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "batch": batch}


@router.get("")
def list_batches() -> dict[str, Any]:
    """查看全部标签派生批次（含已撤销）。"""
    service = _service()
    items = service.list_batches()
    return {"items": items, "total": len(items)}


@router.get("/downstream-labels")
def list_downstream_labels(asset: str | None = None) -> dict[str, Any]:
    """回查图谱写入的下游标签及逐条来源、已纳入计算的边。"""
    service = _service()
    items = service.list_downstream_labels(asset)
    return {"items": items, "total": len(items)}


@router.get("/conflicts")
def list_conflicts(only_open: bool = True) -> dict[str, Any]:
    """查看互斥等级冲突清单。"""
    service = _service()
    items = service.list_conflicts(only_open=only_open)
    return {"items": items, "total": len(items)}


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: int, payload: ConflictResolvePayload) -> dict[str, Any]:
    """确认互斥等级归属，确认后该值才允许成为计算结果。"""
    service = _service()
    conflict, message = service.resolve_conflict(conflict_id, payload.chosen_value)
    if conflict is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "conflict": conflict}


@router.post("/{batch_no}/revoke")
def revoke_batch(batch_no: str) -> dict[str, Any]:
    """撤销整次提交，只消除由该批支持的标记并按剩余来源重算。"""
    service = _service()
    batch, message = service.revoke_batch(batch_no)
    if batch is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "batch": batch}


@router.post("/lineage/{record_id}/correction")
def correct_lineage_edge(record_id: str, payload: LineageCorrectionPayload) -> dict[str, Any]:
    """修正一段血缘关系（或令其失效），随后按剩余来源重算有效标记。"""
    service = _service()
    edge, message = service.correct_lineage_edge(
        record_id,
        new_upstream=payload.new_upstream,
        new_downstream=payload.new_downstream,
        invalidate=payload.invalidate,
    )
    if edge is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "edge": edge}
