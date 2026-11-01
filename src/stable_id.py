"""陈述的**稳定 id**（内容寻址）——设计裁定 D45。

## 为什么要有

顺序号（`S000001`）**不能被引用**：全量重建一次，编号就可能变。
而「这条陈述是《大徐本·說文》的第 37 条，说的是『門内祭，先祖所以徬徨』」
这种引用，是本库作为「可被引用的引用库」的立身之本。

## 怎么算

`stable_id` = **内容地址**：把陈述的**身份字段**做规范化 JSON（键排序、无空格），
取 sha256 前 24 位十六进制。

```
stable_id = "st_" + sha256(canonical_json(身份字段 + 父的内容地址))[:24]
stable_id = "st_" + sha256(...)[:24] + "-k"      （内容重复时，k = 组内序号）
```

## 两条关键裁定（都是踩过坑之后定的）

### 一、父指针哈希**父的内容地址**，不哈希父的 S 号

**问题**：`parent_statement_id` 是 `S000001` 这种**顺序号**。
把它放进哈希，就等于「用会变的号去算不变的号」——
任何在父陈述之前插入/删除的重建（新增一个来源、修一次解析器），
这 **131,343 条（13.5%）** 有父指针的陈述，id 全变。
而文档自己说 S 号「全量重建一次就可能变」——那正是稳定 id 要逃开的东西。

**实测**：同一条子陈述，父号从 `S000001` 换成 `S999999`，
内容地址从 `69e2baf6…` 变成 `572e0506…`。

**修法**：哈希**父的内容地址**（不是父的 S 号）。
父的内容地址只取决于父自己的内容与父链，与编号无关 → 编号变了，地址不变。

*父总在子之前*：实测 131,343 条父子关系里，
父号全部小于子号（0 例相反）、全部同来源（0 例跨源）、
同文件内父也全部在子之前（0 例相反）→ **一趟 S→地址 映射就够**。

⚠ **代价（实测，也是收益）**：改成哈希父的地址后，**真正的重复全部暴露**——
「父的号不同、但父的内容相同」的子陈述，现在内容地址也相同。
重复组从 1,927 组 / 3,856 条涨到 **10,164 组 / 20,360 条**。
这不是新造的重复，是**旧算法用「父的顺序号」这个无意义的量掩盖掉的重复**
（康熙正文里 1,871 个条目被解析出 2–3 份完全相同的副本）。

→ **已修**（设计裁定 **D48**）：按同一把尺子去重 + 归一正文里的 HTML 残留
→ 重复组 **0**、靠次序区分 **0** → 本模块的 `stable_id` **100% 只取决于内容**。

### 二、身份字段与「记录状态」分开

**进哈希（身份）**：`kind` / `char_id` / `work_id` / `edition_id` / `text` /
`reading` / `radical` / `locator` / `source_id` + 父的内容地址。

**不进哈希（状态与指针）**：

| 字段 | 为什么不进 |
|---|---|
| `evidence_url` | **指针**，上游搬家就会变；变了不该换身份 |
| `license` | 是 `source_id` 的函数（唯一事实源在 `sources.jsonl`）——放进哈希**没有任何区分力**，只有漂移 |
| `confidence` | 人工复核一次就变一次 → 必然漂移 |
| `summary_method` | 只区分「原文直录 vs 自撰摘要」，而这两种的 `text` 必然不同 → 已由 `text` 区分 |
| `reviewer` / `review_date` | 复核一次就变一次 → 必然漂移 |

**实测：这样改不损失任何区分力**——
用全部字段与只用身份字段，distinct 数（962,626）、重复组数（10,258）、
重复记录数（10,295）**完全一致**。

⚠ 若把 `license` 留在哈希里，**改一次许可就会改 97 万条 id**——
那就不是稳定 id 了。

## 与其它三个键的分工

| 键 | 回答什么 | 唯一性 | 会变吗 |
|---|---|---|---|
| `statement_id` | 「本库里的第几行」 | 唯一 | **会**（重建即变） |
| `stable_id` | 「**这条内容**是谁」 | **100% 唯一** | 不变（除下面的例外） |
| `char_id` / `canonical_id` | 「说的是哪个字」 | — | 字头变了就变 |
| `search_group` | 「能检索到哪些字」 | — | — |

## 不再有依赖「次序」的 `stable_id`

内容逐字段完全相同的陈述，**内容寻址本来就分不开**——
加组内序号是唯一的办法，而那个序号只能来自「**同一来源内的出现次序**」。
所以只要数据里还有逐字段相同的记录，就有一部分 `stable_id` 依赖次序。

**这个坑已经填平**（设计裁定 **D48**）：上游曾有 **10,164 组 / 20,360 条**
逐字段完全相同的陈述（康熙正文 1,871 个条目被解析出 2–3 份完全相同的副本，
其下还挂着 16,758 条同样重复的引文）→ 去重后 **0 组 / 0 条**。

- **962,670 条**陈述的 `stable_id` **只取决于内容**（含父链）——重建后必然一致，
  **插在中间也不影响**；
- 「组内序号」这条后路仍在代码里（`stable_id_for(addr, occurrence)`），
  但**当前数据一条都用不上**——由已知缺口 **G002** 的探针守着（期望 0）。

## 用法

```python
from stable_id import assign_stable_ids

stats = assign_stable_ids(records)   # 就地写入 record["stable_id"]
```

⚠ **传入顺序必须是「父在前、子在后」**（同一来源内即文件序）。
父不在本批次里会直接报错——不静默降级（那样算出来的地址是错的）。

## 契约测试

设计上把「重建后不变」写成**可执行断言**：
在父之前插入一条 → 父与子的 id 必须一字不差；改父的号 → 子不变；
改父的内容 → 子变；改行政字段 → 不变；全量重算 → 逐条一致。
"""
from __future__ import annotations

import hashlib
import json
import sys

#: `stable_id` 的前缀（與 `statement_id` / `char_id` 區分開）。
PREFIX = "st_"

#: 內容哈希取多少位十六進制。24 位 = 96 bit——對 97 萬條記錄，
#: 生日碰撞概率約 1e-15；實測 962,670 條在 80 bit 內也無碰撞。
HEX_LEN = 24

#: **身份字段**——進內容哈希。
#:
#: 為什麼是這些：`kind` / `char_id` / `text` / `locator` / `source_id`…
#: 任何一個改變都意味著「這是另一條陳述」。挑字段會漏掉區分。
IDENTITY_FIELDS = (
    "kind", "char_id", "work_id", "edition_id", "text", "reading", "radical",
    "locator", "source_id",
)

#: **不进哈希**的字段——「记录状态」与「指针」，改了不该换身份。
#:
#: ⚠ 實測：把這些排除後，distinct 數 / 重複組數 / 重複記錄數**完全不變**
#:   （962,626 / 10,258 / 10,295）——它們**沒有任何區分力**，只有漂移。
EXCLUDED_FIELDS = (
    "statement_id", "stable_id",
    # 父指針**不直接進哈希**——改哈希的是「父的內容地址」（見 `content_address`）。
    "parent_statement_id",
    "evidence_url", "license", "confidence",
    "summary_method", "reviewer", "review_date",
)


def canonical_json(rec: dict, parent_addr: str | None = None) -> str:
    """陳述的規範化 JSON（身份字段 + 父的內容地址）。

    `parent_addr` = 父陳述的**內容地址**（無父則 `None`）。

    ⚠ **為什麼不是父的 S 號**：S 號是順序號，重建就會變；
    把它放進哈希 = 「用會變的號去算不變的號」（見模組說明）。
    """
    sub = {k: rec.get(k) for k in IDENTITY_FIELDS}
    sub["parent_addr"] = parent_addr
    return json.dumps(sub, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_address(rec: dict, parent_addr: str | None = None) -> str:
    """內容地址（不含組內序號）。

    `parent_addr` 是**父陳述的內容地址**——不是它的 `statement_id`。
    """
    payload = canonical_json(rec, parent_addr).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:HEX_LEN]


def stable_id_for(addr: str, occurrence: int = 0) -> str:
    """由內容地址 + 組內序號拼出 `stable_id`。"""
    return PREFIX + addr if occurrence <= 0 else f"{PREFIX}{addr}-{occurrence}"


def assign_stable_ids(records: list[dict]) -> dict:
    """就地給一批陳述賦 `stable_id`。

    **傳入順序必須是「父在前、子在后」**（同一來源內即檔案序）。
    父不在本批次裡 → 直接報錯（不靜默降級：那樣算出來的地址是錯的）。

    返回統計：{"assigned": n, "unique": n, "dup_groups": n, "dup_records": n}。
    """
    addr_of: dict[str, str] = {}
    seen: dict[str, int] = {}
    dup_groups = 0
    dup_records = 0
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
        addr = content_address(rec, paddr)
        addr_of[rec.get("statement_id")] = addr
        occ = seen.get(addr, 0)
        seen[addr] = occ + 1
        if occ == 0:
            pass
        else:
            if occ == 1:
                dup_groups += 1
            dup_records += 1
        rec["stable_id"] = stable_id_for(addr, occ)
    return {
        "assigned": len(records),
        "unique": len(seen),
        "dup_groups": dup_groups,
        "dup_records": dup_records,
    }


def main() -> int:
    """自檢：編碼/哈希的基本性質。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    base = {
        "statement_id": "S000001", "kind": "gloss", "char_id": "C0000001",
        "work_id": "shuowen", "edition_id": "shuowen_xu_shen",
        "text": "惟初太始", "reading": "於悉切", "radical": "一",
        "locator": {"upstream_id": 1}, "evidence_url": "https://x",
        "license": "Apache-2.0", "source_id": "s1", "confidence": "machine",
        "summary_method": None, "reviewer": None, "review_date": None,
        "parent_statement_id": None,
    }
    a = content_address(base)
    # 換父號 → 地址不變（因為沒把父號放進去）
    b = dict(base)
    b["parent_statement_id"] = "S999999"
    assert content_address(b) == a, "父號不該影響地址"
    # 改行政欄位 → 地址不變
    for f in ("evidence_url", "license", "confidence", "reviewer", "review_date"):
        c = dict(base)
        c[f] = "X"
        assert content_address(c) == a, "%s 不該影響地址" % f
    # 改身份欄位 → 地址變
    for f in ("text", "char_id", "kind", "locator", "source_id"):
        c = dict(base)
        c[f] = "X"
        assert content_address(c) != a, "%s 應該影響地址" % f
    print("stable_id 自檢通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
