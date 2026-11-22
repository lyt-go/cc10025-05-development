"""label_batch 领域服务：标签批量登记与沿血缘的标签派生批次。

背景
----
单条标签登记只能处理一张表的直接标签，缺少把同一批次的直接标签和派生标签
一起处理的能力。本服务引入「标签派生批次」：

* 一次批量登记包含多张上下游表的直接标签（同一批次提交）；
* 图谱读入批次后，沿「已核实」的血缘边向下游传播，生成派生标签；
* 同一标签经不同路径到达同一张表会去重，但每条来源数据表和已纳入计算的边
  都会逐条记录，供后续重算判断是否仍有支持；
* 互斥等级（同一标签维度出现多个互斥值）在确认前不得成为计算结果，
  只登记为待确认冲突；
* 撤销批次只会消除由该批支持的标记，再按剩余来源重算有效标记；
* 修正血缘边后同样按剩余来源重算。

状态全部保存在内存（``store``）中，另把批次清单、下游标签、冲突清单落盘到
``app/data/labels`` 作为数据产物，便于审计与排查。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.store import store

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "labels")

# 已核实、可参与标签传播的血缘状态
VERIFIED_LINEAGE_STATUSES = ("已注册", "已生效")
BATCH_MODULE = "label_batch"
DOWNSTREAM_MODULE = "derived_label"

VALID_BATCH_STATUS = ("已提交", "已撤销")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LabelBatchService:
    """标签派生批次引擎：批量登记、传播计算、冲突与撤销重算。"""

    MODULE = BATCH_MODULE
    ENTITY = "标签派生批次"

    def __init__(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # 基础查询
    # ------------------------------------------------------------------
    def list_batches(self) -> list[dict[str, Any]]:
        """批次按编号倒序返回（最新提交在前）。"""
        rows = store.rows(BATCH_MODULE)
        return sorted(rows, key=lambda row: int(row["id"]), reverse=True)

    def get_batch(self, batch_no: str) -> dict[str, Any] | None:
        for row in store.rows(BATCH_MODULE):
            if row.get("批次编号") == batch_no:
                return row
        return None

    def list_downstream_labels(self, asset_id: str | None = None) -> list[dict[str, Any]]:
        """返回图谱写入的下游标签，可按数据表过滤。"""
        rows = store.rows(DOWNSTREAM_MODULE)
        if asset_id:
            rows = [row for row in rows if row.get("目标资产") == asset_id]
        return sorted(rows, key=lambda row: (str(row.get("目标资产")), str(row.get("标签维度"))))

    def list_conflicts(self, only_open: bool = True) -> list[dict[str, Any]]:
        """返回互斥等级冲突清单；默认只看仍待确认的冲突。"""
        rows = store.rows("label_conflict")
        if only_open:
            rows = [row for row in rows if row.get("冲突状态") == "待确认"]
        return rows

    # ------------------------------------------------------------------
    # 批量登记与传播
    # ------------------------------------------------------------------
    def register_batch(
        self,
        direct_labels: list[dict[str, Any]],
        *,
        remark: str = "",
        batch_no: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """登记一个标签派生批次，并立即沿已核实血缘边重算下游标签。

        ``direct_labels`` 每项形如::

            {"asset": "ODS_ORDER_DETAIL", "dimension": "数据等级",
             "value": "L2", "source": "订单域登记"}

        同一张表同一维度若登记了多个互斥值，会在批次内直接记为冲突。
        """
        cleaned, errors = self._clean_direct_labels(direct_labels)
        if errors:
            return None, "批量登记失败：" + "；".join(errors)

        rows = store.rows(BATCH_MODULE)
        next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        code = batch_no or f"LBAT-{next_id:04d}"
        if self.get_batch(code) is not None:
            return None, f"批次编号 {code} 已存在"

        # 批次内同一表同一维度出现多个互斥值 → 直接构成待确认冲突
        internal_conflicts = self._detect_direct_conflicts(cleaned)

        batch = {
            "id": next_id,
            "批次编号": code,
            "status": "已提交",
            "pending": len(internal_conflicts) > 0,
            "abnormal": False,
            "提交时间": _now(),
            "撤销时间": "",
            "备注": remark,
            "直接标签": cleaned,
            "内部冲突": internal_conflicts,
        }
        rows.append(batch)

        recompute = self.recompute()
        self._persist()
        return batch, (
            f"批次 {code} 已提交：直接标签 {len(cleaned)} 条，"
            f"有效派生标记 {recompute['effective_marks']} 个，"
            f"待确认冲突 {len(recompute['conflicts'])} 个"
        )

    def revoke_batch(self, batch_no: str) -> tuple[dict[str, Any] | None, str]:
        """撤销整次提交：只消除由该批支持的标记，再按剩余来源重算。"""
        batch = self.get_batch(batch_no)
        if batch is None:
            return None, f"批次 {batch_no} 不存在"
        if batch["status"] == "已撤销":
            return None, f"批次 {batch_no} 已撤销，不能重复撤销"

        batch["status"] = "已撤销"
        batch["pending"] = False
        batch["撤销时间"] = _now()

        recompute = self.recompute()
        self._persist()
        return batch, (
            f"批次 {batch_no} 已撤销：移除该批全部支持后，"
            f"剩余有效派生标记 {recompute['effective_marks']} 个，"
            f"待确认冲突 {len(recompute['conflicts'])} 个"
        )

    def resolve_conflict(
        self,
        conflict_id: int,
        chosen_value: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """确认互斥等级冲突的归属值，确认后该值才允许成为计算结果。"""
        conflict = store.find("label_conflict", conflict_id)
        if conflict is None:
            return None, f"冲突 {conflict_id} 不存在"
        if chosen_value not in conflict.get("候选值", []):
            return None, f"候选值中不存在 {chosen_value}，无法确认"

        conflict["冲突状态"] = "已确认"
        conflict["确认值"] = chosen_value
        conflict["确认时间"] = _now()

        # 同源互斥会沿血缘向下游传播；确认上游归属后，下游同维度冲突
        # 采用同一确认值级联解决，确认值才允许继续向下传播。
        for other in store.rows("label_conflict"):
            if (other is conflict
                    or other.get("冲突状态") != "待确认"
                    or other.get("标签维度") != conflict["标签维度"]
                    or chosen_value not in other.get("候选值", [])):
                continue
            other["冲突状态"] = "已确认"
            other["确认值"] = chosen_value
            other["确认时间"] = _now()

        recompute = self.recompute()
        self._persist()
        return conflict, (
            f"冲突 {conflict['冲突编号']} 已确认采用 {chosen_value}，"
            f"重算后有效派生标记 {recompute['effective_marks']} 个"
        )

    # ------------------------------------------------------------------
    # 血缘边修正
    # ------------------------------------------------------------------
    def correct_lineage_edge(
        self,
        record_id: str,
        *,
        new_upstream: str | None = None,
        new_downstream: str | None = None,
        invalidate: bool = False,
    ) -> tuple[dict[str, Any] | None, str]:
        """修正一条血缘关系，并按剩余来源重算有效标记。

        ``invalidate=True`` 表示该边核实不通过、退出图谱；否则用新的上下游
        覆盖原边。无论哪种修正，都会触发全量重算。
        """
        lineage_rows = store.rows("lineage_graph")
        target = next(
            (row for row in lineage_rows if str(row.get("记录编号")) == record_id),
            None,
        )
        if target is None:
            return None, f"血缘记录 {record_id} 不存在"

        old_up = str(target.get("上游资产", ""))
        old_down = str(target.get("下游资产", ""))
        if invalidate:
            target["血缘状态"] = "已失效"
            target["status"] = "已失效"
            target["pending"] = False
            target["abnormal"] = True
            change = f"边 {old_up}→{old_down} 核实不通过，已退出图谱"
        else:
            if new_upstream:
                target["上游资产"] = new_upstream
            if new_downstream:
                target["下游资产"] = new_downstream
            target["status"] = "已生效"
            target["血缘状态"] = "已核实"
            target["pending"] = False
            change = (
                f"边修正为 {target.get('上游资产')}→{target.get('下游资产')}"
            )

        recompute = self.recompute()
        self._persist()
        return target, (
            f"{change}；按剩余来源重算后有效派生标记 "
            f"{recompute['effective_marks']} 个，待确认冲突 "
            f"{len(recompute['conflicts'])} 个"
        )

    # ------------------------------------------------------------------
    # 核心：沿已核实血缘边重算
    # ------------------------------------------------------------------
    def _verified_graph(self) -> tuple[
        dict[str, list[dict[str, str]]],
        list[dict[str, str]],
    ]:
        """从血缘模块读取已核实边，构造正向邻接表（保留并行边）。

        返回 ``(出边表, 边清单)``：出边表 ``上游 → [边]`` 供标签向下游传播。
        同一对上下游之间可能存在两条独立核实的血缘边（不同加工链路），
        因此邻接表按边保留，不能按表去重。
        """
        outgoing: dict[str, list[dict[str, str]]] = {}
        edges: list[dict[str, str]] = []
        for row in store.rows("lineage_graph"):
            if row.get("status") not in VERIFIED_LINEAGE_STATUSES:
                continue
            upstream = str(row.get("上游资产", "")).strip()
            downstream = str(row.get("下游资产", "")).strip()
            if not upstream or not downstream or upstream == downstream:
                continue
            edge = {
                "记录编号": str(row.get("记录编号", "")),
                "from": upstream,
                "to": downstream,
            }
            edges.append(edge)
            outgoing.setdefault(upstream, []).append(edge)
        return outgoing, edges

    def recompute(self) -> dict[str, Any]:
        """按所有未撤销批次重新计算下游标签与冲突。

        计算口径：
        1. 只采纳状态为「已提交」批次的直接标签作为来源；
        2. 对每张下游表，沿已核实血缘边递归收集全部上游来源，
           同一标签经不同路径到达只保留一个派生标记，但来源与边逐条留痕；
        3. 同一标签维度出现多个互斥值 → 待确认冲突，确认前不产生有效标记；
        4. 已确认冲突只采纳确认值；
        5. 批次内直接登记的互斥值同样按冲突处理。
        """
        outgoing, edges = self._verified_graph()
        confirmed = self._confirmed_choices()

        active_batches = [
            row for row in store.rows(BATCH_MODULE) if row.get("status") == "已提交"
        ]

        # 第一遍：不带闸门聚合，找出全部互斥冲突节点
        raw_aggregate: dict[tuple[str, str], dict[str, Any]] = {}
        for batch in active_batches:
            batch_no = str(batch["批次编号"])
            for direct in batch["直接标签"]:
                self._collect(
                    raw_aggregate, outgoing,
                    batch_no, str(direct["asset"]),
                    str(direct["dimension"]), str(direct["value"]),
                    direct=direct,
                )

        # 冲突闸门：值为 None 表示未确认，字符串表示已确认采用的值。
        # 闸门只影响「穿过节点继续向下」的传播，不抹掉节点自身候选。
        gates: dict[tuple[str, str], str | None] = {
            (target, dimension): confirmed.get((target, dimension))
            for (target, dimension), bucket in raw_aggregate.items()
            if len(bucket["values"]) > 1
        }

        # 第二遍：带闸门重新聚合，收集被未确认闸门阻断的继承冲突
        aggregate: dict[tuple[str, str], dict[str, Any]] = {}
        blocked_all: list[dict[str, Any]] = []
        for batch in active_batches:
            batch_no = str(batch["批次编号"])
            for direct in batch["直接标签"]:
                blocked = self._collect(
                    aggregate, outgoing,
                    batch_no, str(direct["asset"]),
                    str(direct["dimension"]), str(direct["value"]),
                    direct=direct, gates=gates,
                )
                blocked_all.extend(blocked)

        # 继承冲突：互斥等级未确认时，沿链路把下游表也登记为待确认，
        # 但不写入任何有效标签值；确认后由级联逻辑放行。
        for item in blocked_all:
            key = (item["target"], item["dimension"])
            bucket = aggregate.setdefault(key, {
                "values": set(),
                "supports": {},
                "direct_sources": set(),
                "inherited": True,
            })
            bucket["values"].add(item["value"])
            bucket["inherited"] = True
            self._add_support(
                aggregate, item["batch_no"],
                item["target"], item["dimension"], item["value"],
                support_asset=item["support_asset"],
                path_edges=item["path_edges"], is_direct=False,
                direct=item["direct"],
            )

        conflicts = self._rebuild_conflicts(aggregate)
        self._rebuild_downstream_labels(aggregate, conflicts, confirmed)
        effective = sum(1 for row in store.rows(DOWNSTREAM_MODULE)
                        if row.get("标记状态") == "有效")

        self._persist()
        return {
            "effective_marks": effective,
            "conflicts": conflicts,
            "verified_edges": len(edges),
        }

    def _collect(
        self,
        aggregate: dict[tuple[str, str], dict[str, Any]],
        outgoing: dict[str, list[dict[str, str]]],
        batch_no: str,
        source_asset: str,
        dimension: str,
        value: str,
        *,
        direct: dict[str, Any],
        gates: dict[tuple[str, str], str | None] | None = None,
    ) -> list[dict[str, Any]]:
        """从一个直接标签来源出发，沿血缘出边把派生支持写到每个下游表。

        采用从来源向下的广度遍历：每条可达链路都记录其经过的边，
        同一 (批次, 来源, 目标, 维度) 只保留一条支持（路径去重），
        但边清单合并，保证不同路径（含并行边）都能留痕。

        冲突闸门 ``gates``：(表, 维度) → 确认值（``None`` 表示未确认）。
        闸门只作用于「穿过节点继续向下」：节点自身的互斥候选照常聚合，
        以便登记冲突；未确认闸门不允许任何值穿过，已确认闸门只放行确认值，
        从而保证互斥等级在确认前不会成为更下游的计算结果。
        """
        gates = gates or {}
        # 记录被未确认闸门阻断的传播：下游表继承冲突，但确认前不产生有效值
        blocked: list[dict[str, Any]] = []
        # 1) 直接标签：来源表自身的直接标记（直接登记不被闸门拦截）
        self._add_support(
            aggregate, batch_no, source_asset, dimension, value,
            support_asset=source_asset, path_edges=[], is_direct=True,
            direct=direct,
        )

        def _flood_inherited(
            start: str,
            start_path: list[dict[str, str]],
            seen: set[str],
        ) -> None:
            """从未确认闸门继续向下，把所有可达下游登记为继承冲突（无有效值）。"""
            for edge in outgoing.get(start, []):
                downstream = edge["to"]
                if downstream in seen:
                    continue
                seen.add(downstream)
                blocked.append({
                    "batch_no": batch_no,
                    "target": downstream,
                    "dimension": dimension,
                    "value": carry_value,
                    "support_asset": source_asset,
                    "path_edges": [*start_path, edge],
                    "direct": direct,
                })
                _flood_inherited(downstream, [*start_path, edge], seen)

        # 2) 逐层向下游传播
        # 队列元素：(当前表, 到当前表已走过的边, 当前传播值, 本路径已访问节点)
        queue: list[tuple[str, list[dict[str, str]], str, set[str]]] = [
            (source_asset, [], value, {source_asset}),
        ]
        while queue:
            current, path_so_far, carry_value, path_nodes = queue.pop(0)
            for edge in outgoing.get(current, []):
                downstream = edge["to"]
                if downstream in path_nodes:
                    # 仅阻断当前路径上的环，其他路径仍可到达该节点
                    continue
                new_path = [*path_so_far, edge]
                chosen = gates.get((downstream, dimension), "NO_GATE")

                if chosen == "NO_GATE":
                    # 普通节点：正常聚合派生支持并继续传播
                    self._add_support(
                        aggregate, batch_no, downstream, dimension, carry_value,
                        support_asset=source_asset, path_edges=new_path,
                        is_direct=False, direct=direct,
                    )
                    queue.append((
                        downstream, new_path, carry_value,
                        path_nodes | {downstream},
                    ))
                elif chosen is None:
                    # 未确认冲突闸门：节点自身候选照常聚合（登记冲突用），
                    # 其全部下游登记为继承冲突，值不成为任何计算结果。
                    self._add_support(
                        aggregate, batch_no, downstream, dimension, carry_value,
                        support_asset=source_asset, path_edges=new_path,
                        is_direct=False, direct=direct,
                    )
                    _flood_inherited(
                        downstream, new_path, set(path_nodes | {downstream}),
                    )
                elif carry_value == chosen:
                    # 已确认闸门且当前值即确认值：放行并继续传播
                    self._add_support(
                        aggregate, batch_no, downstream, dimension, carry_value,
                        support_asset=source_asset, path_edges=new_path,
                        is_direct=False, direct=direct,
                    )
                    queue.append((
                        downstream, new_path, carry_value,
                        path_nodes | {downstream},
                    ))
                # 已确认闸门的非确认值：直接丢弃
        return blocked

    def _add_support(
        self,
        aggregate: dict[tuple[str, str], dict[str, Any]],
        batch_no: str,
        target_asset: str,
        dimension: str,
        value: str,
        *,
        support_asset: str,
        path_edges: list[dict[str, str]],
        is_direct: bool,
        direct: dict[str, Any],
    ) -> None:
        key = (target_asset, dimension)
        bucket = aggregate.setdefault(key, {
            "values": set(),
            "supports": {},          # (批次, 来源表) → 支持记录
            "direct_sources": set(),
        })
        bucket["values"].add(value)
        if is_direct:
            bucket["direct_sources"].add(batch_no)

        support_key = (batch_no, support_asset)
        support = bucket["supports"].get(support_key)
        if support is None:
            support = {
                "批次编号": batch_no,
                "来源数据表": support_asset,
                "标签值": value,
                "标签来源": str(direct.get("source", "")),
                "是否直接登记": is_direct,
                "路径边": [],
                "登记时间": _now(),
            }
            bucket["supports"][support_key] = support
        # 同标签不同路径到达：去重支持，合并已纳入计算的边
        existing_edges = {
            (edge["记录编号"], edge["from"], edge["to"])
            for edge in support["路径边"]
        }
        for edge in path_edges:
            edge_key = (edge["记录编号"], edge["from"], edge["to"])
            if edge_key not in existing_edges:
                support["路径边"].append(edge)
                existing_edges.add(edge_key)

    # ------------------------------------------------------------------
    # 冲突与下游标记重建
    # ------------------------------------------------------------------
    def _confirmed_choices(self) -> dict[tuple[str, str], str]:
        """已确认冲突：(目标资产, 维度) → 确认值。"""
        choices: dict[tuple[str, str], str] = {}
        for row in store.rows("label_conflict"):
            if row.get("冲突状态") == "已确认":
                choices[(str(row["目标资产"]), str(row["标签维度"]))] = str(
                    row.get("确认值", "")
                )
        return choices

    def _rebuild_conflicts(
        self, aggregate: dict[tuple[str, str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """根据聚合结果刷新冲突清单，保留历史冲突的确认状态。"""
        existing = {
            (str(row["目标资产"]), str(row["标签维度"])): row
            for row in store.rows("label_conflict")
        }
        store.rows("label_conflict").clear()
        conflicts: list[dict[str, Any]] = []
        next_id = 1
        for (target, dimension), bucket in sorted(aggregate.items()):
            values = sorted(bucket["values"])
            if len(values) <= 1:
                continue
            old = existing.get((target, dimension))
            conflict = {
                "id": next_id,
                "冲突编号": f"LCONF-{next_id:04d}",
                "目标资产": target,
                "标签维度": dimension,
                "候选值": values,
                "支持批次": sorted({
                    support["批次编号"] for support in bucket["supports"].values()
                }),
                "冲突状态": (old or {}).get("冲突状态", "待确认"),
                "确认值": (old or {}).get("确认值", ""),
                "确认时间": (old or {}).get("确认时间", ""),
                "登记时间": _now(),
            }
            # 历史确认值若已不在候选集合（来源被撤销/边修正），退回待确认
            if conflict["冲突状态"] == "已确认" and conflict["确认值"] not in values:
                conflict["冲突状态"] = "待确认"
                conflict["确认值"] = ""
                conflict["确认时间"] = ""
            store.rows("label_conflict").append(conflict)
            conflicts.append(conflict)
            next_id += 1
        return conflicts

    def _rebuild_downstream_labels(
        self,
        aggregate: dict[tuple[str, str], dict[str, Any]],
        conflicts: list[dict[str, Any]],
        confirmed: dict[tuple[str, str], str],
    ) -> None:
        """把聚合结果写成下游标签表，冲突维度在确认前不产出有效标记。"""
        conflict_keys = {
            (str(row["目标资产"]), str(row["标签维度"])) for row in conflicts
        }
        store.rows(DOWNSTREAM_MODULE).clear()
        next_id = 1
        for (target, dimension), bucket in sorted(aggregate.items()):
            supports = sorted(
                bucket["supports"].values(),
                key=lambda item: (item["批次编号"], item["来源数据表"]),
            )
            in_conflict = (target, dimension) in conflict_keys
            chosen = confirmed.get((target, dimension), "")

            if in_conflict and not chosen:
                # 互斥等级确认前不得成为计算结果
                effective_value = ""
                status = "待确认冲突"
                abnormal = True
            else:
                effective_value = chosen or next(iter(bucket["values"]))
                status = "有效"
                abnormal = False

            is_direct = bool(bucket["direct_sources"])
            label = {
                "id": next_id,
                "标记编号": f"DLAB-{next_id:04d}",
                "目标资产": target,
                "标签维度": dimension,
                "标签值": effective_value,
                "候选值": sorted(bucket["values"]),
                "标记状态": status,
                "标记类型": "直接标签" if is_direct else "派生标签",
                "abnormal": abnormal,
                "支持批次数": len({s["批次编号"] for s in supports}),
                "来源记录": [
                    {
                        "批次编号": s["批次编号"],
                        "来源数据表": s["来源数据表"],
                        "标签值": s["标签值"],
                        "是否直接登记": s["是否直接登记"],
                        "已纳入计算的边": s["路径边"],
                        "登记时间": s["登记时间"],
                    }
                    for s in supports
                ],
                "更新时间": _now(),
            }
            store.rows(DOWNSTREAM_MODULE).append(label)
            next_id += 1

    # ------------------------------------------------------------------
    # 入参清洗与批次内冲突
    # ------------------------------------------------------------------
    def _clean_direct_labels(
        self, raw: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        cleaned: list[dict[str, Any]] = []
        errors: list[str] = []
        for index, item in enumerate(raw, start=1):
            asset = str(item.get("asset", "")).strip()
            dimension = str(item.get("dimension", "")).strip()
            value = str(item.get("value", "")).strip()
            if not asset or not dimension or not value:
                errors.append(f"第 {index} 条缺少 asset/dimension/value")
                continue
            cleaned.append({
                "asset": asset,
                "dimension": dimension,
                "value": value,
                "source": str(item.get("source", "")).strip() or "批量登记",
            })
        return cleaned, errors

    def _detect_direct_conflicts(
        self, direct_labels: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], set[str]] = {}
        for item in direct_labels:
            grouped.setdefault((item["asset"], item["dimension"]), set()).add(
                item["value"]
            )
        return [
            {
                "目标资产": asset,
                "标签维度": dimension,
                "候选值": sorted(values),
            }
            for (asset, dimension), values in sorted(grouped.items())
            if len(values) > 1
        ]

    # ------------------------------------------------------------------
    # 数据产物落盘
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        batches_path = os.path.join(DATA_DIR, "label_batches.json")
        labels_path = os.path.join(DATA_DIR, "downstream_labels.json")
        conflicts_path = os.path.join(DATA_DIR, "label_conflicts.json")
        with open(batches_path, "w", encoding="utf-8") as f:
            json.dump(self.list_batches(), f, ensure_ascii=False, indent=2, default=str)
        with open(labels_path, "w", encoding="utf-8") as f:
            json.dump(
                self.list_downstream_labels(), f, ensure_ascii=False, indent=2,
                default=str,
            )
        with open(conflicts_path, "w", encoding="utf-8") as f:
            json.dump(
                self.list_conflicts(only_open=False), f, ensure_ascii=False,
                indent=2, default=str,
            )
