"""写入端提取质量加固：identity 分类收窄 + 状态身份归入 preference + 情绪化自嘲不提取。

背景：memory 2321 把用户 2024 年的一句情绪化自嘲（"一穷二白没啥证书"）存成了
identity 当前事实。根因是 identity 定义把"职业/地点"这类会变的信息列为"不会随
时间改变"，诱导 LLM 把状态当恒定身份。此处断言 prompt 必须修正这两点。
"""
import inspect

import pytest


class TestChunkAnalyzerPrompt:
    """prompt 在 _do_analyze 内部，直接对其断言"""

    @pytest.fixture
    def source(self):
        from ai.preprocessing.chunk_analyzer import _do_analyze
        return inspect.getsource(_do_analyze)

    def test_identity_definition_excludes_mutable_attributes(self, source):
        """identity 定义必须收窄为恒定身份，不得包含会变的职业/地点"""
        identity_lines = [
            line for line in source.splitlines()
            if "identity（" in line
        ]
        assert identity_lines, "未找到 identity 定义"
        import re
        match = re.search(r"identity（([^）]*)）", identity_lines[0])
        assert match, "identity 定义必须是（...）括起来的列表"
        identity_definition = match.group(1)
        assert "姓名" in identity_definition
        assert "职业" not in identity_definition, "会变的职业不应列在身份定义里"
        assert "地点" not in identity_definition, "会变的地点不应列在身份定义里"

    def test_mutable_identity_attributes_go_to_preference(self, source):
        """学校/职业/城市等当前状态必须被引导归入 preference"""
        assert "当前状态" in source
        assert "学校" in source
        assert "preference" in source

    def test_past_states_go_to_experience(self, source):
        """已结束的过去状态（曾就读/曾任职）必须归入 experience 且不可变"""
        assert "已结束的过去状态" in source
        assert "experience" in source
        assert "不可变" in source

    def test_emotional_self_deprecation_must_not_be_extracted(self, source):
        """情绪化自嘲/抱怨不得提炼为长期事实"""
        assert "自嘲" in source
        assert "不得" in source


class TestReflectionPrompt:
    def test_identity_definition_excludes_mutable_attributes(self):
        """reflection 的 identity 定义同样必须收窄"""
        from ai.memory.reflection import reflect_memories

        source = inspect.getsource(reflect_memories)
        identity_lines = [
            line for line in source.splitlines()
            if "identity:" in line
        ]
        assert identity_lines, "未找到 identity 定义"
        identity_definition = identity_lines[0]
        assert "姓名" in identity_definition
        assert "职业" not in identity_definition
        assert "地点" not in identity_definition

    def test_past_states_go_to_experience(self):
        """已结束的过去状态归入 experience，追加不可变"""
        from ai.memory.reflection import reflect_memories

        source = inspect.getsource(reflect_memories)
        assert "已结束的过去状态" in source
        assert "不可变" in source

    def test_emotional_self_deprecation_must_not_be_extracted(self):
        from ai.memory.reflection import reflect_memories

        source = inspect.getsource(reflect_memories)
        assert "自嘲" in source
        assert "不要提炼为长期事实" in source
