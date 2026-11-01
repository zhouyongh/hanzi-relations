"""部首层的推导逻辑（迁移脚本、builder、回归校验三方共用）。

## 为什么要有这个模块

在构建脚本重建时会**重新生成**同样的数据。两处逻辑如果各写一份，
重建一次就会漂移一次——**那就是第二份事实源**（硬规则 2 的教训）。

所以推导逻辑抽到这个模块，**两边 import 同一份**；校验器也 import 同一份，
做「派发件 == 推导结果」的回归。

## 输入必须是**显式参数**，不能是「整个字头表」

这是 D41.4（字头表瘦身）的硬约束：瘦身后字头表**不再有**
`radical` / `radical_kangxi` / `radical_kangxi_simplified` / `residual_strokes`
四列，若本模块直接去读字头表，瘦身后就再也跑不了了。

所以这里的每个函数都只接受**它真正需要的那几项**：

| 事实 | 瘦身前从哪来 | 瘦身后从哪来 |
|---|---|---|
| 康熙部首字 → 部號 | 字頭表投票 | `radicals.jsonl` 的康熙部行 |
| 字 → 歸屬（部） | 字頭表 `radical` 列 | `radical` 邊（char → radical:<sys>:<x>） |
| 說文部名集合 | 字頭表 `radical` 列 | `radicals.jsonl` 的 `system=shuowen` 行 |
| 說文字頭 → 部名 | 字頭表 `radical` 列 | `radical` 邊 + 字頭表 `head` |
| 部間對照 | `radical_map` 陳述的 `locator` | 同左（陳述不瘦） |

## 核心推导：康熙部首字 → 部號

康熙字头的 `radical` 列记的是**部首字**（「木」部里的每个字都记 `radical=「木」`），
而 Unihan 行的 `radical_kangxi`（kRSUnicode）记的是**部号**——
kRSUnicode 本来就是康熙 214 部。

两者**交叉投票**即得映射：某个康熙字 X 的部首字是 R，
若 X 在 Unihan 里的部号是 N，则「R 属于第 N 部」得一票。

实测：**214/214 全部解出**；且与 `data/radicals.jsonl` 里已有的
康熙部行**逐个一致**——两条独立路径互证。

## 说文部名

说文行的 `radical` 列**直接就是说文部名**——不需要任何推导，直接用作边的目标。

## 边目标格式

| 边 | from | to |
|---|---|---|
| `radical`（康熙字） | `char:<康熙字頭 id>` | `radical:kangxi:<部號>` |
| `radical`（说文字） | `char:<說文字頭 id>` | `radical:shuowen:<部名>` |
| `radical_contains` | `radical:kangxi:<N>` ⇄ `radical:shuowen:<部名>` | |
| `radical_owner` | `radical:kangxi:<N>` | `radical:shuowen:<部名>` |

`radical_owner` 的语义是「**这个康熙部首字这个字**，在《說文》里属哪一部」——
與 `radical_contains`（部間包含）與 `same_name`（兩部等同）**都不是一回事**。

**只建有信息量的那几条**：若部首字 R 在《說文》里归的正是 R 部（同义反复），
那它與 `same_name` 重複，不建；若《說文》**没收** R 这个字，
**没有出处可依**，按硬规则 1「無出處，不入庫」不建。
实测：198 条同义反复、7 条有信息量、9 条《說文》未收。
"""
from __future__ import annotations

import collections
from typing import Iterable

#: 部首系的关系类型（迁移时整族重建）。
RADICAL_FAMILY = ("radical", "radical_contains", "radical_owner")

#: 迁移脚本新建的部首系边用的 id 段。
#:
#: 为什么留一段而不是接着最大 id 往下发：
#: **幂等**。重跑迁移时，旧边被剔除、新边用**同样的 id** 重建，
#: 所以「同输入必得同输出」成立（硬规则 11）。
#: 段起点 9,000,000 高于 builder 当前的最大 id（约 2,150,000），
#: 不会与正常构建发号冲突。
MIGRATED_REL_ID_BASE = 9_000_000

#: 康熙部登记表用的**派生来源**（部号来自 Unihan、部首字来自 kangxi-dictionary）。
#:
#: 为什么是派生来源而不是直接挂 `unihan` / `kangxi_dict`：
#: `radicals.jsonl` 的每一行同时含**两套来源的数据**（部号 + 部首字），
#: 挂单一来源就是**低报实际义务**（硬规则 2 的教训）——
#: `radical_map` 这个来源当初就按这个原则登记成 `Unicode-3.0+Apache-2.0`。
KANGXI_RADICALS_SOURCE = {
    "source_id": "kangxi_radicals",
    "name": "康熙 214 部登記表（部號來自 Unihan kRSUnicode，部首字來自 kangxi-dictionary）",
    "url": "https://github.com/unicode-org/unihan-database",
    "kind": "dataset",
    "license": "Unicode-3.0+MIT",
    "redistributable": True,
    "retrieved_at": "2026-10-25T00:00:00Z",
    "version": "derived from Unihan 18.0.0 + kangxi-dictionary main",
    "attribution": "Unicode Han Database contributors; kangxi-dictionary contributors",
    "notes": "部號取自 Unihan 的 kRSUnicode（Unicode-3.0），部首字取自 kangxi-dictionary（MIT）。"
             "兩套標準（康熙筆畫 / Unihan 檢字殘筆數）不混用：殘筆數另存於 radical_lookup 陳述。",
}

#: 康熙部行的說明（**兩處一致**：遷移腳本與 builder 都要用同一句）。
_KANGXI_ROW_NOTE = ("康熙 214 部之一。部号来自 Unihan kRSUnicode（Unicode-3.0），"
                    "部首字来自 kangxi-dictionary（MIT）。")


# --------------------------------------------------------------------------
# 康熙部首字 → 部號
# --------------------------------------------------------------------------

def char_to_kangxi_no(chars: Iterable[dict]) -> dict[str, int]:
    """康熙部首字 → 部号（交叉投票）。**瘦身前**从字头表推导。

    *输入*：含 `head` / `work_id` / `radical` / `radical_kangxi` 的字头行。
    *输出*：部首字 → 部号。实测 214/214。

    投票而不是硬映射，是因为《康熙》的部首字寫法與 Unihan 的部號
    分屬兩個體系，只能靠「部內的每個字都記自己屬哪部」這個規律推出來。
    """
    uni_no: dict[str, int] = {}
    kx_rad: dict[str, str] = {}
    for c in chars:
        h = c.get("head") or ""
        if not h:
            continue
        if c.get("work_id") == "unihan":
            rk = c.get("radical_kangxi")
            if rk:
                base = rk.rstrip("'")
                if base.isdigit():
                    uni_no.setdefault(h, int(base))
        elif c.get("work_id") == "kangxi" and c.get("radical"):
            kx_rad.setdefault(h, c["radical"])

    votes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for h, rad_char in kx_rad.items():
        no = uni_no.get(h)
        if no is not None:
            votes[rad_char][no] += 1

    # 众数取胜；票数相同取部号小者——确定性，不依赖字典顺序。
    out: dict[str, int] = {}
    for rad_char, cnt in votes.items():
        if cnt:
            out[rad_char] = min(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return out


def kangxi_no_to_char(char_to_no: dict[str, int]) -> dict[int, str]:
    """部号 → 部首字（`char_to_kangxi_no` 的逆）。"""
    return {no: ch for ch, no in char_to_no.items()}


def char_to_no_from_radicals(radical_rows: Iterable[dict]) -> dict[str, int]:
    """康熙部首字 → 部号（**从 `radicals.jsonl` 反查**）。

    这是**瘦身后仍然可用**的那条路径：`radicals.jsonl` 的康熙部行
    是「部号 + 部首字」的完整登记，键唯一（校验器 R14 强制），
    所以不需要字头表就能还原映射。

    *输入*：`radicals.jsonl` 的行。
    *输出*：部首字 → 部号（214 条）。
    """
    out: dict[str, int] = {}
    for r in radical_rows:
        if r.get("system") != "kangxi":
            continue
        no = str(r.get("radical") or "")
        ch = r.get("char") or ""
        if no.isdigit() and ch:
            out[ch] = int(no)
    return out


# --------------------------------------------------------------------------
# 字頭索引
# --------------------------------------------------------------------------

def real_char_id_by_head(chars: Iterable[dict]) -> dict[str, str]:
    """字頭字 → 真实字头 id（**排除 `radical_map` 的伪字头**）。

    伪字头的 head 形如 `kx1_sw𧮫`（合成标签），
    只有 206 个的 head 恰好是真实汉字——而那 206 个也不能用：
    它们本身就是该被清掉的伪字头。
    """
    out: dict[str, str] = {}
    for c in chars:
        if c.get("work_id") == "radical_map":
            continue
        h = c.get("head") or ""
        if h and h not in out:
            out[h] = c["char_id"]
    return out


def sw_char_id_by_head(chars: Iterable[dict]) -> dict[str, str]:
    """《說文》字頭字 → 字頭 id（說文的字頭 id 無法從 head 反推，只能查表）。"""
    out: dict[str, str] = {}
    for c in chars:
        if c.get("work_id") == "shuowen" and c.get("head"):
            out.setdefault(c["head"], c["char_id"])
    return out


def shuowen_radical_of(chars: Iterable[dict]) -> dict[str, str]:
    """說文字頭 → 說文部名（**瘦身前**从字头表 `radical` 列直接取）。"""
    out: dict[str, str] = {}
    for c in chars:
        if c.get("work_id") == "shuowen" and c.get("radical"):
            out.setdefault(c["head"], c["radical"])
    return out


def char_radical_pairs(chars: Iterable[dict]) -> list[tuple[str, str, str]]:
    """(字頭 id, work_id, 歸屬值) 列表——部首系的推导輸入。

    * 康熙字頭 → 歸屬值是**部首字**（「木」部裡的每個字都記 `radical=「木」」），
      部号由 `char_to_no` 查得。
    * 說文字頭 → 歸屬值**就是部名**。

    瘦身后字頭表沒有 `radical` 列了，改由 `radical` 邊反推（見
    `pairs_from_edges`），兩條路徑的結果必須一致——校驗器會對賬。
    """
    out: list[tuple[str, str, str]] = []
    for c in chars:
        rad = c.get("radical")
        if not rad:
            continue
        w = c.get("work_id")
        if w in ("kangxi", "shuowen"):
            out.append((c["char_id"], w, rad))
    return out


def pairs_from_edges(edges: Iterable[dict]) -> list[tuple[str, str, str]]:
    """(字頭 id, work_id, 歸屬值) 列表——**从 `radical` 邊反推**（瘦身后的路徑）。

    `radical:kangxi:<N>` 的目標裡是**部号**，要還原成部首字得查部首表；
    這裡直接把**部号**當歸屬值傳下去，因為 `build_radical_edges`
    對康熙部接受部號與部首字兩種寫法（見 `_kangxi_no_of`）。
    """
    out: list[tuple[str, str, str]] = []
    for e in edges:
        if e.get("type") != "radical":
            continue
        frm, to = e.get("from") or "", e.get("to") or ""
        if not frm.startswith("char:") or not to.startswith("radical:"):
            continue
        parts = to.split(":", 2)
        if len(parts) != 3:
            continue
        out.append((frm[5:], parts[1], parts[2]))
    return out
def sw_rad_of_head_from_edges(pairs: Iterable[tuple[str, str, str]],
                              head_by_char_id: dict[str, str]) -> dict[str, str]:
    """說文字頭字 → 說文部名（**从 `radical` 邊反推**，瘦身后的路徑）。

    瘦身后字頭表沒有 `radical` 列了，但「說文某字屬哪部」**就是**那條邊
    ——邊是派生件卻是唯一載體（它的證據是字頭行的 `source_ids`，
    見校驗器 R15 的說明）。所以反推得回來。

    ⚠ 只有**《說文》work** 的字頭才映射；康熙字頭的部首字是另一個維度。
    """
    out: dict[str, str] = {}
    for cid, system, value in pairs:
        if system != "shuowen":
            continue
        head = head_by_char_id.get(cid)
        if head and head not in out:
            out[head] = value
    return out

# --------------------------------------------------------------------------
# 歸屬（部首字歸屬）
# --------------------------------------------------------------------------

def informative_radical_owner(char_to_no: dict[str, int],
                              sw_rad_of_head: dict[str, str],
                              shuowen_radicals: set[str]) -> list[dict]:
    """`radical_owner` 边：**只建有信息量的那几条**。

    *判据*：康熙部首字 R 在《說文》里归的部 S，
    若 S == R（同义反复，與 `same_name` 重複）→ 不建；
    若《說文》没收 R 这个字 → **没有出处**，不建。

    实测：198 同义反复 / 7 有信息量 / 9 未收。

    `sw_rad_of_head`：說文**字頭字** → 說文部名。
    返回 `[{kangxi_no, kangxi_char, shuowen_radical}, ...]`，按部号排序。
    """
    out: list[dict] = []
    for rad_char, no in sorted(char_to_no.items(), key=lambda kv: (kv[1], kv[0])):
        s = sw_rad_of_head.get(rad_char)
        if not s:
            continue                      # 《說文》没收此字 → 无出处
        if s == rad_char and rad_char in shuowen_radicals:
            continue                      # 同义反复
        if s not in shuowen_radicals:
            continue                      # 目标不在部表里 → R10 会报错，不建
        out.append({"kangxi_no": no, "kangxi_char": rad_char,
                    "shuowen_radical": s})
    return out


# --------------------------------------------------------------------------
# 產出：部首表 / 部首系边 / 檢字法陳述
# --------------------------------------------------------------------------

def build_radical_rows(existing_rows: Iterable[dict],
                        char_to_no: dict[str, int],
                        strokes: dict[str, int] | None = None) -> list[dict]:
    """重建 `data/radicals.jsonl`。

    *说文部*：原样保留（**不删数据**——删要出处）。
    *康熙部*：**每个部号一行**，键=部号，带部首字；
      旧的 `system=kangxi`（Unihan 原始值 `"120'"` 之类）与
      `system=kangxi_dict`（部首字为键、**没有任何边指向它**）两个命名空间**取消**。

    `strokes`：部首字 → 康熙笔画（来自 kangxi-dictionary）。
    **拿不到就填 0 并在 note 写明**——不拿 Unihan 的残笔数冒充：
    那是两套标准，混用就会让同一字在不同页面显示不同画数。
    """
    out: list[dict] = []
    for r in existing_rows:
        if r.get("system") == "shuowen":
            out.append(dict(r))

    no_to_char = kangxi_no_to_char(char_to_no)
    for no in sorted(no_to_char):
        kchar = no_to_char[no]
        st = (strokes or {}).get(kchar, 0)
        note = _KANGXI_ROW_NOTE
        if not st:
            note += "部首字笔画待重取上游后补（本轮无上游缓存，故 strokes 暂为 0）。"
        out.append({
            "system": "kangxi",
            "radical": str(no),
            "index": no,
            "char": kchar,
            "strokes": st,
            "source_id": KANGXI_RADICALS_SOURCE["source_id"],
            "license": KANGXI_RADICALS_SOURCE["license"],
            "evidence_url": KANGXI_RADICALS_SOURCE["url"],
            "note": note,
        })
    return out


def _kangxi_no_of(value: str, char_to_no: dict[str, int]) -> int | None:
    """康熙歸屬值 → 部号。

    接受两种写法：**部首字**（「木」）或**部号**（「43」）。
    瘦身后只能从 `radical` 边反推（目标是部号），瘦身前从字头表取（是部首字）。
    两条路径都要能跑，所以这里两种都认。
    """
    if value.isdigit():
        return int(value)
    return char_to_no.get(value)


def build_radical_edges(char_radical: list[tuple[str, str, str]],
                        radical_map_stmts: list[dict],
                        char_to_no: dict[str, int],
                        shuowen_radicals: set[str],
                        sw_rad_of_head: dict[str, str],
                        sw_char_ids: dict[str, str],
                        shuowen_gloss_stmt: dict[str, str]) -> list[dict]:
    """重建部首系的三族边。

    * `char_radical`：`(字頭 id, work_id, 歸屬值)` 列表（见 `char_radical_pairs`）。
    * `radical_map_stmts`：`radical_map` 陳述（部間對照的**唯一**證據來源）。
    * `char_to_no`：康熙部首字 → 部号（也接受「部号直接給」的寫法）。
    * `shuowen_radicals`：部表裡 `system=shuowen` 的部名集合（邊目標必須在表內）。
    * `sw_rad_of_head`：說文字頭字 → 說文部名（`radical_owner` 用）。
    * `sw_char_ids`：說文字頭字 → 字頭 id。
    * `shuowen_gloss_stmt`：字頭 id → 《說文》原文陳述 id（`radical_owner` 的出處）。
      **没有对应陈述的部首字不出边**——「無出處，不入庫」（硬规则 1）。

    返回 `[{type, from, to, kind, statement_ids}, ...]`，**按 (type, from, to) 排序**
    ——排序确定 = 同输入必得同输出（硬规则 11）。

    * `radical`：康熙字頭 → `radical:kangxi:<N>`；說文字頭 → `radical:shuowen:<部名>`。
    * `radical_contains`：康熙部 ⇄ 說文部，**雙向都建**
      （舊實現只在「一個康熙部含多個說文部」時建反向邊，留下 20 條單向）。
    * `radical_owner`：康熙部首字這個字 → 它在《說文》里属的部。
    """
    edges: list[tuple[str, str, str, str, list[str]]] = []

    # ---- radical：字頭 → 部 ----
    for cid, work, value in char_radical:
        if work == "kangxi":
            no = _kangxi_no_of(value, char_to_no)
            if no is None:
                continue
            edges.append(("radical", f"char:{cid}",
                          f"radical:kangxi:{no}", "kangxi", []))
        elif work == "shuowen":
            if value not in shuowen_radicals:
                continue
            edges.append(("radical", f"char:{cid}",
                          f"radical:shuowen:{value}", "shuowen", []))

    # ---- radical_contains：康熙部 ⇄ 說文部 ----
    # 對照記錄在 `radical_map` 陳述的 locator 裡（basis=containment）。
    for st in radical_map_stmts:
        loc = st.get("locator") or {}
        if loc.get("basis") != "containment":
            continue
        no = loc.get("kangxi_no")
        sw = loc.get("shuowen_radical")
        if no is None or not sw:
            continue
        no = int(no) if str(no).isdigit() else no
        if sw not in shuowen_radicals:
            continue                  # 目標不在部表裡 → R10 會報錯，不建
        kx_ref = f"radical:kangxi:{no}"
        sw_ref = f"radical:shuowen:{sw}"
        sid = st["statement_id"]
        edges.append(("radical_contains", kx_ref, sw_ref, "containment", [sid]))
        edges.append(("radical_contains", sw_ref, kx_ref, "contained_in", [sid]))

    # ---- radical_owner：康熙部首字這個字在《說文》里属哪部 ----
    for item in informative_radical_owner(char_to_no, sw_rad_of_head, shuowen_radicals):
        no = item["kangxi_no"]
        sw = item["shuowen_radical"]
        if sw not in shuowen_radicals:
            continue                  # 同上
        # 出處：該康熙部首字在《說文》里的**原文陳述**。
        cid = sw_char_ids.get(item["kangxi_char"])
        sid = shuowen_gloss_stmt.get(cid) if cid else None
        if not sid:
            continue                      # 無出處 → 不建邊
        edges.append(("radical_owner", f"radical:kangxi:{no}",
                      f"radical:shuowen:{sw}", "shuowen_owner", [sid]))

    # 去重（同向同類合併 statement_ids）——對齊 builder 的 dedupe_relations。
    merged: dict[tuple[str, str, str, str], dict] = {}
    order: list[tuple[str, str, str, str]] = []
    for rtype, frm, to, kind, sids in edges:
        key = (rtype, frm, to, kind)
        rec = merged.get(key)
        if rec is None:
            merged[key] = {"type": rtype, "from": frm, "to": to, "kind": kind,
                           "statement_ids": list(sids)}
            order.append(key)
        else:
            for s in sids:
                if s not in rec["statement_ids"]:
                    rec["statement_ids"].append(s)

    out = []
    for key in sorted(order):
        rec = merged[key]
        rec["statement_ids"] = sorted(rec["statement_ids"])
        out.append(rec)
    return out


def build_radical_lookup_rows(chars: Iterable[dict]) -> list[dict]:
    """Unihan 的 kRSUnicode → `radical_lookup` 陳述（**檢字法**維度）。

    康熙 214 部是「**檢字法**」（便於查字），與說文的「從某字」的**造字法**歸屬
    是**兩回事**——所以不能塞進 `radical` 邊，只能是獨立的一條陳述。

    陳述掛在 Unihan 的字頭上，edition = `unihan_krsunicode`，
    `locator` 帶 `radical`（部號，含撇號記號）/ `residual`（殘筆數）/ `simplified`。

    ⚠ **需要字頭表有 `radical_kangxi` 三列**（瘦身前）。
    """
    out: list[dict] = []
    for c in chars:
        if c.get("work_id") != "unihan":
            continue
        rno = c.get("radical_kangxi")
        if not rno:
            continue
        residual = c.get("residual_strokes")
        simplified = bool(c.get("radical_kangxi_simplified"))
        no = rno.rstrip("'")
        text = f"康熙檢字：第 {no} 部"
        if residual not in (None, ""):
            text += f"，殘筆 {residual}"
        if simplified:
            text += "（本字為簡化字，部首屬其繁體本字）"
        out.append({
            "source_id": "unihan",
            "kind": "radical_lookup",
            "char_id": c["char_id"],
            "work_id": "unihan",
            "edition_id": "unihan_krsunicode",
            "text": text,
            "reading": "",
            "radical": "",
            "parent_statement_id": None,
            "locator": {"radical": rno, "residual": residual, "simplified": simplified},
            "evidence_url": "https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip",
            "license": "Unicode-3.0",
            "confidence": "machine",
            "summary_method": None,
            "reviewer": None,
            "review_date": None,
        })
    out.sort(key=lambda r: r["char_id"])
    return out
