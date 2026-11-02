"""Independent BFS cross-check of bounded static DFS. Run on the SSH host."""
import argparse
from collections import deque
import json
from pathlib import Path


def verify(folder):
    graph = json.loads((folder / 'graph.json').read_text())
    dfs = json.loads((folder / 'dfs-depth-30.json').read_text())
    ids = {node['id'] for node in graph['nodes']}
    assert len(ids) == len(graph['nodes'])
    assert all('text' not in node and 'code' not in node for node in graph['nodes'])
    outgoing = {}
    for index, edge in enumerate(graph['edges']):
        assert edge['source'] in ids
        assert edge['target'] is None or edge['target'] in ids
        outgoing.setdefault(edge['source'], []).append((index, edge))
    distance = {graph['root']: 0}
    queue = deque([graph['root']])
    while queue:
        node = queue.popleft()
        if distance[node] >= dfs['max_depth']:
            continue
        for _, edge in outgoing.get(node, []):
            target = edge['target']
            if target is not None and target not in distance:
                distance[target] = distance[node] + 1
                queue.append(target)
    expected = {index for node, depth in distance.items() if depth < dfs['max_depth']
                for index, _ in outgoing.get(node, [])}
    assert set(distance) == set(dfs['order'])
    assert expected == set(dfs['edge_ids'])
    print(json.dumps(dict(status='PASS', nodes=len(ids), edges=len(graph['edges']),
                          reachable_nodes=len(distance), reachable_edges=len(expected),
                          max_depth=dfs['max_depth'], independent_BFS_matches_DFS=True)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path, nargs='?', default=Path('data/static-story'))
    verify(parser.parse_args().folder)
