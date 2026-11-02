import unittest

from static_tree import Builder, dfs, dfs_all


class StaticTests(unittest.TestCase):
    def graph(self, sources):
        builder = Builder()
        for name in sources:
            builder.known_locations.add(name.casefold())
            builder.node(name, declared=True)
        for name, code in sources.items():
            builder.scan(name, code)
        return builder.finish('start')

    def test_event_and_action_choices(self):
        graph = self.graph({'start': "act 'Start': gt 'start','intro'\nif $ARGS[0] = 'intro':\nact 'Continue':\ngt 'room','door'\nend\nend", 'room': "if $ARGS[0]='door':\nact 'Back': gt 'start','intro'\nend"})
        names = {node['id']: node['name'] for node in graph['nodes']}
        edges = [(names[e['source']], names[e['target']], e['option']) for e in graph['edges']]
        self.assertIn(('start', 'start / intro', 'Start'), edges)
        self.assertIn(('start / intro', 'room / door', 'Continue'), edges)
        self.assertIn(('room / door', 'start / intro', 'Back'), edges)
        self.assertEqual(len(dfs(graph)['order']), 3)

    def test_html_exec_and_no_plain_narrative_commands(self):
        code = """*pl '<a href="exec:gt ''room'',''door''">Enter</a>'
*pl "gt 'not code'"
"""
        graph = self.graph({'start': code, 'room': "if $args[0]='door':\nend"})
        choices = [e for e in graph['edges'] if e['kind'] == 'choice']
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0]['option'], 'Enter')

    def test_dynamic_and_missing_targets_not_invented(self):
        graph = self.graph({'start': "act 'Computed': gt $place\nact 'Missing': gt 'unknown'"})
        self.assertEqual([e['resolution'] for e in graph['edges']], ['dynamic', 'missing_location'])
        self.assertTrue(all(e['target'] is None for e in graph['edges']))

    def test_cycle_is_reference_not_infinite_expansion(self):
        graph = self.graph({'start': "act 'Loop': gt 'start'"})
        result = dfs(graph, 30)
        self.assertEqual(result['order'], [graph['root']])
        self.assertEqual(len(result['edge_ids']), 1)

    def test_event_string_case_is_not_silently_merged(self):
        graph = self.graph({'start': "if $args[0]='Door':\nend\nif $args[0]='door':\nend"})
        self.assertIn('start / Door', [n['name'] for n in graph['nodes']])
        self.assertIn('start / door', [n['name'] for n in graph['nodes']])

    def test_later_short_path_reexpands_shared_node_with_more_budget(self):
        graph = dict(root=0, nodes=[dict(id=i) for i in range(5)], edges=[
            dict(source=0, target=1), dict(source=1, target=2),
            dict(source=0, target=2), dict(source=2, target=3), dict(source=3, target=4)])
        self.assertIn(4, dfs(graph, 3)['order'])

    def test_unbounded_forest_records_each_branch_id_once_including_cycles_disconnected(self):
        graph = self.graph({'start': "act 'Loop': gt 'start'\nact 'Next': gt 'room'", 'room': "gt 'start'", 'unlinked': "gt 'room'\ngt $unknown"})
        order, status = dfs_all(graph)
        self.assertEqual(order, [0, 1, 2, 3, 4])
        self.assertEqual(status['edges'], len(graph['edges']))
        self.assertEqual(status['nodes'], len(graph['nodes']))
        self.assertEqual(status['components'], 2)
        self.assertIsNone(status['max_depth'])
        self.assertIsNone(status['max_nodes'])
        self.assertTrue(all(isinstance(item, int) for item in order))

    def test_unbounded_dfs_handles_chain_deeper_than_python_recursion(self):
        graph = dict(root=0, nodes=[dict(id=i) for i in range(2001)], edges=[
            dict(source=i, target=i+1, resolution='static') for i in range(2000)])
        order, status = dfs_all(graph)
        self.assertEqual(len(order), 2000)
        self.assertEqual(status['nodes'], 2001)


if __name__ == '__main__':
    unittest.main()
