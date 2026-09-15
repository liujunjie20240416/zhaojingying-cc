"""Names for one Character's active Imported Chat projections.

``Character.import_data_version`` is the publication pointer.  Legacy
characters have an empty version and therefore keep using their historic table
names until their next successful import.
"""

from __future__ import annotations


def _version_suffix(version: str = "") -> str:
    return f"__import_{version}" if version else ""


def imported_fts_table_name(character_id: int, version: str = "") -> str:
    return f"chat_fts_{character_id}{_version_suffix(version)}"


def imported_vector_table_name(character_id: int, version: str = "") -> str:
    return f"wechat_{character_id}{_version_suffix(version)}"
