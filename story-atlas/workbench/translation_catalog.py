"""Reference-only translation index. Run only on the SSH host."""
import argparse
from functools import lru_cache
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time

from extract import read_json, write_json, digest
from static_tree import Builder

TOKEN = re.compile(r'⟪P\d+⟫')
TAG = re.compile(r'<!--.*?-->|</?[A-Za-z!](?:"[^"]*"|\x27[^\x27]*\x27|[^\x27">])*>', re.S)
EXECUTABLE = re.compile(r'<(script|style)\b[^>]*>.*?</\1\s*>', re.I | re.S)
ENTITY = re.compile(r'&(?:[A-Za-z][\w]+|#\d+|#x[\da-fA-F]+);')


def protect(text):
    if TOKEN.search(text):
        raise ValueError('source_token_collision')
    pieces, protected, index = [], [], 0
    while index < len(text):
        end = None
        if text.startswith('<<', index):
            depth, cursor, quote = 1, index+2, None
            while cursor < len(text) and depth:
                char = text[cursor]
                if quote:
                    if char == quote:
                        if cursor+1 < len(text) and text[cursor+1] == quote:
                            cursor += 2
                            continue
                        quote = None
                elif char in "'\"":
                    quote = char
                elif text.startswith('<<', cursor):
                    depth += 1
                    cursor += 2
                    continue
                elif text.startswith('>>', cursor):
                    depth -= 1
                    cursor += 2
                    continue
                cursor += 1
            if depth:
                raise ValueError('unclosed_interpolation')
            end = cursor
        else:
            match = EXECUTABLE.match(text, index) or TAG.match(text, index) or ENTITY.match(text, index)
            if match:
                end = match.end()
        if end is None:
            pieces.append(text[index])
            index += 1
        else:
            token = f'⟪P{len(protected)}⟫'
            protected.append(text[index:end])
            pieces.append(token)
            index = end
    return ''.join(pieces), protected


def validate_target(source, target):
    protected, pieces = protect(source)
    if not isinstance(target, str) or not target.strip():
        raise ValueError('譯文不能為空')
    if TOKEN.findall(target) != TOKEN.findall(protected):
        raise ValueError('保留片段遺失、增加或順序改變')
    without = TOKEN.sub('', target)
    if TAG.search(without) or '<<' in without or 'exec:' in without.casefold():
        raise ValueError('譯文新增了未核准的標籤或程式片段')
    for index, value in enumerate(pieces):
        target = target.replace(f'⟪P{index}⟫', value)
    return target


class Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('br', 'p', 'div', 'tr', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def visible(text):
    value, _ = protect(text)
    # Tags and interpolation are already tokens, not prose to be translated.
    return TOKEN.sub('', value)


def length(text):
    return sum(not char.isspace() for char in text)


class Sources:
    def __init__(self, data):
        self.data = Path(data)

    @lru_cache(maxsize=16)
    def location(self, file):
        return read_json(self.data / 'locations' / file)

    def value(self, unit):
        item = self.literal_index(unit['file']).get(unit['id'])
        if not item or item['source_sha256'] != unit['source_hash']:
            raise ValueError('Source literal hash changed')
        if unit.get('value_hash') and digest(item['value'].encode('utf-8', 'surrogatepass')) != unit['value_hash']:
            raise ValueError('Source value changed')
        return item['value']

    @lru_cache(maxsize=16)
    def literal_index(self, file):
        return {item['id']: item for item in self.location(file)['literals']}

    def conditions(self, unit):
        loc = self.location(unit['file'])
        field = unit['field']
        code = loc['code'] if field == 'code' else loc['actions'][int(field.split('.')[1])]['code'] if field.endswith('.code') else ''
        return [code[start:end] for start, end in unit.get('conditions', [])]


def build_catalog(data, output):
    started = time.monotonic()
    graph_bytes = (data / 'static-story/graph.json').read_bytes()
    graph = json.loads(graph_bytes)
    node_ids = {(n['location'].casefold(), n['event']): n['id'] for n in graph['nodes']}
    raw = read_json(data / 'graph.json')
    units, counts = [], {}
    for info in raw['nodes']:
        file = info['id'] + '.json'
        loc = read_json(data / 'locations' / file)
        mapped = {}

        def visitor(location, event, items, conditions):
            first = items[0]['value'].casefold()
            command = '*' + items[1]['value'].casefold() if first == '*' and len(items) > 1 else first
            display = items[0]['kind'] == 'string' or command in ('*p', '*pl', '*nl', 'p', 'pl', 'nl', 'msg', 'act')
            depth, candidates = 0, []
            for token in items:
                if token['kind'] == 'symbol' and token['value'] in ('(', '['):
                    depth += 1
                elif token['kind'] == 'symbol' and token['value'] in (')', ']'):
                    depth -= 1
                elif token['kind'] == 'string':
                    selected = display and depth == 0 and (command != 'act' or not candidates)
                    candidates.append(token['start'])
                    mapped[(field, token['start'])] = dict(node=node_ids.get((location.casefold(), event)),
                                                           selected=selected, conditions=conditions)

        builder = Builder(visitor)
        field = 'code'
        builder.scan(loc['name'], loc['code'])
        for index, action in enumerate(loc['actions']):
            field = f'actions.{index}.code'
            builder.scan(loc['name'], action['code'], action['description'])
        for item in loc['literals']:
            if item['classification'] != 'display_candidate':
                continue
            mapping = mapped.get((item['field'], item.get('start')), {})
            node = mapping.get('node', node_ids.get((loc['name'].casefold(), '')))
            flags = []
            if re.search(r'<(?:script|style)\b', item['value'], re.I):
                flags.append('executable_markup_requires_review')
            if item['kind'] != 'description' and not mapping.get('selected'):
                flags.append('expression_requires_review')
            if not item.get('closed', True):
                flags.append('unclosed_literal')
            try:
                prose = visible(item['value'])
                count = length(prose)
            except ValueError as error:
                flags.append(str(error))
                count = 0
            if not count:
                flags.append('no_visible_text')
            if node is None:
                flags.append('unmapped_event')
            role = 'review' if flags else 'eligible'
            counts[role] = counts.get(role, 0) + 1
            units.append(dict(id=item['id'], node=node, file=file, field=item['field'],
                              source_hash=item['source_sha256'], characters=count,
                              value_hash=digest(item['value'].encode('utf-8', 'surrogatepass')),
                              role=role, flags=flags, conditions=mapping.get('conditions', [])))
    result = dict(version=1, graph_hash=digest(graph_bytes), source_hash=graph['source_sha256'],
                  units=units, summary=dict(counts, total=len(units), seconds=round(time.monotonic()-started, 2)),
                  limitations=['Static source units, not a proven continuous playthrough.',
                               'Nested expressions and ambiguous units require review; no automatic model calls.'])
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'catalog.json', result)
    return result['summary']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--output', type=Path, default=Path('data/translation'))
    args = parser.parse_args()
    print(json.dumps(build_catalog(args.data, args.output), indent=2))
