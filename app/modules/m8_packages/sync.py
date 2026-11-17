"""派单同步：把当前证据环划分与已存在的工作包对齐。

幂等：同一批重跑时划分不变，所有签名都已存在，直接全部跳过，
不重复派单、不改变任何责任人。

跨批次（上游名录补充后）：签名未变的包整体继承上一批次的责任人与
已完成决定；签名变了的包不再继承，其关系进入新包重新派单继续裁决；
旧批次数据原地保留，不作任何修改。
"""
from __future__ import annotations

from ... import store
from ...config import REVIEW_GROUPS
from ...manifest import utc_iso, utc_now
from . import assign, policy


def _pairs_by_key(run_id: str) -> dict:
    return {policy.pair_key(p["left_id"], p["right_id"]): p
            for p in store.list_pairs(run_id)}


def _inherit(unit: dict, donor: dict, donor_run_id: str, run_id: str,
             donor_decisions: dict, donor_pairs: dict, current_pairs: dict,
             now: str) -> int:
    """按签名整体继承：责任人照原样，已完成决定按关系内容键搬入本批次。"""
    store.save_package({
        "run_id": run_id,
        "package_id": unit["package_id"],
        "signature": unit["signature"],
        "owner": donor["owner"],
        "status": "open",
        "pair_count": len(unit["pair_keys"]),
        "inherited_from": donor_run_id,
        "created_at": now,
    }, [{"pair_key": key, "pair_id": current_pairs[key]["pair_id"]}
        for key in unit["pair_keys"]])

    carried = []
    for key in unit["pair_keys"]:
        donor_pair = donor_pairs.get(key)
        if donor_pair is None:
            continue
        decision = donor_decisions.get(donor_pair["pair_id"])
        if decision is None:
            continue
        carried.append({
            "run_id": run_id,
            "pair_id": current_pairs[key]["pair_id"],
            "decision": decision["decision"],
            "expected_version": decision["expected_version"],
            "version": decision["version"],
            "operator": decision["operator"],
            "decided_at": decision["decided_at"],
            "carried_from": donor_run_id,
        })
    store.carry_decisions(carried)
    return len(carried)


def sync(run_id: str) -> dict:
    """对齐当前批次的工作包；返回差异报告（全零表示无变化）。"""
    units = policy.split_units(store.list_pairs(run_id))
    by_signature = {unit["signature"]: unit for unit in units}
    now = utc_iso(utc_now())

    invalidated = 0
    for package in store.list_packages(run_id, "open"):
        if package["signature"] not in by_signature:
            store.invalidate_package(run_id, package["package_id"])
            invalidated += 1

    surviving = {package["signature"]
                 for package in store.list_packages(run_id, "open")}
    missing = [unit for unit in units if unit["signature"] not in surviving]

    inherited = created = carried = 0
    if missing:
        current_pairs = _pairs_by_key(run_id)
        donor_run_id = store.previous_run_with_packages(run_id)
        donors = {}
        donor_decisions, donor_pairs = {}, {}
        if donor_run_id:
            donors = {p["signature"]: p
                      for p in store.list_packages(donor_run_id, "open")}
            donor_decisions = {d["pair_id"]: d
                               for d in store.list_decisions(donor_run_id)}
            donor_pairs = _pairs_by_key(donor_run_id)

        fresh = []
        for unit in missing:
            donor = donors.get(unit["signature"])
            if donor is None:
                fresh.append(unit)
                continue
            carried += _inherit(unit, donor, donor_run_id, run_id,
                                donor_decisions, donor_pairs, current_pairs, now)
            inherited += 1

        if fresh:
            loads = {}
            for package in store.list_packages(run_id, "open"):
                loads[package["owner"]] = loads.get(package["owner"], 0) + 1
            owners = assign.choose_owners(
                loads, [unit["package_id"] for unit in fresh], REVIEW_GROUPS)
            for unit in fresh:
                store.save_package({
                    "run_id": run_id,
                    "package_id": unit["package_id"],
                    "signature": unit["signature"],
                    "owner": owners[unit["package_id"]],
                    "status": "open",
                    "pair_count": len(unit["pair_keys"]),
                    "inherited_from": "",
                    "created_at": now,
                }, [{"pair_key": key, "pair_id": current_pairs[key]["pair_id"]}
                    for key in unit["pair_keys"]])
                created += 1

    return {
        "run_id": run_id,
        "units": len(units),
        "inherited": inherited,
        "created": created,
        "invalidated": invalidated,
        "carried_decisions": carried,
        "unchanged": len(units) - inherited - created,
        "open": len(store.list_packages(run_id, "open")),
    }
