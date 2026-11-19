"""C++ feature extraction via a hand-written lexer plus brace-scope analysis.

There is no C++ parser in the standard library, so every feature below is
``TENTATIVE``: deterministic given a successful *lexical* parse, but without full
semantic analysis. The lexer correctly skips ``//`` and ``/* */`` comments, string
and character literals, and preprocessor lines, so those cannot distort counts.

Precision limits (deliberate, not hidden):

* ``functions`` recognises the ``name ( ... ) {`` shape; ``operator`` overloads
  and function-try blocks are not counted.
* ``direct_self_recursion`` matches a call whose callee name equals the enclosing
  function name; it does not detect mutual recursion (A -> B -> A), and an
  overload or a same-name local could produce a false positive.
* ``index_accesses`` counts every ``[``, which includes array type declarations
  (``int a[10]``) as well as subscripts.
* ``container_uses`` counts occurrences of standard container type names
  (``vector``, ``string``, ``map``, ...); a variable that happens to be named
  ``string`` would be counted too.
* ``io_operations`` counts I/O identifiers (``cin``, ``cout``, ``scanf``,
  ``printf``, ``getline``, ...), not the ``<<``/``>>`` stream operators.
"""

from dataclasses import dataclass

from ..contract import Language
from ..features import Confidence, FeatureId, FeatureSet, FeatureValue, ParseStatus, unknown_set

_LOOP_KEYWORDS = frozenset({"for", "while", "do"})
_BRANCH_KEYWORDS = frozenset({"if", "switch"})
_CONTROL_WORDS = frozenset({"if", "for", "while", "switch", "catch", "do"})

_CONTAINER_TYPES = frozenset(
    {
        "vector",
        "array",
        "deque",
        "list",
        "forward_list",
        "string",
        "map",
        "unordered_map",
        "set",
        "unordered_set",
        "multiset",
        "multimap",
        "pair",
        "stack",
        "queue",
        "priority_queue",
    }
)

_IO_IDENTIFIERS = frozenset(
    {"cin", "cout", "cerr", "scanf", "printf", "getline", "puts", "fread", "fwrite"}
)

_TWO_CHAR_SYMBOLS = frozenset(
    {
        ">>",
        "<<",
        "->",
        "::",
        "++",
        "--",
        "==",
        "!=",
        "<=",
        ">=",
        "&&",
        "||",
        "+=",
        "-=",
        "*=",
        "/=",
        "%=",
        "&=",
        "|=",
        "^=",
        "<<=",
        ">>=",
        "->*",
        ".*",
        "...",
    }
)


@dataclass(frozen=True)
class _Token:
    kind: str  # "word" | "number" | "string" | "char" | "symbol" | "eof"
    text: str
    line: int


class CppExtractor:
    language = Language.CPP

    def extract(self, source_code: str) -> FeatureSet:
        if not source_code or not source_code.strip():
            return unknown_set(Language.CPP, ParseStatus.MISSING_SOURCE)

        tokens, errors = _lex(source_code)
        counts = _scan(tokens)
        detail = ", ".join(errors) if errors else None

        values = (
            _tentative(FeatureId.BRANCHES, counts["branches"]),
            _tentative(FeatureId.LOOPS, counts["loops"]),
            _tentative(FeatureId.MAX_LOOP_NESTING, counts["max_loop_nesting"]),
            _tentative(FeatureId.FUNCTIONS, counts["functions"]),
            FeatureValue(
                FeatureId.DIRECT_SELF_RECURSION,
                counts["direct_self_recursion"],
                Confidence.TENTATIVE,
            ),
            _tentative(FeatureId.INDEX_ACCESSES, counts["index_accesses"]),
            _tentative(FeatureId.CONTAINER_USES, counts["container_uses"]),
            _tentative(FeatureId.IO_OPERATIONS, counts["io_operations"]),
        )
        return FeatureSet(Language.CPP, ParseStatus.OK, detail, values)


def _tentative(feature: FeatureId, value: int | bool) -> FeatureValue:
    return FeatureValue(feature, value, Confidence.TENTATIVE)


def _lex(source: str) -> tuple[list[_Token], list[str]]:
    tokens: list[_Token] = []
    errors: list[str] = []
    line = 1
    i = 0
    n = len(source)
    while i < n:
        char = source[i]
        if char in " \t\r\f\v":
            i += 1
        elif char == "\n":
            line += 1
            i += 1
        elif char == "/" and i + 1 < n and source[i + 1] == "/":
            i += 2
            while i < n and source[i] != "\n":
                i += 1
        elif char == "/" and i + 1 < n and source[i + 1] == "*":
            i += 2
            while i < n and not (source[i] == "*" and i + 1 < n and source[i + 1] == "/"):
                if source[i] == "\n":
                    line += 1
                i += 1
            if i >= n:
                errors.append("unterminated_block_comment")
            else:
                i += 2
        elif char == "#":
            while i < n and source[i] != "\n":
                i += 1
        elif char == '"':
            i, line, closed = _consume_quoted(source, i, '"', line)
            if not closed:
                errors.append("unterminated_string")
        elif char == "'":
            i, line, closed = _consume_quoted(source, i, "'", line)
            if not closed:
                errors.append("unterminated_char")
        elif char.isalpha() or char == "_":
            start = i
            while i < n and (source[i].isalnum() or source[i] == "_"):
                i += 1
            tokens.append(_Token("word", source[start:i], line))
        elif char.isdigit():
            start = i
            while i < n and (source[i].isalnum() or source[i] in "._"):
                i += 1
            tokens.append(_Token("number", source[start:i], line))
        else:
            two = source[i : i + 2]
            if two in _TWO_CHAR_SYMBOLS:
                tokens.append(_Token("symbol", two, line))
                i += 2
            else:
                tokens.append(_Token("symbol", char, line))
                i += 1
    tokens.append(_Token("eof", "", line))
    return tokens, errors


def _consume_quoted(source: str, start: int, quote: str, line: int) -> tuple[int, int, bool]:
    i = start + 1
    n = len(source)
    while i < n:
        char = source[i]
        if char == "\\":
            i += 2
            continue
        if char == "\n":
            line += 1
            i += 1
            continue
        if char == quote:
            return i + 1, line, True
        i += 1
    return i, line, False


def _match(tokens: list[_Token]) -> tuple[dict[int, int], dict[int, int]]:
    """Map opening delimiters to their closing index and vice versa (best effort)."""

    open_to_close: dict[int, int] = {}
    close_to_open: dict[int, int] = {}
    stack: list[tuple[str, int]] = []
    for index, token in enumerate(tokens):
        text = token.text
        if text in "([{":
            stack.append((text, index))
        elif text in ")]}":
            if stack and _delimiters_match(stack[-1][0], text):
                _, opening = stack.pop()
                open_to_close[opening] = index
                close_to_open[index] = opening
            else:
                stack.clear()
    return open_to_close, close_to_open


def _delimiters_match(opening: str, closing: str) -> bool:
    return (opening, closing) in {("(", ")"), ("[", "]"), ("{", "}")}


def _scan(tokens: list[_Token]) -> dict[str, int | bool]:
    open_to_close, _ = _match(tokens)
    loops = 0
    branches = 0
    max_nesting = 0
    index_accesses = 0
    container_uses = 0
    io_operations = 0
    functions: list[tuple[str, int, int]] = []
    skip_while: set[int] = set()
    open_loops: list[int] = []

    for index, token in enumerate(tokens):
        while open_loops and index > open_loops[-1]:
            open_loops.pop()

        if token.kind == "symbol":
            if token.text == "[":
                index_accesses += 1
            elif token.text == "?":
                branches += 1
            continue

        if token.kind != "word":
            continue

        text = token.text

        if text in _IO_IDENTIFIERS:
            io_operations += 1
        if text in _CONTAINER_TYPES:
            container_uses += 1

        if text in _BRANCH_KEYWORDS:
            branches += 1
            continue

        if text in _LOOP_KEYWORDS:
            if text == "while" and index in skip_while:
                continue
            loops += 1
            max_nesting = max(max_nesting, len(open_loops) + 1)
            body_end = _loop_body_end(tokens, index, open_to_close)
            if body_end is not None:
                open_loops.append(body_end)
                if text == "do":
                    while_index = _do_while_index(tokens, body_end)
                    if while_index is not None:
                        skip_while.add(while_index)
            continue

        if text in _CONTROL_WORDS:
            continue
        closing = open_to_close.get(index + 1) if index + 1 < len(tokens) else None
        if closing is not None and closing + 1 < len(tokens) and tokens[closing + 1].text == "{":
            body_end = open_to_close.get(closing + 1)
            if body_end is not None:
                functions.append((text, closing + 1, body_end))

    direct_self_recursion = any(
        _calls_itself(tokens, name, body_start, body_end)
        for name, body_start, body_end in functions
    )

    return {
        "branches": branches,
        "loops": loops,
        "max_loop_nesting": max_nesting,
        "functions": len(functions),
        "direct_self_recursion": direct_self_recursion,
        "index_accesses": index_accesses,
        "container_uses": container_uses,
        "io_operations": io_operations,
    }


def _loop_body_end(tokens: list[_Token], index: int, open_to_close: dict[int, int]) -> int | None:
    if tokens[index].text == "do":
        header_end = index + 1
    else:
        opening = index + 1
        while opening < len(tokens) and tokens[opening].text != "(":
            opening += 1
        if opening >= len(tokens):
            return None
        closing = open_to_close.get(opening)
        if closing is None:
            return None
        header_end = closing + 1
    if header_end < len(tokens) and tokens[header_end].text == "{":
        return open_to_close.get(header_end)
    return _statement_end(tokens, header_end)


def _statement_end(tokens: list[_Token], start: int) -> int:
    depth = 0
    index = start
    while index < len(tokens):
        text = tokens[index].text
        if text == ";" and depth == 0:
            return index
        if text in "([{":
            depth += 1
        elif text in ")]}":
            depth -= 1
            if depth == 0 and text == "}":
                return index
        index += 1
    return len(tokens) - 1


def _do_while_index(tokens: list[_Token], body_end: int) -> int | None:
    for index in range(body_end + 1, len(tokens)):
        if tokens[index].kind == "word" and tokens[index].text == "while":
            return index
    return None


def _calls_itself(tokens: list[_Token], name: str, body_start: int, body_end: int) -> bool:
    for index in range(body_start + 1, body_end):
        if tokens[index].kind == "word" and tokens[index].text == name:
            if index + 1 < len(tokens) and tokens[index + 1].text == "(":
                return True
    return False
