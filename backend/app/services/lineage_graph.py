"""lineage_graph 领域服务：血缘图谱管理，接收資产编目和分层策略的上游事件。

跨模块数据流：
    asset_catalog → lineage_graph: 资产注册自动创建血缘节点
    tier_policy → lineage_graph: 分层变更更新血缘节点信息
    lineage_graph → change_notice: 血缘变更触发通知
    label_tag → lineage_graph: 标签变更影响血缘关系
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "lineage")


class LineageGraphService(BaseModuleService):
    MODULE = "lineage_graph"
    ENTITY = "血缘记录"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["记录编号", "上游资产", "下游资产", "依赖类型", "加工逻辑", "生效时间", "变更SHA", "血缘状态"]
    REQUIRED_FIELDS = ["记录编号", "上游资产", "下游资产"]
    STATUS_ORDER = ["草稿", "已注册", "已生效", "已失效"]
    ACTION_RULES = {"提交注册": "已注册", "确认生效": "已生效", "标记失效": "已失效"}
    NEGATIVE_ACTIONS = []

    def __init__(self):
        super().__init__()
        os.makedirs(DATA_DIR, exist_ok=True)

    def register_lineage(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """注册血缘记录：写入血缘数据产物并触发变更通知。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"

        entry["status"] = "已注册"
        entry["pending"] = False
        entry["生效时间"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        upstream = str(entry.get("上游资产", ""))
        downstream = str(entry.get("下游资产", ""))
        lineage_type = str(entry.get("依赖类型", ""))

        # 写入血缘数据产物
        self._write_lineage_record(upstream, downstream, lineage_type)

        # 触发变更通知：血缘变更
        self._trigger_change_notice(downstream, upstream, lineage_type)

        # 图谱边发生变化：读入标签派生批次后按新血缘重算下游标签
        self.recompute_derived_labels()

        return entry, f"{self.ENTITY} {entry_id} 已注册，血缘线 {upstream}→{downstream}"

    def recompute_derived_labels(self) -> dict[str, Any]:
        """血缘图谱读入标签派生批次后，按已核实边重算下游标签。

        实际计算与来源留痕在 LabelBatchService 中完成，这里只做委托，
        保证修正血缘或注册新边后图谱能按剩余来源重算有效标记。
        """
        from app.services.label_batch import LabelBatchService

        return LabelBatchService().recompute()

    def correct_lineage_edge(self, record_id: str, **kwargs: Any) -> tuple[
        dict[str, Any] | None, str,
    ]:
        """修正一条血缘关系并触发派生标签重算（委托给标签批次引擎）。"""
        from app.services.label_batch import LabelBatchService

        return LabelBatchService().correct_lineage_edge(record_id, **kwargs)

    def build_lineage_graph(self) -> dict[str, Any]:
        """构建完整血缘图谱：读取所有血缘记录，合并为图结构。"""
        rows = store.rows(self.MODULE)
        graph: dict[str, list[str]] = {}
        for row in rows:
            if row.get("status") in ("已注册", "已生效"):
                u = str(row.get("上游资产", ""))
                d = str(row.get("下游资产", ""))
                if d not in graph:
                    graph[d] = []
                graph[d].append(u)

        # 写入图谱文件
        path = os.path.join(DATA_DIR, "lineage_graph.json")
        result = {
            "nodes": list(set(
                list(graph.keys()) +
                [u for deps in graph.values() for u in deps]
            )),
            "edges": [
                {"from": u, "to": d}
                for d, deps in graph.items()
                for u in deps
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        return result

    def get_downstream_assets(self, asset_id: str) -> list[str]:
        """获取某资产的所有下游资产。"""
        graph = self.build_lineage_graph()
        downstream = []
        for edge in graph.get("edges", []):
            if edge["from"] == asset_id:
                downstream.append(edge["to"])
        return downstream

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "提交注册":
            return self.register_lineage(entry_id)
        return super().run_action(entry_id, action)

    # ---------- 文件 I/O ----------
    def _write_lineage_record(self, upstream: str, downstream: str,
                               lineage_type: str) -> None:
        filename = f"lineage_{downstream}.json"
        path = os.path.join(DATA_DIR, filename)
        existing = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)

        existing.append({
            "upstream": upstream,
            "downstream": downstream,
            "type": lineage_type,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        })

        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2, default=str)

    def read_lineage_record(self, asset_id: str) -> list[dict[str, Any]]:
        filename = f"lineage_{asset_id}.json"
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ---------- 跨模块副作用 ----------
    def _trigger_change_notice(self, downstream: str, upstream: str,
                                lineage_type: str) -> None:
        """血缘变更触发通知。"""
        notice_rows = store.rows("change_notice")
        next_id = max((int(r.get("id", 0)) for r in notice_rows), default=0) + 1
        notice = {
            "id": next_id,
            "status": "待发送",
            "pending": True,
            "abnormal": False,
            "通知编号": f"CHAN-{next_id:04d}",
            "关联资产": downstream,
            "变更类型": "血缘变更",
            "变更摘要": f"新增长游依赖:{upstream}→{downstream} ({lineage_type})",
            "影响范围": f"下游{downstream}及所有依赖",
            "通知对象": f"{downstream}_owner",
            "送达状态": "待发送",
            "确认状态": "待确认",
        }
        notice_rows.append(notice)