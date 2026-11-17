"""M8 编排：派单同步、工作包视图、有效决定过滤与发布阻断。"""
from __future__ import annotations

from ... import store
from ...errors import NotFound
from ...reasons import B_REVIEW_PENDING, V_UNKNOWN_PACKAGE
from ..m4_pairs import service as pair_service
from . import sync


def ensure_packages(run_id: str) -> dict:
    """先保证候选对最新，再把工作包对齐；幂等，可挂在任何读取路径上。"""
    pair_service.ensure_pairs(run_id)
    return sync.sync(run_id)


def owner_map(run_id: str) -> dict:
    """{pair_id: {package_id, owner}}：每张关系的唯一有效责任人。"""
    ensure_packages(run_id)
    return {row["pair_id"]: {"package_id": row["package_id"], "owner": row["owner"]}
            for row in store.valid_pair_owners(run_id)}


def valid_decisions(run_id: str) -> list:
    """当前有效包覆盖的决定：发布与簇重算只能采用这部分。"""
    ensure_packages(run_id)
    valid_pair_ids = {row["pair_id"] for row in store.valid_pair_owners(run_id)}
    return [item for item in store.list_decisions(run_id)
            if item["pair_id"] in valid_pair_ids]


def _progress(run_id: str) -> dict:
    decided = {item["pair_id"] for item in store.list_decisions(run_id)}
    progress = {}
    for row in store.list_package_pairs(run_id, valid_only=True):
        slot = progress.setdefault(row["package_id"], {"decided": 0, "pending": 0,
                                                       "pending_pairs": []})
        if row["pair_id"] in decided:
            slot["decided"] += 1
        else:
            slot["pending"] += 1
            slot["pending_pairs"].append(row["pair_id"])
    return progress


def packages_view(run_id: str) -> list:
    ensure_packages(run_id)
    progress = _progress(run_id)
    items = []
    for package in store.list_packages(run_id):
        state = progress.get(package["package_id"], {"decided": 0, "pending": 0,
                                                     "pending_pairs": []})
        done = package["status"] == "open" and state["pending"] == 0
        items.append({
            **package,
            "decided": state["decided"],
            "pending": state["pending"],
            "pending_pairs": state["pending_pairs"],
            "state": "invalid" if package["status"] == "invalid"
                     else ("done" if done else "open"),
        })
    return items


def package_detail(run_id: str, package_id: str) -> dict:
    ensure_packages(run_id)
    package = store.get_package(run_id, package_id)
    if package is None:
        raise NotFound(V_UNKNOWN_PACKAGE, package_id)
    records = {record["supplier_id"]: record for record in store.list_records(run_id)}
    pairs = {pair["pair_id"]: pair for pair in store.list_pairs(run_id)}
    decisions = {item["pair_id"]: item for item in store.list_decisions(run_id)}
    rows = []
    for member in store.list_package_pairs(run_id, package_id):
        pair = pairs.get(member["pair_id"], {})
        left = records.get(pair.get("left_id", ""), {})
        right = records.get(pair.get("right_id", ""), {})
        decision = decisions.get(member["pair_id"])
        rows.append({
            "pair_key": member["pair_key"],
            "pair_id": member["pair_id"],
            "valid": member["valid"],
            "left_id": pair.get("left_id", ""),
            "right_id": pair.get("right_id", ""),
            "left_name": left.get("raw_name", ""),
            "right_name": right.get("raw_name", ""),
            "score": pair.get("score", ""),
            "reason_code": pair.get("reason_code", ""),
            "decision": decision["decision"] if decision else "",
            "operator": decision["operator"] if decision else "",
            "carried_from": decision.get("carried_from", "") if decision else "",
        })
    return {"package": package, "pairs": rows}


def publish_blockers(run_id: str) -> list:
    """发布阻断：有效工作包里仍有待决关系的，逐包列出责任人与待决单。"""
    items = []
    for package in packages_view(run_id):
        if package["state"] != "open" or package["pending"] == 0:
            continue
        items.append({
            "reason_code": B_REVIEW_PENDING,
            "package_id": package["package_id"],
            "owner": package["owner"],
            "pending": package["pending"],
            "pairs": package["pending_pairs"],
        })
    return items


def stats(run_id: str) -> dict:
    items = packages_view(run_id)
    open_items = [item for item in items if item["state"] != "invalid"]
    return {
        "total": len(open_items),
        "open": sum(1 for item in open_items if item["state"] == "open"),
        "done": sum(1 for item in open_items if item["state"] == "done"),
        "invalid": sum(1 for item in items if item["state"] == "invalid"),
        "pairs": sum(item["pair_count"] for item in open_items),
        "pending": sum(item["pending"] for item in open_items),
        "owners": sorted({item["owner"] for item in open_items}),
    }
