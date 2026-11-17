"""M8 复核工作包：证据环拆分、唯一责任人、幂等重跑、跨批继承与发布阻断。"""
from __future__ import annotations

import random

import pytest

from app import store
from app.config import REVIEW_GROUPS, SAMPLE_FILES
from app.errors import NotFound
from app.modules.m1_intake import service as intake_service
from app.modules.m5_clusters import service as cluster_service
from app.modules.m6_reviews import service as review_service
from app.modules.m8_packages import assign, biconnected, policy, service
from tests.fixtures import expected as EXP

SUPPLEMENT = "erp_g_supplement.csv"
INTRUSIVE = "erp_h_intrusive.csv"


def _pair(left, right, verdict="review"):
    return {"pair_id": f"P-{left}-{right}", "left_id": left, "right_id": right,
            "verdict": verdict, "score": "0.900000", "reason_code": "P_REVIEW",
            "dice": "0.900000", "ratio": "0.900000", "detail": ""}


def _supplement(run, extra):
    service.ensure_packages(run["run_id"])
    return intake_service.ingest(files=list(SAMPLE_FILES) + [extra])


# ---- 拆分策略 ----

def test_ring_not_split(run):
    """同一证据环（自动边+两条复核边构成的三角形）必须整体派单。"""
    service.ensure_packages(run["run_id"])
    units = policy.split_units(store.list_pairs(run["run_id"]))
    ring = [unit for unit in units if len(unit["pair_keys"]) == 2]
    assert len(ring) == 1
    assert sorted(ring[0]["pair_keys"]) == ["S-0013|S-0078", "S-0031|S-0078"]


def test_bridge_is_singleton(run):
    """桥边自成一包：P-00105 不与任何其他关系共环。"""
    service.ensure_packages(run["run_id"])
    units = policy.split_units(store.list_pairs(run["run_id"]))
    singles = [unit for unit in units if len(unit["pair_keys"]) == 1]
    assert [unit["pair_keys"] for unit in singles] == [["S-0073|S-0074"]]


def test_articulation_split_allowed():
    """8 字形：两个三角形只共享一个关节点，允许拆给不同的组。"""
    pairs = [_pair("A", "B"), _pair("B", "C"), _pair("C", "A"),
             _pair("A", "D"), _pair("D", "E"), _pair("E", "A")]
    units = policy.split_units(pairs)
    assert len(units) == 2
    sizes = sorted(len(unit["pair_keys"]) for unit in units)
    assert sizes == [3, 3]


def test_path_edges_independent():
    """链式无边环：每条桥边独立成包。"""
    pairs = [_pair("A", "B"), _pair("B", "C"), _pair("C", "D")]
    units = policy.split_units(pairs)
    assert len(units) == 3
    assert all(len(unit["pair_keys"]) == 1 for unit in units)


def test_auto_edges_join_ring():
    """自动边参与成环：环上的复核边被拉进同一个原子。"""
    pairs = [_pair("A", "B", "auto"), _pair("B", "C"), _pair("C", "A")]
    units = policy.split_units(pairs)
    assert len(units) == 1
    assert sorted(units[0]["pair_keys"]) == ["B|C", "C|A"]


def _reference_blocks(nodes, edges):
    """参照实现：枚举全部简单环，环上的边并为一类（桥边各自一类）。"""
    adjacency = {node: [] for node in nodes}
    for left, right, key in edges:
        adjacency[left].append((right, key))
        adjacency[right].append((left, key))
    parent = {key: key for _, _, key in edges}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def unite(left_key, right_key):
        parent[find(left_key)] = find(right_key)

    for start in nodes:
        stack = [(start, [start], [])]
        while stack:
            node, path, keys = stack.pop()
            for neighbor, key in adjacency[node]:
                if neighbor == start and len(path) >= 3:
                    for other in keys + [key]:
                        unite(keys[0], other)
                elif neighbor > start and neighbor not in path:
                    stack.append((neighbor, path + [neighbor], keys + [key]))
    groups = {}
    for _, _, key in edges:
        groups.setdefault(find(key), set()).add(key)
    return sorted((sorted(group) for group in groups.values()), key=lambda g: g[0])


def test_biconnected_matches_reference():
    """随机小图：Tarjan 分解与枚举环参照实现一致。"""
    rng = random.Random(20261117)
    for _ in range(30):
        nodes = [f"N{i}" for i in range(rng.randint(2, 7))]
        edges = []
        for i, left in enumerate(nodes):
            for right in nodes[i + 1:]:
                if rng.random() < 0.45:
                    edges.append((left, right, f"{left}|{right}"))
        got = biconnected.components(nodes, edges)
        want = _reference_blocks(nodes, edges)
        assert got == want


# ---- 派单与幂等 ----

def test_every_pair_exactly_one_owner(run):
    """不变量：每张待复核关系恰好属于一个有效工作包。"""
    service.ensure_packages(run["run_id"])
    owners = store.valid_pair_owners(run["run_id"])
    pair_ids = [row["pair_id"] for row in owners]
    assert len(pair_ids) == len(set(pair_ids)) == EXP.PAIRS_REVIEW
    review_ids = {row["pair_id"] for row in store.list_pairs(run["run_id"], "review")}
    assert set(pair_ids) == review_ids


def test_sync_idempotent_rerun(run):
    """同批重跑：不重复派单、不改变任何责任人。"""
    first = service.ensure_packages(run["run_id"])
    assert first["created"] == 2
    before = [(p["package_id"], p["owner"]) for p in store.list_packages(run["run_id"])]
    second = service.ensure_packages(run["run_id"])
    assert second["created"] == second["inherited"] == second["invalidated"] == 0
    assert second["carried_decisions"] == 0
    after = [(p["package_id"], p["owner"]) for p in store.list_packages(run["run_id"])]
    assert before == after
    assert store.count(run["run_id"], "packages") == 2
    assert store.count(run["run_id"], "package_pairs") == EXP.PAIRS_REVIEW


def test_owner_assignment_balanced_deterministic():
    loads = {"复核一组": 2, "复核二组": 0}
    first = assign.choose_owners(loads, ["WP-B", "WP-A", "WP-C"], REVIEW_GROUPS)
    second = assign.choose_owners(loads, ["WP-B", "WP-A", "WP-C"], REVIEW_GROUPS)
    assert first == second
    # 与包号字典序无关的输入顺序也得到同一结果
    shuffled = assign.choose_owners(loads, ["WP-C", "WP-A", "WP-B"], REVIEW_GROUPS)
    assert shuffled == first
    # 持有最少者优先，并列取名单靠前；随后负载递增滚动
    assert first == {"WP-A": "复核二组", "WP-B": "复核三组", "WP-C": "复核二组"}


# ---- 跨批次继承 ----

def test_supplement_inherits_unaffected(run):
    """良性补充：未受影响包的责任人与已完成决定原样继承。"""
    review_service.decide(run["run_id"], EXP.PAIR_REVIEW, "keep", 0, "李审")
    owners_before = {p["package_id"]: p["owner"]
                     for p in store.list_packages(run["run_id"], "open")}
    run2 = _supplement(run, SUPPLEMENT)
    assert run2["run_id"] != run["run_id"]
    report = service.ensure_packages(run2["run_id"])
    assert report["inherited"] == 2
    assert report["created"] == 1
    assert report["carried_decisions"] == 1

    packages = {p["package_id"]: p for p in store.list_packages(run2["run_id"], "open")}
    for package_id, owner in owners_before.items():
        assert packages[package_id]["owner"] == owner
        assert packages[package_id]["inherited_from"] == run["run_id"]
    fresh = [p for p in packages.values() if not p["inherited_from"]]
    assert len(fresh) == 1

    carried = store.get_decision(run2["run_id"], EXP.PAIR_REVIEW)
    assert carried["decision"] == "keep"
    assert carried["carried_from"] == run["run_id"]
    # 旧批次数据原地保留
    assert store.get_decision(run["run_id"], EXP.PAIR_REVIEW)["decision"] == "keep"
    assert len(store.list_packages(run["run_id"], "open")) == 2


def test_intrusive_supplement_replaces_affected(run):
    """侵入补充：受影响包换新派单，其旧决定不继承；未受影响包照常继承。"""
    review_service.decide(run["run_id"], EXP.PAIR_CITY_MISMATCH, "merge", 0, "李审")
    review_service.decide(run["run_id"], EXP.PAIR_REVIEW, "keep", 0, "李审")
    triangle_package = {p["package_id"]: p for p in store.list_packages(run["run_id"])
                        }[[p["package_id"] for p in store.list_packages(run["run_id"])
                            if p["pair_count"] == 2][0]]
    run3 = _supplement(run, INTRUSIVE)
    report = service.ensure_packages(run3["run_id"])
    assert report["inherited"] == 1
    assert report["created"] == 1
    assert report["carried_decisions"] == 1

    packages = {p["package_id"]: p for p in store.list_packages(run3["run_id"], "open")}
    assert triangle_package["package_id"] not in packages  # 旧签名不再有效
    grown = [p for p in packages.values() if p["pair_count"] == 5]
    assert len(grown) == 1 and not grown[0]["inherited_from"]

    # 受影响包里的 merge 决定不进入新批次；未受影响包的 keep 决定继承
    assert store.list_decisions(run3["run_id"]) and all(
        d["pair_id"] != EXP.PAIR_CITY_MISMATCH or d["carried_from"]
        for d in store.list_decisions(run3["run_id"]))
    assert not any(d["decision"] == "merge" for d in store.list_decisions(run3["run_id"]))


def test_publish_adopts_only_valid_decisions(run):
    """发布口径：只有当前有效包的决定参与合并。"""
    review_service.decide(run["run_id"], EXP.PAIR_CITY_MISMATCH, "merge", 0)
    review_service.decide(run["run_id"], EXP.PAIR_REVIEW, "merge", 0)
    cluster_service.ensure_clusters(run["run_id"])
    members = {m["supplier_id"]: m["cluster_id"]
               for m in store.list_cluster_members(run["run_id"])}
    assert members["S-0078"] == members["S-0013"]  # 三角形 merge 生效
    assert members["S-0073"] == members["S-0074"]  # 桥边 merge 生效

    run3 = _supplement(run, INTRUSIVE)
    cluster_service.ensure_clusters(run3["run_id"])
    members3 = {m["supplier_id"]: m["cluster_id"]
                for m in store.list_cluster_members(run3["run_id"])}
    # 受影响包的 merge 未继承：不再合并；未受影响包的 merge 继承：仍合并
    assert members3["S-0078"] != members3["S-0013"]
    assert members3["S-0073"] == members3["S-0074"]


def test_carried_version_chain(run):
    """继承决定的版本链延续：重判从继承版本继续递增。"""
    review_service.decide(run["run_id"], EXP.PAIR_REVIEW, "keep", 0)
    run2 = _supplement(run, SUPPLEMENT)
    service.ensure_packages(run2["run_id"])
    record = review_service.decide(run2["run_id"], EXP.PAIR_REVIEW, "merge", 1, "王核")
    assert record["version"] == 2
    with pytest.raises(Exception):
        review_service.decide(run2["run_id"], EXP.PAIR_REVIEW, "keep", 1)


# ---- 发布阻断 ----

def test_blockers_clear_after_decisions(run):
    blockers = service.publish_blockers(run["run_id"])
    assert len(blockers) == 2
    assert {b["reason_code"] for b in blockers} == {"B_REVIEW_PENDING"}
    assert all(b["owner"] for b in blockers)
    for item in review_service.queue_items(run["run_id"]):
        review_service.decide(run["run_id"], item["pair_id"], "keep", item["version"])
    assert service.publish_blockers(run["run_id"]) == []


def test_manifest_publish_section(run):
    from app.modules.m7_export import service as export_service
    payload = export_service.manifest_json(run["run_id"])
    import json
    manifest = json.loads(payload)
    assert manifest["publish"]["ok"] is False
    assert len(manifest["publish"]["blockers"]) == 2
    for item in review_service.queue_items(run["run_id"]):
        review_service.decide(run["run_id"], item["pair_id"], "keep", item["version"])
    manifest = json.loads(export_service.manifest_json(run["run_id"]))
    assert manifest["publish"]["ok"] is True
    assert manifest["publish"]["blockers"] == []


# ---- API 与页面 ----

def test_packages_api(client):
    response = client.get("/api/packages")
    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["total"] == 2
    assert body["stats"]["pairs"] == EXP.PAIRS_REVIEW
    package_id = body["items"][0]["package_id"]

    detail = client.get(f"/api/packages/{package_id}")
    assert detail.status_code == 200
    assert detail.json()["package"]["package_id"] == package_id
    assert detail.json()["pairs"]

    missing = client.get("/api/packages/WP-0000000000")
    assert missing.status_code == 404
    assert missing.json()["reason_code"] == "V_UNKNOWN_PACKAGE"

    sync = client.post("/api/packages/sync")
    assert sync.status_code == 200
    assert sync.json()["created"] == 0

    blockers = client.get("/api/packages/blockers")
    assert blockers.status_code == 200
    assert blockers.json()["total"] == 2


def test_packages_page(client):
    page = client.get("/packages")
    assert page.status_code == 200
    assert "复核工作包" in page.text
    assert "发布阻断" in page.text
    assert "WP-" in page.text
    assert any(group in page.text for group in REVIEW_GROUPS)


def test_cluster_page_shows_relations(client):
    # 先合并一条复核边，使其成为簇内关系并带上工作包责任人
    decided = client.post(f"/api/reviews/{EXP.PAIR_CITY_MISMATCH}/decision",
                          json={"decision": "merge", "expected_version": 0})
    assert decided.status_code == 200
    page = client.get("/clusters")
    assert page.status_code == 200
    assert "簇内关系" in page.text
    assert "WP-" in page.text


def test_reviews_page_shows_owner(client):
    page = client.get("/reviews")
    assert page.status_code == 200
    assert "责任人" in page.text
    assert "WP-" in page.text


def test_export_page_shows_blockers(client):
    page = client.get("/export")
    assert page.status_code == 200
    assert "发布阻断" in page.text
    assert "B_REVIEW_PENDING" in page.text


def test_unknown_package_detail_raises(run):
    service.ensure_packages(run["run_id"])
    with pytest.raises(NotFound) as error:
        service.package_detail(run["run_id"], "WP-FFFFFFFFFF")
    assert error.value.code == "V_UNKNOWN_PACKAGE"
