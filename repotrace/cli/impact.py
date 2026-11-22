"""Select a function in a saved repository report and list potential affected callers."""
import argparse
import json
from pathlib import Path

from repotrace.reading.analyzer import write_json_report
from repotrace.graph.graph_index import GraphIndex, require
from repotrace.graph.impact import analyze_impact


def load_report(filename, *, max_bytes=64 * 1024 * 1024):
    require(type(max_bytes) is int and max_bytes > 0, 'Report byte limit must be positive.')
    path = Path(filename)
    require(path.is_file(), 'Choose an existing regular JSON report file.')
    with path.open('rb') as stream:
        data = stream.read(max_bytes + 1)
    require(len(data) <= max_bytes, 'Report exceeds the byte limit; increase --max-report-bytes explicitly.')
    return json.loads(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path, help='Saved repository schema 0.2 or 0.3 JSON, not a source folder')
    parser.add_argument('--symbol', help='Exact symbol ID')
    parser.add_argument('--file', help='Exact report-relative file path, e.g. pricing.py')
    parser.add_argument('--function', help='Exact qualified name, e.g. UserService.save')
    parser.add_argument('--list-functions', action='store_true', help='List selectable IDs; optionally filter by --file')
    parser.add_argument('--max-depth', type=int, default=20)
    parser.add_argument('--max-nodes', type=int, default=1000)
    parser.add_argument('--max-edges', type=int, default=100_000)
    parser.add_argument('--max-report-bytes', type=int, default=64 * 1024 * 1024)
    parser.add_argument('--max-index-symbols', type=int, default=200_000)
    parser.add_argument('--max-index-calls', type=int, default=1_000_000)
    parser.add_argument('--include-possible', action='store_true',
                        help='Include uncertain method/constructor candidates; clearly marks their witness paths')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.output:
            require(args.output.resolve() != args.report.resolve(), 'Output must not overwrite the repository input report.')
        report = load_report(args.report, max_bytes=args.max_report_bytes)
        index = GraphIndex(report, max_symbols=args.max_index_symbols, max_calls=args.max_index_calls,
                           include_possible=args.include_possible)
        del report  # The index keeps graph metadata, not source code strings.
        if args.list_functions:
            require(args.symbol is None and args.function is None,
                    '--list-functions cannot be combined with --symbol or --function.')
            result = {'report_kind': 'function_list', 'schema_version': '0.1',
                      'functions': index.functions(args.file)}
        else:
            target = index.select(symbol_id=args.symbol, file=args.file, qualified_name=args.function)
            result = analyze_impact(index, target, max_depth=args.max_depth,
                                    max_nodes=args.max_nodes, max_edges=args.max_edges)
        if args.output:
            write_json_report(result, args.output, overwrite=args.overwrite, protected_paths=[args.report])
            print(f"Saved {result['report_kind']}: {args.output}")
            if 'summary' in result:
                s = result['summary']
                print(f"Potential callers: {s['direct_callers']} direct, {s['indirect_callers']} indirect.")
                if result['status'] == 'limited':
                    print('Traversal limited; check warnings and limits_reached.')
        else:
            print(json.dumps(result, indent=2))
        return 1 if result.get('status') == 'limited' else 0
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        parser.exit(2, f'Cannot build impact report: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
