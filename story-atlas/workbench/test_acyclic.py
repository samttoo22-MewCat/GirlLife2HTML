import unittest

from run_static_acyclic import acyclic_dfs, verify_dag


def graph(edges, count):
    return dict(root=0, nodes=[dict(id=i) for i in range(count)],
                edges=[dict(source=a, target=b, resolution='static') for a, b in edges])


class AcyclicTests(unittest.TestCase):
    def test_back_edges_removed_and_all_original_ids_accounted(self):
        g = graph([(0, 0), (0, 1), (1, 2), (2, 0), (2, 1)], 3)
        kept, removed, _ = acyclic_dfs(g)
        self.assertEqual(kept, [1, 2])
        self.assertEqual(removed, [0, 3, 4])
        self.assertEqual(verify_dag(g, kept), 3)

    def test_convergent_branches_not_discarded_as_duplicates(self):
        g = graph([(0, 1), (1, 3), (0, 2), (2, 3)], 4)
        kept, removed, _ = acyclic_dfs(g)
        self.assertEqual(kept, [0, 1, 2, 3])
        self.assertFalse(removed)
        self.assertEqual(verify_dag(g, kept), 4)

    def test_disconnected_nodes_and_unresolved_edges_recorded(self):
        g = graph([(0, 1), (2, 1), (2, None), (3, 3)], 5)
        kept, removed, roots = acyclic_dfs(g)
        self.assertEqual(set(kept), {0, 1, 2})
        self.assertEqual(removed, [3])
        self.assertEqual(roots, [0, 2, 3, 4])
        self.assertEqual(verify_dag(g, kept), 5)

    def test_no_recursion_or_depth_cap(self):
        g = graph([(i, i+1) for i in range(5000)] + [(5000, 0)], 5001)
        kept, removed, _ = acyclic_dfs(g)
        self.assertEqual(len(kept), 5000)
        self.assertEqual(removed, [5000])
        self.assertEqual(verify_dag(g, kept), 5001)


if __name__ == '__main__':
    unittest.main()
