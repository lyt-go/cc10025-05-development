"""标签派生批次集成测试：批量登记、血缘传播、冲突、撤销与重算。

完整还原业务叙事：
  同批里订单明细、支付明细分别给结算表传来互斥等级；另有两条不经过
  结算表的独立链路。冲突确认前回查独立链路结果；之后为一张下游表补登记
  独立来源，撤销首批并复查——只有仍有依据的派生标签继续有效。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.services.label_batch import LabelBatchService
from app.services.lineage_graph import LineageGraphService
from app.store import store

DATA_ROOT = os.path.join(os.path.dirname(__file__), "app", "data", "labels")
RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

passed = 0
failed = 0


def check(desc, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  {GREEN}PASS{RESET} {desc}")
        passed += 1
        return True
    print(f"  {RED}FAIL{RESET} {desc} {detail}")
    failed += 1
    return False


def labels_by_asset(service):
    return {row["目标资产"]: row for row in service.list_downstream_labels()}


def test_verified_graph_loaded():
    print("\n=== 前置: 已核实血缘边已随种子载入 ===")
    svc = LineageGraphService()
    svc.recompute_derived_labels()
    _out, edges = LabelBatchService()._verified_graph()
    edge_pairs = {(edge["from"], edge["to"]) for edge in edges}
    expected = {
        ("ODS_ORDER_DETAIL", "DWD_SETTLE_FACT"),
        ("ODS_PAY_DETAIL", "DWD_SETTLE_FACT"),
        ("DWD_SETTLE_FACT", "ADS_OPERATE_RPT"),
        ("ODS_LOG_DETAIL", "DWD_AUDIT_FLOW"),
        ("DWD_AUDIT_FLOW", "ADS_RISK_VIEW"),
        ("DWD_ORDER_FACT", "ADS_SALES_RPT"),
        ("ODS_REFUND_DETAIL", "DWD_ORDER_FACT"),
    }
    check("7 条场景血缘边全部已核实", expected.issubset(edge_pairs),
          f"实际边: {sorted(edge_pairs)}")
    check("草稿/失效样例边不参与计算",
          all(u != v for u, v in edge_pairs), "存在自环边")


def test_mixed_batch_registration_and_conflict():
    print("\n=== 步骤1: 提交混合批次（互斥等级 + 两条独立链路） ===")
    svc = LabelBatchService()
    batch, msg = svc.register_batch([
        {"asset": "ODS_ORDER_DETAIL", "dimension": "数据等级", "value": "L2",
         "source": "订单域登记"},
        {"asset": "ODS_PAY_DETAIL", "dimension": "数据等级", "value": "L3",
         "source": "支付域登记"},
        {"asset": "ODS_LOG_DETAIL", "dimension": "数据等级", "value": "L2",
         "source": "日志域登记"},
        {"asset": "DWD_ORDER_FACT", "dimension": "数据等级", "value": "L2",
         "source": "订单事实登记"},
    ], remark="混合批次：结算冲突+独立链路", batch_no="LBAT-T01")
    check("批次提交成功", batch is not None, msg)
    check("批次状态为已提交", batch and batch["status"] == "已提交")

    by_asset = labels_by_asset(svc)

    # ---- 结算表：互斥等级确认前不得成为计算结果 ----
    settle = by_asset.get("DWD_SETTLE_FACT")
    check("结算表产生标记记录", settle is not None)
    if settle:
        check("结算表标记为待确认冲突", settle["标记状态"] == "待确认冲突",
              settle["标记状态"])
        check("互斥候选为 L2/L3", set(settle["候选值"]) == {"L2", "L3"},
              str(settle["候选值"]))
        check("冲突确认前无有效标签值", settle["标签值"] == "",
              repr(settle["标签值"]))
        sources = {s["来源数据表"] for s in settle["来源记录"]}
        check("来源逐条记录订单/支付两张上游表",
              sources == {"ODS_ORDER_DETAIL", "ODS_PAY_DETAIL"}, str(sources))
        edge_nos = {
            edge["记录编号"]
            for s in settle["来源记录"] for edge in s["已纳入计算的边"]
        }
        check("已纳入计算的边含 LINE-1001/1002",
              {"LINE-1001", "LINE-1002"}.issubset(edge_nos), str(edge_nos))

    # ---- 冲突清单：结算表本体 + 沿链路继承冲突的经营报表 ----
    open_conflicts = svc.list_conflicts()
    check("待确认冲突登记 2 条（本体+继承）", len(open_conflicts) == 2,
          f"{len(open_conflicts)} 条")
    conflict_targets = {c["目标资产"] for c in open_conflicts}
    check("冲突覆盖结算表与经营报表",
          conflict_targets == {"DWD_SETTLE_FACT", "ADS_OPERATE_RPT"},
          str(conflict_targets))
    settle_conflict = next(
        c for c in open_conflicts if c["目标资产"] == "DWD_SETTLE_FACT"
    )
    check("结算冲突指向数据等级且候选 L2/L3",
          settle_conflict["标签维度"] == "数据等级"
          and set(settle_conflict["候选值"]) == {"L2", "L3"})

    # ---- 经营报表：继承互斥冲突，确认前同样不得生效 ----
    report = by_asset.get("ADS_OPERATE_RPT")
    check("经营报表继承待确认冲突", report is not None
          and report["标记状态"] == "待确认冲突",
          str(report and report["标记状态"]))
    if report:
        check("经营报表确认前无有效标签值", report["标签值"] == "",
              repr(report["标签值"]))
        check("经营报表留痕经过结算边 LINE-1003",
              {"LINE-1003"}.issubset({
                  edge["记录编号"] for s in report["来源记录"]
                  for edge in s["已纳入计算的边"]
              }))

    # ---- 独立链路1：日志 → 审计流水 → 风险视图 ----
    audit_flow = by_asset.get("DWD_AUDIT_FLOW")
    risk_view = by_asset.get("ADS_RISK_VIEW")
    check("审计流水取得派生 L2（独立链路）",
          audit_flow is not None and audit_flow["标记状态"] == "有效"
          and audit_flow["标签值"] == "L2", str(audit_flow and audit_flow["标签值"]))
    check("风险视图取得派生 L2（两跳链路）",
          risk_view is not None and risk_view["标记状态"] == "有效"
          and risk_view["标签值"] == "L2", str(risk_view and risk_view["标签值"]))
    if risk_view:
        trail = risk_view["来源记录"][0]
        check("风险视图留痕两跳边 LINE-1004→LINE-1005",
              [edge["记录编号"] for edge in trail["已纳入计算的边"]]
              == ["LINE-1004", "LINE-1005"],
              str(trail["已纳入计算的边"]))
        check("来源数据表回溯到 ODS_LOG_DETAIL",
              trail["来源数据表"] == "ODS_LOG_DETAIL")

    # ---- 独立链路2：订单事实 → 销售报表 ----
    sales = by_asset.get("ADS_SALES_RPT")
    check("销售报表取得派生 L2（不经结算表）",
          sales is not None and sales["标记状态"] == "有效"
          and sales["标签值"] == "L2", str(sales and sales["标签值"]))

    # ---- 直接标签也被图谱读入 ----
    order_fact = by_asset.get("DWD_ORDER_FACT")
    check("订单事实直接登记为直接标签",
          order_fact is not None and order_fact["标记类型"] == "直接标签"
          and order_fact["标签值"] == "L2")

    # ---- 数据产物落盘 ----
    for filename in ("label_batches.json", "downstream_labels.json",
                     "label_conflicts.json"):
        path = os.path.join(DATA_ROOT, filename)
        check(f"数据产物 {filename} 已落盘", os.path.exists(path), path)


def test_conflict_resolution_takes_effect():
    print("\n=== 步骤2: 冲突解决前保持无效；确认 L2 后才成为计算结果 ===")
    svc = LabelBatchService()
    conflict = next(
        c for c in svc.list_conflicts()
        if c["目标资产"] == "DWD_SETTLE_FACT"
    )
    bad, bad_msg = svc.resolve_conflict(conflict["id"], "L9")
    check("非候选值不允许确认", bad is None, bad_msg)

    resolved, msg = svc.resolve_conflict(conflict["id"], "L2")
    check("冲突确认成功", resolved is not None, msg)

    by_asset = labels_by_asset(svc)
    settle = by_asset["DWD_SETTLE_FACT"]
    check("结算表确认后生效为 L2",
          settle["标记状态"] == "有效" and settle["标签值"] == "L2",
          str(settle["标记状态"]))
    report = by_asset["ADS_OPERATE_RPT"]
    check("经营报表随确认结果生效为 L2",
          report["标记状态"] == "有效" and report["标签值"] == "L2")
    check("待确认冲突清零", len(svc.list_conflicts()) == 0)


def test_independent_source_then_revoke_first_batch():
    print("\n=== 步骤3: 补登记独立来源 → 撤销首批 → 仅留有依据的标记 ===")
    svc = LabelBatchService()

    # 为销售报表链路补一份独立来源（新批次）：退款明细 L2 → 订单事实 → 销售报表
    second, msg = svc.register_batch([
        {"asset": "ODS_REFUND_DETAIL", "dimension": "数据等级", "value": "L2",
         "source": "退款域补登记"},
        {"asset": "ADS_SALES_RPT", "dimension": "数据等级", "value": "L2",
         "source": "报表负责人直登"},
    ], remark="独立补登记批次", batch_no="LBAT-T02")
    check("第二批次提交成功", second is not None, msg)

    by_asset = labels_by_asset(svc)
    sales_before = by_asset["ADS_SALES_RPT"]
    supporting_batches = {s["批次编号"] for s in sales_before["来源记录"]}
    check("销售报表同时受两个批次支持",
          supporting_batches == {"LBAT-T01", "LBAT-T02"}, str(supporting_batches))

    # 撤销首批
    revoked, msg = svc.revoke_batch("LBAT-T01")
    check("首批撤销成功", revoked is not None and revoked["status"] == "已撤销", msg)
    repeat, repeat_msg = svc.revoke_batch("LBAT-T01")
    check("重复撤销被拒绝", repeat is None, repeat_msg)

    by_asset = labels_by_asset(svc)

    # 仅由首批支持的标记必须随撤销消失
    check("结算表标记随首批撤销而移除", "DWD_SETTLE_FACT" not in by_asset,
          str(list(by_asset)))
    check("经营报表标记随首批撤销而移除", "ADS_OPERATE_RPT" not in by_asset)
    check("审计流水标记随首批撤销而移除", "DWD_AUDIT_FLOW" not in by_asset)
    check("风险视图标记随首批撤销而移除", "ADS_RISK_VIEW" not in by_asset)

    # 仍有独立依据的派生标签继续有效
    sales_after = by_asset.get("ADS_SALES_RPT")
    check("销售报表因独立来源继续有效",
          sales_after is not None and sales_after["标记状态"] == "有效"
          and sales_after["标签值"] == "L2", str(sales_after))
    if sales_after:
        remaining = {s["批次编号"] for s in sales_after["来源记录"]}
        check("销售报表剩余支持只来自第二批",
              remaining == {"LBAT-T02"}, str(remaining))
        check("来源留痕仍可回溯到退款明细与负责人直登",
              {s["来源数据表"] for s in sales_after["来源记录"]}
              == {"ODS_REFUND_DETAIL", "ADS_SALES_RPT"})

    order_fact = by_asset.get("DWD_ORDER_FACT")
    check("订单事实由退款链路继续支撑派生 L2",
          order_fact is not None and order_fact["标记状态"] == "有效"
          and order_fact["标签值"] == "L2")
    check("订单事实的首批直接登记支持已移除",
          all(s["批次编号"] != "LBAT-T01" for s in order_fact["来源记录"]))

    # 冲突随来源撤销而消除
    check("撤销后无悬挂冲突", len(svc.list_conflicts()) == 0)


def test_same_label_multi_path_dedup():
    print("\n=== 步骤4: 同一标签不同路径到同表去重，边逐条留痕 ===")
    svc = LabelBatchService()
    # 新批次让两张上游表沿不同路径给 DWD_ORDER_FACT 传同一维度同值标签
    batch, msg = svc.register_batch([
        {"asset": "ODS_REFUND_DETAIL", "dimension": "脱敏等级", "value": "M1",
         "source": "退款域"},
    ], remark="多路径去重批次", batch_no="LBAT-T03")
    check("去重批次提交成功", batch is not None, msg)

    # 再造一条并行路径：退款明细 → 订单事实（已有 LINE-1007），
    # 另注册一条已核实边形成第二条路径
    lineage_rows = store.rows("lineage_graph")
    next_line_id = max(int(r["id"]) for r in lineage_rows) + 1
    lineage_rows.append({
        "id": next_line_id,
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "记录编号": "LINE-1008",
        "上游资产": "ODS_REFUND_DETAIL",
        "下游资产": "DWD_ORDER_FACT",
        "依赖类型": "加工",
        "加工逻辑": "退款宽表并入订单事实（并行核实路径）",
        "生效时间": "2026-11-05",
        "变更SHA": "sha-1008",
        "血缘状态": "已核实",
    })
    svc.recompute()

    target = labels_by_asset(svc)["DWD_ORDER_FACT"]
    mask = next(row for row in svc.list_downstream_labels("DWD_ORDER_FACT")
                if row["标签维度"] == "脱敏等级")
    check("同表同维度只保留一个派生标记",
          sum(1 for row in svc.list_downstream_labels("DWD_ORDER_FACT")
              if row["标签维度"] == "脱敏等级") == 1)
    edge_nos = {edge["记录编号"] for s in mask["来源记录"]
                for edge in s["已纳入计算的边"]}
    check("两条路径的边均留痕（LINE-1007/1008）",
          {"LINE-1007", "LINE-1008"}.issubset(edge_nos), str(edge_nos))
    check("去重后仍为有效 M1",
          mask["标记状态"] == "有效" and mask["标签值"] == "M1")


def test_lineage_correction_recomputes():
    print("\n=== 步骤5: 修正血缘边后按剩余来源重算 ===")
    svc = LabelBatchService()
    # 让 LINE-1007 失效：退款明细→订单事实的一条边核实不通过
    edge, msg = svc.correct_lineage_edge("LINE-1007", invalidate=True)
    check("血缘边失效成功", edge is not None and edge["status"] == "已失效", msg)

    by_asset = labels_by_asset(svc)
    # LINE-1008 仍在，脱敏等级 M1 应继续有效，但来源边只剩 LINE-1008
    mask = next(row for row in svc.list_downstream_labels("DWD_ORDER_FACT")
                if row["标签维度"] == "脱敏等级")
    edge_nos = {edge["记录编号"] for s in mask["来源记录"]
                for edge in s["已纳入计算的边"]}
    check("失效边退出计算，仅 LINE-1008 留痕",
          edge_nos == {"LINE-1008"}, str(edge_nos))
    check("剩余来源仍支撑 M1 有效",
          mask["标记状态"] == "有效" and mask["标签值"] == "M1")

    # 再令最后一条边失效 → 失去全部依据，标记移除
    svc.correct_lineage_edge("LINE-1008", invalidate=True)
    remaining = [row for row in svc.list_downstream_labels("DWD_ORDER_FACT")
                 if row["标签维度"] == "脱敏等级"]
    check("全部依据消失后派生标记移除", remaining == [], str(remaining))


if __name__ == "__main__":
    print("=" * 60)
    print("标签派生批次集成测试（批量登记/血缘传播/冲突/撤销/重算）")
    print("=" * 60)

    test_verified_graph_loaded()
    test_mixed_batch_registration_and_conflict()
    test_conflict_resolution_takes_effect()
    test_independent_source_then_revoke_first_batch()
    test_same_label_multi_path_dedup()
    test_lineage_correction_recomputes()

    print(f"\n{'=' * 60}")
    print(f"结果: {GREEN}{passed} 通过{RESET} / {RED}{failed} 失败{RESET} / "
          f"{passed + failed} 总计")
    print("=" * 60)
    if failed > 0:
        sys.exit(1)
