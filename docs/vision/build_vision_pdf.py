"""Builds docs/RepoTrace_Vision_and_Path_to_a_Whole_Product.pdf.

    python3 -m venv /tmp/pdfenv && /tmp/pdfenv/bin/pip install "reportlab==4.2.5" pillow
    /tmp/pdfenv/bin/python docs/vision/build_vision_pdf.py

Every number in the text comes from docs/PROJECT_HANDBOOK.md (measured or marked as claimed). Edit the text here, rebuild.
"""
import os
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, ListFlowable, ListItem, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs' / 'RepoTrace_Vision_and_Path_to_a_Whole_Product.pdf'
SUP = '/System/Library/Fonts/Supplemental/'
FONTS = {'Body': 'Arial.ttf', 'Body-B': 'Arial Bold.ttf', 'Body-I': 'Arial Italic.ttf', 'Body-BI': 'Arial Bold Italic.ttf', 'Mono': 'Courier New.ttf'}
if all(os.path.exists(SUP + f) for f in FONTS.values()):
    for name, f in FONTS.items():
        pdfmetrics.registerFont(TTFont(name, SUP + f))
    pdfmetrics.registerFontFamily('Body', normal='Body', bold='Body-B', italic='Body-I', boldItalic='Body-BI')
    BODY, BOLD, ITAL, MONO = 'Body', 'Body-B', 'Body-I', 'Mono'
else:                                                      # other machines: the built-in fonts
    BODY, BOLD, ITAL, MONO = 'Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique', 'Courier'

INK, MUTED, LINE = colors.HexColor('#1b2430'), colors.HexColor('#5b6776'), colors.HexColor('#d5dbe2')
TEAL, TEAL_BG = colors.HexColor('#1f7a6b'), colors.HexColor('#e6f3f0')
AMBER, AMBER_BG = colors.HexColor('#a8650b'), colors.HexColor('#fbf1de')
RED, RED_BG = colors.HexColor('#b23a30'), colors.HexColor('#fbe9e7')
PURPLE = colors.HexColor('#6b4fa3')
PANEL = colors.HexColor('#f4f6f8')
W, H = A4
LM = RM = 20 * mm
CW = W - LM - RM

S = {
    'body': ParagraphStyle('body', fontName=BODY, fontSize=9.6, leading=14, textColor=INK, spaceAfter=5),
    'small': ParagraphStyle('small', fontName=BODY, fontSize=8.2, leading=11.4, textColor=MUTED, spaceAfter=3),
    'cell': ParagraphStyle('cell', fontName=BODY, fontSize=8.4, leading=11.2, textColor=INK),
    'cellb': ParagraphStyle('cellb', fontName=BOLD, fontSize=8.4, leading=11.2, textColor=INK),
    'head': ParagraphStyle('head', fontName=BOLD, fontSize=8.2, leading=10.5, textColor=colors.white),
    'h1': ParagraphStyle('h1', keepWithNext=1, fontName=BOLD, fontSize=19, leading=23, textColor=INK, spaceBefore=2, spaceAfter=3),
    'kicker': ParagraphStyle('kicker', keepWithNext=1, fontName=BOLD, fontSize=8, leading=10, textColor=TEAL, spaceAfter=2),
    'h2': ParagraphStyle('h2', keepWithNext=1, fontName=BOLD, fontSize=12, leading=15.5, textColor=INK, spaceBefore=11, spaceAfter=4),
    'h3': ParagraphStyle('h3', keepWithNext=1, fontName=BOLD, fontSize=10, leading=13, textColor=TEAL, spaceBefore=7, spaceAfter=2),
    'lead': ParagraphStyle('lead', fontName=BODY, fontSize=11.2, leading=16.5, textColor=INK, spaceAfter=7),
    'cap': ParagraphStyle('cap', fontName=ITAL, fontSize=7.8, leading=10.5, textColor=MUTED, spaceAfter=7),
    'mono': ParagraphStyle('mono', fontName=MONO, fontSize=8.3, leading=11.4, textColor=INK, backColor=PANEL, borderPadding=(4, 5, 4, 5), spaceAfter=8),
}
P = lambda t, st='body': Paragraph(t, S[st])


def bullets(items, style='body'):
    return ListFlowable([ListItem(Paragraph(t, S[style]), leftIndent=12, bulletColor=TEAL) for t in items], bulletType='bullet', start='•',
                        leftIndent=12, bulletFontName=BODY, bulletFontSize=9)


def numbered(items):
    return ListFlowable([ListItem(Paragraph(t, S['body']), leftIndent=15) for t in items], bulletType='1', leftIndent=15, bulletFontName=BOLD,
                        bulletFontSize=9, bulletColor=TEAL)


def table(rows, widths, header=True, zebra=True, pad=4):
    data = [[c if not isinstance(c, str) else Paragraph(c, S['head'] if header and i == 0 else S['cell']) for c in row] for i, row in enumerate(rows)]
    t = Table(data, colWidths=[w * CW for w in widths], repeatRows=1 if header else 0)
    style = [('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
             ('TOPPADDING', (0, 0), (-1, -1), pad), ('BOTTOMPADDING', (0, 0), (-1, -1), pad), ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINE),
             ('BOX', (0, 0), (-1, -1), 0.5, LINE)]
    if header:
        style += [('BACKGROUND', (0, 0), (-1, 0), TEAL)]
    if zebra:
        style += [('ROWBACKGROUNDS', (0, 1 if header else 0), (-1, -1), [colors.white, PANEL])]
    t.setStyle(TableStyle(style))
    return t


def callout(text, kind='teal'):
    bg, bar = {'teal': (TEAL_BG, TEAL), 'amber': (AMBER_BG, AMBER), 'red': (RED_BG, RED)}[kind]
    t = Table([[Paragraph(text, S['body'])]], colWidths=[CW])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), bg), ('LINEBEFORE', (0, 0), (0, -1), 3, bar), ('LEFTPADDING', (0, 0), (-1, -1), 10),
                           ('RIGHTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
    return KeepTogether([t, Spacer(1, 6)])


def figure(path, crop, caption):
    """A screenshot cropped to its useful part (left, top, right, bottom in pixels), full text width, with a caption."""
    import tempfile
    from PIL import Image as PI
    im = PI.open(ROOT / path).convert('RGB')
    im = im.crop((crop[0], crop[1], crop[2] if crop[2] else im.width, crop[3]))
    tmp = Path(tempfile.gettempdir()) / ('vision_fig_' + Path(path).parent.parent.name + '_' + Path(path).stem + '.png')
    im.save(tmp)
    w = CW
    img = Image(str(tmp), width=w, height=w * im.size[1] / im.size[0])
    t = Table([[img], [Paragraph(caption, S['cap'])]], colWidths=[CW])
    t.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('BOX', (0, 0), (0, 0), 0.4, LINE),
                           ('TOPPADDING', (0, 0), (0, 0), 0), ('BOTTOMPADDING', (0, 0), (0, 0), 0), ('TOPPADDING', (0, 1), (0, 1), 3)]))
    return KeepTogether([t])


# ---------- drawings -------------------------------------------------------------------------------------
def box(d, x, y, w, h, text, fill=PANEL, stroke=LINE, color=INK, size=8, bold=False, dash=None, sub=None):
    r = Rect(x, y, w, h, rx=4, ry=4, fillColor=fill, strokeColor=stroke, strokeWidth=0.9)
    if dash:
        r.strokeDashArray = dash
    d.add(r)
    lines = text.split('\n')
    top = y + h / 2 + (len(lines) - 1) * (size + 1.5) / 2 - size * 0.35 + (4 if sub else 0)
    for i, ln in enumerate(lines):
        d.add(String(x + w / 2, top - i * (size + 1.5), ln, fontName=BOLD if bold else BODY, fontSize=size, fillColor=color, textAnchor='middle'))
    if sub:
        d.add(String(x + w / 2, y + 5, sub, fontName=BODY, fontSize=6.6, fillColor=MUTED, textAnchor='middle'))


def arrow(d, x1, y1, x2, y2, color=MUTED, dash=None):
    ln = Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=1)
    if dash:
        ln.strokeDashArray = dash
    d.add(ln)
    import math
    a = math.atan2(y2 - y1, x2 - x1)
    s = 4.5
    d.add(Polygon([x2, y2, x2 - s * math.cos(a - .45), y2 - s * math.sin(a - .45), x2 - s * math.cos(a + .45), y2 - s * math.sin(a + .45)],
                  fillColor=color, strokeColor=color, strokeWidth=0.2))


def journey():
    d = Drawing(CW, 215)
    box(d, 0, 92, 62, 34, 'Start', TEAL_BG, TEAL, bold=True, size=9)
    box(d, 100, 160, 100, 34, 'Ask a question', sub='word search, cites file:line')
    box(d, 100, 92, 100, 34, 'Explore the map', sub='folder > file > class > function')
    box(d, 100, 24, 100, 34, 'Check a change', sub='pick two saved versions')
    for y in (177, 109, 41):
        arrow(d, 62, 109, 100, y)
    box(d, 255, 120, 112, 70, 'Function page\nthe code, who calls it,\nwhat it calls, tests\nthat reach it', TEAL_BG, TEAL, size=7.6)
    box(d, 255, 12, 112, 70, 'Review of a change\nWhat to check\nChanged code\nRelated tests\nLimits and history', AMBER_BG, AMBER, size=7.6)
    box(d, 395, 150, 75, 40, 'Web overview\n(optional lens)', colors.white, PURPLE, color=PURPLE, size=7.4, dash=[3, 2])
    arrow(d, 200, 177, 255, 168)
    arrow(d, 200, 109, 255, 150)
    arrow(d, 200, 41, 255, 47)
    arrow(d, 367, 165, 395, 168, PURPLE, [3, 2])
    arrow(d, 395, 158, 367, 158, PURPLE, [3, 2])
    arrow(d, 311, 82, 311, 120, MUTED, [2, 2])
    d.add(String(316, 98, 'open a changed or affected function', fontName=ITAL, fontSize=6.6, fillColor=MUTED))
    d.add(String(0, 6, 'Back (or Esc) always returns one step. Every screen names the version it shows.', fontName=ITAL, fontSize=7, fillColor=MUTED))
    return d


def architecture():
    d = Drawing(CW, 215)
    xs = [0, 84, 168, 252, 336, 420]
    w = 70
    labels = [('Your Git\nrepository', None), ('Engine', 'reading, mapping,\ngraph'), ('Change and\ntest selection', 'changes/'), ('Map file', 'one per version\n(JSON)'),
              ('Local app', 'app/ server,\nno upload'), ('Browser UI', 'guided shell +\nweb overview')]
    for x, (t, sub) in zip(xs, labels):
        box(d, x, 120, w, 52, t, TEAL_BG if t == 'Browser UI' else PANEL, TEAL if t == 'Browser UI' else LINE, bold=True, size=7.8, sub=None)
        if sub:
            for i, ln in enumerate(sub.split('\n')):
                d.add(String(x + w / 2, 128 + (len(sub.split('\n')) - 1 - i) * 8, ln, fontName=BODY, fontSize=6.3, fillColor=MUTED, textAnchor='middle'))
    for a, b in zip(xs, xs[1:]):
        arrow(d, a + w, 146, b, 146)
    box(d, 168, 30, 100, 44, 'Evaluation\nruns tests, scores\nthe map and shortlist', colors.white, MUTED, size=7.2)
    arrow(d, 203, 74, 203, 120, MUTED, [3, 2])
    box(d, 336, 30, 154, 44, 'Optional explain step (off by default)\nretrieval first; a model only with consent\nand always with the same citations', colors.white, PURPLE, color=PURPLE, size=6.9, dash=[3, 2])
    arrow(d, 413, 74, 455, 120, PURPLE, [3, 2])
    d.add(String(0, 6, 'The engine never runs your code. The map file is the single contract: any screen can read it.', fontName=ITAL, fontSize=7, fillColor=MUTED))
    d.add(String(0, 190, 'Read left to right. Dashed = optional or side process.', fontName=ITAL, fontSize=7, fillColor=MUTED))
    return d


def gantt():
    d = Drawing(CW, 205)
    left, colw = 132, (CW - 132) / 3
    weeks = [('Week 1', '16 to 22 Nov'), ('Week 2', '23 to 29 Nov'), ('Week 3', '30 Nov to 6 Dec')]
    for i, (a, b) in enumerate(weeks):
        x = left + i * colw
        d.add(Rect(x, 168, colw - 2, 30, fillColor=TEAL, strokeColor=None))
        d.add(String(x + colw / 2, 186, a, fontName=BOLD, fontSize=8.5, fillColor=colors.white, textAnchor='middle'))
        d.add(String(x + colw / 2, 175, b, fontName=BODY, fontSize=7, fillColor=colors.white, textAnchor='middle'))
    rows = [('Engine accuracy', [(0, 1.7, 'close 2 pattern gaps')], TEAL), ('Live app server', [(0.3, 1.5, 'serve map + change + shortlist')], TEAL),
            ('Design into the real app', [(0.8, 2.0, 'guided shell, web overview, review')], PURPLE),
            ('Testing with people', [(0, 0.8, 'tester pack'), (2.0, 0.95, 'round 2')], AMBER),
            ('Untouched-project score', [(0.2, 0.5, 'pick + freeze'), (2.1, 0.6, 'score once')], RED),
            ('Report, docs, demo', [(1.6, 1.35, 'write up, freeze 6 Dec')], MUTED)]
    for i, (name, bars, col) in enumerate(rows):
        y = 142 - i * 24
        d.add(Line(0, y - 4, CW, y - 4, strokeColor=LINE, strokeWidth=0.4))
        d.add(String(0, y + 3, name, fontName=BOLD, fontSize=7.8, fillColor=INK))
        for s, ln, label in bars:
            x = left + s * colw
            d.add(Rect(x, y - 1, ln * colw - 3, 15, rx=3, ry=3, fillColor=col, strokeColor=None))
            d.add(String(x + 4, y + 3.5, label, fontName=BODY, fontSize=6.7, fillColor=colors.white))
    d.add(String(0, 1, 'Final buffer: 7 December. The exact deadline still needs confirming with the supervisor.', fontName=ITAL, fontSize=7, fillColor=MUTED))
    return d


# ---------- page furniture --------------------------------------------------------------------------------
def cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, H * 0.42, W, H * 0.58, stroke=0, fill=1)
    canvas.setFillColor(TEAL)
    canvas.rect(0, H * 0.42, 10 * mm, H * 0.58, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor('#9be3cd'))
    canvas.setFont(BOLD, 10)
    canvas.drawString(LM + 4 * mm, H - 38 * mm, 'REPOTRACE  ·  PROJECT VISION')
    canvas.setFillColor(colors.white)
    canvas.setFont(BOLD, 31)
    canvas.drawString(LM + 4 * mm, H - 62 * mm, 'From a working engine')
    canvas.drawString(LM + 4 * mm, H - 74 * mm, 'to a whole product')
    canvas.setFont(BODY, 12.5)
    for i, ln in enumerate(['The vision, what two design directions taught us,', 'and what to build, measure and decide in the', 'remaining weeks of the senior project.']):
        canvas.drawString(LM + 4 * mm, H - 92 * mm - i * 17, ln)
    canvas.setFillColor(colors.HexColor('#9be3cd'))
    canvas.setFont(BOLD, 9.5)
    canvas.drawString(LM + 4 * mm, H * 0.42 + 40 * mm, 'STATUS: WORKING DRAFT FOR THE TEAM AND THE SUPERVISOR')
    canvas.setFont(BODY, 9)
    canvas.setFillColor(colors.HexColor('#c9d2dc'))
    for i, ln in enumerate(['Written 15 November 2026 from the project sessions with Claude and ChatGPT.',
                            'Repository: github.com/israelhayal/repotrace (private). Companion files: docs/PROJECT_HANDBOOK.md,',
                            'prototype/lab/index.html (guided design), prototype/index.html (web-of-connections design).',
                            'Honesty rule used throughout: each number says whether it was measured or only claimed.']):
        canvas.drawString(LM + 4 * mm, H * 0.42 + 31 * mm - i * 12.5, ln)
    canvas.restoreState()


def page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(LM, H - 14 * mm, W - RM, H - 14 * mm)
    canvas.setFont(BOLD, 7.5)
    canvas.setFillColor(TEAL)
    canvas.drawString(LM, H - 11 * mm, 'REPOTRACE')
    canvas.setFont(BODY, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(W - RM, H - 11 * mm, 'Vision and path to a whole product  ·  15 Nov 2026')
    canvas.drawString(LM, 10 * mm, 'Evidence, not verdicts. Nothing here claims a change is safe.')
    canvas.drawRightString(W - RM, 10 * mm, 'Page %d' % doc.page)
    canvas.restoreState()


def build():
    doc = BaseDocTemplate(str(OUT), pagesize=A4, leftMargin=LM, rightMargin=RM, topMargin=20 * mm, bottomMargin=18 * mm,
                          title='RepoTrace: from a working engine to a whole product', author='RepoTrace team (Israel Hayalsew and collaborators)',
                          subject='Vision, design directions, plan and evaluation')
    doc.addPageTemplates([PageTemplate(id='cover', frames=[Frame(LM, 20 * mm, CW, H * 0.42 - 32 * mm, id='c', leftPadding=0, rightPadding=0)], onPage=cover),
                          PageTemplate(id='body', frames=[Frame(LM, 18 * mm, CW, H - 38 * mm, id='b', leftPadding=0, rightPadding=0)], onPage=page)])
    from reportlab.platypus import NextPageTemplate
    st = [P('<b>Contents</b>', 'h3')]
    st += [table([['1', 'The vision on one page', '2'], ['2', 'Who it is for, and the three jobs', '3'], ['3', 'What is built and what has been measured', '4'],
                  ['4', 'Two design directions, side by side', '5'], ['5', 'The combined design: guided first, a web when you ask', '8'],
                  ['6', 'How the finished product is put together', '9'], ['7', 'Making the engine more accurate, in order of payoff', '10'],
                  ['8', 'The AI question', '11'], ['9', 'Proving it works', '12'], ['10', 'Plan for the remaining weeks', '13'],
                  ['11', 'Risks, definition of done and decisions needed', '14'], ['A', 'How we got here; glossary; where things are', '15']], [0.07, 0.83, 0.1], header=False, zebra=False, pad=2)]
    st += [NextPageTemplate('body'), PageBreak()]

    # 1 -------------------------------------------------------------------------------------------------
    st += [P('1', 'kicker'), P('The vision on one page', 'h1'),
           P('RepoTrace helps a developer who has just inherited a Python project, or just changed one, answer three questions in minutes: '
             '<b>where is the code for this, what does it connect to, and if I change it, what should I check and which tests should I run first?</b> '
             'It answers from the code alone, never runs it, and says honestly how much it could not see.', 'lead'),
           P('Four principles decide every design question', 'h2'),
           table([['Principle', 'What it means in practice'],
                  ['Evidence, not verdicts', 'Every link is graded <b>sure</b> (the call is written in the code), <b>maybe</b> (one step depends on an assumption) or <b>name only</b>. The tool never says "safe". A quiet result means "nothing found".'],
                  ['Plain words', 'A first-year student should understand every sentence on screen. Jargon is a bug to be reported.'],
                  ['Guided first, powerful second', 'A newcomer is led one step at a time. The whole-project web, the history hints and the topic view are there when asked for.'],
                  ['Local and checkable', 'Standard library only, no upload. Every number on screen can be traced to a line of code, and every claim we make is measured against a recorded run.']],
                 [0.25, 0.75]),
           P('Where we are today', 'h2'),
           bullets(['<b>The engine works</b> and is covered by 484 automatic tests (measured). It maps a project, grades links, compares two Git versions and finds callers, signature breaks and leftover uses of removed names.',
                    '<b>A test shortlist now exists</b> for a change: which existing tests to run first, with the chain that connects each one to the change. On the practice project StudentManagement it found every test that really runs the changed code (78 of 78, measured); on a fresh pytest project it found 29% (45 of 157, measured once, untuned).',
                    '<b>Two clickable designs exist</b> and are described in this document. Neither is in the real app yet. The real app still shows the older outline-and-details screen.']),
           P('What would make it a whole project', 'h2'),
           P('Today RepoTrace is a strong engine, an old screen and two promising prototypes. Five gaps separate that from a finished product:'),
           table([['Gap', 'Why it matters', 'Planned fix (section)'],
                  ['1. The new design is not in the real app', 'Nobody can use the good design on their own project. It shows saved data.', 'Serve the saved-data shape live from the real engine (5, 6)'],
                  ['2. Accuracy on larger pytest projects', 'The shortlist finds under a third of relevant tests where objects come from factories or containers.', 'Close two measured pattern gaps, re-measure on an untouched project (7, 9)'],
                  ['3. No evidence from outside users yet', 'We do not know where a newcomer gets stuck. Passing browser checks is not understanding.', 'Tester pack now, round 2 in week 3 (9, 10)'],
                  ['4. The AI question is unanswered', 'The proposal mentions it; the supervisor has not said whether it is required.', 'Ship retrieval first; one optional, evaluated explain step (8)'],
                  ['5. The story is scattered across chats', 'Teammates cannot see the direction or the evidence.', 'This document and docs/PROJECT_HANDBOOK.md']],
                 [0.27, 0.45, 0.28]),
           PageBreak()]

    # 2 -------------------------------------------------------------------------------------------------
    st += [P('2', 'kicker'), P('Who it is for, and the three jobs', 'h1'),
           P('The user is a student or junior developer working on a Python project they did not write, or wrote a while ago and no longer remember. '
             'They know Python but not necessarily Git history or other people’s architecture. They are reading code in an editor and want to know '
             'what is safe to touch.'),
           table([['Job', 'The question', 'What RepoTrace gives', 'Screen today'],
                  ['J1  Find', 'Where is the code that does X?', 'Ranked word matches on names, paths, comments and code, each with file and line. No AI needed.', 'Search in both prototypes'],
                  ['J2  Understand', 'What does this function connect to?', 'Its real callers and callees as functions, each graded sure or maybe, the code beside it, and how many calls could not be traced.', 'Function page (guided) and map (web)'],
                  ['J3  Review a change', 'I changed this. What should I check, and which tests first?', 'The exact line that still uses a removed name or no longer fits a new signature, the before and after of the changed code, and an ordered test list with its reason and run command.', 'Review tabs']],
                 [0.14, 0.24, 0.42, 0.2]),
           P('What it is deliberately not', 'h2'),
           bullets(['Not a bug finder and not a verdict. It cannot say a change is safe, and says so on screen.',
                    'Not a runtime tool. Python decides some calls while running, so some links are invisible to any tool that only reads code. The screen lists what it could not see.',
                    'Not for other languages, not an editor plugin, not an auto-fixer. An editor side panel is a later step, after the website works.']),
           P('The test of success', 'h2'),
           callout('A newcomer, given a Python project they have never seen, can find the code for a feature, explain what a function connects to, and name the line and the tests '
                   'to check after a change, without help and without misreading what a link means. We measure this with people (section 9), not with screenshots.'),
           P('How the three jobs connect', 'h2'),
           P('One screen sequence serves all three. The drawing shows the journey the finished product should support.'),
           journey(), Spacer(1, 4), P('Figure 1. The user journey. Solid arrows are the main path; dashed arrows are optional or side routes.', 'cap'),
           PageBreak()]

    # 3 -------------------------------------------------------------------------------------------------
    st += [P('3', 'kicker'), P('What is built and what has been measured', 'h1'),
           P('The engine is organised as a pipeline in which each folder may only use the ones before it. A test in the repository enforces this, so the structure cannot rot quietly.'),
           table([['Layer', 'Does', 'State'],
                  ['reading/', 'Reads one file with Python’s <font name="Mono">ast</font>; lists functions and calls.', 'Working'],
                  ['mapping/', 'Joins all files into one map. Grades every link. Resolves imports, methods, constructor injection, pytest fixtures.', 'Working; fixture tracking new'],
                  ['graph/', 'Answers questions of a finished map: who calls what, how far a change reaches.', 'Working'],
                  ['changes/', 'Compares two Git versions: changed code, moved or renamed pairs, signature breaks, test shortlist.', 'Working; shortlist new'],
                  ['report/, app/, cli/', 'Plain-words story, the local app, terminal commands.', 'Working, old screen'],
                  ['evaluation/', 'Runs the real tests and scores the map and the shortlist against what actually happened.', 'Working']],
                 [0.17, 0.63, 0.2]),
           P('Measured results', 'h2'),
           P('The rule: a new project is scored <b>once</b>, untuned, before anything is fixed on it. Results from projects we tuned on are labelled as such and never presented as general.'),
           table([['What was measured', 'Result', 'Reading it'],
                  ['Confident (sure) arrows against recorded runs', '138 of 138 and 156 of 156 right where run', 'Where we say "sure", we have not yet found it wrong. Small samples.'],
                  ['Uncertain (maybe) arrows', '128 of 128 and 399 of 400 right', 'Maybe means a candidate, not a mistake.'],
                  ['Real calls the map finds', '32.7% (Shopkeep), 44.8% (Ledgerly)', 'Coverage is not accuracy. Most missed calls are dynamic Python.'],
                  ['Shortlist on StudentManagement (tuned on)', 'Found 78 of 78 relevant tests; about 94% of suggestions really ran the change', 'The 8 tests that really break after the rename were all listed.'],
                  ['Shortlist on Ledgerly (measured once)', 'Found 45 of 157 (29%); 45 of 46 suggestions right (98%)', 'Almost always right when it speaks, but silent on most tests.'],
                  ['Shortlist on Queuely and Shopkeep (before fixture work)', 'Found 4 of 128 (4 of 5 suggestions right) and 0 of 72', 'Objects come from a dependency container or a factory the engine does not follow yet.']],
                 [0.33, 0.37, 0.3]),
           callout('<b>Plain reading.</b> When RepoTrace suggests a test it is nearly always a test that really reaches the change. On bigger pytest projects it still misses most of them. '
                   'The pytest fixture work is correct and tested but added no measured gain on those projects, because other patterns block them first. We report that as it is.', 'amber'),
           P('Two different truths, measured separately', 'h3'),
           P('<b>Relevant</b> means the test really executes a changed function. <b>Failing</b> means it passes before and fails after. A relevant test can pass; a path in the graph can exist '
             'even when an earlier validation error stops the test before the changed line. The shortlist claims relevance, not failure. Failure truth exists only on StudentManagement so far.'),
           PageBreak()]

    # 4 -------------------------------------------------------------------------------------------------
    st += [P('4', 'kicker'), P('Two design directions, side by side', 'h1'),
           P('After the UI was judged "not even 10%" of the intended tool, two designs were built on the same saved analysis of StudentManagement. Both read the same data file, '
             'so every difference you see is design, not data.'),
           P('Direction A: a web of connections (built with Claude)', 'h2'),
           P('The whole project is a graph you can step into. Dots are folders; clicking opens them into files, classes and functions. Selecting a function brings its real callers and callees onto the graph. '
             'Three views group the same dots by folder, by calls, or by guessed topic. A command bar (Cmd+K) finds code by name or question. Review mode fades everything except what changed and what could be affected.'),
           figure('prototype/screenshots/04-function-neighbours.png', (236, 54, 0, 575),
                  'Figure 2a. Direction A, function selected: its callers and callees appear as functions on the graph, arrows from caller to called, with the code and the lists on the right.'),
           figure('prototype/screenshots/06-review-first-finding.png', (236, 54, 0, 575),
                  'Figure 2b. Direction A, review mode: the changed function is ringed white, affected code yellow; one finding is open at a time with the exact line and the before and after signature.'),
           table([['Strengths', 'Weaknesses'],
                  ['Gives the Obsidian-like feeling of exploring connected code that was asked for. Good at showing the shape of a whole project and at seeing a change in context. Review findings carry the caller line, before and after signature, and the test chain.',
                   'Many controls at once (three views, filters, command bar, modes); a newcomer has to learn them. Labels can overlap on a busy graph. The topic view is guesswork and one topic held 33 of 72 files. Hard to use on a phone. Keyboard cannot reach individual dots.']],
                 [0.5, 0.5], zebra=False),
           P('Direction B: guided, one step at a time (the "Lab", started in ChatGPT, fixed here)', 'h2'),
           P('A calm start page offers two ways in: <i>understand the project</i> or <i>check a change</i>. The map shows only the next level, with the code beside it. A function page '
             'shows callers on the left and callees on the right. A change review is four tabs: what to check, changed code, related tests, limits and history. Each screen says plainly what it is and is not.'),
           figure('prototype/lab/screenshots/04-function-view.png', (222, 62, 0, 745),
                  'Figure 3a. Direction B, function view: callers on the left, the selected function, callees on the right, and the source beside it.'),
           figure('prototype/lab/screenshots/05-review-overview.png', (222, 62, 0, 735),
                  'Figure 3b. Direction B, change review: what happened, the one line to check with its code, then the tests.'),
           table([['Strengths', 'Weaknesses (original) and what was fixed'],
                  ['Far easier to read on first contact. A guided first path with numbered steps. Honest limits box. Works with the keyboard and on a phone. Closest to what a beginner can use without teaching.',
                   'Found in an in-depth check on 13 Nov: some text typed in by hand (true for one example only), the most important tests (those the change cut off) hidden among twelve, jargon labels ("Possible target"), alphabetical search ties, '
                   'only six neighbours shown, no navigation on phones. <b>All fixed</b> in prototype/lab; 10 real-click checks pass. Not yet fixed: it has no whole-project overview.']],
                 [0.4, 0.6], zebra=False),
           P('What the outside reviews said', 'h2'),
           table([['Date', 'Subject', 'Verdict that shaped the design'],
                  ['6 Nov', 'The project map', 'Eight trust bugs (invented links, stale source, hidden missing imports). All reproduced and fixed, each with a regression test.'],
                  ['9 to 10 Nov', 'Direction A', 'Keep the direction. Close five gaps first: graph and findings must show the same version; selecting a function must reveal real neighbours; the review must not read as a report; confidence wording must not imply accuracy; keep the topic view experimental.'],
                  ['12 Nov', 'Test shortlist', 'Never call <font name="Mono">self.method()</font> certain; separate setUp and fixtures from tests; "relevant" and "failing" are different measurements; use both versions; measure pytest before improving it.'],
                  ['13 Nov', 'Direction B', 'Better base than A for newcomers; fix the invented wording, rank lost tests first, plain labels, search ranking, phone navigation.']],
                 [0.14, 0.18, 0.68]),
           P('Head to head', 'h2'),
           table([['Criterion', 'A: web of connections', 'B: guided Lab', 'Better for'],
                  ['First contact for a newcomer', 'Powerful but busy; many controls', 'Clear path, plain start page', 'B'],
                  ['Seeing the shape of the whole project', 'Strong: one graph, three views', 'Weak: one level at a time', 'A'],
                  ['Understanding one function', 'Neighbours on the graph, code in a panel', 'Callers, callees and code side by side', 'Tie'],
                  ['Reviewing a change', 'Change shown in context on the graph; compact findings', 'Four clear tabs; lost-path tests called out', 'B for clarity, A for context'],
                  ['Honesty of wording', 'Good after fixes', 'Very good; explains its limits', 'B'],
                  ['Keyboard and phone', 'Dots not keyboard-reachable; poor on phone', 'Keyboard works; phone navigation fixed', 'B'],
                  ['Matches the "spider web, touch a node" request', 'Yes', 'Partly (step-in map)', 'A'],
                  ['Effort to put in the real app', 'Canvas and a force layout to port', 'Plain HTML and SVG; simpler to serve live', 'B']],
                 [0.27, 0.27, 0.3, 0.16]),
           callout('<b>Conclusion.</b> Neither design alone is the product. B is the better front door and A is the better way to see a whole project and a change in context. The next section combines them.')]
    st += [PageBreak()]

    # 5 -------------------------------------------------------------------------------------------------
    st += [P('5', 'kicker'), P('The combined design: guided first, a web when you ask', 'h1'),
           P('Use Direction B as the shell (start page, search, function page, review tabs) and bring Direction A in as a <b>lens</b> on the map page and on a review. '
             'A newcomer is never shown the web unless they ask. Someone who wants the Obsidian-style picture gets it one click away.'),
           table([['Screen', 'Default behaviour (from B)', 'What A adds'],
                  ['Start', 'Search box, "understand" and "check a change" cards, a guided first path built from the data.', 'Nothing. Stays calm.'],
                  ['Explore the map', 'Step in: folders, then files, then classes and functions, with the code beside it.', 'An "Overview" switch draws the current folder as a web of files with their calls. Lenses: Physical and Logical on by default; Conceptual stays marked experimental.'],
                  ['Function page', 'Callers on the left, callees on the right, code, tests that reach it.', '"Show on the web" focuses this function and its neighbours.'],
                  ['Review a change', 'Four tabs: what to check, changed code, related tests, limits and history. Tests the change cut off come first.', '"Show this change on the web" fades everything except changed and affected code, with removed code drawn dashed from the old version.'],
                  ['Ask', 'Word search with file and line; plain explanation that no AI is involved.', 'Matches highlighted on the web.']],
                 [0.17, 0.43, 0.4]),
           P('Design rules that carry over from both', 'h2'),
           bullets(['<b>Every screen names the version it shows</b>, and graph, code and evidence always come from the same commit. Removed code is drawn from the older version and marked.',
                    '<b>Every sentence is built from the data</b>, never typed for one example. A rename is described only if the engine paired the two functions.',
                    '<b>Confidence is shown by words and line style, never by colour alone</b>: solid is sure, dashed is maybe, dotted is an old-version-only link.',
                    '<b>One finding is open at a time</b>, with the line, the before and after, and the next action together.',
                    '<b>Tests the change cut off come first</b>: connected before the change but not after is the signature of a broken connection.',
                    '<b>Back always works</b>, one step, and restores the place you left.',
                    '<b>Never "safe"</b>. A clean result links straight to what could not be checked.']),
           P('What stays experimental, and why', 'h2'),
           P('The Conceptual view groups code by guessed topic from names and common words. Labels such as "entities, dump, module" do not yet say what a system is about. It ships only behind an "experimental" mark, '
             'with the files that produced each topic listed, and it is not part of the acceptance test. It earns a place in the product only if testers say it helped them understand a project.')]
    st += [PageBreak()]

    # 6 -------------------------------------------------------------------------------------------------
    st += [P('6', 'kicker'), P('How the finished product is put together', 'h1'),
           P('The prototypes already prove the data shape the screens need. The real work is to make the engine produce that shape on demand for any folder the user picks.'),
           architecture(), Spacer(1, 4), P('Figure 4. Architecture of the finished product.', 'cap'),
           P('The contract between engine and screen', 'h2'),
           P('One saved map per version, and one change record per comparison. The prototypes read exactly this, and the real server should emit it:'),
           table([['Piece', 'Contains', 'Source today'],
                  ['Graph (per version)', 'Folders, files, classes and functions with their code, doc line and untraced-call count; links with a grade (sure, maybe, old-version-only) and call counts; class relations', 'prototype/make_data.py builds it from the engine'],
                  ['Review (per comparison)', 'Changed definitions with before and after signature; concerns with the caller line, what, why and the next check; the test shortlist with chain, versions that support it and run command; removed tests; limits; history hints', 'changes/ plus test_selection.py'],
                  ['Search index', 'Word index over names, paths, comments and code, with synonyms and a tie-break by how much other code uses a definition', 'In the prototype page; move to the server if projects are large']],
                 [0.2, 0.55, 0.25]),
           P('What has to be built', 'h2'),
           numbered(['<b>Server endpoints</b> for map, change, search and test shortlist, using the existing <font name="Mono">app/</font> server. Analysis runs in a background job with progress, as the old screen already does.',
                     '<b>One adapter</b> that turns the engine reports into the data shape above, replacing the baked file. The prototype generator is the starting point and the specification.',
                     '<b>Port the Lab shell</b> (plain HTML, CSS and a little JavaScript) into <font name="Mono">repotrace/report/</font>, then add the web overview as a lens.',
                     '<b>Keep the layer rule.</b> The screen reads the map file and nothing else; the architecture test must still pass.',
                     '<b>Browser checks</b> for the real app, ported from prototype/lab/check.mjs: real clicks and keys, no internal calls.'])]
    st += [PageBreak()]

    # 7 -------------------------------------------------------------------------------------------------
    st += [P('7', 'kicker'), P('Making the engine more accurate, in order of payoff', 'h1'),
           P('The shortlist is trustworthy when it speaks but silent on most tests of larger projects. The measurements show why, which tells us what to build. '
             'Each item below is a pattern the engine currently refuses on purpose, because following it wrongly would create false "sure" links.'),
           table([['Rank', 'Pattern the engine does not follow yet', 'Seen in', 'Safe way to add it', 'How we will know'],
                  ['1', 'A field set on a freshly built object from outside its class (for example <font name="Mono">app.inventory = InventoryService(...)</font> inside a builder function)', 'Shopkeep (blocks 0 of 72)', 'Type the field only when every write to it, project-wide, assigns the same class; grade the link maybe; keep refusing on any unclear write', 'Re-measure Shopkeep; confident arrows must stay unchanged'],
                  ['2', 'A tuple or factory return unpacked into names', 'Shopkeep, Queuely', 'Follow the return only when every return statement has the same shape', 'New unit tests with a case that must stay refused'],
                  ['3', 'A parametrized fixture whose branches return the same class', 'Shopkeep (<font name="Mono">any_app</font>)', 'Accept only when every branch builds the same class', 'Re-measure Shopkeep'],
                  ['4', 'A dependency container (<font name="Mono">container.resolve(App)</font>)', 'Queuely', 'Probably leave refused. Report as a known limit.', 'Documented in the limits box'],
                  ['5', 'A test that stops at an early validation error', 'StudentManagement (extra suggestions)', 'Cannot be fixed statically. Keep saying "a listed test may pass".', 'Stated on screen']],
                 [0.06, 0.3, 0.16, 0.28, 0.2]),
           callout('<b>Rules we do not break.</b> A confident link never changes because of new inference; new inference only adds <i>maybe</i> links. Every new rule ships with a test that must stay refused. '
                   'A call through <font name="Mono">self</font> stays maybe, because a subclass could override it.', 'teal'),
           P('Not worth the time before the deadline', 'h2'),
           bullets(['More synonyms and ranking for search: useful, but the second prototype already improved it; stop after the tester feedback.',
                    'Branch-merge preview, architecture-rule checking, the editor side panel: all later. The proposal draft marked them as extensions.',
                    'Supporting other languages: out of scope.'])]
    st += [PageBreak()]

    # 8 -------------------------------------------------------------------------------------------------
    st += [P('8', 'kicker'), P('The AI question', 'h1'),
           P('The original proposal mentions answering questions about the code with a language model (retrieval-augmented generation). Whether it is <b>required</b> is not yet confirmed with the supervisor. '
             'The decision taken so far is retrieval first and the core must work with no AI.'),
           table([['Option', 'What the user gets', 'Cost and risk', 'Recommendation'],
                  ['0. No AI', 'Word search that cites file and line', 'None. Already built.', 'Always on'],
                  ['1. Better retrieval', 'Search that understands meaning, evaluated against option 0', 'Needs embeddings; small risk of confident wrong matches', 'Only if option 0 fails on a fair question set'],
                  ['2. Local model explains matches', 'A short answer citing the same lines, nothing leaves the machine', 'Needs capable hardware; weaker answers; slower', 'Optional, off by default'],
                  ['3. Cloud model explains matches', 'Best answers', 'Code leaves the machine; cost; privacy. Needs explicit consent each time.', 'Optional, off by default, consent dialog']],
                 [0.2, 0.28, 0.32, 0.2]),
           P('How we would judge it fairly', 'h2'),
           numbered(['Write 20 real questions about two projects, each with the correct function written down beforehand.',
                     'Score option 0 first: how often is the right function in the top three?',
                     'An explain step ships only if every sentence of its answer cites a real line, and it measurably beats option 0 on the same questions.',
                     'The answer never replaces the cited matches; it is shown beside them. Wrong or uncited answers are counted and reported.']),
           callout('<b>Decision needed from the supervisor:</b> is an AI explanation required for the senior project, or is evaluated retrieval with an honest explanation of why no model is used acceptable? '
                   'Until answered, we build nothing in this area beyond option 0.', 'amber')]
    st += [PageBreak()]

    # 9 -------------------------------------------------------------------------------------------------
    st += [P('9', 'kicker'), P('Proving it works', 'h1'),
           P('A senior project is judged on evidence. We need three kinds, kept separate so none inflates another.'),
           table([['Evidence', 'How', 'What we may claim', 'What we must not claim'],
                  ['Map accuracy', 'Record real runs of a project on a throwaway copy with the network blocked; score the arrows against them', 'Per project, with the project named and tuned or untuned stated', 'That accuracy generalises from projects we tuned on'],
                  ['Test shortlist', 'Run the real tests at both versions; score relevant and failing separately', 'Recall and precision per project, with the untuned number as the headline', 'That a listed test will fail; that an unlisted test does not matter'],
                  ['Usability', 'Timed tasks with people who have never seen the tool; notes on every hesitation', 'Completion rate, time, and the exact places people were confused', 'That it is easy, from our own use']],
                 [0.15, 0.33, 0.28, 0.24]),
           P('The untouched project', 'h2'),
           P('Ledgerly was measured once and is now a development project, because we read its misses. For the final numbers we need <b>one new Python project nobody has tuned on</b>, '
             'with a short answer key. Pick it in week 1, freeze a copy of the engine, run the scores once in week 3, and report the result as it comes out, good or bad.'),
           P('The user study', 'h2'),
           bullets(['<b>Round 1 (now):</b> the tester pack sent to the three teammates: seven timed tasks on the guided prototype, an accuracy check on their own project, comprehension questions and a try-to-break-it list.',
                    '<b>Round 2 (week 3):</b> three people from outside the team on the real app, same tasks, with changes from round 1 in place.',
                    '<b>Measured per task:</b> finished unaided or not, minutes, where they hesitated, what they misread, a trust rating from 1 to 5.']),
           P('Proposed acceptance thresholds (to agree as a team)', 'h3'),
           table([['Measure', 'Proposed bar'],
                  ['Find, understand and review tasks (A1 to A3), unaided, round 2', 'At least 5 of 6 testers finish each task'],
                  ['A tester can say what <i>sure</i> and <i>maybe</i> mean without help', 'At least 5 of 6'],
                  ['Confident (sure) arrows wrong on the untouched project', 'Zero, or each wrong one explained and fixed with a test'],
                  ['Shortlist precision on the untouched project', 'Report it. Target 90% or better; no target for recall, only honest reporting'],
                  ['Crashes or page errors in the real-app browser check', 'Zero']],
                 [0.7, 0.3])]
    st += [PageBreak()]

    # 10 ------------------------------------------------------------------------------------------------
    st += [P('10', 'kicker'), P('Plan for the remaining weeks', 'h1'),
           P('About three weeks remain to a deadline near 7 December (four weeks from 9 November; to be confirmed). The plan keeps one rule: <b>freeze features at the end of week 2</b>, so week 3 is measuring, fixing and writing.'),
           gantt(), Spacer(1, 4), P('Figure 5. Workstreams across the three weeks.', 'cap'),
           table([['Week', 'Must happen', 'Done when'],
                  ['1  (16 to 22 Nov)', 'Teammates return the tester pack. Decide the combined design. Engine ranks 1 and 2 built with tests. Server serves map, change and shortlist for any folder. Untouched project chosen and engine copy frozen.', 'Round 1 notes read and sorted; server returns the prototype data shape for a folder; two gaps measured before and after'],
                  ['2  (23 to 29 Nov)', 'Lab shell running in the real app on live analysis. Web overview and review lens added. Round 1 fixes in. Browser checks ported. Feature freeze on 29 Nov.', 'A teammate opens the real app on their own project and completes the three jobs'],
                  ['3  (30 Nov to 6 Dec)', 'Score the untouched project once. Round 2 with three outsiders. Fix what two or more stumble on. Final report, docs and a demo script. Release candidate tagged.', 'Numbers recorded as they came out; handbook, README and this document updated; demo rehearsed']],
                 [0.2, 0.5, 0.3]),
           P('A proposed split of the work', 'h2'),
           P('Four people, four lanes. Names are for the team to assign; the lanes follow the workstreams above.', 'small'),
           table([['Lane', 'Owns', 'First task'],
                  ['Engine accuracy', 'mapping/, changes/, their tests; the rank 1 to 3 patterns', 'Field-from-outside pattern with a must-stay-refused test'],
                  ['App and integration', 'app/, report/; the data adapter; browser checks', 'Serve the prototype data shape for a folder'],
                  ['Design and testing', 'The combined screens; the tester pack and round 2', 'Read round 1 notes; list what two or more people stumbled on'],
                  ['Evaluation, report, docs', 'evaluation/, the untouched project, the written report, handbook', 'Choose and freeze the untouched project']],
                 [0.22, 0.42, 0.36])]
    st += [PageBreak()]

    # 11 ------------------------------------------------------------------------------------------------
    st += [P('11', 'kicker'), P('Risks, definition of done and decisions needed', 'h1'),
           P('Risks', 'h2'),
           table([['Risk', 'Likelihood', 'What we do'],
                  ['Porting the design into the live app takes longer than a week', 'Medium', 'Port the guided shell first and ship it alone; add the web overview only if time remains. The prototype keeps working as a demo either way.'],
                  ['New engine rules create a wrong "sure" link', 'Low if rules hold', 'New inference only adds maybe links; confident arrows are re-checked after every change; every rule has a must-stay-refused test.'],
                  ['The untouched project scores badly', 'Medium', 'Report it honestly and explain the blocked patterns. A truthful weak number is better than a flattering tuned one.'],
                  ['Testers do not return notes', 'Medium', 'Fixed return date; keep tasks short; take notes live with one tester if needed.'],
                  ['The supervisor requires an AI step late', 'Unknown', 'Ask this week. If required, build option 2 or 3 only on top of the working search.'],
                  ['Scope grows again (more screens, more features)', 'High', 'The freeze on 29 Nov; the "not worth it before the deadline" list in section 7.']],
                 [0.38, 0.14, 0.48]),
           P('Definition of done', 'h2'),
           P('The project counts as whole when every box can be ticked and shown, not described:', 'small'),
           bullets(['The real app, started with one command, opens the guided design on any Python folder with at least three Git commits.',
                    'The three jobs work there: find, understand, review a change with ordered tests and their run commands.',
                    'Every screen names its version; no sentence claims more than the data supports; no screen says "safe".',
                    'All automatic tests and the real-app browser check pass; the layer rule still holds.',
                    'Accuracy, shortlist and usability results are written down with the untuned numbers as the headline, and limits stated.',
                    'The handbook, README and this vision document match the code. A new teammate can clone, run and understand in an hour.',
                    'A demo script exists and has been rehearsed on a project the audience chooses.']),
           P('Decisions needed', 'h2'),
           table([['#', 'Decision', 'Who', 'By'],
                  ['1', 'Combined design (guided shell plus web lens), or guided only?', 'Team', 'After round 1 notes, 22 Nov'],
                  ['2', 'Is an AI explanation required for the senior project?', 'Supervisor', 'This week'],
                  ['3', 'Exact deadline and what must be delivered (report, demo, code)?', 'Supervisor', 'This week'],
                  ['4', 'Which untouched project for the final score?', 'Team', '18 Nov'],
                  ['5', 'Who owns which lane?', 'Team', '16 Nov'],
                  ['6', 'Push the practice projects (private) so everyone can re-measure?', 'Israel', '16 Nov']],
                 [0.05, 0.57, 0.14, 0.24])]
    st += [PageBreak()]

    # A -------------------------------------------------------------------------------------------------
    st += [P('A', 'kicker'), P('How we got here, and a glossary', 'h1'),
           table([['Date', 'What happened'],
                  ['26 Oct', 'Core promise reset: a simple developer tool that assesses changes, reports impact accurately and resolves calls. UI polish demoted below accuracy and the test shortlist.'],
                  ['31 Oct', 'Restructured into the layered package with an architecture test; first push to GitHub; teammates invited.'],
                  ['2 to 6 Nov', '"Explore the project" map and an ER-style diagram, replaced by a simpler outline and details screen; outside review found eight trust bugs, all fixed with tests.'],
                  ['9 Nov', 'UI judged far from the intent. Vision restated: an Obsidian-like explorer, conceptual, logical and physical views, test guidance. Gate set: approve a prototype before replacing the UI.'],
                  ['10 Nov', 'Prototype A built on real analysis of Queuely, then rebuilt on StudentManagement; outside review closed five gaps.'],
                  ['11 to 12 Nov', 'Test shortlist, pytest fixture tracking and the evaluation runner built. Review: "relevant" is not "failing"; do not promote self-calls.'],
                  ['13 Nov', 'The guided Lab design, built in ChatGPT, was checked in depth, copied and fixed. Tester pack written.'],
                  ['15 Nov', 'Everything pushed to the repository with the project handbook; this document written.']],
                 [0.14, 0.86]),
           P('Glossary', 'h2'),
           table([['Word', 'Plain meaning'],
                  ['Sure link', 'A call written in the code that the tool could follow to one place.'],
                  ['Maybe link', 'A call where one step depends on an assumption, for example a call through <font name="Mono">self</font> that a subclass could override.'],
                  ['Name only', 'Another call has the same name; the weakest evidence.'],
                  ['Not traced', 'A call whose target the tool could not work out. Not proof of nothing there.'],
                  ['Shortlist', 'The existing tests worth running first for a change, each with the chain that connects it to the change.'],
                  ['Lost path', 'A test connected to the changed code before the change but not after. Often means the change broke what the test relies on.'],
                  ['Relevant / failing test', 'Relevant: really executes a changed function when it runs. Failing: passes before the change and fails after it.'],
                  ['Fixture, setUp', 'A pytest fixture builds an object and hands it to tests by name; a unittest setUp method runs before each test of its class.'],
                  ['Untuned or blind', 'Measured on a project we did not adjust the tool to; the only kind of number that suggests generality.']],
                 [0.2, 0.8]),
           P('Where things are', 'h2'),
           table([['What', 'Where'],
                  ['Code and tests', 'github.com/israelhayal/repotrace (private); <font name="Mono">python3 -m unittest discover -s tests -t .</font>'],
                  ['Handbook for teammates', 'docs/PROJECT_HANDBOOK.md'],
                  ['Guided design (recommended base)', 'prototype/lab/index.html and prototype/lab/README.md'],
                  ['Web-of-connections design', 'prototype/index.html and prototype/README.md'],
                  ['Testing and measuring', 'prototype/WALKTHROUGH.md and the shared tester pack; repotrace/evaluation/ (recorder, trace, change and test scores)']],
                 [0.3, 0.7])]
    doc.build(st)
    print('wrote', OUT)


if __name__ == '__main__':
    build()
