"""版本異文的**分類判據**（說文系：段注本 / 小徐本 ↔ 大徐本）。

## 為什麼要抽出來

`edition_variant` 邊的 `kind`，由「這兩本的釋文像不像 → 屬哪一類異文」判定。
而小徐本（系傳）的釋文曾因解析器 bug 少末字——**釋文一改，`kind` 就可能跟著變**。

所以判據必須**只有一份**：重建與一次性遷移都走**同一個函數**——
否則兩邊算出的 `kind` 不一致，等於把「資料漂移」從正文挪到了邊上。

## 判據

以**大徐本**為基準（它是《說文》正文的標準本）：

| 類別 | 條件 | 意思 |
|---|---|---|
| `同（注疏展開）` | 相似度 ≥ 0.75 | 同一說，只是注疏展開 |
| `同（段注展開）` | 前綴重合 ≥ min(len, 6) | 段注開頭複述原文 |
| `段注糾正` | 前 60 字內有「非／誤／訛／不」 | 段玉裁明確否定 → 版本異文 |
| `略異` | 相似度 ≥ 0.45 | 說法略有出入 |
| `異文` | 其餘 | 版本異文 |

⚠ 類別名裡帶「段注」是**沿用下來的名字**——小徐本↔大徐本也用這套判據，
名字有點不贴切，但**改名就是改資料**（下游可能按字面取用），故保留。
"""
from __future__ import annotations

import difflib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shuowen_editions as se  # noqa: E402

#: 段玉裁明確否定的標記（「非。」／「誤」／「訛」…）。
_NEG_RE = re.compile(r"[。；]\s*(非|誤|訛|非也|非謂|不)")

#: `kind` 的封閉取值域（與 builder 產出的字串逐字一致）。
KINDS = ("同（注疏展開）", "同（段注展開）", "段注糾正", "略異", "異文", "無可比")


def norm(text: str, s2t: dict[str, str]) -> str:
    """比較用歸一：去標點空白 + 簡繁統一。

    `s2t` = 單字「簡 → 繁」映射（builder 用 `c.simp_to_trad` 裡的單字對）。
    """
    t = se._norm_text(text)
    return "".join(s2t.get(ch, ch) for ch in t)


def classify(base_text: str, other_text: str,
             s2t: dict[str, str]) -> tuple[str, float]:
    """把「本 ↔ 大徐本」的釋文分成一類。

    回傳 `(kind, 相似度)`。`kind` 取值域見 `KINDS`。
    """
    a = norm(base_text, s2t)
    b = norm(other_text, s2t)
    if not a or not b:
        return "無可比", 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    prefix = 0
    for x, y in zip(a, b):
        if x != y:
            break
        prefix += 1
    neg = _NEG_RE.search((other_text or "")[:60])
    if ratio >= 0.75:
        return "同（注疏展開）", ratio
    if prefix >= min(len(a), 6):
        return "同（段注展開）", ratio
    if neg:
        return "段注糾正", ratio
    if ratio >= 0.45:
        return "略異", ratio
    return "異文", ratio
