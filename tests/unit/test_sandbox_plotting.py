from __future__ import annotations

import unittest

from deep_agent.sandbox.subprocess_dev.backend import SubprocessBackend
from deep_agent.sandbox.types import ExecuteRequest
from tests.helpers.sandbox import sandbox_test_python


class SandboxChinesePlottingTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_python_does_not_load_matplotlib(self):
        backend = SubprocessBackend(python_executable=sandbox_test_python())
        try:
            response = await backend.execute(ExecuteRequest(
                request_id="plain-chinese",
                code='import sys; 中文 = "你好"; {"text": 中文, "plotting_loaded": "matplotlib" in sys.modules}',
            ))
            self.assertEqual(response.status, "ok", response.model_dump())
            self.assertEqual(response.result, {"text": "你好", "plotting_loaded": False})
        finally:
            await backend.aclose()

    async def test_default_font_renders_chinese_and_unicode_minus_without_missing_glyphs(self):
        backend = SubprocessBackend(python_executable=sandbox_test_python())
        try:
            response = await backend.execute(ExecuteRequest(
                request_id="chinese-chart",
                code='''
import warnings
import matplotlib.pyplot as plt
from matplotlib import font_manager, ft2font
from deepagent_files import publish_path
font = font_manager.findfont(font_manager.FontProperties())
charmap = ft2font.FT2Font(font).get_charmap()
text = "中文测试月份销售额收入利润繁體數據−"
assert all(ord(c) in charmap for c in text)
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    fig, ax = plt.subplots()
    ax.plot([-1, 0, 1], [-100, 0, 100], label="销售额")
    ax.set_title("中文测试：月度销售额")
    ax.set_xlabel("月份")
    ax.set_ylabel("收入（万元）")
    ax.legend()
    fig.savefig("outputs/中文图表.png")
    plt.close(fig)
assert not any("Glyph" in str(w.message) and "missing" in str(w.message) for w in caught)
print("中文变量、正文和文件名均可用")
publish_path("中文图表.png")
{"font": font_manager.FontProperties(fname=font).get_name()}
''',
            ))
            self.assertEqual(response.status, "ok", response.model_dump())
            self.assertEqual(response.result, {"font": "Noto Sans CJK SC"})
            self.assertNotIn("missing from font", response.stderr)
            self.assertEqual(response.stdout, "中文变量、正文和文件名均可用\n")
            self.assertEqual(len(response.published_files), 1)
            self.assertTrue(response.published_files[0].data.startswith(b"\x89PNG\r\n\x1a\n"))
        finally:
            await backend.aclose()

    async def test_skill_script_uses_same_font_and_user_can_override_defaults(self):
        backend = SubprocessBackend(python_executable=sandbox_test_python())
        try:
            response = await backend.execute(ExecuteRequest(
                request_id="chinese-skill-chart",
                mode="script",
                code='''
import json
import matplotlib
from matplotlib import font_manager
assert matplotlib.rcParams["font.sans-serif"][0] == "Noto Sans CJK SC"
assert any(f.name == "Noto Sans CJK SC" for f in font_manager.fontManager.ttflist)
matplotlib.rcParams["font.family"] = ["monospace"]
with open("outputs/result.json", "w", encoding="utf-8") as out:
    json.dump({"字体": matplotlib.rcParams["font.family"]}, out, ensure_ascii=False)
''',
            ))
            self.assertEqual(response.status, "ok", response.model_dump())
            self.assertEqual(response.result, {"字体": ["monospace"]})
        finally:
            await backend.aclose()
