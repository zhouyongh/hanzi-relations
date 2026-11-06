"""字形指针契约与 G-identity（单一事实源）。

指针格式（v2，硬切）：
  glyph:<repo>:uid:<unified_id>
例：
  glyph:hanzi-glyphs-svg:uid:004E00-0E00-000001

旧格式 glyph:<repo>:<layer>:<key> 一律非法（G1），不做回落。

unified_id 语法（字形库 ID-FORMAT 6-4-6）：
  ^[0-9A-F]{6}-[0-9A-Z]{4}-[0-9]{6}$

已发布层白名单：与字形库 v1.0.0 账本 13 层对齐（单一事实源本文件）。
层内文件路径由账本解析：resolve_layer_file(uid) → (layer, file)。

G-identity（字头 ↔ 字形账本/index 行）：
  1. 优先：unicode 规范化后等于字头码位（modern_codepoints 或 head）
  2. 否则（无 unicode）：modern_char 必须与字头 head 完全一致
  禁止在本校验里做异体映射。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_NAME = "hanzi-glyphs-svg"
UID_MARKER = "uid"
UID_RE = re.compile(r"^[0-9A-F]{6}-[0-9A-Z]{4}-[0-9]{6}$")

# 与 hanzi-glyphs-svg v1.0.0 账本 13 层对齐；未在此集合的层 → G2。
PUBLISHED_LAYERS = frozenset({
    "oracle",
    "bronze",
    "chu",
    "qinwenzi",
    "seal",
    "clerical",
    "caoshu",
    "kaishu",
    "xingshu",
    "chuanchao",
    "songti",
    "songti-tc",
    "heiti-hk",
})

DEFAULT_GLYPH_REF = "v1.0.0"


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


def parse_glyph_target(to: str) -> tuple[str, str] | None:
    """解析 glyph:<repo>:uid:<unified_id>；非法（含旧 layer:key）返回 None。"""
    if not to or not to.startswith("glyph:"):
        return None
    parts = to.split(":")
    if len(parts) != 4:
        return None
    _, repo, marker, uid = parts
    if marker != UID_MARKER or not repo or not uid:
        return None
    if not UID_RE.match(uid):
        return None
    return repo, uid


def format_glyph_target(unified_id: str, repo: str = REPO_NAME) -> str:
    """生成新指针。unified_id 须已是 6-4-6。"""
    if not UID_RE.match(unified_id):
        raise ValueError(f"非法 unified_id: {unified_id!r}")
    return f"glyph:{repo}:{UID_MARKER}:{unified_id}"


# 兼容旧调用名
format_uid_target = format_glyph_target


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


def identity_match(char_row: dict, glyph_row: dict) -> tuple[bool, str]:
    """G-identity：返回 (是否命中, 原因码)。glyph_row 需含 unicode / modern_char。"""
    head = char_row.get("head") or ""
    cps = char_codepoints(char_row)
    uni = norm_cp(glyph_row.get("unicode"))
    modern = glyph_row.get("modern_char") or ""

    if uni:
        if uni in cps:
            return True, "unicode"
        return False, "unicode-miss"

    if not head:
        return False, "no-head"
    if modern == head:
        return True, "modern"
    return False, "modern-miss"


def default_glyph_repo(relations_root: str | None = None) -> str:
    env = os.environ.get("HANZI_GLYPHS_SVG")
    if env:
        return env
    if relations_root is None:
        relations_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(os.path.dirname(relations_root), "hanzi-glyphs-svg")


def _git_show(repo: str, ref: str, path: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", repo, "show", f"{ref}:{path}"],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace")
        raise FileNotFoundError(f"git show {ref}:{path} @ {repo}: {err}") from exc
    return out.decode("utf-8")


def _git_rev_parse(repo: str, ref: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", repo, "rev-parse", ref],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def load_ledger(
    glyph_repo: str,
    glyph_ref: str | None = DEFAULT_GLYPH_REF,
) -> tuple[dict[str, dict], dict[tuple[str, str], str], dict]:
    """加载账本。

    返回：
      by_uid: unified_id → row
      by_layer_stem: (layer, stem) → unified_id
      meta: 钉版本信息
    """
    meta: dict[str, Any] = {}
    if glyph_ref:
        raw = _git_show(glyph_repo, glyph_ref, "glyphs/glyph-ids.csv")
        try:
            meta = json.loads(_git_show(glyph_repo, glyph_ref, "glyphs/glyph-ids.meta.json"))
        except (FileNotFoundError, json.JSONDecodeError):
            meta = {}
        meta = dict(meta)
        meta["_pinned_ref"] = glyph_ref
        meta["_pinned_sha"] = _git_rev_parse(glyph_repo, glyph_ref)
        meta["_source"] = f"git:{glyph_ref}:glyphs/glyph-ids.csv"
    else:
        path = os.path.join(glyph_repo, "glyphs", "glyph-ids.csv")
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        meta_path = os.path.join(glyph_repo, "glyphs", "glyph-ids.meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        meta = dict(meta)
        meta["_pinned_ref"] = None
        meta["_pinned_sha"] = None
        meta["_source"] = path

    by_uid: dict[str, dict] = {}
    by_layer_stem: dict[tuple[str, str], str] = {}
    collisions = 0
    for row in csv.DictReader(io.StringIO(raw)):
        if (row.get("status") or "").strip() != "active":
            continue
        uid = (row.get("unified_id") or "").strip()
        layer = (row.get("layer") or "").strip()
        file_name = (row.get("file") or "").strip()
        if not uid or not layer or not file_name or not UID_RE.match(uid):
            continue
        stem = Path(file_name).stem
        rec = {
            "unified_id": uid,
            "layer": layer,
            "file": file_name,
            "stem": stem,
            "modern_char": row.get("modern_char") or "",
            "unicode": row.get("unicode") or "",
            "source_id": row.get("source_id") or "",
            "release": row.get("release") or "",
            "status": row.get("status") or "",
        }
        by_uid[uid] = rec
        key = (layer, stem)
        if key in by_layer_stem and by_layer_stem[key] != uid:
            collisions += 1
        by_layer_stem[key] = uid
    meta["_active_count"] = len(by_uid)
    meta["_stem_collisions"] = collisions
    return by_uid, by_layer_stem, meta


def resolve_layer_file(uid: str, by_uid: dict[str, dict]) -> tuple[str, str] | None:
    """unified_id → (layer, file 文件名，含 .svg)。viewer / G3 用。"""
    row = by_uid.get(uid)
    if not row:
        return None
    return row["layer"], row["file"]


def resolve_layer_stem(uid: str, by_uid: dict[str, dict]) -> tuple[str, str] | None:
    row = by_uid.get(uid)
    if not row:
        return None
    return row["layer"], row["stem"]


def load_layer_index(glyph_repo: str, layer: str) -> dict[str, dict]:
    """读 glyphs/<layer>/index.csv，键为文件名 stem。"""
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


def build_identity_lookups(
    by_uid: dict[str, dict],
    layer: str | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """倒排：unicode → [uid…]；仅无 unicode 的行参与 modern_char → [uid…]。"""
    by_uni: dict[str, list[str]] = defaultdict(list)
    by_modern: dict[str, list[str]] = defaultdict(list)
    for uid, row in by_uid.items():
        if layer is not None and row["layer"] != layer:
            continue
        uni = norm_cp(row.get("unicode"))
        if uni:
            by_uni[uni].append(uid)
            continue
        modern = row.get("modern_char") or ""
        if modern:
            by_modern[modern].append(uid)
    for d in (by_uni, by_modern):
        for k in d:
            d[k] = sorted(set(d[k]))
    return dict(by_uni), dict(by_modern)


def uids_for_char(
    char_row: dict,
    by_uni: dict[str, list[str]],
    by_modern: dict[str, list[str]],
) -> list[str]:
    """先 unicode 全量命中；若无命中再 exact modern_char。"""
    uids: list[str] = []
    seen: set[str] = set()
    for cp in char_codepoints(char_row):
        for uid in by_uni.get(cp, []):
            if uid not in seen:
                uids.append(uid)
                seen.add(uid)
    if uids:
        return uids
    head = char_row.get("head") or ""
    if not head:
        return []
    for uid in by_modern.get(head, []):
        if uid not in seen:
            uids.append(uid)
            seen.add(uid)
    return uids


def build_oracle_lookups(index_by_key: dict[str, dict]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """兼容：从 index.csv（stem→row）建倒排，返回 stem 列表。"""
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


def oracle_keys_for_char(
    char_row: dict,
    by_uni: dict[str, list[str]],
    by_modern: dict[str, list[str]],
) -> list[str]:
    """兼容旧 API：返回 stem/uid 列表（取决于 lookups 内容）。"""
    return uids_for_char(char_row, by_uni, by_modern)
