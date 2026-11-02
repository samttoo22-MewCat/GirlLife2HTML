"""Unbounded static DFS: exclude back edges, retain convergent branches.

Writes edge IDs only. Original graph and branch ID mapping stay untouched.
"""
import argparse
from collections import deque
import json
from pathlib import Path
import time

from extract import digest, write_json


def acyclic_dfs(graph):
    outgoing = {}
    for index, edge in enumerate(graph['edges']):
        outgoing.setdefault(edge['source'], []).append(index)
    color, kept, removed, roots = {}, [], [], []
    for root in [graph['root']] + [node['id'] for node in graph['nodes']]:
        if color.get(root):
            continue
        roots.append(root)
        color[root] = 1
        stack = [(root, iter(outgoing.get(root, [])))]
        while stack:
            node, iterator = stack[-1]
            try:
                edge_id = next(iterator)
            except StopIteration:
                color[node] = 2
                stack.pop()
                continue
            target = graph['edges'][edge_id]['target']
            if target is not None and color.get(target) == 1:
                removed.append(edge_id)
                continue
            kept.append(edge_id)
            if target is not None and not color.get(target):
                color[target] = 1
                stack.append((target, iter(outgoing.get(target, []))))
    assert len(color) == len(graph['nodes'])
    assert not set(kept) & set(removed)
    assert set(kept) | set(removed) == set(range(len(graph['edges'])))
    return kept, removed, roots


def verify_dag(graph, kept):
    # Independent Kahn verification, not the DFS colors used to remove edges.
    degree = {node['id']: 0 for node in graph['nodes']}
    outgoing = {}
    for edge_id in kept:
        edge = graph['edges'][edge_id]
        if edge['target'] is not None:
            outgoing.setdefault(edge['source'], []).append(edge['target'])
            degree[edge['target']] += 1
    queue = deque(node for node, value in degree.items() if value == 0)
    count = 0
    while queue:
        node = queue.popleft()
        count += 1
        for target in outgoing.get(node, []):
            degree[target] -= 1
            if degree[target] == 0:
                queue.append(target)
    assert count == len(degree), 'Cycle remains in retained graph'
    return count


def run(folder):
    start = time.monotonic()
    raw = (folder / 'graph.json').read_bytes()
    graph = json.loads(raw)
    kept, removed, roots = acyclic_dfs(graph)
    verified = verify_dag(graph, kept)
    write_json(folder / 'branches-acyclic.json', kept)
    write_json(folder / 'branches-cycles-excluded.json', removed)
    status = dict(nodes=len(graph['nodes']), original_edges=len(graph['edges']),
                  retained_edges=len(kept), excluded_back_edges=len(removed),
                  unresolved_retained_edges=sum(graph['edges'][i]['resolution'] != 'static' for i in kept),
                  max_depth=None, max_nodes=None, graph_sha256=digest(raw),
                  source_sha256=graph['source_sha256'], component_roots=roots,
                  independently_verified_DAG_nodes=verified,
                  seconds=round(time.monotonic()-start, 3),
                  complete_static_edge_partition=True, complete_game_semantics=False,
                  method='DFS back-edge exclusion; convergent completed-node edges retained; order dependent')
    write_json(folder / 'branches-acyclic-status.json', status)
    print(json.dumps({k: v for k, v in status.items() if k != 'component_roots'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path, nargs='?', default=Path('data/static-story'))
    run(parser.parse_args().folder)
