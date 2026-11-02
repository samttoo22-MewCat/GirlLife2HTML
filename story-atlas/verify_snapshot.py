"""Verify every exported file, source span, node/edge and progress row count."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys


def read(path):
    with gzip.open(path,'rt',encoding='utf-8') as file:return json.load(file)


def verify(base):
    summary=json.loads((base/'summary.json').read_text(encoding='utf-8'))
    manifest=json.loads((base/'manifest.json').read_text(encoding='utf-8'))
    paths=set()
    for item in manifest:
        rel=Path(item['path']);assert not rel.is_absolute() and '..' not in rel.parts
        file=base/rel;raw=file.read_bytes()
        assert len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256'],str(rel)
        assert item['path'] not in paths;paths.add(item['path'])
    assert paths=={str(p.relative_to(base)) for p in base.rglob('*') if p.is_file() and p.name!='manifest.json'}
    graph=read(base/'nodes/static-story/graph.json.gz');assert len(graph['nodes'])==19168 and len(graph['edges'])==141109
    # Static edge IDs are their array indices, not an `id` member.
    edge_ids=set(range(len(graph['edges'])));assert len(edge_ids)==141109
    all_ids=read(base/'nodes/static-story/branches-all.json.gz')
    kept=set(read(base/'nodes/static-story/branches-acyclic.json.gz'));cut=set(read(base/'nodes/static-story/branches-cycles-excluded.json.gz'))
    assert set(all_ids)==edge_ids and len(all_ids)==len(edge_ids) and not kept&cut and kept|cut==edge_ids
    extraction=read(base/'nodes/extraction-graph.json.gz');locations=list((base/'nodes/locations').glob('*.json.gz'))
    assert len(locations)==len(extraction['nodes'])==1397
    original_ids={node['id'] for node in extraction['nodes']};assert {p.name[:-8] for p in locations}==original_ids
    checked=0
    for path in locations:
        loc=read(path)
        for literal in loc['literals']:
            if literal['kind'] not in ('string','code_block'):continue
            code=loc['code'] if literal['field']=='code' else loc['actions'][int(literal['field'].split('.')[1])]['code']
            span=code[literal['start']:literal['end']]
            assert hashlib.sha256(span.encode('utf-8','surrogatepass')).hexdigest()==literal['source_sha256']
            checked+=1
    catalog=read(base/'nodes/translation-catalog.json.gz');assert len(catalog['units'])==248199
    assert Counter(u['role'] for u in catalog['units'])==dict(eligible=235431,review=12768)
    counts={};tables={}
    for table,expected in summary['table_rows'].items():
        rows=[]
        for file in sorted((base/'progress').glob(table+'-*.jsonl.gz')):
            with gzip.open(file,'rt',encoding='utf-8') as stream:
                rows.extend(json.loads(line) for line in stream)
        assert len(rows)==expected,(table,len(rows),expected);counts[table]=len(rows);tables[table]=rows
    units={u['id'] for u in catalog['units']};seen=set()
    for row in tables['translations']:
        assert row['unit_id'] in units
        key=(row['run_id'],row['unit_id']);assert key not in seen;seen.add(key)
    sid=summary['session']['id'];completed=sum(r['session_id']==sid and r['state']=='approved' for r in tables['branch_batches'])
    assert completed==summary['completed_in_current_round']
    assert sum(r['status']=='approved' for r in tables['batches'])==summary['approved_batches_all_rounds']
    report=dict(status='PASS',files=len(manifest),locations=len(locations),checked_literal_spans=checked,nodes=len(graph['nodes']),edges=len(edge_ids),catalog_units=len(units),current_round_completed=completed,table_rows=counts)
    print(json.dumps(report,ensure_ascii=False,indent=2));return report


if __name__=='__main__':verify(Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parent/'snapshot')
