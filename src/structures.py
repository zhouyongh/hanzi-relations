"""構形值域的登記與歸一（規格 §4 D43）。

## 這個模組解決什麼

`type=structure` 的邊有 **85,238 條**，目標是 `structure:cjk_decomp:<key>`。
但這個命名空間**沒有登記表**——85,238 個值**無人校驗**，
「這條邊指向的構形到底是什麼」只能靠去翻上游檔案。

這與 D42 修掉的「讀音值域無登記」是**同一個病**（硬規則 29 的孪生漏洞）：
值散落在邊的目標裡，沒有登記項，就查不出「指向虛構值」的邊。

## 為什麼 IDS 文本不在這裡，而在陳述裡

`cjk_decomp` 的 IDS 分解（`ra(㇑)`、`d(白,干)`）是**陳述的 text**——
它是一條有出處的內容（`source_id=cjk_decomp`，Apache-2.0）。
本模組只登記**值的身份**（key → 值的歸屬關係），
**不複制內容**（內容在陳述裡，许可由 `edition_id` join 出去）。

## 與 readings 的對稱性

| | 讀音 | 構形 |
|---|---|---|
| 邊類型 | `phonology` | `structure` |
| 目標 | `reading:<edition_id>:<slug>` | `structure:<source_id>:<key>` |
| 登記表 | `data/readings.jsonl` | `data/structures.jsonl` |
| 值域 | 聲調符號/定位碼/反切/小韻號 | IDS 構形式 |
| 內容載體 | 陳述的 `reading` 欄 | 陳述的 `text` 欄 |

**差異**：讀音的目標第一段是 `edition_id`（硬規則 19 要求，便於校驗器統一判），
構形的目標第一段是 `source_id`——因為 key 是**上游自己的 key**
（`cjk-decomp.txt` 的行首碼位），天然屬於來源而非版本形態。
登記行裡另有 `edition_id`，许可照樣能 join 出去（硬規則 2）。
"""
from __future__ import annotations

from typing import Iterable

#: 構形值的語義類別（**封閉枚舉**，新增需改本文件與規格）。
STRUCTURE_KINDS = ("ids",)   # IDS 構形分解式，如 `d(白,干)`


def structure_id(namespace: str, key: str) -> str:
    """構形的穩定 id：`structure:<namespace>:<key>`。

    `namespace` 是**來源 id**（如 `cjk_decomp`）——key 是上游自己的 key。
    """
    return f"structure:{namespace}:{key}"


def split_structure_target(target: str) -> tuple[str, str] | None:
    """把 `structure:<namespace>:<key>` 拆成 `(namespace, key)`。

    key 裡可能含 `:`（上游 key 一般不含，但別把 key 截斷），
    所以只拆**前兩段**，其餘原樣歸 key。
    """
    if not target.startswith("structure:"):
        return None
    parts = target.split(":", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


def build_structure_rows(edges: Iterable[dict],
                         texts: dict[str, str] | None = None) -> list[dict]:
    """由 `structure` 邊推出 `data/structures.jsonl` 的登記行。

    `texts` 是**可選的**「key → 值文本」對照（從陳述的 text 來）。
    給了就把值寫進登記表（值域因此可查）；沒給就只登記 key 與 id。

    登記行**不含 `license`、不含 `source_id`**：
    許可由 `edition_id` → `editions.jsonl` → `sources.jsonl` join 出去
    （硬規則 2：第二份許可載體是漂移源）。
    """
    acc: dict[str, dict] = {}
    for e in edges:
        if e.get("type") != "structure":
            continue
        target = e.get("to") or ""
        sp = split_structure_target(target)
        if sp is None:
            continue
        namespace, key = sp
        if target in acc:
            continue
        rec = acc[target] = {
            "structure_id": target,
            "namespace": namespace,
            "key": key,
            "value": "",
            "value_kind": "ids",
            "derived_from": "data/relations/",
            "transform": "extract_from_structure_edge",
        }
        if texts:
            t = texts.get(key)
            if t:
                rec["value"] = t
    rows = []
    for key in sorted(acc):
        rows.append(acc[key])
    return rows
