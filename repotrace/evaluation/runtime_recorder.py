"""Record which project functions really call which while a command runs.

THIS SCRIPT EXECUTES THE TARGET'S CODE. It is an evaluation tool, separate from the
static analyzer, and should only be pointed at a project you chose and a copy you can
throw away. It stays inside the standard library, blocks network connections unless told
otherwise, and writes only the JSON file you name.

Example:
  python3 -m repotrace.evaluation.runtime_recorder --root ./copy --path src --cwd ./scratch \\
      --env SHOPKEEP_DB=:memory: --output trace.json -m shopkeep.scenarios

Each recorded edge is: caller function, the line in the caller where the call happened,
callee function, how many times, and whether library code sat between them.
"""
import argparse
import json
import os
import runpy
import socket
import sys
import threading
import time
from collections import Counter

EXCLUDED_PARTS = {'.venv', 'venv', 'site-packages', '__pycache__', '.git', 'node_modules'}
COMPREHENSIONS = {'<listcomp>', '<setcomp>', '<dictcomp>', '<genexpr>'}
MAX_EDGES = 200_000
CO_VARARGS, CO_VARKEYWORDS = 0x04, 0x08


def is_forwarding_shape(code):
    """def wrapper(*args, **kwargs): the usual shape of a decorator wrapper."""
    return (code.co_argcount == 0 and code.co_kwonlyargcount == 0
            and getattr(code, 'co_posonlyargcount', 0) == 0
            and code.co_flags & (CO_VARARGS | CO_VARKEYWORDS) == (CO_VARARGS | CO_VARKEYWORDS))


class Recorder:
    def __init__(self, root, collapse_wrappers=True, record_lines=False):
        self.collapse_wrappers = collapse_wrappers
        self.record_lines = record_lines
        self.executed = {}
        self.root = os.path.realpath(root)
        self.prefix = self.root + os.sep
        self.cache = {}
        self.edges = Counter()
        self.calls = 0
        self.truncated = False

    def classify(self, code):
        """(relative file, function name, first line) for project code, else None."""
        try:
            return self.cache[code]
        except KeyError:
            pass
        result, path = None, code.co_filename
        if path and not path.startswith('<'):
            real = os.path.realpath(path)
            if real.startswith(self.prefix):
                relative = real[len(self.prefix):].replace(os.sep, '/')
                if not EXCLUDED_PARTS.intersection(relative.split('/')):
                    result = (relative, code.co_name, code.co_firstlineno)
        self.cache[code] = result
        return result

    def profile(self, frame, event, arg):
        if event != 'call':
            return
        callee = self.classify(frame.f_code)
        if callee is None or callee[1] == '<module>' or callee[1] in COMPREHENSIONS:
            return
        if self.collapse_wrappers and is_forwarding_shape(frame.f_code):
            return  # the real function behind the wrapper is recorded instead
        self.calls += 1
        caller, call_line, through, via_wrapper = None, None, False, False
        walker = frame.f_back
        while walker is not None:
            key = self.classify(walker.f_code)
            if key is None:
                if caller is None and call_line is None:
                    through = True
                walker = walker.f_back
                continue
            if self.collapse_wrappers and is_forwarding_shape(walker.f_code):
                via_wrapper = True
                walker = walker.f_back
                continue
            if call_line is None:
                call_line = walker.f_lineno
            if key[1] in COMPREHENSIONS:
                # The analyzer attributes comprehension calls to the enclosing function.
                walker = walker.f_back
                continue
            caller = key
            break
        edge = (caller, call_line, callee, through, via_wrapper)
        if edge not in self.edges and len(self.edges) >= MAX_EDGES:
            self.truncated = True
            return
        self.edges[edge] += 1

    def trace_calls(self, frame, event, arg):
        if self.classify(frame.f_code) is None:
            return None
        return self.trace_lines

    def trace_lines(self, frame, event, arg):
        if event == 'line':
            key = self.classify(frame.f_code)
            if key is not None:
                self.executed.setdefault(key[0], set()).add(frame.f_lineno)
        return self.trace_lines

    def install(self):
        sys.setprofile(self.profile)
        threading.setprofile(self.profile)
        if self.record_lines:
            sys.settrace(self.trace_calls)
            threading.settrace(self.trace_calls)

    def remove(self):
        sys.setprofile(None)
        threading.setprofile(None)
        if self.record_lines:
            sys.settrace(None)
            threading.settrace(None)

    def as_json(self):
        def function(key):
            return None if key is None else {'file': key[0], 'name': key[1], 'line': key[2]}
        edges = [{'caller': function(c), 'call_line': line, 'callee': function(e),
                  'through_library': through, 'via_wrapper': via, 'count': count}
                 for (c, line, e, through, via), count in sorted(
                     self.edges.items(), key=lambda item: (str(item[0][0]), item[0][1] or 0, str(item[0][2])))]
        return edges


def block_network():
    def refuse(*args, **kwargs):
        raise OSError('Network access is blocked by runtime_recorder (use --allow-network).')
    original_connect = socket.socket.connect

    def connect(self, address):
        if self.family == getattr(socket, 'AF_UNIX', None):
            return original_connect(self, address)
        return refuse()
    socket.socket.connect = connect
    socket.socket.connect_ex = lambda self, address: refuse()
    socket.getaddrinfo = refuse
    socket.create_connection = refuse


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', required=True, help='Project folder whose functions are recorded')
    parser.add_argument('--path', action='append', default=[], help='Relative import root added to sys.path (repeatable)')
    parser.add_argument('--cwd', help='Run with this working directory (use a scratch folder)')
    parser.add_argument('--env', action='append', default=[], metavar='KEY=VALUE')
    parser.add_argument('--output', required=True, help='JSON file to write')
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--keep-wrappers', action='store_true',
                        help='Record decorator wrapper frames as separate steps (default: collapse them)')
    parser.add_argument('--lines', action='store_true', help='Also record which project lines ran')
    parser.add_argument('-m', dest='module', help='Run a module, e.g. -m pytest')
    parser.add_argument('script_and_args', nargs=argparse.REMAINDER, help='Script path (if no -m), then its arguments')
    args = parser.parse_args(argv)
    if not args.module and not args.script_and_args:
        parser.error('Give either -m MODULE [arguments] or a script path.')
    rest = args.script_and_args[1:] if args.script_and_args and args.script_and_args[0] == '--' else args.script_and_args
    root = os.path.realpath(args.root)
    for item in args.env:
        key, _, value = item.partition('=')
        os.environ[key] = value
    for relative in reversed(args.path):
        sys.path.insert(0, os.path.join(root, relative))
    if args.cwd:
        os.makedirs(args.cwd, exist_ok=True)
        os.chdir(args.cwd)
    if not args.allow_network:
        block_network()
    recorder = Recorder(root, collapse_wrappers=not args.keep_wrappers, record_lines=args.lines)
    exit_code, error, started = 0, None, time.time()
    recorder.install()
    try:
        if args.module:
            sys.argv = [args.module] + rest
            runpy.run_module(args.module, run_name='__main__', alter_sys=True)
        else:
            script = rest[0]
            sys.argv = rest
            sys.path.insert(0, os.path.dirname(os.path.realpath(script)))
            runpy.run_path(script, run_name='__main__')
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except BaseException as exc:  # record the failure; the partial trace is still useful
        exit_code, error = 1, f'{type(exc).__name__}: {exc}'
    finally:
        recorder.remove()
    result = {
        'schema_version': '0.1', 'report_kind': 'runtime_trace', 'root': root,
        'python': sys.version.split()[0], 'command': ([f'-m {args.module}'] if args.module else rest) + [],
        'exit_code': exit_code, 'error': error, 'seconds': round(time.time() - started, 2),
        'network_blocked': not args.allow_network, 'calls_observed': recorder.calls,
        'edges_truncated': recorder.truncated, 'wrappers_collapsed': not args.keep_wrappers,
        'edges': recorder.as_json(),
        'executed_lines': {f: sorted(lines) for f, lines in sorted(recorder.executed.items())},
        'limitations': [
            'Only code paths that actually ran are recorded; unrun code is unknown, not wrong.',
            'Lines are positions in the files as they were when run.',
            'Library frames between two project functions set through_library.',
            'Wrapper-shaped functions (*args, **kwargs) are skipped when wrappers are collapsed.']}
    with open(args.output, 'w', encoding='utf-8') as stream:
        json.dump(result, stream, indent=1)
    print(f'Recorded {len(result["edges"])} distinct edges ({recorder.calls} calls); exit {exit_code}.')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
