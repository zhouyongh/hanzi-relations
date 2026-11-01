# 汉字引用关系库

本仓库是一套以汉字为索引、贯穿多部典籍的引用关系库：查一个汉字，就能看到各个典籍对它的解释、逐句注解，以及它与部首、字形、异体、构件之间的关系。

当前典籍只收录了四部：《說文解字》許慎原文、段玉裁注、《爾雅》、《康熙字典》；支持扩展典籍。

预览：<https://zhouyongh.github.io/hanzi-relations/viewer/index.html>

## 收录内容

| 项目 | 数量 |
|---|---|
| 字头 | 57,917 |
| 条目（原文、注、引文、反切） | 196,021 |
| 关系 | 371,276 |
| 部首 | 540（《說文》全部） |
| 体积 | JSONL 176.8 MiB · SQLite 216 MiB |

四部书的数据来源：

| 书 | 条目 |
|---|---|
| 康熙字典 | 138,645 |
| 段玉裁注 | 34,482 |
| 說文解字（許慎原文） | 21,506 |
| 爾雅 | 1,388 |

其中：字→解释 196,021、字→引文 91,818、段注→原文 22,897、
字→字形 19,212、字→部首 11,047、字→构件与声符 18,540、字→异体 1,244。

## 形式

| 形式 | 位置 | 说明 |
|---|---|---|
| SQLite | `hanzi-relations.db` | 215 MiB，见 [Releases](https://github.com/zhouyongh/hanzi-relations/releases)（推荐） |
| JSONL | `data/` | 全量事实，一来源一目录 |
| 浏览页 | `viewer/index.html` | 单文件，离线可用 |

浏览页另有 `kangxi.html`（康熙字头）与 `compare.html`（按书对比）。

## 用法

**查字**：打开 `viewer/index.html`，深链 `#月`，或直接在搜索框输入。

**写代码**：用 SQLite。默认视图按「字单元」把各书的同一个字收在一起，按书计数：

```sql
SELECT column_id, COUNT(*) FROM v_char_base
WHERE unit_id = (SELECT char_id FROM characters
                 WHERE head = '月' AND work_id = 'shuowen')
GROUP BY column_id ORDER BY 2 DESC, 1;
```

```
kangxi_dict         5
shuowen_duan_yucai  4
shuowen_xu_shen     2
xiaoxue_尔雅        2
```

**读 JSONL**：`data/provenance/<source_id>/` 下每行一条（只摘关键字段）：

```json
{
  "stable_id": "st_1f1bd748fd538ab7af86dba2",
  "char_id": "C004878",
  "edition_id": "shuowen_xu_shen",
  "kind": "gloss",
  "text": "闕也。大陰之精。象形。凡月之屬皆从月。",
  "evidence_url": "https://github.com/shuowenjiezi/shuowen/blob/6553a35076/data/4291.json",
  "license": "Apache-2.0",
  "source_id": "shuowenjiezi_shuowen"
}
```

## 扩展

加一部典籍，加四样东西：

| 加什么 | 内容 |
|---|---|
| 来源 | `data/sources.jsonl` 加一条：许可、地址、能否再分发 |
| 登记列 | 列登记表（`data/columns.jsonl`，由 `src/build_columns.py` 生成）加一行：这一列是什么书、提供哪几类内容 |
| 批陈述 | `data/provenance/<source_id>/` 一个目录，一条一行，带出处 |
| db库 | `db/bundle-<name>.db`，只装这一列的陈述与相关边 |

然后重建 SQLite 与浏览页。查询时装上一个 `.db` 就多一列，删掉即整体移除，
内核四列不受影响。扩展包可以只装一部分内容，不必等收全。

## 数据模型

- **解释条目**是事实单元：一句原文、一个来源、一个出处，带 `source_id`、`evidence_url`、
  `license`；无出处不入库。
- **关系**只连实体：字—解释、字—引文、段注—原文、字—部首、字—字形、字—异体、
  字—构件与声符。
- **按书分列**：同一批字头，每部书一列（`edition_id`）；默认视图取四列。
- **稳定 id**：`stable_id` 由内容算出，重建后不变，可以长期引用。

## 来源与许可

| 范围 | 许可 |
|---|---|
| `data/` 正文：《說文解字》許慎原文 | Apache-2.0（[shuowenjiezi/shuowen](https://github.com/shuowenjiezi/shuowen)，Copyright 2019 Shuowen.org） |
| `data/` 正文：《說文解字注》段玉裁 | Apache-2.0（同上，[shuowenjiezi/shuowen](https://github.com/shuowenjiezi/shuowen)） |
| `data/` 正文：《爾雅》 | Public-Domain（[daizhige-org/daizhigev20](https://github.com/daizhige-org/daizhigev20)） |
| `data/` 正文：《康熙字典》 | MIT（[samsonhoi/kangxi-dictionary](https://github.com/samsonhoi/kangxi-dictionary)） |
| `data/` 编排、`src/**`、`viewer/**`、文档 | CC0 1.0 |
| 字形文件（**不在本库**） | [hanzi-glyphs-svg](https://github.com/zhouyongh/hanzi-glyphs-svg) 逐层许可：CC0 / CC BY 4.0 / OFL 1.1；本库只存指针 |

本库自身（`data/`、`src/**`、`viewer/**`、文档）零 copyleft，可商用、可再分发。
《說文》原文与段注原书早已进入公有领域；这里的 Apache-2.0 是上游**数据集**的许可。
第三方署名、改动声明与许可全文见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)、[LICENSES/](LICENSES/)。

## 相关

- [hanzi-glyphs-svg](https://github.com/zhouyongh/hanzi-glyphs-svg)：字形（篆书等）与逐字图片。
- 本库自己的部分以 CC0 释出：引用、转载、改写都不需要署名；方便的话注明仓库地址即可。
