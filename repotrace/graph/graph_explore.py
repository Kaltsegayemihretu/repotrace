"""Explore the call graph around any function: who calls it, what it calls, layer by layer.

Works on a GraphIndex (source-free). Callers come from its reverse index; callees are derived
from the same edges, so both directions describe the same links and carry the same evidence tier.
"""
from collections import defaultdict, deque

TEST_MARKERS = ('tests', 'test')


def is_test_file(path):
    parts = path.replace('\\', '/').split('/')
    return any(p in TEST_MARKERS or p.startswith('test_') or p.endswith('_test.py') for p in parts)


def tier_of(evidence):
    return 'possible' if evidence['resolution'] == 'POSSIBLE' else 'supported'


class Explorer:
    def __init__(self, index):
        self.index = index
        self.forward = defaultdict(list)          # caller id -> [edge pairs]
        for pairs in index.reverse.values():
            for pair in pairs:
                self.forward[pair['source']].append(pair)
        for pairs in self.forward.values():
            pairs.sort(key=lambda p: p['target'])

    def node(self, symbol_id):
        s = self.index.symbols[symbol_id]
        return {'id': symbol_id, 'name': s['qualified_name'], 'file': s['file'], 'line': s['span']['line'],
                'kind': s['kind'], 'test': is_test_file(s['file']),
                'callers': len(self.index.callers_of(symbol_id)), 'callees': len(self.forward.get(symbol_id, ())),
                'unresolved_outgoing': self.index.unresolved_by_source.get(symbol_id, 0)}

    def search(self, text, limit=30):
        text = text.strip().lower()
        if not text:
            return []
        scored = []
        for symbol_id, s in self.index.symbols.items():
            if s['kind'] == 'CLASS':
                continue
            name, file = s['qualified_name'].lower(), s['file'].lower()
            if text in name or text in file:
                last = name.rsplit('.', 1)[-1]
                rank = 0 if last == text else 1 if last.startswith(text) else 2 if text in last else 3
                scored.append((rank, is_test_file(s['file']), len(name), name, symbol_id))
        scored.sort()
        return [self.node(item[-1]) for item in scored[:limit]]

    def neighbors(self, symbol_id, direction='both', depth=1, limit=80):
        """Nodes and edges within `depth` steps. Callers get negative layers, callees positive."""
        if symbol_id not in self.index.symbols:
            raise KeyError(symbol_id)
        nodes, edges, truncated = {symbol_id: 0}, {}, False
        for sign, adjacent in ((-1, self.index.callers_of), (1, lambda i: self.forward.get(i, ()))):
            if direction not in ('both', 'callers' if sign < 0 else 'callees'):
                continue
            queue, seen = deque([(symbol_id, 0)]), {symbol_id}
            while queue:
                current, distance = queue.popleft()
                if distance >= depth:
                    continue
                for pair in adjacent(current):
                    other = pair['source'] if sign < 0 else pair['target']
                    edges[(pair['source'], pair['target'])] = {
                        'source': pair['source'], 'target': pair['target'], 'tier': tier_of(pair['evidence']),
                        'calls': pair['call_site_count'], 'line': pair['evidence']['span']['line'],
                        'file': pair['evidence']['file']}
                    if other in seen:
                        continue
                    if len(nodes) >= limit:
                        truncated = True
                        continue
                    seen.add(other)
                    nodes.setdefault(other, sign * (distance + 1))
                    queue.append((other, distance + 1))
        edges = {k: e for k, e in edges.items() if e['source'] in nodes and e['target'] in nodes}
        return {'root': symbol_id, 'nodes': [{**self.node(i), 'layer': layer} for i, layer in nodes.items()],
                'edges': list(edges.values()), 'truncated': truncated}
