"""陈述去重：把「内容逐字段完全相同」的重复行收成一条——设计裁定 D48。

## 为什么要有

上游《康熙》把同一条目收了**两遍**，抽取器又按正文里的**每次出现**各发一条引文
（实测：「芻」字的正文里「【孟子】猶芻豢之悅我口。」出现 2 次 → 2 条一模一样的 citation）。
结果：库里原有 972,921 条陈述，其中 **10,196 条**是**逐字段完全相同**的冗余副本
（10,164 组），康熙占 10,183 条；去重后 **962,670 条、重复组 0**。

这不只是「数字虚高」——它直接污染了 `stable_id`：

- 内容寻址**分不开**逐字段相同的陈述 → 只能加「组内序号」后缀区分
  （`st_xxx-1` / `st_xxx-2`）；
- 于是 **1.05% 的 stable_id 依赖「同一来源内的出现次序」**，而不是只由内容决定。
- 而「组内次序」不是内容的性质——上游解析器改一次、插入一条、重复一次，id 就变。

**实测：按本模块的口径去重后，stable_id 重复归零（0 组 / 0 条靠次序）**，
即 100% 由内容决定。

## 去重的尺子 = `stable_id` 的尺子

去重键就是 **`stable_id` 的内容地址**：身份字段
（`kind` / `char_id` / `work_id` / `edition_id` / `text` / `reading` / `radical` /
`locator` / `source_id`）+ **父的内容地址**。

⚠ **绝不能按 `(char, text)` 粗去重**：那会把「同字、同正文、但 kind / work /
父不同」的记录误合并——实测那种记录有 99 行是「同文本但语义不同」的，
粗去重会把它们一起删掉（这是**误删**，不是去重）。

## 去重**不改变任何内容地址**

内容地址只取决于内容与父链；两条内容相同的陈述，删掉其中一条，
另一条的地址**一字不变** → 子陈述的地址也不变（父的内容地址相同）。

唯一会变的是**组内序号后缀**（`st_xxx-1` → `st_xxx`）——那正是要修的东西。

## ⚠ 关键：删掉「父」要把子陈述改指到保留的那条

重复的《康熙》正文（gloss）是**引文（citation）的父**。删掉重复的父，
子陈述就会变成**孤儿**（`parent_statement_id` 指向不存在的陈述）。

实测：冗余陈述里有 **8,272 条子陈述**以它为父——但**那些子陈述自己也都是冗余**
（父的副本 → 子的副本），所以一并删掉 → **实测真正需要改指的只有 1 条**
（归一正文 HTML 残留后出现的那条）。
本模块会把它们**改指到保留的那条父**——因为两者的内容地址相同，
改指**不影响任何 stable_id**。

## 用法

```python
from dedupe import dedupe_statements, prune_relations

kept, stats = dedupe_statements(records)      # records：同一来源内，父在前
rels, rstats = prune_relations(relations, dropped_ids)
```
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stable_id as sid_mod  # noqa: E402

#: 上游 xlsx 单元格里混着的 **HTML 残留**（只有《康熙》有，实测 3,496 处 / 3,428 条）。
#:
#: 它**不是正文内容**，是上游把 HTML 单元格直接存了进来。同一条目的**另一份副本**
#: 在同一位置写的是 `\n\n`——实测 84 组「同一康熙条目收两遍」里有 84 组就是差在
#: 「`<hr>` ↔ `\n\n`」这一处。留着 HTML 标记 → 同一个条目算成两个身份。
#:
#: ⚠ **只换 `<hr>`，不动别的**：正文里的文字一个字都不改（硬规则 1：忠实记录）。
_HR_TAG = re.compile(r"<hr\s*/?>")


def normalize_upstream_text(text: str | None) -> str:
    """把正文里的 HTML 残留归一为纯文本形态。

    - `<hr>` / `<hr/>` → `\\n\\n`（HTML 分隔线 → 段落空行）
      ——**依据**：同一条目的另一份副本在同一位置写的是 `\\n\\n`；
    - 其余字符**一字不动**。

    这不是「美化上游」，而是把**标记语言**换成**纯文本**：
    同一段内容在两种写法下必须算出**同一个身份**。
    """
    if not text:
        return text or ""
    return _HR_TAG.sub("\n\n", text)


def content_addresses(records: list[dict]) -> dict:
    """算每条陈述的内容地址（与 `stable_id` 同一把尺）。

    **传入顺序必须「父在前、子在后」**（同一来源内即文件序）。
    父不在批次内 → 直接报错（不静默降级：那算出来的地址是错的）。
    """
    addr_of: dict[str, str] = {}
    for rec in records:
        pid = rec.get("parent_statement_id")
        if pid:
            paddr = addr_of.get(pid)
            if paddr is None:
                raise ValueError(
                    "父陳述 %r 不在本次賦值範圍內——父必須在子之前"
                    "（陳述 %r，來源 %r）"
                    % (pid, rec.get("statement_id"), rec.get("source_id")))
        else:
            paddr = None
        addr_of[rec.get("statement_id")] = sid_mod.content_address(rec, paddr)
    return addr_of


def dedupe_statements(records: list[dict]) -> tuple[list[dict], dict]:
    """把「内容逐字段完全相同」的陈述收成一条（保留每组首条）。

    返回 `(kept, stats)`。`stats["dropped_ids"]` 是被删的 `statement_id` 集合。
    """
    addr_of = content_addresses(records)

    groups: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    kept: list[dict] = []
    remap: dict[str, str] = {}       # 被删的 id → 保留的 id
    dropped: list[str] = []

    for rec in records:
        addr = addr_of[rec.get("statement_id")]
        counts[addr] = counts.get(addr, 0) + 1
        g = groups.get(addr)
        if g is None:
            groups[addr] = [rec]
            kept.append(rec)
        else:
            # 内容完全相同 → 冗余副本。保留首条，其余删。
            remap[rec.get("statement_id")] = g[0].get("statement_id")
            dropped.append(rec.get("statement_id"))

    # 子陈述的父改指到保留的那条（父的内容地址不变 → stable_id 不变）
    n_repoint = 0
    for rec in kept:
        pid = rec.get("parent_statement_id")
        if pid and pid in remap:
            rec["parent_statement_id"] = remap[pid]
            n_repoint += 1

    stats = {
        "n": len(records),
        "kept": len(kept),
        "dropped": len(dropped),
        "groups": sum(1 for v in counts.values() if v > 1),
        "repointed": n_repoint,
        "dropped_ids": set(dropped),
    }
    return kept, stats


def prune_relations(relations: list[dict], dropped_ids: set) -> tuple[list[dict], dict]:
    """删掉引用了被删陈述的边，并从 `statement_ids` 里剔除被删的 id。

    - `from` / `to` 指向被删陈述的边 → **整条删**；
    - `statement_ids` 里含被删陈述的边 → **剔除**；剔完变空 → 整条删。
    """
    kept: list[dict] = []
    n_del = 0
    n_pruned = 0
    for rel in relations:
        frm = rel.get("from") or ""
        to = rel.get("to") or ""
        if (frm.startswith("statement:") and frm[10:] in dropped_ids) or \
           (to.startswith("statement:") and to[10:] in dropped_ids):
            n_del += 1
            continue
        sids = rel.get("statement_ids")
        if sids:
            new = [s for s in sids if s not in dropped_ids]
            if len(new) != len(sids):
                if new:
                    rel["statement_ids"] = new
                    n_pruned += 1
                else:
                    n_del += 1
                    continue
        kept.append(rel)
    return kept, {"n": len(relations), "kept": len(kept),
                  "deleted": n_del, "pruned": n_pruned}


def main() -> int:
    """自检：构造几条记录，验证「去重不改内容地址」「子陈述改指后 stable_id 不变」。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    def rec(sid, kind, char, text, parent=None, src="s1", loc=None):
        # ⚠ locator 必须**逐字段相同**才算重复——尺子含 locator。
        return {"statement_id": sid, "kind": kind, "char_id": char,
                "work_id": "w", "edition_id": "e", "text": text,
                "reading": None, "radical": None,
                "locator": {"up": 1} if loc is None else loc,
                "source_id": src, "evidence_url": "https://x", "license": "MIT",
                "confidence": "machine", "summary_method": None,
                "reviewer": None, "review_date": None,
                "parent_statement_id": parent}

    # 父 P + 子 C；再一份完全相同的父 P'（被删）+ 一个**只属于 P' 的**子 C'
    # （C' 的正文不同 → 它本身不重复 → 必须被改指到保留的 P，否则变孤儿）
    recs = [rec("S1", "gloss", "C1", "原文"),
            rec("S2", "citation", "C1", "引文", parent="S1"),
            rec("S3", "gloss", "C1", "原文"),
            rec("S4", "citation", "C1", "另一条引文", parent="S3")]

    kept, st = dedupe_statements([dict(r) for r in recs])
    sid_stats = sid_mod.assign_stable_ids(kept)

    assert st["dropped"] == 1, st
    assert st["groups"] == 1, st
    assert st["repointed"] == 1, st
    # S3 被删；S4 的父改指 S1
    assert kept[1]["parent_statement_id"] == "S1", kept[1]
    # 保留的三条：父 S1、子 S2、C'
    assert [r["statement_id"] for r in kept] == ["S1", "S2", "S4"], kept
    # 内容地址不变 → 改指**不影响**子陈述的 stable_id
    a = sid_mod.content_address(kept[0])
    assert sid_mod.content_address(recs[0]) == a, "父的内容地址变了"
    assert sid_mod.content_address(kept[1], a) == sid_mod.content_address(recs[1], a), \
        "子的内容地址变了"
    # 改指后，S4 的地址 = 它原来（以 S3 的地址为父）的地址
    a3 = sid_mod.content_address(recs[2])
    assert sid_mod.content_address(kept[2], a) == sid_mod.content_address(recs[3], a3), \
        "改指后子陈述的内容地址变了"
    # 去重后不该再有「靠次序区分」的 stable_id
    assert sid_stats["dup_groups"] == 0 and sid_stats["dup_records"] == 0, sid_stats
    print("去重自检通过：删 %d 条 / 改指 %d 条 / 内容地址不变 / 重复组归零"
          % (st["dropped"], st["repointed"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
