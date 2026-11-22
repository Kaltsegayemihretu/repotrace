"""One front door:  python3 -m repotrace [command] ...

    python3 -m repotrace                    open the app in your browser (same as `app`)
    python3 -m repotrace --lab              open the new guided screens (the real engine, live, on any folder)
    python3 -m repotrace app [folder]       the local web app
    python3 -m repotrace change REPO ...    compare two commits from the terminal
    python3 -m repotrace map FOLDER ...     map one project (no Git needed) into a JSON report
    python3 -m repotrace impact REPORT ...  ask a saved map what a function affects
    python3 -m repotrace arrows REPORT      print a saved map as plain arrows
"""
import runpy
import sys

COMMANDS = {
    'app': 'repotrace.app.server',
    'change': 'repotrace.cli.change',
    'map': 'repotrace.mapping.repository',
    'impact': 'repotrace.cli.impact',
    'arrows': 'repotrace.cli.arrows',
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] in (['-h'], ['--help'], ['help']):
        print(__doc__)
        return 0
    name = argv[0] if argv and argv[0] in COMMANDS else 'app'
    rest = argv[1:] if argv and argv[0] in COMMANDS else argv
    sys.argv = [f'repotrace {name}'] + rest
    runpy.run_module(COMMANDS[name], run_name='__main__')
    return 0


if __name__ == '__main__':
    sys.exit(main())
