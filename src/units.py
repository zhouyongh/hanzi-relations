"""字单元（unit）与列挂载（binding）层的构建逻辑。

## 为什么要有这一层

本库的**事实层**（`data/characters/` + `data/provenance/` + `data/relations/`）是
「每个来源各造一批字头行」：《說文》11,102 行、《康熙》46,815 行……
同一个「月」在不同来源里各占一行。语义上它们是同一个字，物理上是多行。

用户要的心智模型是「**一个字一行，来源是列**」。这层就是把两者接起来：

    字单元（unit） = 跨来源按 `canonical_id` 归并的**身份**
    列（column）   = 一个 `edition_id`（某部书某个形态）—— 登记在 `data/columns.jsonl`
    挂载（binding）= 某一列的某条内容，挂在本单元的**哪一个字头行**上

三层的关系：

    unit ──< unit_members >── char                （一个单元由若干来源的字头行组成）
    unit ──< unit_bindings >── column + char      （一列挂到单元的哪个字头上）
    column ──> edition ──> work                  （列的语义与许可）

## 为什么锚点是「字单元」而不是「《說文》字頭」

实测：以《說文》字頭为唯一锚点时，
《康熙》46,815 个字頭只有 **28.1%** 能落上来，Unihan 只有 **17.1%**——
锚点若锁死在《說文》，三分之二的康熙内容无处可挂。

《說文》系因此是**主轴**（每个单元优先以說文字頭为代表），而不是唯一锚点。
这与漢典的实践一致：漢典一个字的页面上，说文/段注是「详细解释」下的**两节**，
锚点是那个现代字。

## 与 `search_group` 的分工（设计裁定 D20）

- `canonical_id`（**身份**）：本层用来归并单元，用于**聚合**。
- `search_group`（**可达性**）：含异体/简繁边，**只用于检索候选扩展**，
  绝不用于本层的归并——否则「說/説/说」「協/恊/叶/葉」会被混成一锅。

`method` 字段记录每条挂载的实际方式：
- `self`        —— 单元的代表字頭本身就是这条内容所在的那一行（说文系各列）
- `canonical`   —— 按 `canonical_id` 归并过来的（康熙、韵书、六经、Unihan …）
- `reachability`—— 该列只用于检索候选，**不可用于聚合**（异体类列）
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# ---------------------------------------------------------------------------
# 列登记表
# ---------------------------------------------------------------------------

COLUMN_ROLES = ("core", "optional", "support")

#: 挂载策略——**二值**。
#:
#: 这是列登记表里**唯一真正由人裁定、且真的会改变行为**的字段：
#: - `aggregate`    —— 这一列的内容**可以**参与「聚合说解」
#: - `reachability` —— 只作**检索候选**，不可聚合（异体类列）
#:
#: 旧版的 `anchor_policy`（self / canonical / reachability）把三件事混在一起：
#: 「挂法是 self 还是 canonical」是**数算出来的**（`build_bindings` 按
#: `unit_id == char_id` 判定），不是声明；把它写成声明，就必然漂移——
#: 实测 101 列里有 **79 列**的声明与实际不符（10,270 行挂成 canonical、
#: 239,030 行挂成 self）。漂移的声明等于没有声明。
#:
#: 所以：**策略收成二位（真决策），观察值另存为派生字段 `anchor_observed`**
#: （由 `unit_bindings` 反算，见 build_sqlite.py 的 `v_unit_columns`）。
COLUMN_AGGREGATIONS = ("aggregate", "reachability")

#: 列的**层级**——决定「一个字的默认视图里有几列」。
#:
#: 这是「广度有余、精度不足」的直接修正：库里有 101 列，但读者打开一个字时
#: 一次看到 68 列 96 行，等于什么都没说。默认只给 **4 列**（`base`），
#: 其余按需展开——「勾上才出现」，而不是一次全塞。
#:
#: - `base`     —— 默认列，**恰好 4 个**（见 `BASE_COLUMNS`），每列都必须过硬校验
#: - `standard` —— 主干对照：說文系其余、廣韻、主要字書。一键展开
#: - `extended` —— 其余：蒙學、詩文、Unihan 欄位、工具書屬性。逐列勾选
COLUMN_TIERS = ("base", "standard", "extended")

#: `base` 层的列——**顺序即界面顺序**。
#:
#: 为什么是这四个：一个字先要「本义」（許慎原文）、「注」（段注）、
#: 「最早的字书训释」（《爾雅》）、「后出的总汇」（《康熙》）。
#: 大徐本**不在 base**：它与許慎原文是同一部书的另一个转录本（实测 73% 逐字相同），
#: 并排显示就是复制；作为独立一列以后按需加入（`standard`）。
#: 这不是从数据里算出来的，是按「打开一个字最想先看到什么」裁定的裁定；
#: 改动要同时改 `src/build_columns.py` 的裁定表、本元组、R26 与文档。
BASE_COLUMNS = (
    "shuowen_xu_shen",     # 許慎《說文解字》原文
    "shuowen_duan_yucai",  # 段玉裁注
    "xiaoxue_尔雅",        # 《爾雅》
    "kangxi_dict",         # 《康熙字典》
)

#: 列的**物理分库**（bundle）。
#:
#: 「加一列就多一个库文件，勾上再合并查」——一个 bundle 就是一个 `.db` 文件，
#: 里面只放这一组列的陈述与相关的边；骨架（字头 / 字单元 / 列登记）每个库都带一份。
#:
#: ⚠ 数量上限 8：SQLite 默认 `SQLITE_MAX_ATTACHED = 10`，留两个余量给临时库
#: 与用户自己的库。校验器 **R26** 强制 bundle 数 ≤ 8。
#: v1 只有 `base` 里有列；其余名字留给扩展包（默认不建空库）。
COLUMN_BUNDLES = ("base", "shuowen", "xiaoxue", "classics", "unihan", "misc")

#: 除 `base` 外，`standard` 层的列（其余一律 `extended`）。
STANDARD_COLUMNS = frozenset({
    # 說文系其余版本与对照层
    "shuowen_xu_xuan", "shuowen_xu_kai", "shuowen_duan_ben",
    "shuowen_xu_kai_ben", "leipian_cites_shuowen",
    # 韵书 / 主要字书（主干对照的第二梯队）
    "guangyun", "xiaoxue_集韵", "xiaoxue_重修玉篇", "xiaoxue_类篇",
    "xiaoxue_重修广韵", "xiaoxue_洪武正韵", "xiaoxue_古今韵会举要",
})


def tier_of(column_id: str) -> str:
    """列 → 层级。**纯函数**（builder / 校验器 / 浏览页共用）。"""
    if column_id in BASE_COLUMNS:
        return "base"
    if column_id in STANDARD_COLUMNS:
        return "standard"
    return "extended"


def bundle_of(column_id: str) -> str:
    """列 → 物理分库名。**纯函数**（builder / 校验器 / 查询层共用）。

    分库按「一列跟着它所属的那批书走」：
    `base` 恰好等于 base 层的四列（所以 `core.db` = 骨架 + 这四列）；
    說文系其余 / 小學書 / 蒙學与詩文 / Unihan / 工具書屬性 各一个库。
    """
    if column_id in BASE_COLUMNS:
        return "base"
    if column_id.startswith("shuowen") or column_id.startswith("leipian"):
        return "shuowen"
    if column_id.startswith("xiaoxue_"):
        return "xiaoxue"
    if column_id.startswith("classic_"):
        return "classics"
    if column_id.startswith("unihan"):
        return "unihan"
    return "misc"

#: 各语义维度（`provides` 的取值域）。封闭枚举，新增需改本文件与枚举。
PROVIDES = (
    "gloss",       # 释义
    "commentary",  # 注家评注
    "note",        # 校语
    "citation",    # 条目内嵌的他书引文
    "occurrence",  # 文献用例（字↔句）
    "reading",     # 读音
    "rhyme",       # 韵类归属
    "variant",     # 异体关系
    "structure",   # 构形分解
    "radical",     # 部首
    "lookup",      # 檢字法（康熙 214 部：部號 + 殘筆數）——**不是**造字法歸屬
    "frequency",   # 字频
    "serial",      # 刻本流水号
    "alignment",   # 引文对照 / 版本差异
)

#: 陈述 `kind` → 语义维度（`provides` 的取值域）。**单值映射**。
#:
#: 为什么是单值：旧版是「provides → 允许的 kind 集合」的多值映射，
#: 结果**跨语义维度的别名**全被放行（`structure→gloss`、`serial→gloss`、
#: `radical→gloss|note`）——85,238 条 IDS 构形分解与 43,777 条刻本流水号
#: 都被标成 `kind=gloss`，与真释义混排，下游拼说解会拼出「…·C-04915」。
#: 改成「kind → 唯一维度」后，一条 IDS 只能是 `structure`，
#: 一条流水号只能是 `serial`，**跨维度不可能再蒙混过关**。
#:
#: 封闭枚举，新增需改本文件与枚举。
KIND_DIMENSION: dict[str, str] = {
    "gloss":           "gloss",       # 释义
    "note":            "note",        # 校语
    "commentary":      "commentary",  # 注家评注
    "citation":        "citation",    # 条目内嵌的他书引文
    "variant_note":    "variant",     # 异体说明
    "phonology":       "reading",     # 读音 / 韵类 / 反切
    "frequency":       "frequency",   # 字频
    "radical_lookup":  "lookup",      # 康熙 214 部检字法（部号 + 残笔数）
    "structure":       "structure",   # 构形分解（IDS）
    "serial":          "serial",      # 刻本流水号
    "radical_compare": "radical",     # 部间对照（康熙部 ↔ 说文部）
    "radical_alt":     "radical",     # 替代部首（异体部首检字）
}

#: 维度 → **同义的 provides 写法**（真同义，不是跨语义别名）。
#:
#: 只有「同一维度的两种叫法」才允许在这里登记：
#: - `reading` 维度：韵类归属（`rhyme`）也是音韵
#: - `citation` 维度：文献用例（`occurrence`）、引文对照（`alignment`）
PROVIDES_SYNONYMS: dict[str, set[str]] = {
    "reading":  {"rhyme"},
    "citation": {"occurrence", "alignment"},
}

#: 代表字頭的優先序：**說文系是主軸**。
PRIMARY_WORK_ORDER = (
    "shuowen", "kangxi", "guangyun", "xiaoxue", "classics",
    "unihan", "unihan_wip", "cjk_decomp", "hanzi_frequency",
    "opencc", "radical_map",
)


def load_column_specs(path: str | None = None) -> list[dict]:
    """读 `data/columns.jsonl`（列登记表）。

    登记表只存**设计声明**——`role` / `provides` / `aggregation` / `note`。
    `work_id` / `axis` / `license` / `name` 一律从 `editions.jsonl` 与
    `sources.jsonl` join 出来，不重复存——**两处事实源会漂移**（设计裁定 D4 的教训）。
    """
    path = path or os.path.join(DATA, "columns.jsonl")
    out: list[dict] = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def validate_column_specs(specs: list[dict]) -> list[str]:
    """列登记表的机检。返回问题列表（空 = 没问题）。"""
    problems: list[str] = []
    seen: set[str] = set()
    for s in specs:
        cid = s.get("column_id")
        if not cid:
            problems.append("列缺 column_id")
            continue
        if cid in seen:
            problems.append(f"列 {cid} 重复登记")
        seen.add(cid)
        if s.get("role") not in COLUMN_ROLES:
            problems.append(f"列 {cid} 的 role 非法：{s.get('role')!r}（取值域 {COLUMN_ROLES}）")
        if s.get("aggregation") not in COLUMN_AGGREGATIONS:
            problems.append(f"列 {cid} 的 aggregation 非法：{s.get('aggregation')!r}"
                            f"（取值域 {COLUMN_AGGREGATIONS}）")
        tier = s.get("tier")
        if tier not in COLUMN_TIERS:
            problems.append(f"列 {cid} 的 tier 非法：{tier!r}（取值域 {COLUMN_TIERS}）")
        elif (tier == "base") != (cid in BASE_COLUMNS):
            problems.append(
                f"列 {cid} 的 tier={tier} 与 BASE_COLUMNS 不符——"
                f"base 层必须恰好是 {BASE_COLUMNS}")
        if s.get("bundle") not in COLUMN_BUNDLES:
            problems.append(f"列 {cid} 的 bundle 非法：{s.get('bundle')!r}"
                            f"（取值域 {COLUMN_BUNDLES}）")
        elif s.get("bundle") != bundle_of(cid):
            problems.append(
                f"列 {cid} 的 bundle={s.get('bundle')!r} 与分库规则不符"
                f"（应为 {bundle_of(cid)!r}）")
        prov = s.get("provides") or []
        if not prov:
            problems.append(f"列 {cid} 没有 provides")
        for p in prov:
            if p not in PROVIDES:
                problems.append(f"列 {cid} 的 provides 取值非法：{p!r}")
    return problems


def check_column_kinds(specs: list[dict],
                       kinds_by_column: dict[str, set[str]]) -> list[str]:
    """R13：列声明的 `provides` 必须覆盖该列**实际出现**的陈述 kind。

    *判据*：每条陈述的 `kind` 经 `KIND_DIMENSION` 映射到**唯一语义维度**，
    该维度必须在这一列的 `provides` 里（或落在 `PROVIDES_SYNONYMS` 的同义写法里）。

    *为什么不用「provides → 允许多个 kind」的映射*：多值映射会放进
    **跨语义维度的别名**（`structure→gloss`、`serial→gloss`），
    于是「85,238 条 IDS 写成释义」「43,777 条流水号写成释义」都被判为合法——
    校验器成了橡皮图章。单值映射让这类错误**无法通过**。

    *为什么不做反向检查*（"provides 里的每个维度都必须在该列出现"）：
    一列可以声明「将来会提供」的维度（说文列的 `radical` / `reading` 由
    `radical` 边与 `readings` 表承载，不在陈述里），那是**字头行级**的能力。
    """
    problems: list[str] = []
    spec_by_id = {s["column_id"]: s for s in specs}
    for cid, actual in sorted(kinds_by_column.items()):
        spec = spec_by_id.get(cid)
        if spec is None:
            continue   # 未登记的列由 R12 报
        prov = set(spec.get("provides") or [])
        for k in sorted(actual):
            dim = KIND_DIMENSION.get(k)
            if dim is None:
                problems.append(f"列 {cid} 出现未登记的 kind {k!r}（KIND_DIMENSION 里没有）")
                continue
            if dim in prov:
                continue
            if prov & PROVIDES_SYNONYMS.get(dim, set()):
                continue
            problems.append(
                f"列 {cid} 实际出现 kind {k!r}（{dim} 维度），"
                f"但 provides={sorted(prov)} 不覆盖该维度")
    return problems


# ---------------------------------------------------------------------------
# 字单元
# ---------------------------------------------------------------------------

def build_units(chars: list[dict]) -> tuple[list[dict], list[dict]]:
    """由字头表归并出字单元与成员表。

    返回 `(units, members)`：
    - `units`：`unit_id`（= 该单元的 `canonical_id`）、代表字頭、码位并集、成员来源
    - `members`：`(unit_id, char_id, work_id, head, is_primary)`

    *确定性*：成员按 `char_id` 排序；代表字頭按 `PRIMARY_WORK_ORDER` 取先命中者，
    同序再按 `char_id` 取小者——同输入必得同输出（设计裁定 D8）。
    """
    by_unit: dict[str, list[dict]] = {}
    for c in chars:
        uid = c.get("canonical_id") or c["char_id"]
        by_unit.setdefault(uid, []).append(c)

    units: list[dict] = []
    members: list[dict] = []
    for uid in sorted(by_unit):
        group = sorted(by_unit[uid], key=lambda r: r["char_id"])
        rank = {w: i for i, w in enumerate(PRIMARY_WORK_ORDER)}
        primary = min(group, key=lambda r: (rank.get(r.get("work_id"), 99), r["char_id"]))
        cps: list[str] = []
        works: set[str] = set()
        for c in group:
            for cp in (c.get("modern_codepoints") or []):
                if cp not in cps:
                    cps.append(cp)
            works.add(c.get("work_id") or "")
        units.append({
            "unit_id": uid,
            "head": primary.get("head"),
            "primary_char_id": primary["char_id"],
            "primary_work": primary.get("work_id"),
            "codepoints": cps,
            "n_members": len(group),
            "works": sorted(works),
            "has_shuowen": any(c.get("work_id") == "shuowen" for c in group),
        })
        for c in group:
            members.append({
                "unit_id": uid,
                "char_id": c["char_id"],
                "work_id": c.get("work_id"),
                "head": c.get("head"),
                "is_primary": 1 if c["char_id"] == primary["char_id"] else 0,
            })
    return units, members


# ---------------------------------------------------------------------------
# 挂载
# ---------------------------------------------------------------------------

def build_bindings(chars: list[dict], stmts: list[dict],
                   specs: list[dict]) -> tuple[list[dict], list[dict]]:
    """由陈述推出「列 → 单元 → 字頭」的挂载表与覆盖矩阵。

    返回 `(bindings, coverage)`：
    - `bindings`：`(unit_id, column_id, char_id, method, n_statements, kinds)`
    - `coverage`：`(unit_id, column_id, n_statements, kinds, source_ids)` —— 覆盖矩阵

    `column_id` **就是 `edition_id`**（一列 = 一部书的某个形态），
    所以挂载完全由陈述的 `(char_id, edition_id)` 决定，不需要额外的人工标注。
    """
    char_by_id = {c["char_id"]: c for c in chars}
    spec_by_id = {s["column_id"]: s for s in specs}

    agg: dict[tuple[str, str, str], dict] = {}
    for s in stmts:
        cid = s.get("char_id")
        col = s.get("edition_id")
        c = char_by_id.get(cid)
        if c is None or not col:
            continue
        uid = c.get("canonical_id") or cid
        key = (uid, col, cid)
        rec = agg.get(key)
        if rec is None:
            rec = agg[key] = {
                "unit_id": uid, "column_id": col, "char_id": cid,
                "method": "self", "n_statements": 0,
                "kinds": set(), "source_ids": set(),
            }
        rec["n_statements"] += 1
        if s.get("kind"):
            rec["kinds"].add(s["kind"])
        if s.get("source_id"):
            rec["source_ids"].add(s["source_id"])

    bindings: list[dict] = []
    for key in sorted(agg):
        rec = agg[key]
        spec = spec_by_id.get(rec["column_id"], {})
        aggregation = spec.get("aggregation", "aggregate")
        if aggregation == "reachability":
            # 这一列**只作检索候选**——不可参与聚合说解。
            method = "reachability"
        else:
            # 挂法由数据决定（该字头行是不是这个单元的代表行），不是声明。
            method = "self" if rec["unit_id"] == rec["char_id"] else "canonical"
        bindings.append({
            "unit_id": rec["unit_id"],
            "column_id": rec["column_id"],
            "char_id": rec["char_id"],
            "method": method,
            "n_statements": rec["n_statements"],
            "kinds": sorted(rec["kinds"]),
            "source_ids": sorted(rec["source_ids"]),
        })

    cov: dict[tuple[str, str], dict] = {}
    for b in bindings:
        key = (b["unit_id"], b["column_id"])
        rec = cov.get(key)
        if rec is None:
            rec = cov[key] = {
                "unit_id": b["unit_id"], "column_id": b["column_id"],
                "n_statements": 0, "kinds": set(), "source_ids": set(),
                "char_ids": [],
            }
        rec["n_statements"] += b["n_statements"]
        rec["kinds"].update(b["kinds"])
        rec["source_ids"].update(b["source_ids"])
        rec["char_ids"].append(b["char_id"])

    coverage: list[dict] = []
    for key in sorted(cov):
        rec = cov[key]
        coverage.append({
            "unit_id": rec["unit_id"],
            "column_id": rec["column_id"],
            "n_statements": rec["n_statements"],
            "kinds": sorted(rec["kinds"]),
            "source_ids": sorted(rec["source_ids"]),
            "char_ids": sorted(rec["char_ids"]),
        })
    return bindings, coverage
