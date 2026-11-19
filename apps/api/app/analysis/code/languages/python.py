"""Python feature extraction via the standard library ``ast`` module.

Because the whole module is parsed, every feature below is ``RELIABLE``. Comments,
string literals and docstrings are ignored by the AST, so they cannot distort
counts. The only non-reliable outcome is ``UNPARSEABLE``, which yields an all
``UNKNOWN`` feature set.

``direct_self_recursion`` means a call site inside the function's own body whose
callee name equals the function name. It covers direct self-calls only: mutual
recursion (A -> B -> A) is not detected. A same-name local shadowing the function
is a theoretical false positive that Python style makes extremely rare.
"""

import ast

from ..contract import Language
from ..features import Confidence, FeatureId, FeatureSet, FeatureValue, ParseStatus, unknown_set

_IO_NAMES = frozenset({"input", "print", "open"})
_CONTAINER_BUILTINS = frozenset({"list", "tuple", "set", "dict", "frozenset"})
_LOOP_TYPES = (ast.For, ast.AsyncFor, ast.While)


class PythonExtractor:
    language = Language.PYTHON

    def extract(self, source_code: str) -> FeatureSet:
        if not source_code or not source_code.strip():
            return unknown_set(Language.PYTHON, ParseStatus.MISSING_SOURCE)
        try:
            tree = ast.parse(source_code)
        except (SyntaxError, ValueError, MemoryError, RecursionError):
            return unknown_set(Language.PYTHON, ParseStatus.UNPARSEABLE)

        counts = _Counts()
        _Visitor(counts).visit(tree)
        counts.max_loop_nesting = _max_loop_nesting(tree)
        counts.direct_self_recursion = _has_direct_self_recursion(tree)

        return FeatureSet(
            Language.PYTHON,
            ParseStatus.OK,
            None,
            (
                _int(FeatureId.BRANCHES, counts.branches),
                _int(FeatureId.LOOPS, counts.loops),
                _int(FeatureId.MAX_LOOP_NESTING, counts.max_loop_nesting),
                _int(FeatureId.FUNCTIONS, counts.functions),
                FeatureValue(
                    FeatureId.DIRECT_SELF_RECURSION,
                    counts.direct_self_recursion,
                    Confidence.RELIABLE,
                ),
                _int(FeatureId.INDEX_ACCESSES, counts.index_accesses),
                _int(FeatureId.CONTAINER_USES, counts.container_uses),
                _int(FeatureId.IO_OPERATIONS, counts.io_operations),
            ),
        )


def _int(feature: FeatureId, value: int) -> FeatureValue:
    return FeatureValue(feature, value, Confidence.RELIABLE)


class _Counts:
    def __init__(self) -> None:
        self.branches = 0
        self.loops = 0
        self.max_loop_nesting = 0
        self.functions = 0
        self.direct_self_recursion = False
        self.index_accesses = 0
        self.container_uses = 0
        self.io_operations = 0


class _Visitor(ast.NodeVisitor):
    def __init__(self, counts: _Counts) -> None:
        self.counts = counts

    def visit_If(self, node: ast.If) -> None:
        self.counts.branches += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.counts.branches += 1
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.counts.branches += 1
        self.generic_visit(node)

    def _visit_loop(self, node: ast.AST) -> None:
        self.counts.loops += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.counts.functions += 1
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.counts.functions += 1
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.counts.functions += 1
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.counts.index_accesses += 1
        self.generic_visit(node)

    def _visit_container(self, node: ast.AST) -> None:
        self.counts.container_uses += 1
        self.generic_visit(node)

    visit_List = _visit_container
    visit_Tuple = _visit_container
    visit_Set = _visit_container
    visit_Dict = _visit_container
    visit_ListComp = _visit_container
    visit_SetComp = _visit_container
    visit_DictComp = _visit_container
    visit_GeneratorExp = _visit_container

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in _IO_NAMES:
                self.counts.io_operations += 1
            elif node.func.id in _CONTAINER_BUILTINS:
                self.counts.container_uses += 1
        self.generic_visit(node)


def _max_loop_nesting(tree: ast.AST) -> int:
    """Deepest loop-in-loop depth across the module (0 when there are no loops)."""

    maximum = 0

    def descend(node: ast.AST, depth: int) -> None:
        nonlocal maximum
        maximum = max(maximum, depth)
        for child in ast.iter_child_nodes(node):
            descend(child, depth + 1 if isinstance(child, _LOOP_TYPES) else depth)

    descend(tree, 0)
    return maximum


def _has_direct_self_recursion(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(
                _is_named_call(inner, node.name)
                for statement in node.body
                for inner in ast.walk(statement)
            ):
                return True
    return False


def _is_named_call(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
