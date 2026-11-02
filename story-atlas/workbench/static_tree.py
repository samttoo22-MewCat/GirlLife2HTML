"""Build a names/references-only static QSP event graph from the EXISTING export.

No game execution. Conditional reachability and dynamic targets remain unproven.
"""
import argparse
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import time

from extract import lex, NAV, DYNAMIC, digest, write_json, read_json


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.active = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == 'a':
            self.active = [attrs.get('href', ''), []]
        elif tag.lower() == 'img' and self.active:
            self.active[1].append(attrs.get('alt', ''))

    def handle_data(self, data):
        if self.active:
            self.active[1].append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self.active:
            self.links.append((self.active[0], ''.join(self.active[1]).strip() or '查看'))
            self.active = None


def event_value(tokens):
    for i in range(len(tokens) - 5):
        if (tokens[i]['value'].casefold() in ('$args', 'args') and
                [x['value'] for x in tokens[i+1:i+5]] == ['[', '0', ']', '=']):
            token = tokens[i+5]
            if token['kind'] == 'string' and '<<' not in token['value']:
                return token['value']
    return None


def arguments(tokens):
    stop = next((i for i, token in enumerate(tokens) if token['kind'] == 'separator'), len(tokens))
    args = tokens[1:stop]
    if args and args[0]['value'] == '(':
        args = args[1:]
    if not args or args[0]['kind'] != 'string' or '<<' in args[0]['value']:
        return None, None, 'dynamic'
    name = args[0]['value']
    if len(args) > 1 and args[1]['value'] not in (',', ')'):
        return None, None, 'dynamic'
    event = ''
    if len(args) > 2 and args[1]['value'] == ',':
        token = args[2]
        if token['kind'] == 'string' and '<<' not in token['value'] and (len(args) < 4 or args[3]['value'] in (',', ')')):
            event = token['value']
        else:
            return name, None, 'dynamic_event'
    return name, event, 'static'


class Builder:
    def __init__(self, visitor=None):
        self.nodes, self.edges, self.lookup = [], [], {}
        self.known_locations = set()
        self.visitor = visitor

    def node(self, location, event='', declared=False):
        # Location lookup is case-insensitive; do not silently merge event
        # string values which differ by case.
        key = (location.casefold(), event)
        if key not in self.lookup:
            index = len(self.nodes)
            self.lookup[key] = index
            self.nodes.append(dict(id=index, name=location + (' / ' + event if event else ''),
                                   location=location, event=event, declared=declared))
        index = self.lookup[key]
        if declared:
            self.nodes[index]['declared'] = True
        return index

    def scan(self, location, code, action=None):
        tokens = list(lex(code))
        statements, current = [], []
        for token in tokens:
            if token['kind'] == 'comment':
                continue
            if token['kind'] == 'separator':
                if current:
                    statements.append((current, token['value']))
                current = []
            else:
                current.append(token)
        if current:
            statements.append((current, '\n'))
        stack = []
        for index, (items, delimiter) in enumerate(statements):
            line = items[0]['line']
            stack = [block for block in stack if block['inline'] is None or block['inline'] >= line]
            first = items[0]['value'].casefold()
            if first == 'end':
                if stack:
                    stack.pop()
                continue
            if first in ('else', 'elseif'):
                for n in range(len(stack)-1, -1, -1):
                    if stack[n]['kind'] == 'if':
                        stack = stack[:n]
                        break
            if first in ('if', 'elseif', 'else', 'act', 'loop', 'for'):
                event = event_value(items) if first in ('if', 'elseif') else None
                if event is not None:
                    self.node(location, event, True)
                label = next((t['value'] for t in items[1:] if t['kind'] == 'string'), None) if first == 'act' else None
                next_line = statements[index+1][0][0]['line'] if index+1 < len(statements) else -1
                stack.append(dict(kind='if' if first in ('if', 'elseif', 'else') else first,
                                  event=event, action=label,
                                  span=[items[0]['start'], items[-1]['end']],
                                  inline=line if delimiter == ':' and next_line == line else None))
            event = next((b['event'] for b in reversed(stack) if b['event'] is not None), '')
            label = next((b['action'] for b in reversed(stack) if b['action'] is not None), action)
            source = self.node(location, event, True)
            if self.visitor:
                self.visitor(location, event, items, [b['span'] for b in stack if b['kind'] == 'if'])
            for position, token in enumerate(items):
                command = token['value'].casefold()
                if token['kind'] == 'word' and command in NAV | DYNAMIC:
                    target_location, target_event, resolution = arguments(items[position:]) if command in NAV else (None, None, 'dynamic')
                    self.edges.append(dict(source=source, target_location=target_location,
                                           target_event=target_event, resolution=resolution,
                                           kind='choice' if label is not None else ('transition' if command in ('gt', 'goto', 'xgt', 'xgoto') else 'call'),
                                           option=label))
                if token['kind'] == 'string' and 'exec:' in token['value'].lower():
                    parser = Links()
                    parser.feed(token['value'])
                    for href, title in parser.links:
                        if not href.casefold().startswith('exec:'):
                            continue
                        inner = list(lex(href[5:]))
                        for j, item in enumerate(inner):
                            if item['kind'] == 'word' and item['value'].casefold() in NAV:
                                dest, arg, resolution = arguments(inner[j:])
                                self.edges.append(dict(source=source, target_location=dest,
                                                       target_event=arg, resolution=resolution,
                                                       kind='choice', option=title))

    def finish(self, entry):
        for edge in self.edges:
            location, event = edge.pop('target_location'), edge.pop('target_event')
            edge['target'] = None
            if location is None:
                continue
            if location.casefold() not in self.known_locations:
                edge['resolution'] = 'missing_location'
                continue
            if event is None:
                edge['resolution'] = 'dynamic_event'
                continue
            edge['target'] = self.node(location, event)
            if not self.nodes[edge['target']]['declared']:
                edge['resolution'] = 'event_needs_review'
        # Do not merge by option title: identical text may have different conditions.
        return dict(version=1, root=self.node(entry), nodes=self.nodes, edges=self.edges,
                    limitations=['Static syntax candidates; conditions are not evaluated.',
                                 'Shared nodes/cycles are references, not repeated path text.',
                                 'Dynamic/generated code, computed targets and aliases may remain unresolved.'])


def dfs(graph, depth=30):
    outgoing = {}
    for index, edge in enumerate(graph['edges']):
        outgoing.setdefault(edge['source'], []).append(index)
    best, order, traversed = {}, [], set()
    stack = [(graph['root'], 0)]
    while stack:
        node, level = stack.pop()
        budget = depth - level
        if best.get(node, -1) >= budget:
            continue
        if node not in best:
            order.append(node)
        best[node] = budget
        if budget == 0:
            continue
        children = []
        for edge_id in outgoing.get(node, []):
            traversed.add(edge_id)
            target = graph['edges'][edge_id]['target']
            if target is not None:
                children.append((target, level+1))
        stack.extend(reversed(children))
    return dict(root=graph['root'], max_depth=depth, order=order, edge_ids=sorted(traversed),
                scope='reachable static graph through 30 edges, not every runtime state/path sequence')


def dfs_all(graph):
    """Iterative DFS forest: every static edge ID once, no depth/node cap.

    Cycles/shared destinations are references in graph.json. Disconnected
    components are included after the source-entry component.
    """
    outgoing = {}
    for index, edge in enumerate(graph['edges']):
        outgoing.setdefault(edge['source'], []).append(index)
    visited, order, roots = set(), [], []
    root_counts = None
    for root in [graph['root']] + [node['id'] for node in graph['nodes']]:
        if root in visited:
            continue
        roots.append(root)
        visited.add(root)
        stack = [iter(outgoing.get(root, []))]
        while stack:
            try:
                edge_id = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            order.append(edge_id)
            target = graph['edges'][edge_id]['target']
            if target is not None and target not in visited:
                visited.add(target)
                stack.append(iter(outgoing.get(target, [])))
        if root_counts is None:
            root_counts = dict(nodes=len(visited), edges=len(order))
    assert len(order) == len(set(order)) == len(graph['edges'])
    assert len(visited) == len(graph['nodes'])
    return order, dict(nodes=len(visited), edges=len(order), components=len(roots),
                       component_roots=roots, entry_component=root_counts,
                       unresolved_edge_ids=[i for i, edge in enumerate(graph['edges'])
                                            if edge['resolution'] != 'static'],
                       max_depth=None, max_nodes=None,
                       scope='all extracted static edge IDs once; cycles referenced, not infinite repeated routes')


def build(data, output):
    start = time.monotonic()
    source = read_json(data / 'graph.json')
    builder = Builder()
    for info in source['nodes']:
        builder.known_locations.add(info['name'].casefold())
        builder.node(info['name'], declared=True)
    for info in source['nodes']:
        loc = read_json(data / 'locations' / (info['id'] + '.json'))
        builder.scan(loc['name'], loc['code'])
        for action in loc['actions']:
            builder.scan(loc['name'], action['code'], action['description'])
    entry = next(n['name'] for n in source['nodes'] if n['id'] == source['entry'])
    graph = builder.finish(entry)
    graph['source_sha256'] = source['source']['sha256']
    traversal = dfs(graph)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'graph.json', graph)
    write_json(output / 'dfs-depth-30.json', traversal)
    result = dict(nodes=len(graph['nodes']), edges=len(graph['edges']),
                  reachable_nodes_depth_30=len(traversal['order']),
                  reachable_edges_depth_30=len(traversal['edge_ids']),
                  resolutions=dict(Counter(e['resolution'] for e in graph['edges'])),
                  seconds=round(time.monotonic()-start, 2),
                  bytes=sum(p.stat().st_size for p in output.iterdir()))
    write_json(output / 'summary.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path(__file__).parent / 'data')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'data/static-story')
    args = parser.parse_args()
    print(json.dumps(build(args.data, args.output), indent=2))
