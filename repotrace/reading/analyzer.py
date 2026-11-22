"""Read one Python file and link simple local calls without executing it."""
import argparse
import ast
import hashlib
import io
import json
import platform
import tokenize
from pathlib import Path
from repotrace.reading.resolver import resolve_local_calls

MAX_FILE_BYTES = 2 * 1024 * 1024


def expression(node):
    """Turn an expression into readable text; keep missing values as None."""
    return ast.unparse(node) if node is not None else None


def location(node):
    # AST columns are zero-based UTF-8 byte offsets, not character positions.
    return {
        "line": node.lineno,
        "column": node.col_offset,
        "end_line": node.end_lineno,
        "end_column": node.end_col_offset,
    }


def parameters(arguments):
    """Read all five parameter kinds and align their defaults correctly."""
    records = []

    def add(arg, kind, default=None, required=False):
        records.append({
            "name": arg.arg,
            "kind": kind,
            "annotation": expression(arg.annotation),
            "default": expression(default),
            "required": required,
        })

    positional = arguments.posonlyargs + arguments.args
    default_start = len(positional) - len(arguments.defaults)
    for index, arg in enumerate(positional):
        kind = ("POSITIONAL_ONLY" if index < len(arguments.posonlyargs)
                else "POSITIONAL_OR_KEYWORD")
        default = arguments.defaults[index - default_start] if index >= default_start else None
        add(arg, kind, default, index < default_start)

    if arguments.vararg:
        add(arguments.vararg, "VAR_POSITIONAL")
    for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        add(arg, "KEYWORD_ONLY", default, default is None)
    if arguments.kwarg:
        add(arguments.kwarg, "VAR_KEYWORD")
    return records


class CodeExtractor(ast.NodeVisitor):
    def __init__(self, source, file_key):
        self.source = source
        self.file_key = file_key
        self.module_id = file_key + "::module"
        self.scopes = [(self.module_id, "MODULE", "")]
        self.context = "MODULE_BODY"
        self.symbols = []
        self.imports = []
        self.calls = []
        self.edges = []
        self.id_counts = {}

    def unique_id(self, base):
        """Some Python versions give f-string nodes identical source spans."""
        count = self.id_counts.get(base, 0) + 1
        self.id_counts[base] = count
        return base if count == 1 else f"{base}#{count}"

    def visit_with_context(self, nodes, context):
        previous = self.context
        self.context = context
        try:
            for node in nodes:
                if isinstance(node, ast.AST):
                    self.visit(node)
        finally:
            self.context = previous

    def visit_definition(self, node):
        parent_id, parent_kind, parent_name = self.scopes[-1]
        qualified_name = parent_name + "." + node.name if parent_name else node.name
        is_class = isinstance(node, ast.ClassDef)
        kind = "CLASS" if is_class else "METHOD" if parent_kind == "CLASS" else "FUNCTION"
        if not is_class and parent_kind in {"FUNCTION", "METHOD", "LAMBDA"}:
            kind = "NESTED_FUNCTION"
        # Include position to distinguish repeated definitions with the same name.
        identifier = self.unique_id(f"{self.file_key}::{qualified_name}@{node.lineno}:{node.col_offset}")
        first_line = min([node.lineno] + [d.lineno for d in node.decorator_list])
        source_segment = "\n".join(self.source.splitlines()[first_line - 1:node.end_lineno])
        record = {
            "id": identifier, "name": node.name, "qualified_name": qualified_name,
            "kind": kind, "is_async": isinstance(node, ast.AsyncFunctionDef),
            "parent_id": parent_id, "file": self.file_key,
            "span": location(node), "start_line_including_decorators": first_line,
            "docstring": ast.get_docstring(node, clean=False),
            "decorators": [expression(d) for d in node.decorator_list],
            "source": source_segment,
            "syntax_sha256": hashlib.sha256(
                ast.dump(node, include_attributes=False).encode("utf-8")
            ).hexdigest(),
        }
        if is_class:
            record["bases"] = [expression(b) for b in node.bases]
            record["class_keywords"] = [
                {"name": k.arg, "value": expression(k.value)} for k in node.keywords
            ]
        else:
            record["parameters"] = parameters(node.args)
            record["return_annotation"] = expression(node.returns)
            record["type_comment"] = node.type_comment
        record["type_parameters"] = [expression(p) for p in getattr(node, "type_params", [])]
        self.symbols.append(record)
        self.edges.append({"kind": "CONTAINS", "source": parent_id,
                           "target": identifier, "evidence": location(node)})

        # These expressions are outside the new function/class body.
        self.visit_with_context(node.decorator_list, "DECORATOR")
        self.visit_with_context(getattr(node, "type_params", []), "TYPE_PARAMETER")
        if is_class:
            self.visit_with_context(node.bases, "CLASS_BASE")
            self.visit_with_context(node.keywords, "CLASS_KEYWORD")
        else:
            self.visit_signature(node.args)
            self.visit_with_context([node.returns], "ANNOTATION")

        self.scopes.append((identifier, "CLASS" if is_class else "FUNCTION", qualified_name))
        try:
            self.visit_with_context(node.body, "CLASS_BODY" if is_class else "FUNCTION_BODY")
        finally:
            self.scopes.pop()

    visit_FunctionDef = visit_definition
    visit_AsyncFunctionDef = visit_definition
    visit_ClassDef = visit_definition

    def visit_signature(self, args):
        self.visit_with_context(args.defaults + args.kw_defaults, "DEFAULT_VALUE")
        all_args = args.posonlyargs + args.args + args.kwonlyargs
        all_args += [arg for arg in (args.vararg, args.kwarg) if arg is not None]
        self.visit_with_context([arg.annotation for arg in all_args], "ANNOTATION")

    def visit_Lambda(self, node):
        self.visit_signature(node.args)
        parent_id, _, parent_name = self.scopes[-1]
        name = f"<lambda>@{node.lineno}:{node.col_offset}"
        qualified = parent_name + "." + name if parent_name else name
        identifier = self.unique_id(self.file_key + "::" + qualified)
        self.symbols.append({"id": identifier, "kind": "LAMBDA", "name": name,
                             "qualified_name": qualified, "parent_id": parent_id,
                             "file": self.file_key, "span": location(node),
                             "parameters": parameters(node.args),
                             "source": ast.get_source_segment(self.source, node)})
        self.edges.append({"kind": "CONTAINS", "source": parent_id,
                           "target": identifier, "evidence": location(node)})
        self.scopes.append((identifier, "LAMBDA", qualified))
        try:
            self.visit_with_context([node.body], "LAMBDA_BODY")
        finally:
            self.scopes.pop()

    def visit_AnnAssign(self, node):
        self.visit(node.target)
        self.visit_with_context([node.annotation], "ANNOTATION")
        if node.value is not None:
            self.visit(node.value)

    def visit_Call(self, node):
        self.calls.append({
            "id": self.unique_id(f"{self.file_key}::call@{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}"),
            "enclosing_symbol_id": self.scopes[-1][0], "context": self.context,
            "file": self.file_key, "span": location(node),
            "expression": expression(node.func),
            "arguments": [expression(a) for a in node.args],
            "keywords": [{"name": k.arg, "value": expression(k.value)} for k in node.keywords],
            "target_id": None, "resolution": "UNRESOLVED",
            "reason": "Awaiting scope-aware call resolution.",
        })
        self.generic_visit(node)

    def visit_Import(self, node):
        self.imports.append({
            "kind": type(node).__name__, "scope_id": self.scopes[-1][0],
            "file": self.file_key, "span": location(node),
            "module": getattr(node, "module", None), "level": getattr(node, "level", 0),
            "names": [{"name": a.name, "alias": a.asname} for a in node.names],
        })

    visit_ImportFrom = visit_Import


def analyze_file(filename, *, root=None, max_bytes=MAX_FILE_BYTES):
    """Read one file; root makes every file key relative to the project folder."""
    path = Path(filename).absolute()
    report = {
        "schema_version": "0.6", "report_kind": "single_file",
        "status": "error", "file": path.as_posix(),
        "analyzer_python": platform.python_version(), "diagnostics": [],
        "symbols": [], "imports": [], "calls": [], "edges": [],
        "limitations": [
            "Bare calls in functions and lambdas use lexical scope lookup and are linked as LIKELY.",
            "Reassignments and conditional definitions remain ambiguous; full execution order is not modeled.",
            "Dynamic namespace mutation and external monkey-patching are not modeled; impact analysis is pending.",
            "IDs are local to this project snapshot; matching across revisions or projects is not implemented.",
            "Calls inside branches and comprehensions are potential call sites, not execution guarantees.",
            "Comprehension calls, generic type scopes and annotation evaluation are not resolved yet.",
            "Python syntax support depends on the interpreter running this analyzer.",
        ],
    }
    stage = "identity"
    try:
        path = path.resolve()
        if root is not None:
            project_root = Path(root).resolve()
            if not project_root.is_dir():
                raise ValueError("The project root must be an existing directory.")
            file_key = path.relative_to(project_root).as_posix()
        else:
            file_key = path.as_posix()
        report.update(file=file_key, module_id=file_key + "::module",
                      file_key_kind="repository_relative" if root is not None else "absolute")
        if not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer.")
        stage = "read"
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError(f"File exceeds the {max_bytes}-byte analysis limit.")
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
        report.update({"encoding": encoding, "size_bytes": len(raw),
                       "content_sha256": hashlib.sha256(raw).hexdigest(),
                       "source": source, "line_count": len(source.splitlines())})
        stage = "parse"
        tree = ast.parse(source, filename=file_key, type_comments=True)
        stage = "extract"
        extractor = CodeExtractor(source, file_key)
        extractor.visit(tree)
        report.update({
            "status": "ok", "module_id": extractor.module_id,
            "docstring": ast.get_docstring(tree, clean=False),
            "symbols": extractor.symbols, "imports": extractor.imports,
            "calls": extractor.calls, "edges": extractor.edges,
        })
        stage = "resolve"
        resolve_local_calls(tree, source, report)
    except (OSError, UnicodeError, SyntaxError, ValueError, RecursionError, RuntimeError) as exc:
        report["status"] = "error"
        # A failed file must not contribute a half-finished graph.
        for field in ("symbols", "imports", "calls", "edges"):
            report[field] = []
        report.pop("resolution_summary", None)
        report["diagnostics"].append({"stage": stage, "type": type(exc).__name__,
                                      "message": str(exc), "line": getattr(exc, "lineno", None)})
    return report


def write_json_report(report, filename, *, overwrite=False, protected_paths=()):
    """Save a JSON report without accidentally replacing source or linked files."""
    output = Path(filename)
    if output.suffix.lower() != ".json":
        raise ValueError("Choose an output filename ending in .json.")
    if output.is_symlink():
        raise ValueError("The output must not be a symbolic link.")
    if output.exists():
        if output.stat().st_nlink > 1:
            raise ValueError("The output must not be a hard-linked file.")
        if any(output.samefile(source) for source in protected_paths if Path(source).exists()):
            raise ValueError("The output must not overwrite an analyzed source file.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w" if overwrite else "x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=True)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Python source file to read")
    parser.add_argument("--output", type=Path, help="Optional JSON output file")
    parser.add_argument("--root", type=Path, help="Project folder used for relative file identities")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing JSON report")
    parser.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES)
    args = parser.parse_args()
    if args.max_file_bytes < 1:
        parser.error("--max-file-bytes must be positive.")
    if args.output and args.output.resolve() == Path(args.file).resolve():
        parser.error("The output must not overwrite the input source file.")
    report = analyze_file(args.file, root=args.root, max_bytes=args.max_file_bytes)
    output = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if args.output:
        try:
            write_json_report(report, args.output, overwrite=args.overwrite,
                              protected_paths=[Path(args.file)])
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Cannot write report: {exc}\nUse --overwrite only to replace an existing JSON report.\n")
        print(f"Analysis {report['status']}: {args.output}")
    else:
        print(output, end="")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
