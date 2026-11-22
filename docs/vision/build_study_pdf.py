"""Builds docs/RepoTrace_Defense_Study_Roadmap.pdf from docs/DEFENSE_STUDY_ROADMAP.md.

    python3 -m venv /tmp/pdfenv && /tmp/pdfenv/bin/pip install "reportlab==4.2.5"
    /tmp/pdfenv/bin/python docs/vision/build_study_pdf.py
"""
import os
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (CondPageBreak, KeepTogether, ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

ROOT = Path(__file__).resolve().parents[2]
SRC, OUT = ROOT / 'docs' / 'DEFENSE_STUDY_ROADMAP.md', ROOT / 'docs' / 'RepoTrace_Defense_Study_Roadmap.pdf'
SUP = '/System/Library/Fonts/Supplemental/'
FONTS = {'Body': 'Arial.ttf', 'Body-B': 'Arial Bold.ttf', 'Body-I': 'Arial Italic.ttf', 'Body-BI': 'Arial Bold Italic.ttf', 'Mono': 'Courier New.ttf'}
if all(os.path.exists(SUP + f) for f in FONTS.values()):
    for name, f in FONTS.items():
        pdfmetrics.registerFont(TTFont(name, SUP + f))
    pdfmetrics.registerFontFamily('Body', normal='Body', bold='Body-B', italic='Body-I', boldItalic='Body-BI')
    BODY, BOLD, MONO = 'Body', 'Body-B', 'Mono'
else:
    BODY, BOLD, MONO = 'Helvetica', 'Helvetica-Bold', 'Courier'

INK, MUTED, LINE, TEAL, PANEL = (colors.HexColor(c) for c in ('#1b2430', '#5b6776', '#d5dbe2', '#1f7a6b', '#f4f6f8'))
st = {
    'p': ParagraphStyle('p', fontName=BODY, fontSize=10, leading=14.5, textColor=INK, spaceAfter=5),
    'h1': ParagraphStyle('h1', fontName=BOLD, fontSize=21, leading=26, textColor=INK, spaceAfter=8),
    'h2': ParagraphStyle('h2', fontName=BOLD, fontSize=14, leading=18, textColor=TEAL, spaceBefore=14, spaceAfter=6, keepWithNext=1),
    'h3': ParagraphStyle('h3', fontName=BOLD, fontSize=11, leading=15, textColor=INK, spaceBefore=8, spaceAfter=4, keepWithNext=1),
    'li': ParagraphStyle('li', fontName=BODY, fontSize=10, leading=14, textColor=INK, spaceAfter=2),
    'cell': ParagraphStyle('cell', fontName=BODY, fontSize=9, leading=12, textColor=INK),
    'code': ParagraphStyle('code', fontName=MONO, fontSize=7.6, leading=10.5, textColor=INK, backColor=PANEL, borderColor=LINE, borderWidth=0.6,
                           borderPadding=6, spaceBefore=4, spaceAfter=10, leftIndent=6, rightIndent=6),
}


def inline(text):
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'`([^`]+)`', lambda m: f'<font name="{MONO}" size="9" color="#0f5a4e">{m.group(1)}</font>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<![\w*])\*([^*]+)\*(?![\w*])', r'<i>\1</i>', text)
    return text


def table(rows, width):
    head, body = rows[0], rows[1:]
    n = len(head)
    data = [[Paragraph(f'<b>{inline(c)}</b>', st['cell']) for c in head]] + [[Paragraph(inline(c), st['cell']) for c in r] for r in body]
    widths = [width * 0.17, width * 0.83] if n == 2 and len(head[0]) < 12 else [width / n] * n
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), PANEL), ('GRID', (0, 0), (-1, -1), 0.5, LINE), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                           ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    return [t, Spacer(1, 8)]


def build():
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title='RepoTrace: study roadmap for the defense', author='RepoTrace team')
    width = A4[0] - 40 * mm
    lines, flow, i = SRC.read_text(encoding='utf-8').split('\n'), [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith('```'):
            i += 1; block = []
            while not lines[i].startswith('```'):
                block.append(lines[i]); i += 1
            flow.append(Preformatted('\n'.join(block), st['code'])); i += 1; continue
        if ln.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                if not re.match(r'^\|[\s:|-]+\|$', lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip('|').split('|')])
                i += 1
            flow += table(rows, width); continue
        m = re.match(r'^(#{1,3}) (.*)', ln)
        if m:
            if len(m.group(1)) == 2:
                flow.append(CondPageBreak(55 * mm))
            flow.append(Paragraph(inline(m.group(2)), st[f'h{len(m.group(1))}'])); i += 1; continue
        m = re.match(r'^(\s*)(-|\d+\.) (.*)', ln)
        if m:
            ordered, items = m.group(2) != '-', []
            while i < len(lines) and (mm_ := re.match(r'^(\s*)(-|\d+\.) (.*)', lines[i])):
                text = mm_.group(3); i += 1
                while i < len(lines) and lines[i].startswith('  ') and not re.match(r'^\s*(-|\d+\.) ', lines[i]) and lines[i].strip():
                    text += ' ' + lines[i].strip(); i += 1
                items.append(ListItem(Paragraph(inline(text), st['li']), leftIndent=16))
            flow.append(ListFlowable(items, bulletType='1' if ordered else 'bullet', bulletFontName=BODY, bulletFontSize=9,
                                     leftIndent=16, bulletColor=TEAL, start='1' if ordered else '•'))
            flow.append(Spacer(1, 4)); continue
        if not ln.strip():
            i += 1; continue
        para = ln.strip(); i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r'^(#|\||```|\s*(-|\d+\.) )', lines[i]):
            para += ' ' + lines[i].strip(); i += 1
        flow.append(Paragraph(inline(para), st['p']))

    def footer(canvas, d):
        canvas.saveState(); canvas.setFont(BODY, 8); canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 10 * mm, 'RepoTrace: study roadmap for the defense'); canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f'{d.page}')
        canvas.restoreState()
    doc.build(flow, onFirstPage=footer, onLaterPages=footer)


if __name__ == '__main__':
    build(); print(OUT)
