import unittest

from repotrace.evaluation.change_score import parse_truth

SAMPLE = '''
## step-02 — x

**Changed (AST-level):**
- modified:
  - `a/b.py`: `f`, `C.m`, `<module>`
- added:
  - `a/b.py`: `g`, `K <class>`

**Affected, direct** (callers):
- `a/c.py`: `h`, `logged.<locals>.wrapper`

**Affected, indirect** (further; 2 functions):
- `a/d.py`: `i`
- `scripts/x.py`: `main`

---

## step-10 — broken

**Changed (AST-level):**
- added:
  - `a/p.py`: `<UNPARSEABLE FILE>`

**Affected, direct** (callers):
- (none)
'''


class ParseTests(unittest.TestCase):
    def test_sections_kinds_and_special_names(self):
        steps = parse_truth(SAMPLE)
        s = steps[2]
        self.assertEqual(s['changed']['modified'], {('a/b.py', 'f'), ('a/b.py', 'C.m')})
        self.assertEqual(s['changed']['added'], {('a/b.py', 'g')})
        self.assertEqual(s['module_level'], {'a/b.py'})
        self.assertEqual(s['direct'], {('a/c.py', 'h'), ('a/c.py', 'logged.wrapper')})
        self.assertEqual(s['indirect'], {('a/d.py', 'i'), ('scripts/x.py', 'main')})
        self.assertEqual(steps[10]['unparseable'], ['a/p.py'])
        self.assertEqual(steps[10]['direct'], set())


if __name__ == '__main__':
    unittest.main()
