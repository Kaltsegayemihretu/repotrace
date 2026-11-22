"""Build prototype/lab/data.js: saved analysis of one practice project, produced by the SAME adapter the real app uses.

    python3 prototype/lab/make_lab_data.py ../StudentManagement

Only the wording of each comparison's title and the sample questions are curated here (a demo can choose its own examples);
every link, test, finding and line comes from repotrace/report/lab_data.py.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from repotrace.changes.change_impact import analyze_change_impact          # noqa: E402
from repotrace.mapping.repository import analyze_repository                # noqa: E402
from repotrace.changes.git_snapshot import run_git                         # noqa: E402
from repotrace.report import lab_data                                      # noqa: E402
from repotrace.report.dashboard import build_view                          # noqa: E402

COMPARISONS = {
    'StudentManagement': [('step-06', 'step-07', 'Rename: read_course_by_id → fetch_course_by_id (one caller forgotten)'),
                          ('step-04', 'step-05', 'A phone field added to Student'),
                          ('step-07', 'step-08', 'A function moved from a service to a repository'),
                          ('step-10', 'step-11', 'An unused function deleted')]}
QUESTIONS = {'StudentManagement': ['where is an email checked?', 'how are students saved?', 'what happens when a course is full?', 'how does a student enroll in a course?']}


def main():
    folder = (Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / 'StudentManagement').resolve()
    name = folder.name
    head = run_git(folder, 'rev-parse', '--short', 'HEAD').decode().strip()
    report = analyze_repository(folder)
    main_graph, _ = lab_data.build_graph(report, f'working folder (commit {head})', name)
    reviews, graphs = [], {}
    for base, new, label in COMPARISONS.get(name, []):
        keep = {}
        change = analyze_change_impact(folder, base, new, include_possible=True, keep=keep)
        review, graph = lab_data.build_review(change, build_view(change, str(folder)), keep['reports'], str(folder), name)
        review['label'], review['base'], review['head'] = label, base, new
        reviews.append(review)
        graphs[review['graph']] = graph
    data = lab_data.payload(name, main_graph, reviews, graphs)
    data['questions'] = QUESTIONS.get(name, data['questions'])
    out = Path(__file__).with_name('data.js')
    out.write_text('window.PROTO = ' + json.dumps(data, separators=(',', ':')) + ';\n', encoding='utf-8')
    print(f'{out.name}: {out.stat().st_size // 1024} KB | graphs {list(data["graphs"])} | '
          + ' | '.join(f"{r['base']}..{r['head']}: {len(r['concerns'])} concerns, {len(r['tests'])} tests" for r in reviews))


if __name__ == '__main__':
    main()
