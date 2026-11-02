"""SSH-host-only entrypoint: no depth/node limit, branch IDs only."""
import argparse
import json
from pathlib import Path
import time

from extract import write_json, digest
from static_tree import dfs_all


def run(folder):
    start = time.monotonic()
    raw = (folder / 'graph.json').read_bytes()
    graph = json.loads(raw)
    order, status = dfs_all(graph)
    status['source_sha256'] = graph['source_sha256']
    status['graph_sha256'] = digest(raw)
    status['seconds'] = round(time.monotonic() - start, 3)
    status['complete_static_edge_inventory'] = True
    status['complete_game_semantics'] = False
    write_json(folder / 'branches-all.json', order)
    write_json(folder / 'branches-all-status.json', status)
    result = {k: status[k] for k in ('nodes', 'edges', 'components', 'entry_component',
                                   'max_depth', 'max_nodes', 'seconds',
                                   'complete_static_edge_inventory', 'complete_game_semantics')}
    result['unresolved_edges'] = len(status['unresolved_edge_ids'])
    result['branch_ids_bytes'] = (folder / 'branches-all.json').stat().st_size
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path, nargs='?', default=Path('data/static-story'))
    run(parser.parse_args().folder)
