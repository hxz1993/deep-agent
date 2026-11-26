"""用审批模板和本次核实的纯文本字段生成 BPP 表单正文。"""
import json
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

from deepagent_files import publish_path


def scope_fields(scope):
    def label(key):
        value = scope.get(key)
        return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""

    year, period = label("year"), label("period")
    scenario, version = label("scenario"), label("version")
    parts = [
        f"{year}年" if year.isdecimal() else year,
        "" if period == "以表单期间为准" else period,
        "预算" if scenario.casefold() == "budget" else scenario,
        "工作版" if version.casefold() == "working" else version,
    ]
    return {
        "organization": label("organization") or "未提供",
        "budget_range": " / ".join(part for part in parts if part) or "未提供",
        "form": label("form") or "未提供",
        "batch": label("batch") or "未提供",
    }


def render(inputs):
    asset = Path(__file__).resolve().parents[1] / "assets/bpp-sales-opinion.html"
    root = ET.parse(asset).getroot()

    def fill(node, values):
        if node.get("data-section") == "scope":
            values = header
        field = node.attrib.pop("data-field", None)
        if field is not None:
            value = values if field == "." else values.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"审批意见字段 {field} 需要填写数值、结论或具体缺口")
            node.text = value.strip()
        for child in list(node):
            group = child.attrib.pop("data-repeat", None)
            if group is None:
                fill(child, values)
                continue
            rows = values.get(group)
            if not isinstance(rows, list) or not rows:
                raise ValueError(f"审批意见 {group} 至少需要一项")
            position = list(node).index(child)
            node.remove(child)
            for i, item in enumerate(rows):
                if child.get("data-field") != "." and not isinstance(item, dict):
                    raise ValueError(f"审批意见 {group} 的每一项须包含模板需要的字段")
                row = deepcopy(child)
                fill(row, item)
                node.insert(position + i, row)

    if not isinstance(inputs, dict):
        raise ValueError("审批意见 inputs 应为对象")
    scope = inputs.get("scope")
    if isinstance(scope, str) and scope.strip():
        # 旧版的一段口径文字只保留作依据，不能拿来推断四个送审字段。
        if isinstance(inputs.get("sources"), list) and inputs["sources"]:
            inputs = {**inputs, "sources": [*inputs["sources"], scope.strip()]}
        scope = {}
    if not isinstance(scope, dict):
        raise ValueError("审批意见 scope 应为送审信息对象")
    header = scope_fields(scope)
    fill(root, inputs)
    return ET.tostring(root, encoding="unicode", method="html")


if __name__ == "__main__":
    inputs = json.loads(Path("inputs.json").read_text(encoding="utf-8"))
    html = render(inputs)
    Path(publish_path("approval.html", mime="text/html")).write_text(html, encoding="utf-8")
