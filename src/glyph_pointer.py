"""字形指针契约与 G-identity（单一事实源）。

指针格式：
  glyph:<repo>:<layer>:<key>
例：
  glyph:hanzi-glyphs-svg:oracle:0386
  glyph:hanzi-glyphs-svg:seal:3D000

已发布层（v1）：oracle、seal。未在白名单的层不得出现在 glyph 边上。

G-identity（字头 ↔ 字形 index 行）：
  1. 优先：index.unicode 规范化后等于字头码位（modern_codepoints 或 head 的码位）
  2. 否则（index 无 unicode）：index.modern_char 必须与字头 head 完全一致
  禁止在本校验里做异体映射——异体是 relations 的边类型，不是指针身份。
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_NAME = "hanzi-glyphs-svg"
PUBLISHED_LAYERS = frozenset({"oracle", "seal"})


def norm_cp(value: Any) -> str | None:
    """把 U+4E00 / 4E00 / 004E00 收成大写十六进制（至少 4 位）。"""
    if value is None:
        return None
    s = str(value).upper().replace("U+", "").strip()
    if not s:
        return None
    try:
        return f"{int(s, 16):04X}"
    except ValueError:
        return None


def parse_glyph_target(to: str) -> tuple[str, str, str] | None:
    """解析 glyph:<repo>:<layer>:<key>；非法返回 None。"""
    if not to or not to.startswith("glyph:"):
        return None
    parts = to.split(":")
    if len(parts) != 4:
        return None
    _, repo, layer, key = parts
    if not repo or not layer or not key:
        return None
    return repo, layer, key


def format_glyph_target(layer: str, key: str, repo: str = REPO_NAME) -> str:
    return f"glyph:{repo}:{layer}:{key}"


def char_codepoints(char_row: dict) -> set[str]:
    """字头可用的码位集合（modern_codepoints + head）。"""
    out: set[str] = set()
    cps = char_row.get("modern_codepoints") or []
    if isinstance(cps, str):
        cps = [cps]
    for cp in cps:
        n = norm_cp(cp)
        if n:
            out.add(n)
    head = char_row.get("head") or ""
    if head and len(head) == 1:
        out.add(f"{ord(head):04X}")
    return out


def load_layer_index(glyph_repo: str, layer: str) -> dict[str, dict]:
    """读 glyphs/<layer>/index.csv，键为文件名 stem（如 0386、3D000）。"""
    path = os.path.join(glyph_repo, "glyphs", layer, "index.csv")
    if not os.path.isfile(path):
        return {}
    out: dict[str, dict] = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("file") or "").strip()
            if not raw:
                continue
            key = Path(raw).stem
            out[key] = row
    return out


def build_oracle_lookups(index_by_key: dict[str, dict]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """倒排：unicode → keys；仅无 unicode 的行参与 modern_char → keys。"""
    by_uni: dict[str, list[str]] = defaultdict(list)
    by_modern: dict[str, list[str]] = defaultdict(list)
    for key, row in index_by_key.items():
        uni = norm_cp(row.get("unicode"))
        if uni:
            by_uni[uni].append(key)
            continue
        modern = row.get("modern_char") or ""
        if modern:
            by_modern[modern].append(key)
    for d in (by_uni, by_modern):
        for k in d:
            d[k] = sorted(d[k])
    return dict(by_uni), dict(by_modern)


def identity_match(char_row: dict, index_row: dict) -> tuple[bool, str]:
    """G-identity：返回 (是否命中, 原因码)。"""
    head = char_row.get("head") or ""
    cps = char_codepoints(char_row)
    uni = norm_cp(index_row.get("unicode"))
    modern = index_row.get("modern_char") or ""

    if uni:
        if uni in cps:
            return True, "unicode"
        return False, "unicode-miss"

    if not head:
        return False, "no-head"
    if modern == head:
        return True, "modern"
    return False, "modern-miss"


def oracle_keys_for_char(
    char_row: dict,
    by_uni: dict[str, list[str]],
    by_modern: dict[str, list[str]],
) -> list[str]:
    """先 unicode 全量命中；若无命中再 exact modern_char（仅无 unicode 的字形行）。"""
    keys: list[str] = []
    seen: set[str] = set()
    for cp in char_codepoints(char_row):
        for key in by_uni.get(cp, []):
            if key not in seen:
                keys.append(key)
                seen.add(key)
    if keys:
        return keys
    head = char_row.get("head") or ""
    if not head:
        return []
    for key in by_modern.get(head, []):
        if key not in seen:
            keys.append(key)
            seen.add(key)
    return keys
