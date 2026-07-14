import lancedb


def test_delete_semantic_index_entries_removes_only_selected_memory_ids(
    monkeypatch,
    tmp_path,
):
    from ai.memory import semantic

    monkeypatch.setattr(semantic, "_STORAGE_DIR", str(tmp_path))
    db = lancedb.connect(tmp_path)
    db.create_table(
        "semantic_7",
        data=[
            {
                "vector": [0.1, 0.2],
                "id": "ai-vector",
                "text": "AI memory",
                "metadata": {"memory_id": "11"},
            },
            {
                "vector": [0.2, 0.3],
                "id": "import-vector",
                "text": "Imported memory",
                "metadata": {"memory_id": "12"},
            },
            {
                "vector": [0.3, 0.4],
                "id": "user-vector",
                "text": "User memory",
                "metadata": {"memory_id": "13"},
            },
        ],
    )

    assert semantic.delete_semantic_index_entries(7, [11]) is True

    # Lance table handles are versioned snapshots; reopen to observe the
    # deletion committed through the production helper's separate connection.
    remaining = db.open_table("semantic_7").to_arrow().column("metadata").to_pylist()
    assert remaining == [{"memory_id": "12"}, {"memory_id": "13"}]
