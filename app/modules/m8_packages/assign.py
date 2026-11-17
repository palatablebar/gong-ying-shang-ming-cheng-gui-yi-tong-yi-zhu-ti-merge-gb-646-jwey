"""派单：新工作包在复核组间的确定性分配。

只给"没有可继承归属"的新包派组；已有有效包的组一律不动。
分配规则：按工作包号升序，依次派给当前持有有效包最少的组，
并列时取组名单靠前者。输入相同则结果相同，重跑不会改派。
"""
from __future__ import annotations


def choose_owners(loads: dict, package_ids, groups) -> dict:
    """loads: {组: 已有有效包数}；返回 {package_id: 组}。"""
    rank = {group: position for position, group in enumerate(groups)}
    current = {group: int(loads.get(group, 0)) for group in groups}
    assignment = {}
    for package_id in sorted(package_ids):
        group = min(groups, key=lambda name: (current[name], rank[name]))
        assignment[package_id] = group
        current[group] += 1
    return assignment
