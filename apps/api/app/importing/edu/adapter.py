"""资产类型 → 解析适配器的注册表。

V1.4 要求"新增数据来源只补适配器"，所以这里只回答一个问题：
这种资产现在有人能解析吗？能，返回适配器名；不能，返回 None。
真正的解析实现仍留在 parser.py / archive.py，本模块不搬运解析逻辑。
"""

from app.importing.edu import detect

DUAL_SHEET_XLSX = "dual_sheet_xlsx"

# 键是 detect 出来的 kind，值是适配器名。加新格式就在这里加一行。
ADAPTERS: dict[str, str] = {
    detect.XLSX: DUAL_SHEET_XLSX,
}


def adapter_for(kind: str) -> str | None:
    return ADAPTERS.get(kind)


def supported_kinds() -> tuple[str, ...]:
    return tuple(sorted(ADAPTERS))
