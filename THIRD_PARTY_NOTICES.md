# 第三方署名与权利保留

本仓库使用下列第三方材料。**每一项的许可以其原始声明为准**；本文件只是汇总，
不替代各来源自己的许可条款。许可原文放在 `LICENSES/`。

本仓库**没有**对下游附加任何额外限制，也**不得**被附加：本仓库自己的内容以
CC0 1.0 释出，CC0 是放弃权利，不是许可别人加条件。

**v1 的数据只有三个来源**（见 `data/sources.jsonl`）：《說文》检索数据（Apache-2.0）、
康熙字典数据集（MIT）、殆知閣小学书正文（Public-Domain）。v1 **不含** Unihan、廣韻、
构形分解、诗文集、OpenCC、字频表等扩展来源——它们将来以扩展包形式加入，不在这份文件里。

---

## 一、数据来源

### 《說文解字》檢索數據（shuowenjiezi/shuowen）

- **来源**：<https://github.com/shuowenjiezi/shuowen>（取 `data/<id>.json`，上游 9,833 字）
- **许可**：**Apache License 2.0**（全文 `LICENSES/Apache-2.0.txt`）
- **署名**：`shuowenjiezi/shuowen contributors`
- **本库的用法**：拆成「陈述 / 边」两表；許慎原文与段注各占一列、不合并；
  段注用 `parent_statement_id` 回指被注原文；《說文》重文单独立字头；补 `mpinyin`。
  本版由此得到 **11,102 个說文系字头、21,506 条原文陈述、34,482 条段注陈述**。
- **改动声明**：**已改动**（字段重排、拆分、对齐、增加派生字段）；正文仅去除首尾空白
  （4 条记录：3 条首空格、1 条首制表符），未作其他增删。
- **关于 `evidence_url`**：說文系陈述的 `evidence_url` 指向上游**固定 commit
  `6553a35076` 的 `data/<id>.json`**（`locator.upstream_id` 就是该文件名），
  一条陈述一个文件，与取数处一致。上游只在 `volume` 里标卷次，没有给维基文库链接；
  `zh.wikisource.org` 的 15 个卷页是按 `volume` 拼出的**第二阅读页**，未必与数据集
  逐字一致，不再作为 `evidence_url`。正文取自 `shuowenjiezi/shuowen` 以 Apache-2.0
  发布的数据集，而《說文解字》原书成书于公元 100 年前后、早已进入公有领域。
  **本库不主张对正文的任何新著作权**；下游再分发这一层时按 Apache-2.0 履行署名、
  附许可全文、注明改动即可。

### 康熙字典數據集（samsonhoi/kangxi-dictionary）

- **来源**：<https://github.com/samsonhoi/kangxi-dictionary>（取 `kx_full.xlsx` 全量）
- **许可**：**MIT License**（`Copyright (c) 2021 Samson Hoi`，全文
  `LICENSES/MIT-samsonhoi-kangxi-dictionary.txt`）
- **署名**：`Samson Hoi; samsonhoi/kangxi-dictionary contributors`
- **本库的用法**：按字头归并、去重后 **46,815 个字头、138,645 条陈述**；
  条目内嵌的「【書名】引文」拆成独立陈述并挂 `citation` 边（91,818 条），
  被引之书另建 `cites_work` 边（50 条）。
- **改动声明**：**已改动**（拆分为陈述与边；引文独立成条并挂回所属康熙条目）。
  《康熙字典》原书本身是公有领域，改编不产生新的著作权。

### 殆知閣《說文》三本正文与传统小学书（daizhige-org/daizhigev20）

- **来源**：<https://github.com/daizhige-org/daizhigev20>，`data` 分支，`儒藏/小學/`
- **许可**：**Public-Domain**（公有领域古籍的忠实录入）
- **署名**：殆知閣（daizhige.org）原始数据维护者；古籍作者：許慎、段玉裁、徐鍇、
  劉熙、張揖、郭璞、楊雄、顧野王、司馬光、王筠等
- **本库的用法**：取 39 本传统小学书的正文；本版进入 `xiaoxue_尔雅` 一列的是
  **《爾雅》正文 1,388 条陈述**（按「。」与【】切分，切分规则逐书记在 `locator`）。
- **改动声明**：**已改动**（拆分为陈述与边；按（部, 字头）对齐；码位归一；
  段注本的简体字头用本库简繁表归一到繁体）。

> **许可判定**：上游仓库**没有 LICENSE 文件**（GitHub `license` 字段为 `null`）。
> 本库按 `Public-Domain` 收录，依据是：
> 1. **内容本身是公有领域古籍**——《說文解字》《爾雅》《方言》《釋名》《廣雅》等
>    全部成书于 1929 年前；
> 2. **上游自己的法律判断**：其 README 写明「对公有领域古籍原文的忠实录入或复制
>    缺乏独创性，不产生新的版权，其文本仍属公有领域」；
> 3. **逐书核对来源**：上游 `.sources/` 目录逐条记录每部书的来源；核对结果是
>    39 本全部标注「转换自『殆知阁』GitHub 仓库中的 txt 版本」，**无一来自维基文库**
>    （`.sources/wikisource/` 里只有《全唐文》《姑妄言》《宋史》三部，不在收集范围内）。
>    v1 没有随附当时的核对脚本（取数/核对脚本留待扩展包一并发布），
>    核对结论与逐书清单已写进 `data/sources.jsonl` 的 `notes`。
>
> **残余风险**：上游无 LICENSE 文件，等于未明确授权；若上游或第三方日后主张权利，
> 可按来源整块移除 `data/provenance/daizhige_xiaoxue_books/`，不影响其他数据。

---

## 二、字形层（只存指针，不分发内容）

### hanzi-glyphs-svg

- **来源**：<https://github.com/zhouyongh/hanzi-glyphs-svg>
- **使用方式**：**仅存指针**（`glyph:<repo>:<layer>:<key>`），**不复制任何字形文件**。
  本版有 **19,212 条 `glyph` 边**指向它。
- **许可**：随该仓库逐层不同（甲骨 JGW 批 CC BY 4.0；汉典各批 CC0 1.0 等），
  详见该仓库的 `LICENSE-DATA` 与 `THIRD_PARTY_NOTICES.md`。
- **说明**：本仓库**不分发**上述字形内容，故其许可不传递到本仓库；
  使用字形须直接遵循 `hanzi-glyphs-svg` 的条款。

---

## 三、考虑过但**未入库**的来源

下列来源**内容不在本仓库**，也没有在 `data/sources.jsonl` 登记
（登记了反而是噪音）。记在这里是为了说明「为什么没收」：

| 来源 | 条款要点 | 本仓库的处理 |
|---|---|---|
| **ctext.org** | bulk RDF 标 **CC BY-NC-SA 3.0**；站内明文「嚴禁使用自動下載軟体」，对自动请求返 403 | 不入库；仅指针 |
| **漢字叔叔 hanziyuan.net** | 页脚 `Copyright © 1994 - 2017 Richard Sears ... All rights received.`；无许可文件 | 不入库；仅指针 |
| **教育部《異體字字典》** | 教育部「國語辭典公眾授權網」未列本词典；其 form-data 政策另有限制 | 不入库；仅指针 |
| **教育部《重編國語辭典修訂本》等四部** | **CC BY-ND 3.0**（禁止改作） | 不入库；仅指针 |
| **北京師大《說文解字》研究與應用平台** | 站点未标明数据许可 | 不入库；仅指针 |
| **BabelStone IDS** | 许可未明示 | 不入库；仅指针 |

---

## 四、本仓库自身

| 范围 | 许可 | 全文 |
|---|---|---|
| `src/**`、`viewer/**` | **CC0 1.0** | `LICENSE-CODE`（CC0 说明；全文见根 `LICENSE`） |
| `README.md`、`AGENTS.md`、`THIRD_PARTY_NOTICES.md`、`LICENSE-DATA` | **CC0 1.0** | `LICENSE-DOCS` |
| `data/` 的**编排、索引、关系结构**（本仓库整理的部分） | **CC0 1.0** | 同上 |
| `data/` 的**正文内容** | **逐源**：Apache-2.0 / MIT / Public-Domain | `data/sources.jsonl` + `LICENSES/` |

**`data/` 没有单一协议覆盖整库**：下游须按每条记录 / 每个来源的许可分别使用。
`data/sources.jsonl` 提供机器可读的对照表，`src/verify_relations.py` 强制
「陈述的许可 = 其来源登记的许可」，不一致即报错。

当前入库的全部来源为 **Apache-2.0 / MIT / Public-Domain**——零 copyleft。

---

## 五、权利保留

- 汉字字形本身是三千年前的古文字，不主张著作权；
- 本仓库只对自己整理的**关系结构、索引、脚本、文档**主张权利（且以 CC0 放弃）；
- 上游材料的一切权利归各自权利人所有；
- 若你是相关权利人并认为本仓库内容侵犯了你的权利，请通过 Issue 或
  <1279190619@qq.com> 联系我们，我们会在核实后移除相应条目。

**本仓库不向任何下游施加限制**：既不禁止商业使用，也不要求署名，
更不禁止再分发或闭源集成。
