"""Objective accessibility checks that need no browser: WCAG contrast of every theme's colors."""
import re
import unittest
from pathlib import Path

TEMPLATE = (Path(__file__).resolve().parents[1] / 'repotrace' / 'report' / 'dashboard_template.html').read_text(encoding='utf-8')

# (foreground, background) pairs that really appear together in the page, with the WCAG minimum.
TEXT_PAIRS = [('ink', 'bg'), ('ink', 'panel'), ('muted', 'bg'), ('muted', 'panel'), ('accent-ink', 'accent'),
              ('sup', 'sup-bg'), ('pos', 'pos-bg'), ('weak', 'weak-bg'), ('warn', 'warn-bg'), ('ink', 'soft'),
              ('ink', 'code'), ('muted', 'code'), ('ink', 'add'), ('ink', 'del'), ('ink', 'hl'), ('ink', 'weak-bg'),
              ('ink', 'warn-bg'), ('accent', 'bg'), ('accent', 'panel'), ('sup', 'panel'), ('warn', 'panel'), ('pos', 'panel')]
LINE_PAIRS = [('sup', 'bg'), ('pos', 'bg'), ('weak', 'bg'), ('accent', 'bg'), ('sup', 'panel'), ('pos', 'panel'), ('ctl', 'bg'), ('ctl', 'panel')]


def theme_tokens():
    themes = {}
    for name, pattern in (('light', r':root,:root\[data-theme="light"\]\{([^}]*)\}'), ('dark', r':root\[data-theme="dark"\]\{([^}]*)\}'),
                          ('contrast', r':root\[data-theme="contrast"\]\{([^}]*)\}')):
        block = re.search(pattern, TEMPLATE).group(1)
        themes[name] = dict(re.findall(r'--([a-z-]+):(#[0-9a-fA-F]{3,6})', block))
    auto = re.search(r'@media \(prefers-color-scheme:dark\)\{:root\[data-theme="auto"\]\{([^}]*)\}\}', TEMPLATE).group(1)
    themes['auto-dark'] = dict(re.findall(r'--([a-z-]+):(#[0-9a-fA-F]{3,6})', auto))
    return themes


def luminance(hex_color):
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class ContrastTests(unittest.TestCase):
    def test_every_theme_defines_the_same_tokens(self):
        themes = theme_tokens()
        keys = set(themes['light'])
        for name, tokens in themes.items():
            self.assertEqual(set(tokens), keys, name)

    def test_text_meets_wcag_aa_in_every_theme(self):
        for name, tokens in theme_tokens().items():
            for fg, bg in TEXT_PAIRS:
                self.assertGreaterEqual(ratio(tokens[fg], tokens[bg]), 4.5, f'{name}: {fg} on {bg} = {ratio(tokens[fg], tokens[bg]):.2f}')

    def test_lines_and_borders_meet_3_to_1_for_the_graph(self):
        for name, tokens in theme_tokens().items():
            for fg, bg in LINE_PAIRS:
                minimum = 3.0
                self.assertGreaterEqual(ratio(tokens[fg], tokens[bg]), minimum, f'{name}: {fg} on {bg} = {ratio(tokens[fg], tokens[bg]):.2f}')

    def test_controls_have_visible_borders(self):
        self.assertIn('input[type=text],select,.seg,.tab,.gbtn{border-color:var(--ctl)}', TEMPLATE)

    def test_high_contrast_theme_reaches_aaa(self):
        tokens = theme_tokens()['contrast']
        for fg, bg in [('ink', 'bg'), ('ink', 'panel'), ('muted', 'bg'), ('accent-ink', 'accent'), ('sup', 'sup-bg'), ('pos', 'pos-bg'), ('warn', 'warn-bg')]:
            self.assertGreaterEqual(ratio(tokens[fg], tokens[bg]), 7.0, f'{fg} on {bg} = {ratio(tokens[fg], tokens[bg]):.2f}')


if __name__ == '__main__':
    unittest.main()
