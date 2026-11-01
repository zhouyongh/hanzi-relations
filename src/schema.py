"""契约的**单一事实源**：封闭枚举 + 记录字段规格 + schema 版本（设计裁定 D44）。

## 为什么要有这个文件

契约之前散在三处，彼此独立：

1. `AGENTS.md` 的枚举表——人读的，但**改代码的人不一定看**；
2. `src/units.py` / `src/readings.py` / `src/structures.py` 各自的层内枚举；
3. `src/verify_relations.py` 里**另抄**的 `REL_TYPES` / `CONFIDENCE` /
   `ALLOWED_LICENSES`——校验器自己的一份。

第 3 处就是「第二份事实源」的实例：校验器认 17 种 `relation.type`，
builder 却按字面量硬编码——**builder 能产出校验器不认的类型**，
而这件事只有等校验器报警才知道。

更贵的代价在上一轮已经付过一次：`verify_relations.py` 曾有一份自己的
`STMT_KINDS`，与 `units.KIND_DIMENSION` 漂移过一次，
R13 因此变成「橡皮图章」——校验器拿自己抄的枚举去对账自己的数据。

## 本模块的定位

**它是聚合点，不是新的副本**：

- 层内枚举仍归各层模块所有（`units` / `readings` / `structures`），
  本模块 `import` 它们并重新导出——**每个枚举仍然只有一份定义**；
- 没有归属层的跨层枚举（`REL_TYPES` / `VARIANT_KINDS` / `CONFIDENCE` /
  `ALLOWED_LICENSES` …）**归本模块所有**，从 `verify_relations.py` 搬过来；
- `RECORD_SCHEMAS` 是各类 JSONL 记录的字段规格，
  `src/build_schemas.py` 由此**生成** `schemas/*.schema.json`——
  **schema 文件是产物，不是事实源**（与 `data/columns.jsonl` 同理）。

## 版本

| 版本 | 含义 | 落在哪 |
|---|---|---|
| `SCHEMA_VERSION` | JSONL 记录**结构**版本（改字段就 +1） | `MANIFEST.json` / `build.json` |
| `SQLITE_SCHEMA_VERSION` | SQLite **物理结构**版本 | `PRAGMA user_version` |

两者分开：JSONL 加一个可选字段不必重建 SQLite 的表结构，
但 SQLlite 的表结构变了就必须 `user_version` +1，
好让「手里的库是不是这份数据构建的」可判定。
"""
from __future__ import annotations

import units
import readings
import structures

# ---------------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------------

#: JSONL 记录结构的版本。**改字段就 +1**（加可选字段也算——
#: 下游按版本判断「这份数据有没有某字段」）。
#:
#: **v2**（2026-10-29）：附加 `stable_id`（内容寻址）——见 D45。
SCHEMA_VERSION = 2

#: SQLite 物理结构的版本 → `PRAGMA user_version`。
#:
#: **v2**（2026-10-29）：`statements` 表加 `stable_id` 列 + 唯一索引
#: `idx_stmt_stable`，并加视图 `v_statement_stable`——见 D45。
#: **v3**（2026-10-30）：边端點整数化（`refs` + `relation_edges` + 兼容视图
#: `relations`）；`unit_coverage` 物化表移除，改为瘦身的 `unit_columns`——见 D50。
SQLITE_SCHEMA_VERSION = 3

# ---------------------------------------------------------------------------
# 封闭枚举（**唯一副本**）
# ---------------------------------------------------------------------------

#: 关系 `type` 的封闭枚举（17 种）。
#:
#: 从 `verify_relations.py` 搬到这里——**校验器与 builder 共用这一份**。
RELATION_TYPES = (
    "asserts",         # 字头持有某条陈述（char → statement）
    "commentary_on",   # 某陈述是对另一陈述的注（statement → statement）
    "variant_of",      # 异体 / 重文 / 古今字 / 简繁（char → char）
    "glyph",           # 字 ↔ 字形指针
    "radical",         # 字 ↔ 部（归属的唯一载体）
    "radical_owner",   # 康熙部首字在《說文》里属哪一部（部 → 部）
    "radical_contains",  # 康熙一部包含《說文》哪几部（1:N）
    "phonology",       # 字 ↔ 音 / 韵类（赋值边 + 比对边）
    "structure",       # 字 ↔ 构形分解
    "citation",        # 字在条目内的他书引文
    "cited_in",        # 某字出现在某句里
    "cites_work",      # 某陈述引用了另一部书
    "edition_of",      # 陈述 ↔ 某注家形态
    "quotes",          # 这条引文引用了那条原文陈述
    "edition_variant",  # 同一字在不同刻本的释文差异
    "component",       # 字 → 构件（说文「从X」的义符/形符）
    "phonetic_component",  # 字 → 声符（说文「X声」）
)

#: `variant_of` 边的 `kind` 子类（**封闭枚举**，10 种）。
#:
#: ⚠ **AGENTS.md 原来只列了 8 种，漏了 `unihan_traditional` 与
#: `unihan_simplified`**（各 6,924 条，来自 Unihan 的
#: `kTraditionalVariant` / `kSimplifiedVariant`）。
#: 那两条与 `simplification`（OpenCC 口径的简繁）语义不同：
#: 前者是 Unihan 自己标的「繁体字 / 简体字变体」，后者是通用简繁。
#: 混在一起下游就分不清「这条简繁关系是谁说的」。
VARIANT_KINDS = (
    "simplification",             # 通用简繁（OpenCC STCharacters）
    "unihan_semantic",            # Unihan kSemanticVariant
    "unihan_specialized_semantic",  # Unihan kSpecializedSemanticVariant
    "unihan_traditional",         # Unihan kTraditionalVariant
    "unihan_simplified",          # Unihan kSimplifiedVariant
    "unihan_z",                   # Unihan kZVariant
    "unihan_japanese_old",        # Unihan kJapaneseOldVariant
    "unihan_japanese_new",        # Unihan kJapaneseNewVariant
    "shuowen_variant",            # 《說文》条下的古文 / 重文
    "guangyun_variant",           # 《廣韻》正字〈異體〉
)

#: 各类边的 `kind` 子类（**封闭枚举**）。
#:
#: 没有出现在这张表里的 `type`，**不允许带 `kind`**——
#: 「边带 kind」是例外，不是常态（否则 kind 就失去了分类意义）。
#:
#: ⚠ `edition_variant` 的 kind 是**描述性短语**（「同（注疏展開）」/
#: 「異文」/「略異」…），不是分类码。它仍然闭集：值多一个就要有人决定。
RELATION_KINDS: dict[str, tuple[str, ...]] = {
    "radical": ("kangxi", "shuowen"),
    "radical_contains": ("containment", "contained_in"),
    "radical_owner": ("shuowen_owner",),
    "variant_of": VARIANT_KINDS,
    "phonology": (
        "fanqie_same", "fanqie_same_initial", "fanqie_same_rime",
        "fanqie_diff", "fanqie_homophone", "fanqie_unknown",
        "cut_initial", "cut_rime",
    ),
    "quotes": ("quote", "leipian_cites_shuowen", "xiaoxue_citation"),
    "cites_work": ("shuowen_citation", "leipian_citation"),
    "component": ("semantic",),
    "phonetic_component": ("phonetic",),
    "edition_variant": (
        "同（注疏展開）", "同（段注展開）", "異文", "略異", "段注糾正",
    ),
}

#: 陈述 `kind` → 语义维度（**单值映射**，唯一副本在 `units.KIND_DIMENSION`）。
STATEMENT_KINDS: tuple[str, ...] = tuple(units.KIND_DIMENSION)

#: 语义维度（`provides` 的取值域）。唯一副本在 `units.PROVIDES`。
PROVIDES_VALUES: tuple[str, ...] = tuple(units.PROVIDES)

#: 列的角色。唯一副本在 `units.COLUMN_ROLES`。
COLUMN_ROLES: tuple[str, ...] = tuple(units.COLUMN_ROLES)

#: 列的挂载策略（二值）。唯一副本在 `units.COLUMN_AGGREGATIONS`。
COLUMN_AGGREGATIONS: tuple[str, ...] = tuple(units.COLUMN_AGGREGATIONS)

#: 列的层级（base / standard / extended）。唯一副本在 `units.COLUMN_TIERS`。
COLUMN_TIERS: tuple[str, ...] = tuple(units.COLUMN_TIERS)

#: 列的物理分库（bundle）。唯一副本在 `units.COLUMN_BUNDLES`。
COLUMN_BUNDLES: tuple[str, ...] = tuple(units.COLUMN_BUNDLES)

#: 读音登记项的 `value_kind`。唯一副本在 `readings.VALUE_KINDS`。
VALUE_KINDS: tuple[str, ...] = tuple(readings.VALUE_KINDS)

#: 构形登记项的 `value_kind`。唯一副本在 `structures.STRUCTURE_KINDS`。
STRUCTURE_KINDS: tuple[str, ...] = tuple(structures.STRUCTURE_KINDS)

#: 注家 / 刻本两个轴（**不可混称**）。
EDITION_AXES = ("annotator", "edition")

#: 部首系统。
RADICAL_SYSTEMS = ("shuowen", "kangxi")

#: 韵类 / 韵目系统。
RHYME_SYSTEMS = ("guangyun_xiao", "guangyun_rhyme")

#: 陈述的置信度。
CONFIDENCE = ("machine", "reviewed", "curated")

#: 自撰摘要的方法（`redistributable=false` 的来源必填；硬规则 2）。
SUMMARY_METHODS = ("paraphrase", "translation", "analysis")

#: 许可白名单：只允许这些标识进库。
#:
#: **零 copyleft**：share-alike（CC-BY-SA）、弱 copyleft（EPL / MPL）、GPL / LGPL
#: 一律不收；CC-BY-ND（禁止改作）与 CC-BY-NC（非商业限制）同样排除。
#:
#: **Public-Domain** 是特例：用于「公有领域古籍的忠实录入」——
#: 上游没有 LICENSE 文件（license=null），但内容本身是成书于 1929 年前的公版作品，
#: 录入不产生新版权。**必须逐书核对来源**，确认不是从维基文库（CC BY-SA）导入的；
#: 核对结论写在 `data/sources.jsonl` 的 `notes` 里（校验器 L5 强制）。
ALLOWED_LICENSES = (
    "Apache-2.0", "MIT", "CC0-1.0", "Unicode-3.0",
    "CC-BY-4.0", "ODC-By-1.0", "Public-Domain",
)

#: 指针型来源允许的许可（内容不入库，只存指针）。
POINTER_ONLY_LICENSES = ("NOASSERTION", "CC-BY-NC-SA-3.0", "CC-BY-ND-3.0")


# ---------------------------------------------------------------------------
# 记录字段规格（→ `schemas/*.schema.json`，由 build_schemas.py 生成）
# ---------------------------------------------------------------------------

def _str(desc: str = "") -> dict:
    d: dict = {"type": "string"}
    if desc:
        d["description"] = desc
    return d


def _nullable_str(desc: str = "") -> dict:
    d: dict = {"type": ["string", "null"]}
    if desc:
        d["description"] = desc
    return d


def _arr(item: dict, desc: str = "") -> dict:
    d: dict = {"type": "array", "items": item}
    if desc:
        d["description"] = desc
    return d


def _enum(values, desc: str = "") -> dict:
    d: dict = {"type": "string", "enum": list(values)}
    if desc:
        d["description"] = desc
    return d


_CHAR_ID = {"type": "string", "pattern": r"^C\d{6,7}$",
            "description": "字头 id（身份，按码位归并前的行 id）"}
_STMT_ID = {"type": "string", "pattern": r"^S\d{6,7}$", "description": "陈述 id（事实单元）"}
_REL_ID = {"type": "string", "pattern": r"^R\d{6,7}$", "description": "边 id"}

#: 各类 JSONL 记录的 JSON Schema（draft 2020-12 子集）。
#:
#: **这是产物的事实源**：`schemas/*.schema.json` 由 `src/build_schemas.py`
#: 从这里生成，改契约改这里、跑生成器，不手改 schema 文件。
RECORD_SCHEMAS: dict[str, dict] = {
    # ---------------- 字头 ----------------
    "characters": {
        "title": "字头（characters）",
        "description": "每个来源各造一批字头行；「一个字一行、来源是列」的心智模型。",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "char_id", "head", "work_id", "canonical_id", "search_group",
            "source_ids", "modern_codepoints", "pinyin", "volume", "upstream_id",
        ],
        "properties": {
            "char_id": _CHAR_ID,
            "head": _str("字头（繁体原字）"),
            "work_id": _str("来源 id（字头来自哪部书）"),
            "canonical_id": _str("**身份**键：跨来源按码位归并（不可用于聚合异体）"),
            "search_group": _str("**可达性**键：异体/简繁连通分量（只可检索候选）"),
            "source_ids": _arr({"type": "string"}, "该字头出现过的来源 id 列表"),
            "modern_codepoints": _arr({"type": "string", "pattern": r"^[0-9A-F]{4,6}$"},
                                       "对应的现代码位（十六进制）"),
            "pinyin": _str("拼音（可能为空串）"),
            "mpinyin": _str("多音拼音（可选，仅部分来源有）"),
            "volume": _str("卷次"),
            "upstream_id": {"type": ["string", "integer", "null"],
                            "description": "上游自己的条目号（可空）"},
        },
    },
    # ---------------- 陈述 ----------------
    "statements": {
        "title": "陈述（statements）",
        "description": "事实单元。许可、证据、出处都在陈述上；边不带许可。",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "statement_id", "stable_id", "kind", "char_id", "work_id", "edition_id",
            "text", "reading", "radical", "parent_statement_id", "locator",
            "evidence_url", "license", "source_id", "confidence",
            "summary_method", "reviewer", "review_date",
        ],
        "properties": {
            "statement_id": _STMT_ID,
            "stable_id": {"type": "string",
                          "pattern": r"^st_[0-9a-f]{24}(-\d+)?$",
                          "description": "**内容地址**：重建后不变，可被外部引用"
                                         "（见 D45）"},
            "kind": _enum(STATEMENT_KINDS, "陈述的语义类别（封闭枚举）"),
            "char_id": _str("挂在哪个字头上"),
            "work_id": _str("作品 id"),
            "edition_id": _str("版本形态 id（列 = 一个 edition_id）"),
            "text": _str("正文"),
            "reading": _str("读音 / 反切（可能为空串）"),
            "radical": _str("部首（可能为空串）"),
            "parent_statement_id": _nullable_str("所注的原文陈述（段注回指说文）"),
            "locator": {"type": "object", "description": "上游定位信息（自由结构）"},
            "evidence_url": _str("出处 URL——**无出处不入库**（硬规则 1）"),
            "license": _str("本条陈述的许可（逐条记，不搞整库一个许可）"),
            "source_id": _str("来源 id → `data/sources.jsonl`"),
            "confidence": _enum(CONFIDENCE),
            "summary_method": _nullable_str("自撰摘要的方法（指针型来源必填）"),
            "reviewer": _nullable_str("复核人"),
            "review_date": _nullable_str("复核日期"),
        },
    },
    # ---------------- 边 ----------------
    "relations": {
        "title": "边（relations）",
        "description": "边只连实体、不带许可。许可在陈述上。",
        "type": "object",
        "additionalProperties": False,
        "required": ["relation_id", "type", "from", "to"],
        "properties": {
            "relation_id": _REL_ID,
            "type": _enum(RELATION_TYPES, "关系类型（封闭枚举）"),
            "from": _str("起点，形如 `char:C000001` / `statement:S000001` / `radical:kangxi:1`"),
            "to": _str("终点，形如 `statement:S000001` / `char:C000001` / `reading:<edition>:<slug>`"),
            "kind": _str("边的子类（取值受 `RELATION_KINDS` 约束）"),
            "statement_ids": _arr({"type": "string"},
                                  "支撑这条边的陈述 id 列表（`variant_of` 等必填）"),
        },
    },
    # ---------------- 列登记表 ----------------
    "columns": {
        "title": "列登记表（columns）",
        "description": "**产物**：由 `src/build_columns.py` 的裁定表生成。"
                       "只存设计声明；work_id/axis/license 由 editions/sources join。",
        "type": "object",
        "additionalProperties": False,
        "required": ["column_id", "role", "provides", "aggregation", "tier", "bundle", "note"],
        "properties": {
            "column_id": _str("列 id（== edition_id：一列 = 一个 edition_id）"),
            "role": _enum(COLUMN_ROLES, "core / optional / support"),
            "provides": _arr({"type": "string", "enum": list(PROVIDES_VALUES)},
                             "该列提供的语义维度"),
            "aggregation": _enum(COLUMN_AGGREGATIONS, "aggregate / reachability（二值策略）"),
            "tier": _enum(COLUMN_TIERS,
                          "base（默认 4 列）/ standard（主干对照）/ extended（按需勾选）"),
            "bundle": _enum(COLUMN_BUNDLES,
                            "物理分库名：base / shuowen / xiaoxue / classics / unihan / misc"),
            "note": _str("说明"),
        },
    },
    # ---------------- 读音登记 ----------------
    "readings": {
        "title": "读音登记项（readings）",
        "description": "读音值域登记表。**不含 license / source_id**——由 edition_id join。",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "reading_id", "edition_id", "value", "slug", "value_kind",
            "normalized", "tone", "locations", "derived_from", "transform",
        ],
        "properties": {
            "reading_id": {"type": "string", "pattern": r"^reading:[^:]+:.+$"},
            "edition_id": _str("版本形态 id → editions → sources（许可 join 路径）"),
            "value": _str("原始值"),
            "slug": _str("值域内的 slug（目标里用的那段）"),
            "value_kind": _enum(VALUE_KINDS, "值的语义类别（封闭枚举）"),
            "normalized": _str("归一化值——**跨 edition 同音连接的键**"),
            "tone": {"type": ["integer", "null"],
                     "description": "声调；**判不出是 null 不是 0**（韵类/反切/编码恒 null）"},
            "locations": _arr({"type": "string"}, "定位码列表"),
            "derived_from": _str("派生来源"),
            "transform": _str("加工方式"),
        },
    },
    # ---------------- 构形登记 ----------------
    "structures": {
        "title": "构形登记项（structures）",
        "description": "构形值域登记表。**不含 license / source_id**——由 namespace(=source_id) join。",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "structure_id", "namespace", "key", "value", "value_kind",
            "derived_from", "transform",
        ],
        "properties": {
            "structure_id": {"type": "string", "pattern": r"^structure:[^:]+:.+$"},
            "namespace": _str("命名空间（= 来源 id，key 是上游自己的 key）"),
            "key": _str("上游的 key"),
            "value": _str("构形值（如 `d(白,干)`）"),
            "value_kind": _enum(STRUCTURE_KINDS, "值的语义类别（封闭枚举）"),
            "derived_from": _str("派生来源"),
            "transform": _str("加工方式"),
        },
    },
    # ---------------- 来源 ----------------
    "sources": {
        "title": "来源（sources）",
        "description": "**许可闸门的唯一事实源**：一源一条。",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "source_id", "name", "url", "kind", "license", "redistributable",
            "attribution", "notes", "retrieved_at", "version",
        ],
        "properties": {
            "source_id": _str("来源 id"),
            "name": _str("来源名"),
            "url": _str("上游地址"),
            "kind": _str("来源类型"),
            "license": _str("许可标识（逐源记）"),
            "redistributable": {"type": "boolean",
                                "description": "原文可否再分发；false 时只存指针 + 自撰摘要"},
            "attribution": _str("署名要求"),
            "notes": _str("备注（Public-Domain 必须写明逐书核对的方式与结果）"),
            "retrieved_at": _str("取回时间"),
            "version": _str("上游版本 / commit sha（**不写分支名**）"),
        },
    },
    # ---------------- 注家形态 ----------------
    "editions": {
        "title": "注家形态（editions）",
        "description": "axis 区分 annotator（注家）与 edition（刻本），**不可混称**。",
        "type": "object",
        "additionalProperties": False,
        "required": ["edition_id", "work_id", "axis", "name", "note"],
        "properties": {
            "edition_id": _str("版本形态 id（列 id）"),
            "work_id": _str("作品 id"),
            "axis": _enum(EDITION_AXES),
            "name": _str("名称"),
            "note": _str("说明"),
        },
    },
    # ---------------- 部首 ----------------
    "radicals": {
        "title": "部首（radicals）",
        "description": "说文 606 部 + 康熙 214 部。**归属走边，检字法走陈述**。",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "system", "radical", "index", "char", "license",
            "source_id", "evidence_url",
        ],
        "properties": {
            "system": _enum(RADICAL_SYSTEMS),
            "radical": _str("部名"),
            "index": {"type": "integer", "description": "部号"},
            "char": _str("部首字"),
            "strokes": {"type": "integer", "description": "部首字笔画数（康熙部有）"},
            "license": _str("许可"),
            "source_id": _str("来源 id"),
            "evidence_url": _str("出处 URL"),
            "note": _str("说明"),
        },
    },
    # ---------------- 韵类 ----------------
    "rhyme_groups": {
        "title": "韵类 / 韵目（rhyme_groups）",
        "description": "《廣韻》小韵 3,884 + 韵目 206。",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "system", "group_id", "rhyme", "rhyme_class", "fanqie", "xiao_index",
            "char_count", "license", "source_id", "evidence_url",
        ],
        "properties": {
            "system": _enum(RHYME_SYSTEMS),
            "group_id": _str("组 id"),
            "rhyme": _str("韵目"),
            "rhyme_class": _str("小韵类"),
            "fanqie": _str("反切"),
            "xiao_index": _str("小韵号"),
            "xiao_count": {"type": "integer", "description": "该小韵的字数"},
            "char_count": {"type": "integer", "description": "该韵目的字数"},
            "tone_classes": _str("声调类别"),
            "license": _str("许可"),
            "source_id": _str("来源 id"),
            "evidence_url": _str("出处 URL"),
        },
    },
    # ---------------- 异体来源标签 ----------------
    "source_tags": {
        "title": "异体来源标签（source_tags）",
        "description": "Unihan 异体来源标签 → 工具书对照。",
        "type": "object",
        "additionalProperties": False,
        "required": ["tag", "tool", "upstream_tags", "count", "note"],
        "properties": {
            "tag": _str("本库的标签"),
            "tool": _str("对应的工具书"),
            "upstream_tags": _arr({"type": "string"}, "上游的原始标签"),
            "count": {"type": "integer", "description": "条数"},
            "note": _str("说明"),
        },
    },
}

#: 记录类型 → 数据文件（用于 `check_schema.py` 与 manifest）。
#: 值为「相对 `data/` 的路径」；分片目录按 `part-*.jsonl` 展开。
RECORD_FILES: dict[str, tuple[str, ...]] = {
    "characters": ("characters/",),
    "statements": ("provenance/*/",),
    "relations": ("relations/",),
    "columns": ("columns.jsonl",),
    "readings": ("readings.jsonl",),
    "structures": ("structures.jsonl",),
    "sources": ("sources.jsonl",),
    "editions": ("editions.jsonl",),
    "radicals": ("radicals.jsonl",),
    "rhyme_groups": ("rhyme_groups.jsonl",),
    "source_tags": ("source_tags.jsonl",),
}
