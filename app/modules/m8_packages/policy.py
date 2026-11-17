"""拆分策略：何时允许把一个主体簇的候选关系分给多个复核组。

证据图 = 自动合并边 + 待复核边（拒绝边不参与，它从不改变簇形态）。
同一双连通分量（证据环）内的待复核关系，其判定可能经传递闭包互相矛盾
（例如环上两条边一条判合并、一条判保留，最终簇必然违背其中一条），
因此环是派单的最小原子，禁止拆开，必须整体派给同一个复核组。

允许拆分的边界：仅共享单个主体记录（关节点）、不共处一个环的关系，
判定结果无论怎么组合都不会互相矛盾，可以分给不同的组并行裁决。
桥边自成一环（只含自己），天然独立成包。

由此每个工作包恰好对应一个双连通分量的待复核边集合：
- 小簇、单环簇：整簇一个包，不会被拆；
- 大簇：沿关节点拆成多个环包并行派单，不会整簇压给一个组；
- 每张关系（边）只属于一个分量，因此只有一个有效责任人。
"""
from __future__ import annotations

import hashlib

from . import biconnected

EDGE_VERDICTS = ("auto", "review")


def pair_key(left_id: str, right_id: str) -> str:
    """关系的内容键：与批次内编号无关，跨批次稳定。"""
    return f"{left_id}|{right_id}"


def signature_of(pair_keys) -> str:
    """工作包签名：包内关系内容键集合的摘要，内容变则签名变。"""
    digest = hashlib.sha256()
    for key in sorted(pair_keys):
        digest.update(key.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def package_id_for(signature: str) -> str:
    return "WP-" + signature[:10].upper()


def split_units(pairs) -> list:
    """把候选对切成派单原子：返回 [{signature, package_id, pair_keys, pairs}]。

    pairs 为 pair_scores 行；只消费 verdict 为 auto/review 的边，
    输出仅含待复核（review）边非空的分量，按 package_id 排序。
    """
    edges, review_by_key = [], {}
    for pair in pairs:
        if pair["verdict"] not in EDGE_VERDICTS:
            continue
        key = pair_key(pair["left_id"], pair["right_id"])
        edges.append((pair["left_id"], pair["right_id"], key))
        if pair["verdict"] == "review":
            review_by_key[key] = pair

    nodes = sorted({node for edge in edges for node in edge[:2]})
    units = []
    for block in biconnected.components(nodes, edges):
        review_keys = [key for key in block if key in review_by_key]
        if not review_keys:
            continue
        signature = signature_of(review_keys)
        units.append({
            "signature": signature,
            "package_id": package_id_for(signature),
            "pair_keys": review_keys,
            "pairs": [review_by_key[key] for key in review_keys],
        })
    return sorted(units, key=lambda unit: unit["package_id"])
