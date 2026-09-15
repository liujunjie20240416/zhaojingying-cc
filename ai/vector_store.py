"""LanceDB 向量库的位置，以及距离到相关度的换算。

这个模块**刻意保持零依赖**（只用 pathlib），所以 ai/ 和 api/ 的任何一层都可以
安全导入它，不会引入循环。

使用方一律写成 `from ai.vector_store import STORAGE_DIR as _STORAGE_DIR`：
保留各模块自己的模块级 `_STORAGE_DIR`，是为了让
`tests/test_semantic_index_delete.py` 里那个 monkeypatch 仍然能生效——它直接
重绑 `semantic._STORAGE_DIR`。
"""

from __future__ import annotations

from pathlib import Path

# 本文件必须待在 ai/ 下。如果哪天再搬动它，下面这行断言会当场炸掉——
# 因为一旦位置变了，.parent 的层数就不再对，而**算错的路径不会自己报错**：
# lancedb.connect() 会默默把目录建出来，然后语义检索永远返回空结果。
_AI_DIR = Path(__file__).resolve().parent
assert _AI_DIR.name == "ai", f"vector_store.py 应该在 ai/ 下，实际在: {_AI_DIR}"

STORAGE_DIR = str(_AI_DIR / "documents" / "lancedb_storage")


def lance_distance_to_relevance(distance: float) -> float:
    """Convert LanceDB distance (lower is better) to relevance (higher is better)."""
    value = max(0.0, float(distance))
    return 1.0 / (1.0 + value)
