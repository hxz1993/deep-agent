"""标题生成模块单元测试。"""

import unittest

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel

from deep_agent.agent.history import _extract_assistant_text
from deep_agent.agent.title_generator import _clean_title, generate_title


class TestCleanTitle(unittest.TestCase):
    """测试标题清洗函数。"""

    def test_empty_input(self):
        self.assertIsNone(_clean_title(""))
        self.assertIsNone(_clean_title(None))

    def test_remove_thinking_blocks(self):
        result = _clean_title("thinking这是思考内容thinking  快速排序")
        self.assertIn("快速排序", result)
        self.assertNotIn("thinking", result)

    def test_remove_quotes(self):
        result = _clean_title('"快速排序"')
        self.assertEqual(result, "快速排序")

    def test_remove_title_prefix(self):
        result = _clean_title("Title: 快速排序")
        self.assertEqual(result, "快速排序")

    def test_remove_trailing_punctuation(self):
        result = _clean_title("快速排序。")
        self.assertEqual(result, "快速排序")

    def test_normal_title(self):
        result = _clean_title("Python 快速排序实现")
        self.assertEqual(result, "Python 快速排序实现")


class TestExtractAssistantText(unittest.TestCase):
    """测试助手文本提取函数。"""

    def test_empty_messages(self):
        self.assertEqual(_extract_assistant_text([]), "")

    def test_only_user_messages(self):
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="你好")]),
        ]
        self.assertEqual(_extract_assistant_text(msgs), "")

    def test_single_text_response(self):
        msgs = [
            ModelResponse(parts=[TextPart(content="你好！")]),
        ]
        self.assertEqual(_extract_assistant_text(msgs), "你好！")

    def test_multiple_text_parts(self):
        msgs = [
            ModelResponse(parts=[TextPart(content="第一行"), TextPart(content="第二行")]),
        ]
        self.assertEqual(_extract_assistant_text(msgs), "第一行\n第二行")

    def test_empty_content(self):
        msgs = [
            ModelResponse(parts=[TextPart(content="")]),
        ]
        self.assertEqual(_extract_assistant_text(msgs), "")


class TestGenerateTitle(unittest.IsolatedAsyncioTestCase):
    """测试标题生成函数。"""

    async def test_no_model_configured(self) -> None:
        """未配置专用模型且无主模型时返回 None。"""
        result = await generate_title("用户消息", "助手响应")
        self.assertIsNone(result)

    async def test_falls_back_to_main_model(self) -> None:
        model = TestModel(custom_output_text="Python 装饰器入门")
        result = await generate_title(
            "请解释装饰器",
            "装饰器是……",
            main_model=model,
        )
        self.assertEqual(result, "Python 装饰器入门")


if __name__ == "__main__":
    unittest.main()
