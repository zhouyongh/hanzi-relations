"""压缩倒排索引（单字 + 双字）——设计裁定 D47。

## 为什么自己建，不用 FTS5

实测（本仓库自己的数据，962,670 条陈述 / 正文 1,621 万字）：

| 方案 | 存储 | 1 字查询 | 2 字查询 | 3 字及以上 |
|---|---|---|---|---|
| `LIKE '%字%'` 扫表 | 0 | ~270 ms | ~265 ms | ~265 ms |
| FTS5 `unicode61` | — | **答不了**（整串汉字当一个 token） | 答不了 | 勉强 |
| FTS5 `trigram` | +140.8 MiB | 答不了（需 ≥3 字） | 答不了 | 可 |
| **本模块（单字+双字压缩倒排）** | **+54.0 MiB** | 0.08–14.2 ms（随命中数） | 0.05–0.39 ms | 0.2–17 ms |

**FTS5 现成分词器答不了 1–2 字的汉字查询**——这不是配置问题，是分词器的问题：
`unicode61` 把整串汉字当成一个 token，`MATCH '"天地"'` 只命中 2 行，而 `LIKE` 命中 666 行。

**为什么「压缩倒排」比「行式倒排」小一个数量级**：
行式表把 (term, id) 拆成两列存 1,134 万行，每行都要付 SQLite 的行开销 + 索引开销——
实测 **+294 MiB**（上一轮估的 +592 MiB 是没压过的旧口径）。
而倒排索引真正的形态是「**每个词一条压缩后的 posting list**」：
delta 编码 + varint 后，1,134 万个 posting 只占 **12.31 MiB**（未压缩 int32 是 43.27 MiB）。

## 查询口径

| 查询 | 走哪张表 | 是否精确 |
|---|---|---|
| 1 字 | `inverted_unigram` | ✅ 精确 |
| 2 字 | `inverted_bigram`（**相邻**二字组） | ✅ 精确 |
| ≥3 字 | 相邻二字组求交 → **回原文校验子串** | ✅ 精确（候选集是超集，校验后精确） |

**为什么要回原文校验**：二字组求交只能保证「这些二字组都出现过」，
不能保证它们**首尾相接**（"天地玄黄" 可能来自"天地…玄黄"两处）。
候选集在 ≥3 字时已经很小（实测「天地玄黄」候选 0 条），回查原文校验很便宜。

## 编码

posting list = 该词命中的 `rid`（稠密序号，= 语句在 `statements` 表里的行号），
按升序 delta 编码 + LEB128 varint。

*为什么用 `rid` 而不是 `statement_id`*：前者是整数，delta 编码能压到 1–3 字节；
后者是字符串（"S701422"），存不进去。
*为什么用显式的 `rid` 列而不是 SQLite 的隐式 rowid*：`VACUUM` **可能改 rowid**
（官方文档：「没有显式 INTEGER PRIMARY KEY 的表，VACUUM 可能改 ROWID」）——
那会静默毁掉整个索引。所以 `statements` 表把 `rid` 定为 `INTEGER PRIMARY KEY`。

## 与 `stable_id` 的分工

倒排索引是**派生件**（存在 SQLite 里，不进 `data/`）；
`stable_id`（D45）是**事实源里的稳定键**——引用某条陈述用 `stable_id`，
检索用这里的 `rid`。
"""
from __future__ import annotations

import os
import sys
from array import array

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def encode_deltas(ids: list[int]) -> bytes:
    """升序整数序列 → delta + varint 编码的 blob。"""
    out = bytearray()
    prev = 0
    for i in ids:
        v = i - prev
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                out.append(b | 0x80)
            else:
                out.append(b)
                break
        prev = i
    return bytes(out)


def decode_deltas(blob: bytes) -> list[int]:
    """delta + varint blob → 升序整数序列。"""
    out: list[int] = []
    pos = 0
    prev = 0
    n = len(blob)
    while pos < n:
        shift = 0
        val = 0
        while True:
            b = blob[pos]
            pos += 1
            val |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        prev += val
        out.append(prev)
    return out


def build_postings(texts: list[str]) -> tuple[dict[str, array], dict[str, array]]:
    """建单字 / 双字倒排表。

    `texts[i]` 对应 `rid = i + 1`（**必须**与 `statements` 表的 `rid` 一致）。

    返回 `(unigram, bigram)`：两个 `dict[str, array('I')]`。
    """
    uni: dict[str, array] = {}
    bi: dict[str, array] = {}
    for idx, txt in enumerate(texts):
        if not txt:
            continue
        rid = idx + 1
        for ch in set(txt):
            a = uni.get(ch)
            if a is None:
                a = array("I")
                uni[ch] = a
            a.append(rid)
        for k in range(len(txt) - 1):
            g = txt[k:k + 2]
            a = bi.get(g)
            if a is None:
                a = array("I")
                bi[g] = a
            a.append(rid)
    return uni, bi


def candidates(con, query: str) -> tuple[set[int], bool]:
    """查候选集。

    返回 `(rids, exact)`：`exact=True` 表示候选集**已经是精确答案**；
    `exact=False` 表示还需要回原文校验子串（≥3 字查询的情形）。

    *为什么 ≥3 字不直接给精确答案*：二字组求交只能保证「这些二字组都出现过」，
    不能保证它们首尾相接。候选集通常已经很小，回查原文校验很便宜。
    """
    q = query.strip()
    if not q:
        return set(), True
    if len(q) == 1:
        row = con.execute("SELECT postings FROM inverted_unigram WHERE term=?",
                           (q,)).fetchone()
        return (set(decode_deltas(row[0])) if row else set()), True
    if len(q) == 2:
        row = con.execute("SELECT postings FROM inverted_bigram WHERE term=?",
                           (q,)).fetchone()
        return (set(decode_deltas(row[0])) if row else set()), True
    grams = [q[i:i + 2] for i in range(len(q) - 1)]
    lists = []
    for g in grams:
        row = con.execute("SELECT postings FROM inverted_bigram WHERE term=?",
                           (g,)).fetchone()
        if not row:
            return set(), True
        lists.append(set(decode_deltas(row[0])))
    lists.sort(key=len)
    cur = set(lists[0])
    for s in lists[1:]:
        cur &= s
        if not cur:
            break
    return cur, False


def search(con, query: str) -> list[int]:
    """精确检索：返回命中的 `rid` 列表（升序）。"""
    rids, exact = candidates(con, query)
    if exact or not rids:
        return sorted(rids)
    # 回原文校验子串（候选集小的时候很快）
    q = query
    out = []
    chunk = 400
    items = sorted(rids)
    for i in range(0, len(items), chunk):
        part = items[i:i + chunk]
        marks = ",".join("?" * len(part))
        for (rid, text) in con.execute(
                "SELECT rid, text FROM statements WHERE rid IN (%s)" % marks, part):
            if text and q in text:
                out.append(rid)
    return sorted(out)


def connect(db_path: str):
    """打開本庫的 SQLite，並註冊 `inverted_rids(term, kind)` 標量函數。

    有了它，**純 SQL** 也能查倒排索引（不需要自己寫 varint 解碼）：

    ```sql
    SELECT s.statement_id, s.text
    FROM statements s
    WHERE s.rid IN (SELECT value FROM json_each(inverted_rids('天地', 'bigram')));
    ```

    `kind` 傳 `'unigram'`（單字）或 `'bigram'`（相鄰二字組）。
    """
    import json
    import sqlite3

    con = sqlite3.connect(db_path)

    def _rids(term, kind="bigram"):
        table = ("inverted_unigram"
                 if str(kind).lower().startswith("uni") else "inverted_bigram")
        row = con.execute("SELECT postings FROM %s WHERE term=?" % table,
                           (term,)).fetchone()
        return json.dumps(decode_deltas(row[0]) if row else [])

    con.create_function("inverted_rids", 2, _rids)
    return con


def main() -> int:
    """自检：编码/解码往返。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    for seq in ([], [1], [1, 2, 3], [1, 1000, 1001, 999999], list(range(1, 500))):
        assert decode_deltas(encode_deltas(seq)) == seq, seq
    print("encode/decode 往返自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
