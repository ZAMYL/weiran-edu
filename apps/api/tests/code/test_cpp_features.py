from app.analysis.code import Confidence, CppExtractor, FeatureId, Language, ParseStatus

extractor = CppExtractor()


def _value(code: str, feature: FeatureId):
    return extractor.extract(code).get(feature)


def test_loops_branches_and_functions() -> None:
    code = (
        "int main() {\n"
        "    for (int i = 0; i < 10; i++) {\n"
        "        if (i < 5) { continue; }\n"
        "    }\n"
        "    while (true) { break; }\n"
        "    return 0;\n"
        "}\n"
    )
    result = extractor.extract(code)
    assert result.status is ParseStatus.OK
    assert result.language is Language.CPP
    assert result.get(FeatureId.LOOPS).value == 2
    assert result.get(FeatureId.BRANCHES).value == 1
    assert result.get(FeatureId.FUNCTIONS).value == 1
    assert result.get(FeatureId.MAX_LOOP_NESTING).value == 1
    assert result.get(FeatureId.DIRECT_SELF_RECURSION).value is False


def test_nested_loop_depth() -> None:
    code = (
        "for (int i = 0; i < 3; i++) {\n"
        "    for (int j = 0; j < 3; j++) {\n"
        "        cout << i << j;\n"
        "    }\n"
        "}\n"
    )
    result = extractor.extract(code)
    assert result.get(FeatureId.LOOPS).value == 2
    assert result.get(FeatureId.MAX_LOOP_NESTING).value == 2
    assert result.get(FeatureId.IO_OPERATIONS).value == 1


def test_recursive_function() -> None:
    code = "int fact(int n) {\n    if (n <= 1) return 1;\n    return n * fact(n - 1);\n}\n"
    result = extractor.extract(code)
    assert result.get(FeatureId.FUNCTIONS).value == 1
    assert result.get(FeatureId.DIRECT_SELF_RECURSION).value is True
    assert result.get(FeatureId.BRANCHES).value == 1


def test_container_and_io_and_index() -> None:
    code = (
        "#include <vector>\n"
        "#include <iostream>\n"
        "using namespace std;\n"
        "int main() {\n"
        "    vector<int> a(10);\n"
        "    cin >> a[0];\n"
        "    cout << a[0];\n"
        "}\n"
    )
    result = extractor.extract(code)
    assert result.get(FeatureId.CONTAINER_USES).value == 1
    assert result.get(FeatureId.IO_OPERATIONS).value == 2
    assert result.get(FeatureId.INDEX_ACCESSES).value == 2
    assert result.get(FeatureId.FUNCTIONS).value == 1


def test_do_while_is_one_loop() -> None:
    code = "int main() {\n    int i = 0;\n    do { i++; } while (i < 3);\n    return 0;\n}\n"
    result = extractor.extract(code)
    assert result.get(FeatureId.LOOPS).value == 1


def test_switch_and_ternary_are_branches() -> None:
    code = (
        "int f(int x) {\n    switch (x) { case 1: return 2; default: return 0; }\n}\n"
        "int g(int x) { return x ? 1 : 0; }\n"
    )
    result = extractor.extract(code)
    assert result.get(FeatureId.BRANCHES).value == 2
    assert result.get(FeatureId.FUNCTIONS).value == 2


def test_comments_and_preprocessor_are_ignored() -> None:
    code = (
        "// for while if int main\n"
        "/* for while if */\n"
        "#define for while\n"
        "int main() {\n"
        "    return 0;\n"
        "}\n"
    )
    result = extractor.extract(code)
    assert result.get(FeatureId.LOOPS).value == 0
    assert result.get(FeatureId.BRANCHES).value == 0
    assert result.get(FeatureId.FUNCTIONS).value == 1


def test_all_cpp_features_are_tentative() -> None:
    result = extractor.extract("int main() { return 0; }\n")
    for feature in FeatureId:
        assert result.get(feature).confidence is Confidence.TENTATIVE


def test_missing_source_yields_unknown() -> None:
    result = extractor.extract("   ")
    assert result.status is ParseStatus.MISSING_SOURCE
    assert all(value.value is None for value in result.values)
