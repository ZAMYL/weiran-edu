"""B-01-S2：标题行/说明行的表头保真定向测试（全合成数据）。

只验证通用行结构线索：不依赖固定行号、Sheet 名、文件名或人数。
"""

from io import BytesIO

from openpyxl import Workbook

from app.importing.edu import detect
from app.importing.edu.extract import extract_document


def xlsx_bytes(rows: list[list]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "随机页_Q7"
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def test_xlsx_title_row_is_not_mistaken_for_header():
    data = xlsx_bytes(
        [
            ["2026年春季编程测验成绩汇总"],
            ["参与人", "题目", "得分", "评语"],
            ["U2301", "A1", 8, "思路清晰"],
            ["U2302", "A1", 4, "边界不足"],
        ]
    )

    document = extract_document(detect.XLSX, data)

    table = document.tables[0]
    assert table.header == ("参与人", "题目", "得分", "评语")
    assert table.rows == (("U2301", "A1", "8", "思路清晰"), ("U2302", "A1", "4", "边界不足"))
    assert any("2026年春季编程测验成绩汇总" in line for line in document.text_lines)
    assert any("HEADER_PREAMBLE_SKIPPED" in w for w in document.warnings)


def test_xlsx_wide_title_row_same_behaviour():
    data = xlsx_bytes(
        [
            ["编程测验", None, None, None],
            ["参与人", "题目", "得分", "评语"],
            ["U2303", "A2", 9, ""],
        ]
    )

    table = extract_document(detect.XLSX, data).tables[0]

    assert table.header == ("参与人", "题目", "得分", "评语")
    assert table.rows == (("U2303", "A2", "9", ""),)


def test_csv_preamble_without_comma_detects_and_preserves_columns():
    data = (
        "导出说明：2026年春季测验\n"
        "学员编号,作答题目,最终成绩,判定状态\n"
        "U2301,A1,8,通过\n"
        "U2302,A1,4,未通过\n"
    ).encode()

    assert detect.detect_asset(data).kind == detect.CSV
    document = extract_document(detect.CSV, data)
    table = document.tables[0]
    assert table.header == ("学员编号", "作答题目", "最终成绩", "判定状态")
    assert table.rows == (("U2301", "A1", "8", "通过"), ("U2302", "A1", "4", "未通过"))
    assert any("导出说明" in line for line in document.text_lines)


def test_csv_preamble_with_comma_never_truncates_data_columns():
    data = (
        "导出时间,2026-11-22\n"
        "学员编号,作答题目,最终成绩,判定状态\n"
        "U2301,A1,8,通过\n"
        "U2303,A2,9,通过\n"
    ).encode()

    table = extract_document(detect.CSV, data).tables[0]

    assert table.header == ("学员编号", "作答题目", "最终成绩", "判定状态")
    assert table.rows == (("U2301", "A1", "8", "通过"), ("U2303", "A2", "9", "通过"))


def test_slightly_narrow_header_is_not_preamble():
    # 尾部多一列数据：表头只是略窄，必须留在表头位，且数据列不被截断。
    data = "姓名,分数,\n甲,3,优秀\n乙,4,良好\n".encode()

    document = extract_document(detect.CSV, data)
    table = document.tables[0]
    assert table.header == ("姓名", "分数", "")
    assert table.rows == (("甲", "3", "优秀"), ("乙", "4", "良好"))
    assert document.warnings == []


def test_numeric_first_row_is_flagged_uncertain_without_data_loss():
    data = b"1,2\n3,4\n"

    document = extract_document(detect.CSV, data)

    assert any("HEADER_UNCERTAIN" in w for w in document.warnings)
    table = document.tables[0]
    assert table.header == ("1", "2")
    assert table.rows == (("3", "4"),)


def test_narrow_first_row_with_numeric_candidate_is_uncertain_not_reassigned():
    # 首行窄、满宽行又含数值：无法可靠判断时保留首行并显式存疑。
    data = "说明\n1,2,3\n4,5,6\n".encode()

    document = extract_document(detect.CSV, data)

    assert any("HEADER_UNCERTAIN" in w for w in document.warnings)
    table = document.tables[0]
    assert table.header == ("说明", "", "")
    assert table.rows == (("1", "2", "3"), ("4", "5", "6"))


def test_single_column_csv_keeps_legacy_behaviour():
    data = "名字\n甲\n乙\n".encode()

    table = extract_document(detect.CSV, data).tables[0]

    assert table.header == ("名字",)
    assert table.rows == (("甲",), ("乙",))


def test_normal_xlsx_and_csv_have_no_new_warnings():
    normal_xlsx = xlsx_bytes([["姓名", "分数"], ["甲", 3], ["乙", 4]])
    normal_csv = "姓名,分数\n甲,3\n乙,4\n".encode()

    xlsx_doc = extract_document(detect.XLSX, normal_xlsx)
    csv_doc = extract_document(detect.CSV, normal_csv)
    assert xlsx_doc.warnings == [] and csv_doc.warnings == []
    assert xlsx_doc.tables[0].header == ("姓名", "分数")
    assert xlsx_doc.tables[0].rows == (("甲", "3"), ("乙", "4"))
    assert csv_doc.tables[0].rows == (("甲", "3"), ("乙", "4"))


def test_two_row_data_with_missing_cells_not_mistaken_for_preamble():
    # 缺失单元格导致数据行略窄，不应触发表头重判。
    data = "参与人,题目,得分\nU2301,A1,\nU2302,A2,9\n".encode()

    document = extract_document(detect.CSV, data)
    table = document.tables[0]
    assert table.header == ("参与人", "题目", "得分")
    assert table.rows == (("U2301", "A1", ""), ("U2302", "A2", "9"))
    assert document.warnings == []
