"""Read-only QSPGAME inventory. Static references, NOT an executable story AST.

Format references: QSPFoundation/qsp qsp/game.c (qspOpenGame),
qsp/coding.c (qspDecodeString), qsp/coding.h (QSP_CODREMOV=5).
No external packages, model calls, script execution, or source replacement.
"""
from __future__ import annotations

import argparse
from array import array
from collections import Counter
import hashlib
import gzip
import json
from pathlib import Path
import re
import shutil
import sys


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    path = Path(path)
    if path.is_file():
        return json.loads(path.read_bytes())
    return json.loads(gzip.decompress(path.with_name(path.name + '.gz').read_bytes()))


def crypt(text, decode=True):
    # Shift UTF-16 code units, not Unicode code points (including astral text).
    units = array('H')
    units.frombytes(text.encode('utf-16-le', 'surrogatepass'))
    if sys.byteorder != 'little':
        units.byteswap()
    for i, ch in enumerate(units):
        if decode:
            units[i] = 5 if ch == 65531 else ((ch + 5) & 65535 if ch else 0)
        else:
            units[i] = 65531 if ch == 5 else ((ch - 5) & 65535 if ch else 0)
    if sys.byteorder != 'little':
        units.byteswap()
    return units.tobytes().decode('utf-16-le', 'surrogatepass')


def parse(data):
    if not data.startswith('QSPGAME\r\n'.encode('utf-16-le')):
        raise ValueError('Only UTF-16LE QSPGAME containers are supported; source left untouched')
    parts = data.decode('utf-16-le', 'surrogatepass').split('\r\n')
    if len(parts) < 5 or parts[-1] != '':
        raise ValueError('Truncated QSP container')
    count = int(crypt(parts[3]))
    if not 0 < count <= 100000:
        raise ValueError('Invalid location count')
    cursor = 4
    locations = []

    def take():
        nonlocal cursor
        if cursor >= len(parts) - 1:
            raise ValueError('Truncated location/action data')
        result = crypt(parts[cursor])
        cursor += 1
        return result

    for _ in range(count):
        name, description, code = take(), take(), take()
        actions_count = int(take())
        if not 0 <= actions_count <= 1000:
            raise ValueError('Invalid action count')
        actions = [dict(zip(('image', 'description', 'code'), (take(), take(), take())))
                   for _ in range(actions_count)]
        locations.append(dict(name=name, description=description, code=code, actions=actions))
    if cursor != len(parts) - 1:
        raise ValueError('Unconsumed QSP fields')
    return parts[:3], locations


def rebuild(header, locations):
    parts = list(header) + [crypt(str(len(locations)), False)]
    for loc in locations:
        fields = [loc['name'], loc['description'], loc['code'], str(len(loc['actions']))]
        for act in loc['actions']:
            fields.extend(act[k] for k in ('image', 'description', 'code'))
        parts.extend(crypt(s, False) for s in fields)
    return ('\r\n'.join(parts) + '\r\n').encode('utf-16-le', 'surrogatepass')


WORD = re.compile(r'[$\w]+')


def lex(code):
    """Strings/brace blocks are opaque; doubled quotes are QSP escaping.

    Offsets are Python Unicode character offsets in the preserved code field.
    Multiline strings and comments are never scanned as direct navigation code.
    """
    i, line = 0, 1
    while i < len(code):
        start, start_line = i, line
        ch = code[i]
        if ch in ' \t\r':
            i += 1
            continue
        if ch == '\n':
            i += 1
            line += 1
            yield dict(kind='separator', value='\n', start=start, end=i, line=start_line)
            continue
        if ch == '!':
            end = code.find('\n', i)
            i = len(code) if end < 0 else end
            yield dict(kind='comment', value=code[start:i], start=start, end=i, line=start_line)
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            value = []
            closed = False
            while i < len(code):
                if code[i] == quote:
                    if i + 1 < len(code) and code[i + 1] == quote:
                        value.append(quote)
                        i += 2
                        continue
                    i += 1
                    closed = True
                    break
                value.append(code[i])
                i += 1
            line += code[start:i].count('\n')
            yield dict(kind='string', value=''.join(value), start=start, end=i,
                       line=start_line, closed=closed)
            continue
        if ch == '{':
            depth = 1
            i += 1
            while i < len(code) and depth:
                depth += (code[i] == '{') - (code[i] == '}')
                i += 1
            line += code[start:i].count('\n')
            yield dict(kind='code_block', value=code[start:i], start=start, end=i,
                       line=start_line, closed=depth == 0)
            continue
        m = WORD.match(code, i) if ch.isalnum() or ch in '_$' else None
        if m:
            i += len(m[0])
            yield dict(kind='word', value=m[0], start=start, end=i, line=start_line)
        else:
            i += 1
            yield dict(kind='separator' if ch in ':&' else 'symbol', value=ch,
                       start=start, end=i, line=start_line)


NAV = {'gt', 'goto', 'xgt', 'xgoto', 'gs', 'gosub', 'func'}
DYNAMIC = {'dynamic', 'dyneval', 'jump', 'inclib', 'openqst'}
DISPLAY = {'*p', '*pl', '*nl', 'p', 'pl', 'nl', 'msg', 'act', 'addobj', 'addqst'}


def analyze(code, owner, field):
    tokens = list(lex(code))
    literals, edges, warnings = [], [], []
    statement = []
    # Store enclosing branch headers as evidence only; not evaluated conditions.
    stack = []

    def process(items):
        nonlocal stack
        if not items:
            return
        text = code[items[0]['start']:items[-1]['end']]
        first = items[0]['value'].lower()
        if first in ('end', 'else', 'elseif') and stack:
            stack.pop()
        if first in ('if', 'elseif', 'else', 'act', 'loop', 'for'):
            stack.append([field, items[0]['start'], items[-1]['end']])
        prefix = ''.join(t['value'] for t in items[:2]).lower()
        for j, t in enumerate(items):
            if t['kind'] in ('string', 'code_block'):
                before = items[j-1]['value'].lower() if j else ''
                if t['kind'] == 'code_block':
                    classification = 'deferred_code'
                elif before in NAV:
                    classification = 'location_reference'
                elif prefix.startswith(('*p', '*nl')) or first in DISPLAY or j == 0:
                    classification = 'display_candidate'
                else:
                    classification = 'needs_review'
                literals.append(dict(id=owner + ':' + field + ':' + str(t['start']),
                                     field=field, **t, classification=classification,
                                     source_sha256=digest(code[t['start']:t['end']].encode('utf-8', 'surrogatepass')),
                                     translation_status='pending'))
                if not t['closed']:
                    warnings.append(dict(field=field, offset=t['start'], reason='unclosed_literal'))
            command = t['value'].lower()
            # Commands must occur outside strings/comments. Function calls may
            # occur in expressions; other matches are conservative candidates.
            if t['kind'] != 'word' or command not in NAV | DYNAMIC:
                continue
            args = items[j+1:]
            if args and args[0]['value'] == '(':
                args = args[1:]
            target = args[0] if args else None
            tail = args[1]['value'] if len(args) > 1 else None
            static = (command in NAV and target and target['kind'] == 'string'
                      and target['closed'] and '<<' not in target['value']
                      and tail in (None, ',', ')'))
            edges.append(dict(source=owner, field=field, line=t['line'], offset=t['start'],
                              command=command, target_name=target['value'] if static else None,
                              resolution='static_candidate' if static else 'dynamic',
                              statement_span=[items[0]['start'], items[-1]['end']],
                              branch_headers=list(stack[-8:]), branch_context_truncated=len(stack) > 8,
                              evidence='lexical_candidate'))
    for t in tokens:
        if t['kind'] == 'comment':
            continue
        if t['kind'] == 'separator':
            process(statement)
            statement = []
        else:
            statement.append(t)
    process(statement)
    return literals, edges, warnings


def export(source, output):
    if output.exists():
        raise ValueError('Output exists; choose a new directory to preserve earlier exports')
    data = source.read_bytes()
    source_hash = digest(data)
    header, locations = parse(data)
    if rebuild(header, locations) != data:
        raise ValueError('Round-trip differs; refusing to export an unverified decode')
    output.mkdir(parents=True)
    (output / 'locations').mkdir()
    names, nodes, edges, warnings = {}, [], [], []
    counts = Counter()
    for index, loc in enumerate(locations):
        node_id = 'loc-' + digest(loc['name'].casefold().encode('utf-8', 'surrogatepass'))[:20]
        if loc['name'].casefold() in names:
            # Do not merge duplicate names: QSP loads the first; preserve others.
            node_id += '-duplicate-' + str(index)
            warnings.append(dict(node=node_id, reason='duplicate_location_name'))
        else:
            names[loc['name'].casefold()] = node_id
        loc['id'] = node_id
        loc['index'] = index
    for loc in locations:
        literals, local_edges, local_warnings = analyze(loc['code'], loc['id'], 'code')
        for index, act in enumerate(loc['actions']):
            ls, es, ws = analyze(act['code'], loc['id'], 'actions.' + str(index) + '.code')
            literals.extend(ls)
            local_edges.extend(es)
            local_warnings.extend(ws)
        for field, value in [('description', loc['description'])] + [
                ('actions.' + str(i) + '.description', a['description']) for i, a in enumerate(loc['actions'])]:
            if value:
                literals.append(dict(id=loc['id'] + ':' + field, field=field,
                                     kind='description', value=value, classification='display_candidate',
                                     translation_status='pending', source_sha256=digest(value.encode('utf-8', 'surrogatepass'))))
        counts.update(x['classification'] for x in literals)
        loc['literals'] = literals
        loc['warnings'] = local_warnings
        nodes.append(dict(id=loc['id'], name=loc['name'], index=loc['index'],
                          literals=len(literals), actions=len(loc['actions']), warnings=len(local_warnings)))
        edges.extend(local_edges)
        warnings.extend(dict(node=loc['id'], **w) for w in local_warnings)
        write_json(output / 'locations' / (loc['id'] + '.json'), loc)
    for index, edge in enumerate(edges):
        edge['id'] = 'edge-' + str(index)
        name = edge['target_name']
        edge['target'] = names.get(name.casefold()) if name is not None else None
        if name is not None:
            edge['resolution'] = 'resolved_location' if edge['target'] else 'missing_location'
    summary = dict(locations=len(nodes), edges=len(edges), literals=sum(counts.values()),
                   classifications=dict(counts), resolutions=dict(Counter(e['resolution'] for e in edges)),
                   warnings=len(warnings), source_bytes=len(data), source_sha256=source_hash,
                   round_trip_byte_identical=True)
    graph = dict(schema_version=1, source=dict(filename=source.name, sha256=source_hash),
                 entry=nodes[0]['id'], summary=summary, nodes=nodes, edges=edges,
                 limitations=['Static location-reference graph, not an executable story tree.',
                              'Branch headers are lexical evidence, not proven path conditions.',
                              'Dynamic targets, exec links, brace code and interpolated strings require review.',
                              'All source code/description/action fields are preserved; display candidates are NOT approved translations.'],
                 warnings=warnings)
    write_json(output / 'graph.json', graph)
    # Header metadata is necessary for byte-identical reconstruction, not story text.
    write_json(output / 'container.json', dict(header=header, source_sha256=source_hash))
    if digest(source.read_bytes()) != source_hash:
        raise ValueError('Source changed during export')
    return summary


def write_json(path, obj):
    # ensure_ascii also preserves any unpaired UTF-16 surrogate without data loss.
    encoded = json.dumps(obj, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
    if shutil.disk_usage(path.parent).free - len(encoded) < 512 * 1024 * 1024:
        raise ValueError('Insufficient disk space: keeping a 512 MiB safety reserve; partial export is not complete')
    path.write_bytes(encoded)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(export(args.source, args.output), indent=2))
