"""边双连通分解：Tarjan 低链算法，输入有序则输出确定。

同一双连通分量内的边共处一个证据环：环上任何两条边的判定都可能通过
传递闭包互相矛盾，因此环是派单的最小原子，禁止拆开。
桥边各自成为一个分量，天然可以独立派单。
"""
from __future__ import annotations


def components(nodes, edges) -> list:
    """返回边集合的划分：每个元素是一个分量包含的边键有序列表。

    nodes: 可迭代节点；edges: [(left, right, key), ...]，key 唯一且无自环。
    结果按分量内最小边键排序，分量内边键升序，与调用顺序无关。
    """
    adjacency = {}
    for node in nodes:
        adjacency.setdefault(node, [])
    for left, right, key in sorted(edges, key=lambda edge: edge[2]):
        adjacency.setdefault(left, []).append((right, key))
        adjacency.setdefault(right, []).append((left, key))
    for node in adjacency:
        adjacency[node].sort()

    disc, low = {}, {}
    edge_stack = []
    blocks = []
    clock = 0

    for start in sorted(adjacency):
        if start in disc:
            continue
        # 迭代式 DFS：栈元素为 (节点, 邻接下标, 父节点, 父边键)
        stack = [(start, 0, None, None)]
        disc[start] = low[start] = clock
        clock += 1
        while stack:
            node, index, parent, parent_key = stack[-1]
            if index < len(adjacency[node]):
                neighbor, key = adjacency[node][index]
                stack[-1] = (node, index + 1, parent, parent_key)
                if key == parent_key:
                    continue
                if neighbor not in disc:
                    edge_stack.append(key)
                    disc[neighbor] = low[neighbor] = clock
                    clock += 1
                    stack.append((neighbor, 0, node, key))
                elif disc[neighbor] < disc[node]:
                    edge_stack.append(key)
                    low[node] = min(low[node], disc[neighbor])
            else:
                stack.pop()
                if parent is not None:
                    low[parent] = min(low[parent], low[node])
                    if low[node] >= disc[parent]:
                        block = []
                        while True:
                            block.append(edge_stack.pop())
                            if block[-1] == parent_key:
                                break
                        blocks.append(sorted(block))

    return sorted(blocks, key=lambda block: block[0])
