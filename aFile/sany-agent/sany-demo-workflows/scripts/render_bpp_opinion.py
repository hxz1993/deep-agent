"""用审批模板和本次核实的纯文本字段生成 BPP 表单正文。"""
import json
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

from deepagent_files import publish_path


def render(inputs):
    asset = Path(__file__).resolve().parents[1] / "assets/bpp-sales-opinion.html"
    root = ET.parse(asset).getroot()

    def fill(node, values):
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
    fill(root, inputs)
    return ET.tostring(root, encoding="unicode", method="html")


if __name__ == "__main__":
    inputs = json.loads(Path("inputs.json").read_text(encoding="utf-8"))
    html = render(inputs)
    Path(publish_path("approval.html", mime="text/html")).write_text(html, encoding="utf-8")
