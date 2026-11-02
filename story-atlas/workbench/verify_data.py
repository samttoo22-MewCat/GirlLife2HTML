"""Verify all JSON locations reconstruct the original QSP byte-for-byte."""
import argparse
import json
from pathlib import Path

from extract import digest, rebuild, read_json


def verify(source, data):
    graph = read_json(data / 'graph.json')
    container = read_json(data / 'container.json')
    locations = [read_json(data / 'locations' / (node['id'] + '.json'))
                 for node in graph['nodes']]
    original = source.read_bytes()
    assert digest(original) == graph['source']['sha256'] == container['source_sha256']
    assert rebuild(container['header'], locations) == original, 'Exported JSON does not reconstruct the source'
    ids = {n['id'] for n in graph['nodes']}
    assert len(ids) == len(graph['nodes']) == len(locations)
    for edge in graph['edges']:
        assert edge['source'] in ids
        assert edge['target'] is None or edge['target'] in ids
    count = 0
    for loc in locations:
        for literal in loc['literals']:
            if literal['kind'] not in ('string', 'code_block'):
                continue
            if literal['field'] == 'code':
                code = loc['code']
            else:
                code = loc['actions'][int(literal['field'].split('.')[1])]['code']
            raw = code[literal['start']:literal['end']]
            assert digest(raw.encode('utf-8', 'surrogatepass')) == literal['source_sha256']
            count += 1
    print(json.dumps(dict(status='PASS', locations=len(locations), checked_literal_spans=count,
                          edges=len(graph['edges']), source_sha256=digest(original),
                          exported_json_roundtrip_byte_identical=True), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('data', type=Path)
    args = parser.parse_args()
    verify(args.source, args.data)
