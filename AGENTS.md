# AGENTS.md — hanzi-relations（base 版）

汉字**引用关系库**：以字为索引，把各部典籍对同一个字的解释、彼此的版本差异，以及
字与字形 / 部首 / 音韵 / 异体的关系，结构化成**可机读、可追溯**的引用关系。

本仓库只做**关系与引用**。字形文件在 `hanzi-glyphs-svg`，本仓库只存指向它的指针。

## 版本边界（v1）

**v1 只发 base 四列**：`shuowen_xu_shen`（許慎原文）/ `shuowen_duan_yucai`（段注）/
`xiaoxue_尔雅`（爾雅）/ `kangxi_dict`（康熙）。
其余典籍将来以**扩展包**形式加入（一列一登记、装一个库查一列，删掉即消失）。
`src/build_columns.py` 里保留着其余列的裁定，但 v1 的 `data/columns.jsonl` 只有四列。

**v1 一点扩展来源都不含**（这是硬边界，不是「暂时没加」）：

- 没有 Unihan：没有 `work_id=unihan` 的字头行（上一版曾有 36,594 行**零陈述**的空壳，
  只为给康熙行当身份锚点），没有 `unihan` 来源，`source_tags.jsonl` 是**空表**；
- 没有康熙 214 部：那套「字 → 部」来自 Unihan `kRSUnicode`，整批去掉；
  `radicals.jsonl` 只剩《說文》540 部；
- 没有韵书 / 字书 / 诗文集 / 广韵 / 构形 / 流水号；`readings` / `structures` /
  `rhyme_groups` 都是空表（表留着，表示「这一层可以登记，本版没内容」）。

身份不靠外部字段：字与字的对应只用**码位**（`canonical_id`）+ **《說文》自己的古文/重文**
（`variant_of`，1,244 条）。康熙正文里的异体说法（「彝古作𢇏」）**还没有变成边**，
登记为 **G004**（196 处），修法是「从正文抽异体」，不在 v1。


## 目录

```
data/
  characters/     字头（分片目录）
  relations/      边（分片目录）
  provenance/     陈述，一源一目录（分片）
  sources.jsonl   来源登记（许可闸门的唯一事实源）
  editions.jsonl  注家 / 版本形态
  columns.jsonl   列登记表（产物；裁定表在 src/build_columns.py）
  radicals.jsonl  部首页（本版只有《說文》540 部）
  readings.jsonl / structures.jsonl / rhyme_groups.jsonl / source_tags.jsonl
                  值域登记表（本版全为空表：音韵、构形、出处标签都来自扩展包）
  known_gaps.jsonl  已知缺口（每条带可复跑探针）
src/              构建 / 校验 / 生成浏览页
schemas/          机器可读契约（由 src/schema.py 生成，勿手改）
viewer/           生成的浏览页（勿手改）
LICENSES/         第三方许可原文（Apache-2.0 / MIT）
LICENSE           本仓库自己的许可：CC0 1.0 全文
MANIFEST.json     逐文件 sha256 清单（产物）
```

## 数据模型要点

- **陈述是事实单元**：带 `source_id` + `evidence_url` + `license`；无出处不入库。
  只存 `data/provenance/<source_id>/`，**没有全量副本**（一源一目录才能在授权变化时整块移除）。
- **边只连实体、不带许可**：`asserts` / `commentary_on` / `variant_of` / `glyph` /
  `radical` / `phonology` / `structure` / `citation` / `cites_work` / `quotes` /
  `component` / `phonetic_component` / `edition_variant` / `edition_of` / `cited_in`。
  枚举的事实源是 **`src/schema.py`**，本文与其中的表都是给人读的。
- **段注对齐是一等关系**：段注陈述用 `parent_statement_id` 回指它所注的說文原文，
  不得压平成一行文本。
- **列 = 一个 `edition_id`**：`tier ∈ {base, standard, extended}`，`bundle` 是纯函数
  （`units.bundle_of`）。`base` 恰四列，唯一事实源 `units.BASE_COLUMNS`，校验器 R26 钉住。
  ⚠ 大徐本**不在 base**（与許慎原文 73% 逐字相同，并排显示就是复制），以后作 `standard` 加。
- **身份的两种口径**：`canonical_id`（按码位归并的身份）用于聚合；
  `search_group`（异体/简繁连通分量）**只用于检索候选扩展**，禁止 join 聚合。
- **部首只走边与陈述**：归部 = `radical` 边；康熙检字法 = `kind=radical_lookup` 陈述。
  字头表**不得**再有 `radical` / `radical_kangxi*` / `residual_strokes` 四列（R19）。
- **值域要登记**：音韵边目标是 `reading:<edition_id>:<slug>`（`data/readings.jsonl` 一条一登记）；
  构形边目标是 `structure:<source>:<key>`（`data/structures.jsonl`）。
  登记表**不带许可**——许可由 `edition_id` → `editions.jsonl` → `sources.jsonl` join。
- **反切是陈述不是字段**：`kind=phonology`，`parent_statement_id` 回指所属说解；
  `statements.reading` 全列为空（非空即 R24 报错）。
- **稳定 id**：`stable_id = "st_" + sha256(身份字段 + 父的内容地址)[:24]`，
  算法只在 `src/stable_id.py`；`statement_id`（S 号）会被重排，不能对外引用。

## 硬规则（会破坏数据的那些）

1. **无出处不入库**；来源许可以 `data/sources.jsonl` 为唯一事实源，逐条记 `license`。
2. **零 copyleft**：只用 Apache-2.0 / MIT / CC0 / Unicode-3.0 一类；
   Public-Domain 例外须逐书核对来源（维基文库导入的不算公版）。
3. **单个文件 ≤ 40 MB**（GitHub 拒绝 >100 MiB，本仓库自己更严）：大表一律分片目录，
   用 `jsonl_io.write_jsonl_sharded`。
4. **写文件原子写**：先写 `.tmp` 再 `os.replace`；构建被打断不得留半截文件。
5. **新增一列 = 往 `src/build_columns.py` 的裁定表加一行**，再跑
   `python src/build_columns.py` 生成 `data/columns.jsonl`（直接手改 jsonl 会被覆盖）。
   `tier` / `bundle` 不写进裁定表（纯函数算出来）。
6. **改枚举改 `src/schema.py`**（唯一事实源），再 `python src/build_schemas.py`；
   校验器不许自己抄枚举。`schemas/*.json` 是产物。
7. **数据身份看 `MANIFEST.json`**（逐文件 sha256），不看 DB 的一个 sha256。
8. **视图不许带坏视图**：改视图或加列后跑 `src/build_sqlite.py`（它做视图三层冒烟），
   再 `python src/build_bundles.py --only base`、`python src/query_bundles.py --char 月` 抽样。
9. **已知缺口登记在 `data/known_gaps.jsonl`**，探针在 `src/known_gaps.py`；
   校验输出里必须看得见它们，不许掩盖。
10. **改名改结构先改本文件**；提交信息说明「为什么」。

## 校验

```bash
python src/verify_relations.py        # 结构校验（R1–R26），退出码 1 = 有问题
python src/check_schema.py            # 字段级契约校验（全量）
python src/build_manifest.py          # 生成 MANIFEST.json；--check 只验不写
python src/build_columns.py           # → data/columns.jsonl
python src/build_sqlite.py            # → hanzi-relations.db + build.json（末尾视图冒烟）
python src/build_bundles.py --only base  # → db/bundle-base.db（v1 只有 base）
python src/query_bundles.py --char 月  # 合并查示例
python src/build_viewer.py            # → viewer/*.html
python src/build_viewer.py --check    # 产物是否与 data/ 一致
```

> v1 只保留当前构建链。代码注释里提到的 `build_relations.py`、`migrate_*.py`、
> `check_*.py` 等是历史一次性脚本，**没有随 v1 发布**；不要把注释里的文件名当入口。

> `verify_relations.py` 查得出「有条目错了」，查不出「有条目少了」——
> 后者要拿上游原始数据逐字段对账（v1 未带取数脚本，等扩展包一并补）。

## 许可

- 数据逐源不同（见 `data/sources.jsonl`）；`src/**`、`viewer/**` 与文档一律 **CC0 1.0**
  （根 `LICENSE` / `LICENSE-CODE` / `LICENSE-DOCS`）；本仓库对数据的编排也是 CC0。
- 第三方许可原文在 `LICENSES/`；本版 3 源：Apache-2.0 / MIT / Public-Domain，零 copyleft。
- **不得给下游附加额外限制**（「仅限非商业」「禁止再分发」之类）。
