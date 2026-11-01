"""已知缺口登记表：把「数据里已知的缺陷」**登记**下来，并**每次重测**——设计裁定 D46。

## 为什么要有这个

D41.7 曾列过一张「已知缺口」表——但那是**散文**，
机器查不了：缺口变大了没人知道，缺口被悄悄修掉了也没人知道。

本模块照搬**迁移台账**（D44.5）的做法：
- 缺口登记在 `data/known_gaps.jsonl`（一缺口一条）；
- 每条带一个**可复跑的探针**，探针**对着 `data/` 重测**；
- 重测结果与登记**不一致就报错**（变大 = 又坏了；变小 = 修好了，该更新登记）。

**登记 != 掩盖**：缺口在每次校验的输出里都明写出来。

## 为什么不用「直接报错」

缺口是**已知的**（上游有损，或需要另一份数据源才能修）。
直接报错会让 CI 永久红——红到人就习惯了，等于没有。
所以：**登记在册的缺口不报错，但必须每次都出现**；
不在册的同类缺口**立刻报错**。

## 登记一条缺口

```json
{"gap_id": "G001_...", "kind": "placeholder_char_head", "scope": "characters",
 "expected_heads": 1, "expected_statements": 4424,
 "spec": "D46", "recovery": "按 head 重新对齐（一次性脚本，未随 v1 发布）",
 "evidence": "...", "note": "..."}
```

`kind` 决定用哪个探针（封闭枚举，新增要改本文件与探针）。
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jsonl_io import read_jsonl_any  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
GAPS = os.path.join(DATA, "known_gaps.jsonl")

#: 上游用来表示「字头丢失」的占位符。
PLACEHOLDERS = ("□", "▢", "■", "□")

#: 缺口 kind → 探针函数名（封闭枚举；新增要改本文件与探针）。
GAP_KINDS = ("placeholder_char_head", "duplicate_statements",
             "kangxi_split_parent_citations", "xu_kai_missing_fanqie_head",
             "xu_kai_split_parent_notes", "duan_ben_merged_entries",
             "kangxi_variant_prose_without_edge")

#: 段注本正文里「下一条条目的字头 + 拼音」：`唐tánɡ` / `棠tánɡ` / `桂ɡuìp240上`。
#: 一条陈述自己的字头**不在正文里**，所以正文里出现这种串就是它把下一条也吞进来了。
_DUAN_HEAD = re.compile(
    r"[\u3400-\u9fff\uf900-\ufaff]"
    r"[a-zA-Zɡ\u0251-\u02ff\u0300-\u036f\u00e0-\u00ff]{2,}(?:p\d+)?[上中下]?")


def load_gaps() -> list[dict]:
    """读缺口登记表。"""
    return read_jsonl_any(GAPS)


def _measure_placeholder_heads() -> tuple[int, int]:
    """数占位符字头与其挂着的**说解类**陈述数。

    ⚠ B7（设计裁定 D53）之后，同一句话可能会多出一条 `kind=phonology` 的
    反切子陈述（反切原先挤在 `reading` 欄里，不单独占一行）。缺口说的是
    「这句话没有可判定的字头」，所以只数**说解**——否则同一个缺口会因为
    「多了一条子陈述」而看起来变大，那是口径漂移，不是数据变坏。
    """
    chars = read_jsonl_any(os.path.join(DATA, "characters"))
    ph = [c for c in chars if (c.get("head") or "") in PLACEHOLDERS]
    ph_ids = {c.get("char_id") for c in ph}
    if not ph_ids:
        return 0, 0
    n = 0
    prov = os.path.join(DATA, "provenance")
    for src in sorted(os.listdir(prov)):
        d = os.path.join(prov, src)
        if not os.path.isdir(d):
            continue
        for st in read_jsonl_any(d):
            if st.get("char_id") in ph_ids and st.get("kind") != "phonology":
                n += 1
    return len(ph), n


def _measure_duplicate_statements() -> tuple[int, int]:
    """数「内容完全相同、只能靠组内序号区分」的陈述。

    **判据 = 去重的那把尺子本身**（设计裁定 D48）：身份字段 + 父的内容地址
    ——即 `src/dedupe.py` 用的同一个模块。

    *为什么不再用「`stable_id` 后缀」当代理*：那是**间接**数法
    （靠「有没有 `-k` 后缀」倒推重复），而探针该量的就是「重复行」本身。
    两者其实等价（`stable_id` 的哈希输入就是这些字段），但直接量更清楚，
    而且**修法与探针用同一把尺子** → 修完不会「探针报不一致、CI 红」。

    实测（去重前）：10,164 组 / 10,196 条靠次序区分；
    **去重后应为 0 / 0**——不为 0 说明重建又产生了重复。
    """
    import dedupe as dedupe_mod  # noqa: PLC0415

    groups = 0
    records = 0
    prov = os.path.join(DATA, "provenance")
    for src in sorted(os.listdir(prov)):
        d = os.path.join(prov, src)
        if not os.path.isdir(d):
            continue
        stmts = read_jsonl_any(d)
        if not stmts:
            continue
        _, st = dedupe_mod.dedupe_statements(stmts)
        groups += st["groups"]
        records += st["dropped"]
    return groups, records


def _measure_kangxi_split_parents() -> tuple[int, int]:
    """数《康熙》里「同字同正文同 locator、但挂在两个**不同**父条目下」的引文。

    这些**不是**重复行：两个父是两条**不同**的康熙条目
    （实测一条多了「俗卻字。（卻）」的前缀）——
    按本库规则「不同 entry 不合并」→ **不强并**，登记在册（宁可少收不可错收）。
    """
    prov = os.path.join(DATA, "provenance")
    d = os.path.join(prov, "kangxi_dict")
    if not os.path.isdir(d):
        return 0, 0
    stmts = read_jsonl_any(d)
    by_id = {s.get("statement_id"): s for s in stmts}

    def jkey(v):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    buckets: dict = {}
    for s in stmts:
        if s.get("kind") != "citation":
            continue
        key = (s.get("char_id"), s.get("edition_id"), s.get("text"),
               jkey(s.get("locator")))
        buckets.setdefault(key, []).append(s)
    groups = 0
    records = 0
    for v in buckets.values():
        if len(v) < 2:
            continue
        parents = {x.get("parent_statement_id") for x in v}
        if len(parents) > 1:
            groups += 1
            records += len(v)
    return groups, records


def _measure_xu_kai_missing_head() -> tuple[int, int]:
    """数《小徐本·系傳》里「反切**缺上字**」的陈述。

    反切固定是「上字 + 下字 + 反」＝ 3 個字。系傳裡有 **49 條**只存了
    「下字 + 反」——**上游缺字**（上游用「□」標註缺字，見正文裡的
    `□\\(找不到该字\\)`、`□\\(不清楚\\)` 之類）。

    *與 D49 的區分*：D49 修的是**解析器多吃 1 個字**（已修）；
    這 49 條是**上游本來就少給**，修不了——只能登記在冊。

    返回 `(條數, 組數)`；組數恆為 1（同一個 edition），這裡只是為了與
    其它探針的 `(a, b)` 形狀一致。
    """
    import re  # noqa: PLC0415
    short_re = re.compile(r"^[一-鿿](反|切)$")
    n = 0
    for st in read_jsonl_any(os.path.join(DATA, "provenance", "daizhige_xiaoxue")):
        if st.get("edition_id") != "shuowen_xu_kai_ben":
            continue
        # B7（设计裁定 D53）：反切已陈述化——值在 kind=phonology 陈述的 text 里，
        # reading 欄恒为空。**判据换成 text**，条数口径不变（仍是那 49 条）。
        if st.get("kind") != "phonology":
            continue
        rd = (st.get("text") or "").strip()
        if rd and short_re.match(rd):
            n += 1
    return n, 1


def _measure_xu_kai_split_parent_notes() -> tuple[int, int]:
    """数《小徐本·系傳》「同正文但**挂在不同父条目**下」的注文。

    与 `G003`（《康熙》引文）**同类**：两条父是两条**不同**的系傳条目，
    按本库规则「不同 entry 不合并」→ **不强并**，登记在册。

    判据 = (char_id, edition_id, text, locator) 相同、但 `parent_statement_id` 不同。
    """
    stmts = read_jsonl_any(os.path.join(DATA, "provenance", "daizhige_xiaoxue"))

    def jkey(v):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    buckets: dict = {}
    for s in stmts:
        if s.get("edition_id") != "shuowen_xu_kai_ben" or s.get("kind") != "note":
            continue
        key = (s.get("char_id"), s.get("edition_id"), s.get("text"),
               jkey(s.get("locator")))
        buckets.setdefault(key, []).append(s)
    groups = 0
    records = 0
    for v in buckets.values():
        if len(v) < 2:
            continue
        parents = {x.get("parent_statement_id") for x in v}
        if len(parents) > 1:
            groups += 1
            records += len(v)
    return groups, records


def _measure_duan_ben_merged_entries() -> tuple[int, int]:
    """数 `shuowen_duan_ben`（段注本·殆知閣）里**一条陈述挤了多个条目**的。

    判据不是字数，而是**正文里出现了下一条的字头 + 拼音**——
    该列的上游是带拼音标注的排印本，每条条目的字头后跟「tánɡ」这样的注音；
    一条陈述自己的字头不在正文里，所以正文里再出现一个就是它把下一条吞了。

    实测「周」（C000974）：一条陈述里同时有「周」与「唐tánɡ」两条，
    于是「唐」的段注内容挂在「周」的字头下——**这不只是排版难看，是内容挂错字头**。
    """
    stmts = read_jsonl_any(os.path.join(DATA, "provenance", "daizhige_xiaoxue"))
    n = 0
    chars = set()
    for s in stmts:
        if s.get("edition_id") != "shuowen_duan_ben":
            continue
        if _DUAN_HEAD.search(s.get("text") or ""):
            n += 1
            chars.add(s.get("char_id"))
    return n, len(chars)


def _measure_kangxi_variant_prose_without_edge() -> tuple[int, int]:
    """数《康熙》正文里写了「古作 X」、但库里没有 `variant_of` 边的情形。

    《康熙》条目常用散文说异体：「【集韻】上，古作丄」「【說文】彝古作𢇏」。
    这些是**关系**，可库里的 `variant_of` 边只来自《說文》自己的古文/重文
    （1,244 条，`kind=shuowen_variant`）——康熙正文里的说法没有一条变成边，
    于是 `彝` 与 `𢇏` 在库里是两个互不相连的身份（用户据此发现）。

    修法不是硬造边（硬造就是无证关系），而是「从正文抽异体」那一步——还没做。
    返回 `(没有边的条数, 正文出现「古作」的总次数)`。
    """
    chars = read_jsonl_any(os.path.join(DATA, "characters"))
    by_head: dict[str, set[str]] = {}
    canon = {}
    for c in chars:
        by_head.setdefault(c.get("head") or "", set()).add(c.get("char_id"))
        canon[c.get("char_id")] = c.get("canonical_id") or c.get("char_id")
    linked: set[tuple] = set()
    rel_dir = os.path.join(DATA, "relations")
    for e in read_jsonl_any(rel_dir):
        if e.get("type") != "variant_of":
            continue
        a = e.get("from", "").partition(":")[2]
        b = e.get("to", "").partition(":")[2]
        ca, cb = canon.get(a, a), canon.get(b, b)
        linked.add((ca, cb))
        linked.add((cb, ca))
    pat = re.compile(r"古作([\u3400-\u9fff])")
    hits = missing = 0
    for s in read_jsonl_any(os.path.join(DATA, "provenance", "kangxi_dict")):
        if s.get("kind") != "gloss":
            continue
        me = canon.get(s.get("char_id"))
        for ch in pat.findall(s.get("text") or ""):
            hits += 1
            tgts = by_head.get(ch)
            if not tgts or not any((me, canon.get(t)) in linked for t in tgts):
                missing += 1
    return missing, hits


def probe(gap: dict) -> tuple[bool, str]:
    """重测一条缺口，返回 `(是否与登记一致, 实测描述)`。"""
    kind = gap.get("kind")
    if kind not in GAP_KINDS:
        return False, f"未知的缺口 kind {kind!r}（取值域 {GAP_KINDS}）"
    if kind == "placeholder_char_head":
        heads, stmts = _measure_placeholder_heads()
        exp_h = gap.get("expected_heads")
        exp_s = gap.get("expected_statements")
        ok = (exp_h is None or heads == exp_h) and (exp_s is None or stmts == exp_s)
        return ok, (f"占位符字头 {heads} 个 / 挂着的陈述 {stmts:,} 条"
                    f"（登记：{exp_h} 个 / {exp_s:,} 条）")
    if kind == "duplicate_statements":
        groups, extra = _measure_duplicate_statements()
        exp_g = gap.get("expected_duplicate_groups")
        exp_e = gap.get("expected_duplicate_records")
        ok = (exp_g is None or groups == exp_g) and (exp_e is None or extra == exp_e)
        return ok, (f"内容完全相同、只能靠次序区分的陈述 {extra:,} 条 / {groups:,} 组"
                    f"（登记：{exp_e:,} 条 / {exp_g:,} 组）")
    if kind == "kangxi_split_parent_citations":
        groups, extra = _measure_kangxi_split_parents()
        exp_g = gap.get("expected_duplicate_groups")
        exp_e = gap.get("expected_duplicate_records")
        ok = (exp_g is None or groups == exp_g) and (exp_e is None or extra == exp_e)
        return ok, (f"《康熙》同正文但挂在不同父条目下的引文 {extra:,} 条 / {groups:,} 组"
                    f"（登记：{exp_e:,} 条 / {exp_g:,} 组）")
    if kind == "xu_kai_missing_fanqie_head":
        n, _ = _measure_xu_kai_missing_head()
        exp = gap.get("expected_statements")
        ok = (exp is None or n == exp)
        return ok, (f"《系傳》反切缺上字的陈述 {n:,} 条"
                    f"（登记：{exp if exp is not None else '—'} 条）")
    if kind == "xu_kai_split_parent_notes":
        groups, extra = _measure_xu_kai_split_parent_notes()
        exp_g = gap.get("expected_duplicate_groups")
        exp_e = gap.get("expected_duplicate_records")
        ok = (exp_g is None or groups == exp_g) and (exp_e is None or extra == exp_e)
        return ok, (f"《系傳》同正文但挂在不同父条目下的注文 {extra:,} 条 / {groups:,} 组"
                    f"（登记：{exp_e:,} 条 / {exp_g:,} 组）")
    if kind == "duan_ben_merged_entries":
        n, heads = _measure_duan_ben_merged_entries()
        exp_n = gap.get("expected_duplicate_records")
        exp_h = gap.get("expected_heads")
        ok = (exp_n is None or n == exp_n) and (exp_h is None or heads == exp_h)
        return ok, (f"《段注本》一条陈述吞了多个条目的 {n:,} 条 / 挂在 {heads:,} 个字头下"
                    f"（登记：{exp_n:,} 条 / {exp_h:,} 个字头）")
    if kind == "kangxi_variant_prose_without_edge":
        missing, hits = _measure_kangxi_variant_prose_without_edge()
        exp_m = gap.get("expected_duplicate_records")
        exp_h = gap.get("expected_statements")
        ok = (exp_m is None or missing == exp_m) and (exp_h is None or hits == exp_h)
        return ok, (f"《康熙》正文「古作 X」共 {hits} 处，其中 {missing} 处没有 `variant_of` 边"
                    f"（登记：{exp_h} 处 / {exp_m} 处无边）")


def check_all() -> list[str]:
    """重测全部登记缺口，返回**不一致**的描述（空列表 = 全部与登记一致）。"""
    problems: list[str] = []
    for gap in load_gaps():
        try:
            ok, detail = probe(gap)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"缺口 {gap.get('gap_id')} 探针异常：{exc}")
            continue
        if not ok:
            gid = gap.get("gap_id")
            problems.append(
                f"缺口 {gid} 与登记不一致：{detail}。"
                f"修好了就删掉这条登记（data/known_gaps.jsonl），变坏了就修数据。"
                f"（设计编号 {gap.get('spec')}；恢复：{gap.get('recovery')}）")
    return problems


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    gaps = load_gaps()
    print("=" * 68)
    print("已知缺口登记表")
    print("=" * 68)
    if not gaps:
        print("（没有登记任何缺口）")
        return 0
    for gap in gaps:
        ok, detail = probe(gap)
        print("  %-34s %-6s %s" % (gap.get("gap_id"),
                                   "一致" if ok else "不一致", detail))
        print("      %s" % (gap.get("note") or ""))
        print("      设计编号 %s ｜ 恢复 %s" % (gap.get("spec"), gap.get("recovery")))
    problems = check_all()
    print()
    if problems:
        for p in problems:
            print("[问题] %s" % p)
        return 1
    print("全部登记缺口与当前数据一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
