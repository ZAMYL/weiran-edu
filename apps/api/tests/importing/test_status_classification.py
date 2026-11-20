"""状态分类：只有明确通过/明确失败才进入统计，其余一律单列。"""

import pytest

from app.importing.edu.answersheet import (
    STATUS_FAIL,
    STATUS_NOT_ATTEMPTED,
    STATUS_PARTIAL,
    STATUS_PASS,
    STATUS_UNKNOWN,
    classify_status,
)


@pytest.mark.parametrize(
    ("status", "known", "expected"),
    [
        ("答案正确", True, STATUS_PASS),
        ("答案错误", True, STATUS_FAIL),
        ("运行时错误", True, STATUS_FAIL),
        ("运行超时", True, STATUS_FAIL),
        ("内存超限", True, STATUS_FAIL),
        ("编译错误", True, STATUS_FAIL),
        ("部分正确", True, STATUS_PARTIAL),
        ("未作答", True, STATUS_NOT_ATTEMPTED),
        ("等待评测", True, STATUS_UNKNOWN),
        ("等待评测", False, STATUS_UNKNOWN),
        ("", True, STATUS_UNKNOWN),
        (None, True, STATUS_UNKNOWN),
        # 未知状态即使带"正确"字样以外的任何文本，都不能算失败
        ("正在排队", True, STATUS_UNKNOWN),
    ],
)
def test_classify_status(status, known, expected):
    assert classify_status(status, known=known) == expected


def test_fractional_milliseconds_are_parsed_exactly():
    from app.importing.edu.answersheet import _duration_ms

    assert _duration_ms("0.5 ms") == 0.5
    assert round(_duration_ms("0.5 ms") * 1000) == 500
    assert _duration_ms("2.00 ms") == 2.0
    assert _duration_ms("未知") is None
