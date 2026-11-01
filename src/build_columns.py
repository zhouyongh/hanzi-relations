"""生成 `data/columns.jsonl`（列登记表）。

## 这张表是什么

「一个字一行，来源是列」——**列**是消费层的基本单位。
一行 `data/columns.jsonl` 就是**一列**，也就是一个 `edition_id`：

```json
{"column_id":"shuowen_duan_yucai","role":"core",
 "provides":["commentary","citation"],"aggregation":"aggregate",
 "note":"段玉裁注。parent_statement_id 回指所注的說文原文陳述。"}
```

## 为什么它是事实源，不是派生数据

`role` / `provides` / `aggregation` 是**设计裁定**——「哪些书进第一批」
「这一列能提供什么语义」「这一列能不能参与聚合」都不是能从数据里算出来的，
只能由人裁定。所以它进版本库、当事实源。

但**骨架**（有哪些 `edition_id`）来自 `data/editions.jsonl`，
而 `editions.jsonl` 是构建产物（生成脚本未随 v1 发布）——所以本脚本的输入是它。
两处事实源会漂移，所以这里只声明**设计**，不重复存 `work_id` / `axis` / `license`
（那些一律 join `editions.jsonl` 与 `sources.jsonl`）。

## 为什么要留成脚本，而不是手写 jsonl

裁定表有 101 行手写 jsonl 的话，改一条 role 要在几百行里找位置，
而且「哪些 edition 还没裁定」只能靠肉眼。
留在脚本里可以：
1. `--dry` 立刻看出**有没有漏裁的 edition**；
2. 裁定理由（`note`）与裁定结果写在同一处，改一处不漏另一处；
3. 同输入必得同输出（AGENTS.md 硬规则 11）。

## 用法

```bash
python src/build_columns.py --dry    # 只报覆盖情况，不写盘
python src/build_columns.py          # 生成 data/columns.jsonl
```

`--dry` 在**有 edition 未裁定**或**裁定表里有多余 edition** 时退出码为 1。
校验器 R11 也查同一件事（`src/verify_relations.py`）——
这里是「生成前提醒」，那里是「入库前把关」。
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jsonl_io import read_jsonl, write_jsonl  # noqa: E402
from units import (BASE_COLUMNS, COLUMN_BUNDLES, COLUMN_TIERS,  # noqa: E402
                   bundle_of, tier_of)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data")
EDS = os.path.join(DATA, "editions.jsonl")
OUT = os.path.join(DATA, "columns.jsonl")

# ---- 枚举 ----
CORE, OPT, SUP = "core", "optional", "support"
AGG, REACH = "aggregate", "reachability"

# 裁定表：edition_id -> (role, provides, aggregation, note)
R: dict[str, tuple[str, list[str], str, str]] = {}


def put(ids, role: str, provides: list[str], policy: str, note: str = "") -> None:
    for i in ids:
        R[i] = (role, provides, policy, note)


# ------------------------------------------------------------------
# 《說文》系：主軸列（第一批的核心）
# ------------------------------------------------------------------
put(["shuowen_xu_shen"], CORE,
    ["gloss", "reading", "radical", "variant", "citation"], AGG,
    "許慎原文（大徐本正文）。本庫主軸：說文系各列的字頭即主軸行。"
    "（含重文/古文的 variant_note 與引文 citation，故 provides 須含 variant/citation。）")
put(["shuowen_xu_xuan"], CORE, ["note", "reading"], AGG,
    "徐鉉校語。**provides 含 reading**：反切已陈述化（B7），"
    "反切本身就是这一列的一条 kind=phonology 陈述。")
put(["shuowen_xu_kai"], CORE, ["note", "reading"], AGG,
    "徐鍇《說文解字系傳》。provides 含 reading（B7）。")
put(["shuowen_duan_yucai"], CORE, ["commentary", "citation", "reading"], AGG,
    "段玉裁注。parent_statement_id 回指所注的說文原文陳述。")
put(["shuowen_da_xu"], CORE, ["gloss", "reading"], AGG,
    "大徐本刻本正文。provides 含 reading（B7：反切独立成陈述）。")
put(["shuowen_duan_ben"], CORE, ["commentary"], AGG,
    "段注本刻本正文。**role 仍 core，但界面默认档是 extended**"
    "（`units.DEFAULT_HIDDEN`）：与段注正文只重叠 3%，却 43.0% 的字头有一条 >120 字连排"
    "（段注 24.1%），按「分得清」这条判据它是**对照本**，不是默认本。")
put(["shuowen_xu_kai_ben"], CORE, ["gloss", "note", "reading"], AGG,
    "小徐本系傳刻本正文。provides 含 reading（B7）。")
# ⚠ 《說文》四刻本（陳昌治/段玉裁/祁寯藻/藤花榭）**2026-10-31 整列删除**：
# 四列各 1 万余条陈述、100% 是 kind=serial，正文只有 `C-00001` 这种流水号，
# 一条释文都没有；而这些号在字形层 `hanzi-glyphs-svg/glyphs/seal/external.csv`
# （11,328 行，四本并列，带逐件 blob sha1）已经齐了。关系库再存一份 =
# 同一事实两处表示（硬规则 27/28）。字形指针按码位，删列不断指针。
# 判定与证据见一次性的列整理记录；该次迁移把 101 列收到 97 列。
# （迁移脚本是历史一次性脚本，未随 v1 发布。）
put(["leipian_cites_shuowen"], CORE, ["citation", "alignment", "reading"], AGG,
    "《類篇》引《說文》對照層。")

# ------------------------------------------------------------------
# 《康熙字典》與韻書/字書
# ------------------------------------------------------------------
put(["kangxi_dict"], CORE, ["gloss", "citation"], AGG,
    "《康熙字典》說解 + 條目內嵌引文。")
put(["guangyun"], CORE, ["rhyme", "reading", "variant"], AGG,
    "《廣韻》韻類 + 反切（含「正字〈異體〉」拆出的異體說明）。")
put(["xiaoxue_" + n for n in [
    "九经字样", "五经文字", "佩觿", "六艺纲目", "切韵指掌图", "别雅", "骈雅", "埤雅",
    "尔雅注", "尔雅注疏", "尔雅翼", "尔雅", "班马字类", "字鉴", "字诂", "字防",
    "急就篇", "洪武正韵", "古今韵会举要", "孙氏唐韵考", "唐韵正", "广雅", "续方言",
    "释名", "輶轩使者绝代语释别国方言", "原本广韵", "匡谬正俗",
    "龙龛手鉴", "集韵", "重修玉篇", "重修广韵", "类篇",
]], CORE, ["gloss"], AGG,
    "傳統小學書正文。字頭掛在《說文》字頭上。")
# ⚠ `xiaoxue_方言`（524 条）**2026-10-31 删除**：与 `xiaoxue_輶轩使者…方言`（1,127 条）
# 是同一部书的另一次切分（共同字头 364/384，正文零条相同只是切法不同）——留全的那一列。
# 同批留下的还有 `xiaoxue_原本广韵`：它 57.2% 正文与重修**不同**，是《廣韻》两个刻本的真
# 版本差异，删了是丢版本对照，不是去重。判定见 `列整理 - 保留清单.md` §12.3。

# ------------------------------------------------------------------
# 蒙學十二種（識字教學底本）→ 第一批
# ------------------------------------------------------------------
put(["classic_" + n for n in [
    "sanzijing", "baijiaxing", "qianziwen", "dizigui", "zhuzijiaxun", "shenglvqimeng",
    "youxueqionglin", "wenzimengqiu", "zengguangxianwen", "qianjiashi",
    "tangshisanbaishou", "guwenguanzhi",
]], CORE, ["occurrence"], AGG, "蒙學識字底本（《古文觀止》《唐詩三百首》同屬此批）。")

# ------------------------------------------------------------------
# 六經 + 詩文集 + 大詩集 → 可選列（用戶勾選要查的列才帶出來）
# ------------------------------------------------------------------
put(["classic_" + n for n in ["shijing", "lunyu", "mengzi", "daxue", "zhongyong", "chuci"]],
    OPT, ["occurrence"], AGG, "六經用例。role=optional：查詢時用戶勾選才帶出。")
put(["classic_" + n for n in ["wudai", "caocao", "youmengying", "shuimotangshi", "nalan",
                             "songci", "yuanqu", "quantshi"]],
    OPT, ["occurrence"], AGG,
    "詩文集與大詩集（聚合層）。")
# ⚠ `classic_yudingquantshi`（7,347 条）**2026-10-31 删除**：正文是
# 「《御定全唐詩》中出現 1628 次」——那是**词频统计**，不是用例；同一语料的诗在
# `classic_quantshi`（12,848 条）。它却占两倍边（asserts + citation 各 7,347）。

# ------------------------------------------------------------------
# 工具書屬性（支撐列，不參與「引用」）
# ------------------------------------------------------------------
put(["unihan_k" + n for n in [
    "mandarin", "hanyupinyin", "cantonese", "fanqie", "japaneseon", "japanesekun",
    "xhc1983", "korean", "tang", "hanyupinlu",
]], SUP, ["reading"], AGG, "Unihan 讀音欄位。")
put(["unihan_" + n for n in [
    "traditional", "simplified", "semantic",
    "specialized_semantic", "z", "japanese_old", "japanese_new",
]], SUP, ["variant"], REACH,
    "Unihan 異體欄位。aggregation=reachability：**只作檢索候選，不可聚合**。")
# ⚠ `unihan_simplification`（7,169 条）**2026-10-31 删除**：由 `kSimplifiedVariant` +
# `kTraditionalVariant` 派生，正文就是那个字本身，`locator` 里存着 field/simplified/traditional
# ——同一事实的第二处表示；它的 4,351 条 `simplification` 边与 `unihan_traditional` 的边
# **逐对重合**（实测 4,351/4,351），删掉后那些关系仍以 `unihan_traditional` 的 kind 存在。
put(["unihan_krsunicode"], SUP, ["lookup"], AGG,
    "Unihan kRSUnicode（康熙 214 部**檢字法**）。部號 + 殘筆數 + 簡化撇號記號"
    "逐字成一條 `radical_lookup` 陳述（`locator` 帶 radical / residual / simplified）。"
    "與《說文》的**造字法**歸屬是兩個維度——後者走 `radical` 邊。")
put(["unihan_wip_definition"], SUP, ["gloss"], AGG, "Unicode 暫定欄位 kDefinition。")
put(["unihan_wip_phonetic"], SUP, ["reading"], AGG, "Unicode 暫定欄位 kPhonetic。")
put(["unihan_wip_meyerwempe"], SUP, ["reading"], AGG, "Unicode 暫定欄位 kMeyerWempe。")
put(["unihan_wip_alt_radicals"], SUP, ["radical"], AGG, "Unicode 暫定欄位 AlternateRadicals。")
put(["cjk_decomp_structure"], SUP, ["structure"], AGG, "cjk-decomp 構形分解（IDS）。")
put(["opencc_s2t"], SUP, ["variant"], REACH, "OpenCC 簡→繁單字對照。")
put(["radical_map"], SUP, ["radical"], AGG, "說文 540 部 ↔ 康熙 214 部對照。")
put(["hanzi_frequency_rank"], SUP, ["frequency"], AGG, "Jun Da 現代漢字使用頻率排名。")


def main() -> int:
    global R
    eds = read_jsonl(EDS)
    if not eds:
        print(f"!! 讀不到 {EDS}——editions.jsonl 是構建產物（生成腳本未隨 v1 發布），缺失說明 data/ 不完整")
        return 1

    edition_ids = {e.get("edition_id") for e in eds}
    # v1 只发 base 四列：裁定表里其余列属于将来的扩展包，
    # 不在本仓库的登记表里出现。
    R = {k: v for k, v in R.items() if k in edition_ids}
    missing = sorted(edition_ids - set(R))
    extra = sorted(set(R) - edition_ids)

    print(f"editions={len(eds)}  已裁定={len(R)}")
    if missing:
        print(f"!! 未裁定的 edition（{len(missing)}）——它们的内容挂不到任何一列：")
        for m in missing:
            print("   ", m)
    if extra:
        print(f"!! 裁定表里多出的 edition（{len(extra)}）——editions.jsonl 里已不存在：")
        for m in extra:
            print("   ", m)

    if "--dry" in sys.argv:
        print("(dry run：未写盘)")
        return 1 if (missing or extra) else 0

    # 输出顺序 = editions.jsonl 的顺序（该文件按 edition_id 排序写出，稳定）
    #
    # `tier` / `bundle` **不写进裁定表**：它们是纯函数（`units.tier_of` /
    # `units.bundle_of`），由 column_id 唯一决定。写进裁定表就成了第二份
    # 事实源——101 行里漏改一行就漂移，而漂移的声明等于没有声明（D40 的教训）。
    # 裁定表仍然只写「真决策」：role / provides / aggregation / note。
    rows = []
    for e in eds:
        role, prov, pol, note = R[e["edition_id"]]
        cid = e["edition_id"]
        rec = {
            "column_id": cid,
            "role": role,
            "provides": prov,
            "aggregation": pol,
            "tier": tier_of(cid),
            "bundle": bundle_of(cid),
        }
        if note:
            rec["note"] = note
        rows.append(rec)

    write_jsonl(OUT, rows)
    print(f"\nwritten: {OUT}  ({len(rows)} rows)")
    print("role:", dict(Counter(r["role"] for r in rows)))
    print("tier:", dict(Counter(r["tier"] for r in rows)))
    print("bundle:", dict(Counter(r["bundle"] for r in rows)))
    base = [r["column_id"] for r in rows if r["tier"] == "base"]
    print("base 层（%d）: %s" % (len(base), " / ".join(base)))
    if len(base) != len(BASE_COLUMNS) or set(base) != set(BASE_COLUMNS):
        print("!! base 层与 units.BASE_COLUMNS 不一致——界面默认视图会错")
        return 1
    n_bundles = len({r["bundle"] for r in rows})
    if n_bundles > 8:
        print(f"!! bundle 数 {n_bundles} > 8：SQLite 默认最多 10 个 ATTACH，"
              f"留不出余量（取值域 {COLUMN_BUNDLES}）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
