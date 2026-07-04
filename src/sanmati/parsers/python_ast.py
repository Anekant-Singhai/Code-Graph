from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sanmati.models import CodeGraph
from sanmati.parsers.base import AnalysisContext
from sanmati.scanner import ProjectFile


DATA_KIND_CALLS = {
    "json.load": "json",
    "json.loads": "json",
    "json.dump": "json",
    "json.dumps": "json",
    "yaml.safe_load": "yaml",
    "yaml.load": "yaml",
    "tomllib.load": "toml-config",
    "tomllib.loads": "toml-config",
    "configparser.ConfigParser": "ini-config",
    "ET.parse": "xml",
    "ElementTree.parse": "xml",
    "xml.etree.ElementTree.parse": "xml",
    "csv.reader": "csv",
    "csv.DictReader": "csv",
    "pandas.read_csv": "csv",
    "pd.read_csv": "csv",
    "pandas.read_json": "json",
    "pd.read_json": "json",
    "requests.get": "api-response",
    "requests.post": "api-response",
    "httpx.get": "api-response",
    "httpx.post": "api-response",
    "torch.tensor": "tensor",
    "torch.Tensor": "tensor",
    "torch.from_numpy": "tensor",
    "tensorflow.constant": "tensor",
    "tf.constant": "tensor",
    "numpy.array": "array",
    "np.array": "array",
    "PIL.Image.open": "image",
    "Image.open": "image",
    "open": "file",
}

SINK_CALLS = {
    "print": "stdout",
    "logging.info": "log",
    "logging.warning": "log",
    "logging.error": "log",
    "json.dump": "json-file",
    "Path.write_text": "text-file",
    "write_text": "text-file",
    "write": "file",
    "pandas.DataFrame.to_csv": "csv-file",
    "to_csv": "csv-file",
    "requests.post": "api",
    "httpx.post": "api",
}


@dataclass(slots=True)
class Scope:
    node_id: str
    name: str
    kind: str


class PythonParser:
    name = "python-ast"

    def can_parse(self, project_file: ProjectFile) -> bool:
        return project_file.suffix == ".py"

    def parse(self, project_file: ProjectFile, graph: CodeGraph, context: AnalysisContext) -> None:
        file_id = graph.file_node_id(project_file.path)
        graph.add_node(
            file_id,
            "file",
            project_file.relative_path,
            path=project_file.path,
            category="python",
            size_bytes=project_file.size_bytes,
        )
        try:
            text = project_file.path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = project_file.path.read_text(encoding="cp1252")
        except OSError as exc:
            graph.nodes[file_id].properties["parse_error"] = str(exc)
            return

        module_name = _module_name(project_file.path, graph.root)
        module_id = f"module:{module_name}"
        graph.add_node(module_id, "module", module_name, path=project_file.path)
        graph.add_edge(file_id, module_id, "DEFINES_MODULE")

        try:
            tree = ast.parse(text, filename=str(project_file.path))
        except SyntaxError as exc:
            graph.nodes[file_id].properties["parse_error"] = f"Syntax error: {exc}"
            return

        visitor = _PythonAstVisitor(
            graph=graph,
            context=context,
            file_id=file_id,
            module_id=module_id,
            module_name=module_name,
            path=project_file.path,
        )
        visitor.visit(tree)
        graph.nodes[module_id].properties["docstring"] = ast.get_docstring(tree)
        graph.nodes[module_id].properties["line_count"] = text.count("\n") + 1


class _PythonAstVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        graph: CodeGraph,
        context: AnalysisContext,
        file_id: str,
        module_id: str,
        module_name: str,
        path: Path,
    ) -> None:
        self.graph = graph
        self.context = context
        self.file_id = file_id
        self.module_id = module_id
        self.module_name = module_name
        self.path = path
        self.scope_stack: list[Scope] = [Scope(module_id, module_name, "module")]
        self.variable_stack: list[dict[str, str]] = [{}]
        self.import_aliases: dict[str, str] = {}

    @property
    def current_scope(self) -> Scope:
        return self.scope_stack[-1]

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.name
            local = alias.asname or name.split(".")[0]
            self.import_aliases[local] = name
            dep_id = self.graph.dependency_node_id(name)
            self.graph.add_node(dep_id, "dependency", name, ecosystem="python", line=node.lineno)
            self.graph.add_edge(self.current_scope.node_id, dep_id, "IMPORTS", line=node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            imported = f"{module}.{alias.name}".strip(".")
            local = alias.asname or alias.name
            self.import_aliases[local] = imported
            dep_id = self.graph.dependency_node_id(imported)
            self.graph.add_node(dep_id, "dependency", imported, ecosystem="python", line=node.lineno)
            self.graph.add_edge(self.current_scope.node_id, dep_id, "IMPORTS", line=node.lineno)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        class_id = f"class:{self.module_name}.{node.name}"
        bases = [_safe_unparse(base) for base in node.bases]
        decorators = [_safe_unparse(item) for item in node.decorator_list]
        self.graph.add_node(
            class_id,
            "class",
            node.name,
            path=self.path,
            line=node.lineno,
            qualified_name=f"{self.module_name}.{node.name}",
            bases=bases,
            decorators=decorators,
            docstring=ast.get_docstring(node),
        )
        self.graph.add_edge(self.current_scope.node_id, class_id, "CONTAINS")
        self.scope_stack.append(Scope(class_id, node.name, "class"))
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_async=True)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_async: bool) -> None:
        owner = self.current_scope
        if owner.kind == "class":
            qualified = f"{self.module_name}.{owner.name}.{node.name}"
            kind = "method"
        else:
            qualified = f"{self.module_name}.{node.name}"
            kind = "function"
        function_id = f"{kind}:{qualified}"
        args = [arg.arg for arg in node.args.args]
        decorators = [_safe_unparse(item) for item in node.decorator_list]
        returns = _safe_unparse(node.returns) if node.returns is not None else None
        self.graph.add_node(
            function_id,
            kind,
            node.name,
            path=self.path,
            line=node.lineno,
            qualified_name=qualified,
            args=args,
            decorators=decorators,
            returns=returns,
            async_function=is_async,
            docstring=ast.get_docstring(node),
        )
        self.graph.add_edge(owner.node_id, function_id, "CONTAINS")
        if node.name == "main" or any("click.command" in item or "typer" in item for item in decorators):
            self.graph.add_edge(self.file_id, function_id, "ENTRY_POINT", reason="main_or_cli_decorator")

        self.scope_stack.append(Scope(function_id, node.name, kind))
        self.variable_stack.append({})
        for arg in args:
            parameter_id = self._declare_variable(arg, line=node.lineno, parameter=True)
            self.graph.add_edge(function_id, parameter_id, "HAS_PARAMETER", line=node.lineno)
        for statement in node.body:
            self.visit(statement)
        self.variable_stack.pop()
        self.scope_stack.pop()

    def visit_If(self, node: ast.If) -> None:
        condition = _safe_unparse(node.test)
        condition_id = f"condition:{self.current_scope.node_id}:{node.lineno}"
        self.graph.add_node(
            condition_id,
            "condition",
            condition,
            path=self.path,
            line=node.lineno,
            expression=condition,
        )
        self.graph.add_edge(self.current_scope.node_id, condition_id, "HAS_CONDITION")
        if self.current_scope.kind == "module" and _is_main_guard(node.test):
            self.graph.add_edge(self.file_id, self.current_scope.node_id, "ENTRY_POINT", reason="if __name__ == '__main__'")

        for call in _calls_inside(node.body):
            target = self._call_target_id(call)
            self.graph.add_edge(
                self.current_scope.node_id,
                target,
                "CALLS_UNDER_CONDITION",
                condition=condition,
                line=getattr(call, "lineno", None),
            )

        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node, "for")

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node, "async for")

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node, "while")

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While, loop_kind: str) -> None:
        expression = _safe_unparse(node)
        loop_id = f"loop:{self.current_scope.node_id}:{node.lineno}"
        self.graph.add_node(
            loop_id,
            "loop",
            loop_kind,
            path=self.path,
            line=node.lineno,
            expression=expression,
        )
        self.graph.add_edge(self.current_scope.node_id, loop_id, "HAS_LOOP")
        if isinstance(node, (ast.For, ast.AsyncFor)):
            for target_name in _assigned_names(node.target):
                target_id = self._declare_variable(target_name, line=node.lineno, value_expression=f"for target in {_safe_unparse(node.iter)}")
                self.graph.add_edge(loop_id, target_id, "DECLARES_VARIABLE", line=node.lineno, reason="loop target")
                for source_name in _loaded_names(node.iter):
                    source_id = self._lookup_variable(source_name, line=node.lineno)
                    self.graph.add_edge(source_id, target_id, "ITERATES_TO", line=node.lineno, reason="loop iteration value flow")
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_assignment(node.targets, node.value, getattr(node, "lineno", None))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        targets = [node.target]
        self._record_assignment(targets, node.value, getattr(node, "lineno", None), annotation=_safe_unparse(node.annotation))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = self._resolve_call_name(node.func)
        target_id = self._call_target_id(node)
        self.graph.add_node(target_id, "call_target", call_name, resolved=False)
        self.graph.add_edge(self.current_scope.node_id, target_id, "CALLS", line=node.lineno)
        callsite_id = self._callsite_id(node)
        self.graph.add_node(
            callsite_id,
            "callsite",
            call_name,
            path=self.path,
            line=node.lineno,
            call_name=call_name,
            expression=_safe_unparse(node),
        )
        self.graph.add_edge(self.current_scope.node_id, callsite_id, "HAS_CALLSITE", line=node.lineno)
        self.graph.add_edge(callsite_id, target_id, "CALLS", line=node.lineno, reason="callsite targets call symbol")
        self._connect_argument_flows(node, callsite_id)

        data_kind = self._data_kind_for_call(call_name)
        if data_kind:
            self._add_data_kind_edge(self.current_scope.node_id, data_kind, line=node.lineno, call=call_name)
        sink = self._sink_for_call(call_name)
        if sink:
            sink_id = f"sink:{sink}"
            self.graph.add_node(sink_id, "sink", sink)
            self.graph.add_edge(self.current_scope.node_id, sink_id, "WRITES_TO", line=node.lineno, call=call_name)

        for literal in _argument_string_literals(node):
            matched_file = self._connect_literal_file(literal, line=node.lineno)
            if matched_file is not None or self._call_consumes_data_literal(call_name):
                self._connect_literal_data_kind(literal, line=node.lineno, reason="path literal at data/file call")

        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        return

    def visit_Return(self, node: ast.Return) -> None:
        return_id = f"return:{self.current_scope.node_id}:{node.lineno}"
        self.graph.add_node(
            return_id,
            "return",
            "return",
            path=self.path,
            line=node.lineno,
            expression=_safe_unparse(node.value),
        )
        self.graph.add_edge(self.current_scope.node_id, return_id, "RETURNS", line=node.lineno)
        if node.value is not None:
            for name in _loaded_names(node.value):
                source_id = self._lookup_variable(name, line=node.lineno)
                self.graph.add_edge(source_id, return_id, "RETURNS_VALUE", line=node.lineno)
        self.generic_visit(node)

    def _record_assignment(
        self,
        targets: Iterable[ast.expr],
        value: ast.expr | None,
        line: int | None,
        *,
        annotation: str | None = None,
    ) -> None:
        for target in targets:
            name = _assignment_target_name(target)
            if not name:
                continue
            var_id = self._declare_variable(
                name,
                line=line,
                annotation=annotation,
                value_expression=_safe_unparse(value) if value is not None else None,
            )
            self.graph.add_edge(self.current_scope.node_id, var_id, "DECLARES_VARIABLE")
            if value is not None:
                for source_name in _loaded_names(value):
                    source_id = self._lookup_variable(source_name, line=line)
                    if source_id != var_id:
                        self.graph.add_edge(source_id, var_id, "DATA_FLOWS_TO", line=line, reason="assignment expression")
                call_names = [self._resolve_call_name(call.func) for call in ast.walk(value) if isinstance(call, ast.Call)]
                for call_name in call_names:
                    call_id = f"call:{call_name}"
                    self.graph.add_node(call_id, "call_target", call_name, resolved=False)
                    self.graph.add_edge(call_id, var_id, "ASSIGNS_CALL_RESULT", line=line, reason="call result assigned")
                    data_kind = self._data_kind_for_call(call_name)
                    if data_kind:
                        self._add_data_kind_edge(var_id, data_kind, line=line, call=call_name)

    def _call_target_id(self, node: ast.Call) -> str:
        call_name = self._resolve_call_name(node.func)
        return f"call:{call_name}"

    def _callsite_id(self, node: ast.Call) -> str:
        return f"callsite:{self.current_scope.node_id}:{node.lineno}:{node.col_offset}"

    def _resolve_call_name(self, node: ast.AST) -> str:
        raw = _dotted_name(node)
        if not raw:
            return _safe_unparse(node)
        first, *rest = raw.split(".")
        if first in self.import_aliases:
            resolved = self.import_aliases[first]
            return ".".join([resolved, *rest]) if rest else resolved
        return raw

    def _data_kind_for_call(self, call_name: str) -> str | None:
        if call_name in DATA_KIND_CALLS:
            return DATA_KIND_CALLS[call_name]
        return None

    def _sink_for_call(self, call_name: str) -> str | None:
        if call_name in SINK_CALLS:
            return SINK_CALLS[call_name]
        for suffix, sink in SINK_CALLS.items():
            if call_name.endswith("." + suffix):
                return sink
        return None

    def _add_data_kind_edge(self, source_id: str, data_kind: str, *, line: int | None, call: str) -> None:
        data_id = self.graph.data_kind_node_id(data_kind)
        self.graph.add_node(data_id, "data_kind", data_kind)
        self.graph.add_edge(source_id, data_id, "USES_DATA_KIND", line=line, call=call)

    def _connect_literal_file(self, value: str, *, line: int | None) -> ProjectFile | None:
        match = self.context.match_string_to_file(value)
        if match is None:
            return None
        target_id = self.graph.file_node_id(match.path)
        if target_id not in self.graph.nodes:
            self.graph.add_node(target_id, "file", match.relative_path, path=match.path, category=match.category)
        self.graph.add_edge(self.current_scope.node_id, target_id, "REFERENCES_FILE", line=line, literal=value)
        if match.category in {"config", "dependency-manifest"}:
            self.graph.add_edge(self.current_scope.node_id, target_id, "DEPENDS_ON_CONFIG", line=line, literal=value)
        return match

    def _connect_literal_data_kind(self, value: str, *, line: int | None, reason: str) -> None:
        suffix_map = {
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".xml": "xml",
            ".csv": "csv",
            ".txt": "text",
            ".png": "image",
            ".jpg": "image",
            ".jpeg": "image",
        }
        lowered = value.lower()
        for suffix, data_kind in suffix_map.items():
            if lowered.endswith(suffix):
                data_id = self.graph.data_kind_node_id(data_kind)
                self.graph.add_node(data_id, "data_kind", data_kind)
                self.graph.add_edge(
                    self.current_scope.node_id,
                    data_id,
                    "USES_DATA_KIND",
                    line=line,
                    call="path-literal",
                    reason=reason,
                    confidence=0.7,
                )
                return

    def _connect_argument_flows(self, node: ast.Call, callsite_id: str) -> None:
        for index, arg in enumerate(node.args):
            for name in _loaded_names(arg):
                source_id = self._lookup_variable(name, line=getattr(arg, "lineno", None))
                self.graph.add_edge(source_id, callsite_id, "PASSES_ARGUMENT", line=getattr(arg, "lineno", None), argument_index=index)
        for keyword in node.keywords:
            if keyword.value is None:
                continue
            for name in _loaded_names(keyword.value):
                source_id = self._lookup_variable(name, line=getattr(keyword.value, "lineno", None))
                self.graph.add_edge(
                    source_id,
                    callsite_id,
                    "PASSES_ARGUMENT",
                    line=getattr(keyword.value, "lineno", None),
                    argument_name=keyword.arg,
                )
        receiver = _call_receiver_name(node.func)
        if receiver:
            source_id = self._lookup_variable(receiver, line=node.lineno)
            self.graph.add_edge(source_id, callsite_id, "RECEIVER_VALUE", line=node.lineno, reason="method receiver value flow")

    def _call_consumes_data_literal(self, call_name: str) -> bool:
        return call_name in DATA_KIND_CALLS or call_name in {
            "pathlib.Path",
            "Path",
            "open",
            "builtins.open",
        }

    def _declare_variable(
        self,
        name: str,
        *,
        line: int | None,
        annotation: str | None = None,
        value_expression: str | None = None,
        parameter: bool = False,
    ) -> str:
        var_id = f"variable:{self.current_scope.node_id}:{name}"
        self.graph.add_node(
            var_id,
            "variable",
            name,
            path=self.path,
            line=line,
            annotation=annotation,
            value_expression=value_expression,
            parameter=parameter,
        )
        if _is_simple_name(name):
            self.variable_stack[-1][name] = var_id
        return var_id

    def _lookup_variable(self, name: str, *, line: int | None) -> str:
        for scope in reversed(self.variable_stack):
            if name in scope:
                return scope[name]
        var_id = f"variable:{self.current_scope.node_id}:{name}"
        self.graph.add_node(var_id, "variable", name, path=self.path, line=line, inferred=True)
        self.variable_stack[-1][name] = var_id
        return var_id


def _module_name(path: Path, root: Path) -> str:
    relative = path.resolve().relative_to(root.resolve()).with_suffix("")
    parts = [part for part in relative.parts if part != "__init__"]
    return ".".join(parts) if parts else path.stem


def _safe_unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return node.__class__.__name__


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
        return node.attr
    return None


def _assignment_target_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _dotted_name(node)
    if isinstance(node, ast.Tuple):
        names = [_assignment_target_name(item) for item in node.elts]
        return ", ".join(name for name in names if name)
    return None


def _assigned_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for item in node.elts:
            names.extend(_assigned_names(item))
        return names
    return []


def _loaded_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load):
            names.append(item.id)
    return sorted(set(names))


def _call_receiver_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id
    return None


def _is_simple_name(name: str) -> bool:
    return name.isidentifier()


def _calls_inside(statements: Iterable[ast.stmt]) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for statement in statements:
        calls.extend(item for item in ast.walk(statement) if isinstance(item, ast.Call))
    return calls


def _string_literals(node: ast.AST) -> list[str]:
    return [item.value for item in ast.walk(node) if isinstance(item, ast.Constant) and isinstance(item.value, str)]


def _argument_string_literals(node: ast.Call) -> list[str]:
    literals: list[str] = []
    for item in [*node.args, *(keyword.value for keyword in node.keywords if keyword.value is not None)]:
        literals.extend(_string_literals_without_nested_calls(item))
    return literals


def _string_literals_without_nested_calls(node: ast.AST) -> list[str]:
    literals: list[str] = []

    class LiteralVisitor(ast.NodeVisitor):
        def visit_Call(self, call_node: ast.Call) -> None:
            return

        def visit_Constant(self, constant_node: ast.Constant) -> None:
            if isinstance(constant_node.value, str):
                literals.append(constant_node.value)

    LiteralVisitor().visit(node)
    return literals


def _is_main_guard(node: ast.AST) -> bool:
    text = _safe_unparse(node)
    return "__name__" in text and "__main__" in text
