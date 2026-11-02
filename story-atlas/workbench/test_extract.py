import unittest

from extract import analyze, crypt, parse, rebuild


class ExtractionTests(unittest.TestCase):
    def test_codec_utf16_units(self):
        for text in ['English 繁體中文 😀', '\x00\x05\r\n', '\ud800']:
            self.assertEqual(crypt(crypt(text, False)), text)

    def test_container_roundtrip(self):
        locs = [dict(name='start', description='Welcome', code="*pl 'Hello'\nact 'Go': gt 'room'",
                     actions=[dict(image='img.jpg', description='Continue', code="gs 'room'")]),
                dict(name='room', description='', code='', actions=[])]
        data = rebuild(['QSPGAME', 'test', ''], locs)
        header, parsed = parse(data)
        self.assertEqual(parsed, locs)
        self.assertEqual(rebuild(header, parsed), data)

    def test_truncated_and_foreign_formats_rejected(self):
        for data in [b'garbage', 'QSPGAME\r\nx\r\ny\r\n'.encode('utf-16-le')]:
            with self.assertRaises(ValueError):
                parse(data)

    def test_static_dynamic_missing_and_quotes(self):
        code = "! gt 'ignored'\n*pl 'gt ''not a call'''\ngt 'room'\ngs $place\ngt 'pre' + $suffix\ngt '<<$place>>'\ngs 'O''Brien'"
        literals, edges, _ = analyze(code, 'loc', 'code')
        self.assertEqual(len(edges), 5)
        self.assertEqual([e['target_name'] for e in edges], ['room', None, None, None, "O'Brien"])
        for t in literals:
            self.assertTrue(t['closed'])
            self.assertTrue(code[t['start']:t['end']])

    def test_multiline_and_embedded_exec_preserved_not_executed(self):
        text = "*pl 'first\nsecond <a href=\"exec:gt ''other''\">Go</a>'\n{gt 'later'}"
        literals, edges, warnings = analyze(text, 'loc', 'code')
        self.assertEqual(len(literals), 2)
        self.assertFalse(edges)
        self.assertFalse(warnings)
        self.assertIn('exec:', literals[0]['value'])
        self.assertEqual(literals[1]['classification'], 'deferred_code')

    def test_branch_evidence(self):
        _, edges, _ = analyze("if score > 2:\ngt 'room'\nend", 'loc', 'code')
        self.assertEqual(edges[0]['branch_headers'], [['code', 0, 12]])

    def test_unclosed_string_flagged(self):
        _, _, warnings = analyze("*pl 'unclosed", 'loc', 'code')
        self.assertEqual(warnings[0]['reason'], 'unclosed_literal')

    def test_branch_evidence_uses_spans_not_repeated_long_text(self):
        code = "act '" + ('long label ' * 10000) + "':\ngt 'room'\nend"
        literals, edges, _ = analyze(code, 'loc', 'code')
        self.assertNotIn('branch_headers', literals[0])
        self.assertEqual(edges[0]['branch_headers'][0][0], 'code')
        self.assertLess(len(str(edges)), 1000)


if __name__ == '__main__':
    unittest.main()
