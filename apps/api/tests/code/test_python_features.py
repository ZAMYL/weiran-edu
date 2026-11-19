from app.analysis.code import (
    Confidence,
    FeatureId,
    Language,
    ParseStatus,
    PythonExtractor,
)

extractor = PythonExtractor()


def _value(code: str, feature: FeatureId):
    return extractor.extract(code).get(feature)


def test_loops_and_branches() -> None:
    code = "for i in range(10):\n    if i < 5:\n        continue\nwhile True:\n    break\n"
    result = extractor.extract(code)
    assert result.status is ParseStatus.OK
    assert result.language is Language.PYTHON
    assert result.get(FeatureId.LOOPS).value == 2
    assert result.get(FeatureId.BRANCHES).value == 1
    assert result.get(FeatureId.MAX_LOOP_NESTING).value == 1
    assert result.get(FeatureId.FUNCTIONS).value == 0


def test_nested_loop_depth() -> None:
    code = "for i in range(3):\n    for j in range(3):\n        print(i, j)\n"
    result = extractor.extract(code)
    assert result.get(FeatureId.LOOPS).value == 2
    assert result.get(FeatureId.MAX_LOOP_NESTING).value == 2
    assert result.get(FeatureId.IO_OPERATIONS).value == 1


def test_three_level_loop_nesting() -> None:
    code = "for a in x:\n    while b:\n        for c in y:\n            pass\n"
    assert extractor.extract(code).get(FeatureId.MAX_LOOP_NESTING).value == 3


def test_recursive_function() -> None:
    code = "def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\n"
    result = extractor.extract(code)
    assert result.get(FeatureId.FUNCTIONS).value == 1
    assert result.get(FeatureId.DIRECT_SELF_RECURSION).value is True
    assert result.get(FeatureId.BRANCHES).value == 1


def test_mutual_recursion_is_not_direct_self_recursion() -> None:
    code = (
        "def even(n):\n"
        "    return n == 0 or odd(n - 1)\n"
        "def odd(n):\n"
        "    return n != 0 and even(n - 1)\n"
    )
    result = extractor.extract(code)
    assert result.get(FeatureId.FUNCTIONS).value == 2
    assert result.get(FeatureId.DIRECT_SELF_RECURSION).value is False


def test_containers_and_index_access() -> None:
    code = "a = [1, 2, 3]\nb = {'x': 1}\nc = (1, 2)\nd = {1, 2}\nprint(a[0], b['x'])\n"
    result = extractor.extract(code)
    assert result.get(FeatureId.CONTAINER_USES).value == 4
    assert result.get(FeatureId.INDEX_ACCESSES).value == 2
    assert result.get(FeatureId.IO_OPERATIONS).value == 1


def test_comments_and_strings_are_ignored() -> None:
    code = '# for while if def\ntext = "for while if print input"\nx = text  # loop\nprint(x)\n'
    result = extractor.extract(code)
    assert result.get(FeatureId.LOOPS).value == 0
    assert result.get(FeatureId.BRANCHES).value == 0
    assert result.get(FeatureId.FUNCTIONS).value == 0
    assert result.get(FeatureId.IO_OPERATIONS).value == 1


def test_ternary_and_lambda_are_counted() -> None:
    code = "f = lambda x: x + 1 if x else 0\n"
    result = extractor.extract(code)
    assert result.get(FeatureId.FUNCTIONS).value == 1
    assert result.get(FeatureId.BRANCHES).value == 1


def test_all_features_are_reliable_for_parseable_python() -> None:
    result = extractor.extract("x = 1\n")
    for feature in FeatureId:
        assert result.get(feature).confidence is Confidence.RELIABLE


def test_missing_source_yields_unknown() -> None:
    for code in ("", "   \n\t "):
        result = extractor.extract(code)
        assert result.status is ParseStatus.MISSING_SOURCE
        assert all(value.value is None for value in result.values)


def test_unparseable_yields_unknown() -> None:
    result = extractor.extract("def :")
    assert result.status is ParseStatus.UNPARSEABLE
    assert all(value.value is None for value in result.values)


def test_max_loop_nesting_is_zero_without_loops() -> None:
    assert _value("x = 1\n", FeatureId.MAX_LOOP_NESTING).value == 0
