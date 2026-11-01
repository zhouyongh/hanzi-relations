"""从 data/*.jsonl 构建 SQLite 分发件。

设计（见设计裁定 D8）：
- JSONL 是事实源，SQLite 是**派生件**（不入库，走 Release 附件）；
- **确定性**：固定排序键、固定 PRAGMA、同输入必得同输出，
  下游可用 sha256 校验手里的库是不是这份数据构建的。

用法：
    python src/build_sqlite.py [--out hanzi-relations.db] [--hash-out build.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# JSONL 讀寫（分片支持）：見 src/jsonl_io.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jsonl_io import read_jsonl, read_jsonl_any, read_jsonl_dir  # noqa: E402

# 壓縮倒排索引（規格 §4 D47）：見 src/inverted.py
import inverted as inverted_mod  # noqa: E402

# 字單元 / 掛載層：見 src/units.py 與規格 §4 D40
import units as units_mod  # noqa: E402

# 契約單一事實源（封閉枚舉 + schema 版本）：見 src/schema.py 與規格 §4 D44
import schema as schema_mod  # noqa: E402

SCHEMA = """
PRAGMA journal_mode = DELETE;

-- 字頭
CREATE TABLE characters (
    char_id           TEXT PRIMARY KEY,
    canonical_id     TEXT,          -- 跨来源归并键（按现代码位）：**身份**
    search_group     TEXT,          -- 检索可达组（异体/简繁 连通分量）：**可达性**
    head              TEXT NOT NULL,
    work_id           TEXT NOT NULL,
    upstream_id       TEXT,
    volume            TEXT,
    -- ⚠ **沒有 radical / radical_kangxi / radical_kangxi_simplified / residual_strokes**
    -- 四列（D41.4 瘦身）：歸屬走 `v_char_radical`（`radical` 邊），
    -- 檢字法走 `v_radical_lookup`（`radical_lookup` 陳述）。
    -- 字頭表只留**身份**字段。
    modern_codepoints TEXT,   -- JSON 数组
    pinyin            TEXT,
    mpinyin           TEXT,
    source_ids        TEXT    -- JSON 数组
);
CREATE INDEX idx_char_head ON characters(head);
CREATE INDEX idx_char_work ON characters(work_id);
CREATE INDEX idx_char_cps  ON characters(modern_codepoints);
CREATE INDEX idx_char_canon ON characters(canonical_id);
CREATE INDEX idx_char_group ON characters(search_group);

-- 陳述（事實單元）
CREATE TABLE statements (
    -- **rid = 稠密序號**：壓縮倒排索引（規格 D47）指向它。
    -- 為什麼不用 SQLite 的隱式 rowid：官方文檔寫明「沒有顯式 INTEGER PRIMARY KEY
    -- 的表，VACUUM **可能**改 ROWID」——那會**靜默毀掉整個倒排索引**。
    -- 為什麼不讓 statement_id 當主鍵：那要多付一份 unique 索引；
    -- rid 已經是主鍵，statement_id 只需要 UNIQUE 約束。
    rid                INTEGER PRIMARY KEY,
    statement_id       TEXT NOT NULL UNIQUE,
    stable_id          TEXT,   -- 內容地址（規格 D45）：重建後不變，可被外部引用
    kind                TEXT NOT NULL,
    char_id             TEXT,
    work_id             TEXT,
    edition_id          TEXT,
    text                TEXT,
    reading             TEXT,
    radical             TEXT,
    parent_statement_id TEXT,
    locator             TEXT,   -- JSON
    evidence_url        TEXT,
    license             TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    confidence          TEXT,
    summary_method      TEXT,
    reviewer            TEXT,
    review_date         TEXT
);
CREATE INDEX idx_stmt_char   ON statements(char_id);
CREATE INDEX idx_stmt_work   ON statements(work_id);
CREATE INDEX idx_stmt_edit   ON statements(edition_id);
CREATE INDEX idx_stmt_src    ON statements(source_id);
CREATE INDEX idx_stmt_parent ON statements(parent_statement_id);
CREATE INDEX idx_stmt_lic    ON statements(license);
-- **stable_id 必須全域唯一**——它是「引用某一條陳述」的公共鑰匙。
-- 用 UNIQUE 而不是普通索引：撞了就讓建庫失敗，而不是靜默存兩份一樣的 id。
-- （實測 962,670 條陳述 → 962,670 個不重複的 stable_id，零撞車。）
CREATE UNIQUE INDEX idx_stmt_stable ON statements(stable_id);
-- 複合索引：字單元層的掛載查詢靠 (char_id, edition_id) 定位陳述。
-- 沒有它時 SQLite 會改用 idx_stmt_edit (edition_id) 驅動——對《爾雅注疏》這類
-- 大列，每個掛載行都要掃過整列的陳述再過濾 char_id。
-- 實測：v_char_full 查「月」單元 68 行要 0.50s；加了複合索引後同一查詢 < 0.01s。
CREATE INDEX idx_stmt_char_edit ON statements(char_id, edition_id);

-- 壓縮倒排索引（規格 D47）：單字 + 相鄰二字組
-- 存的是「每個詞一條 delta+varint 壓縮的 posting list」——
-- 1,134 萬個 posting 只占 **12.31 MiB**（未壓縮 int32 是 43.27 MiB）。
-- 對照：行式表 (term, rid) 要 **+294 MiB**；
--       FTS5 trigram 要 **+140.8 MiB**，而且**答不了 1–2 字的漢字查詢**。
-- 查詢方式見 src/inverted.py（`connect()` 註冊 inverted_rids() 函數）。
CREATE TABLE inverted_unigram (
    term     TEXT PRIMARY KEY,
    postings BLOB NOT NULL
) WITHOUT ROWID;
CREATE TABLE inverted_bigram (
    term     TEXT PRIMARY KEY,
    postings BLOB NOT NULL
) WITHOUT ROWID;

-- 邊：端點先去重成 `refs` 的整數 id，`relation_edges` 只存 id（規格 D50 / schema v3）。
-- 兼容視圖 `relations` 仍提供 from_ref / to_ref —— 既有 30+ 個視圖**不用改**。
CREATE TABLE refs (
    ref_id INTEGER PRIMARY KEY,
    ref    TEXT NOT NULL UNIQUE
);
CREATE TABLE relation_edges (
    relation_id TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    from_id     INTEGER NOT NULL,
    to_id       INTEGER NOT NULL,
    kind        TEXT,          -- 邊的子類（variant_of 用：simplification / semantic / …）
    statement_ids TEXT         -- JSON 陣列：支撐這條邊的陳述 id（variant_of 用）
);
CREATE INDEX idx_edge_type ON relation_edges(type);
CREATE INDEX idx_edge_from ON relation_edges(from_id);
CREATE INDEX idx_edge_to   ON relation_edges(to_id);
CREATE INDEX idx_edge_kind ON relation_edges(kind);

CREATE VIEW relations AS
SELECT e.relation_id, e.type, f.ref AS from_ref, t.ref AS to_ref,
       e.kind, e.statement_ids
FROM relation_edges e
JOIN refs f ON f.ref_id = e.from_id
JOIN refs t ON t.ref_id = e.to_id;

-- 來源（許可閘門的事實源）
CREATE TABLE sources (
    source_id      TEXT PRIMARY KEY,
    name           TEXT,
    url            TEXT,
    kind           TEXT,
    license        TEXT,
    redistributable INTEGER,   -- 0/1
    retrieved_at   TEXT,
    version        TEXT,
    attribution    TEXT,
    notes          TEXT
);
CREATE INDEX idx_src_lic ON sources(license);

-- 部首
CREATE TABLE radicals (
    system     TEXT NOT NULL,
    radical    TEXT NOT NULL,
    "index"    INTEGER,
    char       TEXT,
    strokes    INTEGER,
    source_id  TEXT,
    license    TEXT,
    evidence_url TEXT,
    note       TEXT,        -- 說明（如「部首字筆畫待重取上游後補」）
    PRIMARY KEY (system, radical)
);

-- 韻類/韻目（廣韻）
-- system: guangyun_xiao = 韻類（聲母×等×韻目×聲調，3,884 個，每類一個反切）
--         guangyun_rhyme = 韻目（《廣韻》本傳的 206 韻）
CREATE TABLE rhyme_groups (
    system       TEXT NOT NULL,
    group_id     TEXT NOT NULL,
    rhyme        TEXT,
    rhyme_class  TEXT,      -- 音韻地位（聲母+等+韻目+聲調）
    fanqie       TEXT,      -- 反切
    xiao_index   TEXT,      -- 上游的小韻字號
    char_count   INTEGER,
    xiao_count   INTEGER,   -- 韻目層：該韻目下的韻類數
    tone_classes TEXT,      -- 韻目層：該韻目下的聲調類
    source_id    TEXT,
    license      TEXT,
    evidence_url TEXT,
    PRIMARY KEY (system, group_id)
);
CREATE INDEX idx_rhyme_rhyme ON rhyme_groups(rhyme);
-- ⚠ 主鍵是 (system, group_id)：**單獨按 group_id 查用不上它**（前綴不匹配），
-- 而 `v_rhyme_group` 正是拿 `substr(ref,18)` 當 group_id 去 join。
-- 沒有這個單列索引時，SQLite 只能對 4,090 行的小表做 `SCAN rg`：
-- 26 萬條音韻邊 × 4,090 = 10 億次比較，實測 `COUNT(*)` 要 10.9 秒。
CREATE INDEX idx_rhyme_gid ON rhyme_groups(group_id);

-- 注家形態（axis: annotator 注家 / edition 刻本）
CREATE TABLE editions (
    edition_id TEXT PRIMARY KEY,
    work_id    TEXT,
    axis       TEXT,
    name       TEXT,
    note       TEXT
);

-- 列登記表（**設計聲明**：哪些列存在、各是什麼角色、掛載策略是什麼）
-- 事實源是 data/columns.jsonl；work_id/axis/license 由 editions/sources join 得出。
CREATE TABLE columns (
    column_id     TEXT PRIMARY KEY,   -- == edition_id（一列 = 一部書的某個形態）
    role          TEXT NOT NULL,      -- core（第一批）/ optional（按需帶出）/ support（工具書屬性）
    provides      TEXT NOT NULL,      -- JSON 陣列：語義維度（gloss/reading/occurrence/…）
    aggregation   TEXT NOT NULL,      -- aggregate（可聚合說解）/ reachability（僅檢索候選）
    tier          TEXT NOT NULL,      -- base（默認 4 列）/ standard（主幹對照）/ extended（按需勾選）
    bundle        TEXT NOT NULL,      -- 物理分庫：base / shuowen / xiaoxue / classics / unihan / misc
    work_id       TEXT,
    axis          TEXT,
    name          TEXT,
    license       TEXT,
    note          TEXT
);
CREATE INDEX idx_col_role ON columns(role);
CREATE INDEX idx_col_tier ON columns(tier);
CREATE INDEX idx_col_bundle ON columns(bundle);
CREATE INDEX idx_col_work ON columns(work_id);

-- 字單元（跨來源按 canonical_id 歸並的**身份**；說文系為主軸）
CREATE TABLE units (
    unit_id         TEXT PRIMARY KEY,
    head            TEXT,
    primary_char_id TEXT,
    primary_work    TEXT,
    codepoints      TEXT,   -- JSON 陣列
    n_members       INTEGER,
    works           TEXT,   -- JSON 陣列
    has_shuowen     INTEGER -- 1/0：該單元是否含《說文》字頭
);
CREATE INDEX idx_unit_sw ON units(has_shuowen);

CREATE TABLE unit_members (
    unit_id    TEXT NOT NULL,
    char_id    TEXT NOT NULL,
    work_id    TEXT,
    head       TEXT,
    is_primary INTEGER,
    PRIMARY KEY (unit_id, char_id)
);
CREATE INDEX idx_um_char ON unit_members(char_id);

-- 掛載：某一列的內容挂在該單元的哪個字頭行上
CREATE TABLE unit_bindings (
    unit_id      TEXT NOT NULL,
    column_id    TEXT NOT NULL,
    char_id      TEXT NOT NULL,
    method       TEXT,      -- self（就是主軸行）/ canonical（按身份歸並）/ reachability（僅檢索）
    n_statements INTEGER,
    kinds        TEXT,      -- JSON 陣列
    source_ids   TEXT,      -- JSON 陣列
    PRIMARY KEY (unit_id, column_id, char_id)
);
CREATE INDEX idx_ub_unit ON unit_bindings(unit_id);
CREATE INDEX idx_ub_col  ON unit_bindings(column_id);

-- 覆蓋矩陣：單元 × 列（**瘦身版**，規格 D50）。
--
-- schema v3 去掉了原來的物化表（54.9 MB + 兩個索引 43 MB，且 `char_ids` 無人使用）。
-- 現在只留「單元 × 列 → 陳述數」這一列真正被查詢的欄位（WITHOUT ROWID，約 27 MB）；
-- `kinds` / `source_ids` 改由 `v_unit_columns` 對 `unit_bindings` **逐列現算**
-- （按單元只算 ~66 列，走 `idx_ub_unit`，實測 < 5 ms）。
CREATE TABLE unit_columns (
    unit_id      TEXT NOT NULL,
    column_id    TEXT NOT NULL,
    n_statements INTEGER,
    PRIMARY KEY (unit_id, column_id)
) WITHOUT ROWID;

-- 便捷視圖：一個字的全部說解
CREATE VIEW v_char_statements AS
SELECT c.char_id, c.head, c.work_id, s.statement_id, s.stable_id, s.kind, s.edition_id,
       s.text, s.reading, s.radical, s.parent_statement_id,
       s.evidence_url, s.license, s.source_id, s.confidence,
       s.locator,
       json_extract(s.locator, '$.serial')    AS serial,
       json_extract(s.locator, '$.chapter')   AS chapter,
       json_extract(s.locator, '$."小韻號"')  AS rhyme_class,
       json_extract(s.locator, '$."韻目"')    AS rhyme_head
FROM characters c JOIN statements s ON s.char_id = c.char_id;

-- 便捷視圖：**穩定 id** ↔ 內部主鍵（規格 D45）
-- 「引用本庫的某一條陳述」用 `stable_id`——它是內容地址，重建一次也不變；
-- `statement_id` 是本庫內部的行號（分段號），全量重建後**可能改**。
-- 所以：外部引用用 stable_id，庫內關聯用 statement_id。
CREATE VIEW v_statement_stable AS
SELECT s.stable_id, s.statement_id,
       c.head, c.char_id, c.modern_codepoints, c.work_id AS char_work,
       s.kind, s.edition_id, s.work_id, s.source_id, s.license,
       json_extract(s.locator, '$.volume')     AS volume,
       json_extract(s.locator, '$.upstream_id') AS upstream_id,
       json_extract(s.locator, '$.radical')    AS radical_name,
       json_extract(s.locator, '$.seq')        AS seq
FROM statements s
LEFT JOIN characters c ON c.char_id = s.char_id;

-- 便捷视图：段注對說文原文的對照（版本差異層的核心）
CREATE VIEW v_commentary AS
SELECT p.statement_id AS gloss_id, p.work_id, p.edition_id AS gloss_edition,
       p.text AS gloss_text, p.locator AS gloss_locator,
       c.statement_id AS note_id, c.edition_id AS note_edition,
       c.text AS note_text, c.locator AS note_locator,
       c.license, c.source_id, c.evidence_url
FROM statements p JOIN statements c ON c.parent_statement_id = p.statement_id
WHERE c.kind = 'commentary';

-- 便捷視圖：字 ↔ 部（**歸屬的唯一表示**，規格 D41.4）
-- 字頭表已瘦身（不再有 `radical` 列），部首歸屬一律走 `radical` 邊。
-- `radical_name` 是部號（康熙，如 "43"）或部名（說文，如 "走"）。
--
-- ⚠ **必須走整數 id 再回查 `refs`，不能 `ON r.from_ref = 'char:' || ch.char_id`**：
--   那等於對 `relations`（2.2M 行的兼容視圖）+ 11 萬字頭做**字串拼接的交叉連接**，
--   任何不能把謂詞推進去的查詢（`COUNT(*)`、全表掃）都是 O(n²)。
--   實測老寫法 `SELECT COUNT(*) FROM v_char_radical` 跑 10 分鐘不返回，
--   而按 `char_id` 過濾只要 1 ms——「按 id 查很快」掩蓋了「全表根本用不了」。
CREATE VIEW v_char_radical AS
SELECT ch.char_id, ch.head, ch.work_id,
       CASE WHEN rt.ref LIKE 'radical:kangxi:%' THEN 'kangxi'
            WHEN rt.ref LIKE 'radical:shuowen:%' THEN 'shuowen' END AS radical_system,
       CASE WHEN rt.ref LIKE 'radical:kangxi:%'
            THEN substr(rt.ref, length('radical:kangxi:') + 1)
            WHEN rt.ref LIKE 'radical:shuowen:%'
            THEN substr(rt.ref, length('radical:shuowen:') + 1) END AS radical_name,
       rt.ref AS radical_ref
FROM relation_edges e
JOIN refs fr ON fr.ref_id = e.from_id
JOIN refs rt ON rt.ref_id = e.to_id
JOIN characters ch ON ch.char_id = substr(fr.ref, 6)   -- 'char:' 5 字元
WHERE e.type = 'radical';

-- 便捷視圖：康熙 214 部**檢字法**（規格 D41.1）
-- 與《說文》的**造字法**歸屬是兩個維度：這裡只回答
-- 「這字按康熙檢字法歸第幾部、殘幾筆」。
CREATE VIEW v_radical_lookup AS
SELECT ch.char_id, ch.head, s.statement_id, s.text,
       json_extract(s.locator, '$.radical')   AS kangxi_no,
       json_extract(s.locator, '$.residual')  AS residual_strokes,
       json_extract(s.locator, '$.simplified') AS simplified
FROM statements s
JOIN characters ch ON ch.char_id = s.char_id
WHERE s.kind = 'radical_lookup';

-- 便捷視圖：康熙部首字在《說文》里屬哪一部（**≠「部首等同」**）
CREATE VIEW v_radical_owner AS
SELECT r.from_ref AS kangxi_radical_ref, r.to_ref AS shuowen_radical_ref,
       r.statement_ids
FROM relations r
WHERE r.type = 'radical_owner';

-- 便捷視圖：某字被哪些書引用（康熙條目內的內嵌引文）
CREATE VIEW v_cited_works AS
SELECT ch.head, ch.char_id, ch.modern_codepoints,
       ch.volume,
       json_extract(s.locator, '$.cited_book') AS cited_book,
       s.statement_id, s.text, s.evidence_url, s.license, s.source_id
FROM statements s
JOIN characters ch ON ch.char_id = s.char_id
WHERE s.kind = 'citation';

-- ⚠ **v_seal_editions 已随 M014 一起删掉**（2026-11-01）。
-- 它查的四个刻本列（`shuowen_ccz` / `thx` / `qjz` / `dyc`）在 M014 里整列删除
-- （43,777 条只有流水号的 `kind=serial` 陈述），视图从此**必然返回 0 行**。
-- 留着它的代价不是「多一个视图」，而是**用户第一句就查空**：
-- README 曾把它当「字 × 刻本矩阵」的示例，而示例输出是手写的、从没跑过。
-- 刻本流水号仍在字形层（`hanzi-glyphs-svg/glyphs/seal/external.csv` 33 列），
-- 要查去那边查；本库不再提供这一层。

-- 便捷視圖：說文 540 部 ↔ 康熙 214 部對照（只含同名確定的映射）
CREATE VIEW v_radical_map AS
SELECT ch.head AS radical_char, ch.char_id, ch.modern_codepoints,
       json_extract(s.locator, '$.shuowen_radical') AS shuowen_radical,
       json_extract(s.locator, '$.kangxi_no')     AS kangxi_no,
       json_extract(s.locator, '$.basis')         AS basis
FROM statements s
JOIN characters ch ON ch.char_id = s.char_id
WHERE s.edition_id = 'radical_map';

-- 便捷視圖：跨來源的同一個字（按 modern_codepoints 歸並）
-- 例：說文的「說」與 OpenCC 的「說」是兩個 char_id，但 canonical_id 相同。
CREATE VIEW v_same_char AS
SELECT canonical_id,
       COUNT(DISTINCT work_id) AS n_works,
       COUNT(*) AS n_heads,
       GROUP_CONCAT(DISTINCT work_id) AS works,
       GROUP_CONCAT(DISTINCT head) AS heads
FROM characters
WHERE canonical_id IS NOT NULL
GROUP BY canonical_id;

-- 便捷視圖：簡→繁對照（**只含真正的簡繁關係**）
-- ⚠ **只取 kind='simplification' 的邊**。
-- 早期版本按 `type='variant_of'` 全取，把《說文》重文（弌→一）、Unihan 語義異體
-- （說→説）、日文異體等一律標成「簡→繁」，造出成片假簡繁對——
-- 而 README 正是拿這個視圖當示例。獨立審查抓出來了。
-- 邊的兩端用 canonical_id 歸並（跨來源的同一個字）。
CREATE VIEW v_simplification AS
SELECT DISTINCT sc.head AS simplified, tc.head AS traditional,
       sc.canonical_id AS simplified_canon,
       tc.canonical_id AS traditional_canon,
       sc.work_id AS simp_work, tc.work_id AS trad_work,
       r.kind AS kind
FROM relations r
JOIN characters sc ON sc.char_id = substr(r.from_ref, 6)   -- 'char:' 是 5 字元
JOIN characters tc ON tc.char_id = substr(r.to_ref, 6)
WHERE r.type = 'variant_of'
  AND r.kind = 'simplification'
  AND sc.head <> tc.head;

-- 便捷視圖：全部異體關係（按 kind 分類，不限簡繁）
CREATE VIEW v_variant AS
SELECT sc.head AS variant, tc.head AS standard,
       sc.canonical_id AS variant_canon, tc.canonical_id AS standard_canon,
       r.kind AS kind, r.statement_ids AS statement_ids
FROM relations r
JOIN characters sc ON sc.char_id = substr(r.from_ref, 6)
JOIN characters tc ON tc.char_id = substr(r.to_ref, 6)
WHERE r.type = 'variant_of' AND sc.head <> tc.head;

-- 便捷視圖：檢索可達組（同一個字的各種寫法）
-- canonical_id 是「身份」（按碼位），search_group 是「可達性」（異體/簡繁連通分量）。
-- 例：說文的「閒」(U+9592) 與 OpenCC 的「間」(U+9593) 碼位不同、是兩個身份，
--     但 Unihan 的 kSpecializedSemanticVariant 把二者連成一組——
--     所以搜簡體「间」要能查到《說文》的「閒」。
--
-- ⚠⚠ **本視圖只用於「檢索候選擴展」，絕不可用於聚合說解**。
-- 它是組內的笛卡爾積——用它 join 聚合會把「說/説/说」或「協/恊/叶/葉」的
-- 說解混成一鍋。要聚合說解，請用 `canonical_id`（那是身份口徑）。
CREATE VIEW v_char_lookup AS
SELECT DISTINCT q.head AS query_head, q.work_id AS query_work,
       q.modern_codepoints AS query_codepoints,
       t.char_id, t.head AS reachable_head, t.work_id AS reachable_work,
       t.modern_codepoints AS reachable_codepoints
FROM characters q
JOIN characters t ON t.search_group = q.search_group;

-- 便捷視圖：任一檢索詞 → 它能到達的《說文》字頭
--
-- ⚠ **部首不能從 characters.radical 取**：那一列在 D41.4「字頭表瘦身」時
--   就刪了（部首歸屬改走 `radical` 邊）。而 SQLite **建視圖時不檢查列是否存在**
--   （列是運行時解析的）→ 視圖建得好好的，**一查就報 "no such column: t.radical"**。
--   這是實測踩過的坑：`v_reachable_shuowen` 就這麼隨著派發件發出去很久了，
--   而沒有任何校驗器查視圖。現改走 `radical` 邊（部首歸屬的唯一載體）。
--
-- 用 GROUP BY 子查詢而不是直接 join：一個字頭若有多條說文部邊，
-- 直接 join 會把行數放大。
-- ⚠ **必须 DISTINCT**：`q JOIN t ON t.search_group = q.search_group` 是**组内笛卡尔积**
--   （一个可达组里有 N 个说文字头 → 每个检索词都出 N 行）——实测 148,955 行里
--   只有 **25,551 行是不重复的**（重复率 82.8%）。紧邻的 `v_char_lookup` 就写了
--   DISTINCT，这里也要。
CREATE VIEW v_reachable_shuowen AS
SELECT DISTINCT q.head AS query_head, q.modern_codepoints AS query_codepoints,
       t.char_id, t.head AS shuowen_head,
       substr(r.to_ref, 17) AS shuowen_radical,
       t.volume
FROM characters q
JOIN characters t ON t.search_group = q.search_group
LEFT JOIN (
    SELECT from_ref, MIN(to_ref) AS to_ref
    FROM relations
    WHERE type = 'radical' AND to_ref LIKE 'radical:shuowen:%'
    GROUP BY from_ref
) r ON r.from_ref = 'char:' || t.char_id
WHERE t.work_id = 'shuowen';

-- 讀音登記表（規格 §4 D42）
--
-- 讀音邊的目標 `reading:<edition_id>:<slug>` 指向這裡。
-- **不含 license / source_id**——許可的唯一事實源是 `data/sources.jsonl`，
-- 由 `edition_id` join 出去（硬規則 2）。
CREATE TABLE readings (
    reading_id   TEXT PRIMARY KEY,
    edition_id   TEXT NOT NULL,
    value        TEXT NOT NULL,
    slug         TEXT,
    value_kind   TEXT,      -- reading / reading_list / location_reading / fanqie / rhyme_class / code
    normalized   TEXT,      -- 派生：聲調符號→數字聲調
    tone         INTEGER,   -- 派生：1–5；判不出來是 NULL（不是 0）
    locations    TEXT,      -- JSON 数组：Unihan 的定位碼
    license      TEXT,      -- 派生：由 edition 的陳述 source_id → sources.jsonl join
    derived_from TEXT,
    transform    TEXT
);
CREATE INDEX idx_read_ed   ON readings(edition_id);
CREATE INDEX idx_read_kind ON readings(value_kind);
CREATE INDEX idx_read_norm ON readings(normalized);

-- 構形值域登記表（規格 §4 D43）
--
-- `type=structure` 邊的目標 `structure:<source>:<key>` 指向這裡。
-- **不含 license / source_id**——與 readings 同理，許可由 edition_id join 出去。
CREATE TABLE structures (
    structure_id  TEXT PRIMARY KEY,
    namespace     TEXT NOT NULL,   -- 來源 id（key 是上游自己的 key）
    edition_id    TEXT,            -- 供 join editions/sources 取許可
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,   -- 構形分解式（IDS）
    value_kind    TEXT,            -- ids
    derived_from  TEXT,
    transform     TEXT
);
CREATE INDEX idx_struct_ns  ON structures(namespace);
CREATE INDEX idx_struct_key ON structures(key);

-- 便捷視圖：某個構形 → 哪些字有這個構形
CREATE VIEW v_structure_chars AS
SELECT st.structure_id, st.namespace, st.key, st.value, st.value_kind,
       ch.head AS char, ch.char_id, ch.modern_codepoints
FROM relations r
JOIN structures st ON st.structure_id = r.to_ref
JOIN characters ch ON ch.char_id = substr(r.from_ref, 6)
WHERE r.type = 'structure';

-- 便捷視圖：某個讀音 → 哪些字讀它（同音字查詢）
CREATE VIEW v_reading_chars AS
SELECT rd.normalized, rd.tone, rd.value_kind, rd.reading_id, rd.edition_id,
       ch.head AS char, ch.char_id, ch.modern_codepoints
FROM relations r
JOIN readings rd ON rd.reading_id = r.to_ref
JOIN characters ch ON ch.char_id = substr(r.from_ref, 6)
WHERE r.type = 'phonology';

-- 便捷視圖：讀音登記表 + 該讀音的字數
CREATE VIEW v_reading AS
SELECT rd.reading_id, rd.edition_id, ed.name AS edition_name, rd.value,
       rd.slug, rd.value_kind, rd.normalized, rd.tone, rd.license,
       (SELECT COUNT(*) FROM v_reading_chars c WHERE c.reading_id = rd.reading_id) AS n_chars
FROM readings rd
JOIN editions ed ON ed.edition_id = rd.edition_id;

-- 便捷視圖：某字的全部讀音（含歸一化後的同音字）
CREATE VIEW v_char_readings AS
SELECT ch.head AS char, ch.char_id, rd.reading_id, rd.edition_id,
       rd.value, rd.value_kind, rd.normalized, rd.tone,
       (SELECT COUNT(*) FROM v_reading_chars c2 WHERE c2.normalized = rd.normalized
        AND c2.normalized <> '') AS n_homophones
FROM relations r
JOIN readings rd ON rd.reading_id = r.to_ref
JOIN characters ch ON ch.char_id = substr(r.from_ref, 6)
WHERE r.type = 'phonology';

-- 便捷視圖：韻書互證（字 ↔ 韻類 ↔ 韻目）
-- 韻類 = 聲母×等×韻目×聲調（廣韻 3,884 個，每類恰好一個反切）
-- 韻目 = 《廣韻》本傳的 206 韻
--
-- ⚠ 讀音邊的目標格式在規格 D42 裡改成了 `reading:<edition_id>:<slug>`，
--   `'reading:guangyun:'` 連冒號是 **17** 個字元，所以偏移是 `substr(..., 18)`。
--   舊版寫 17（註解說「16 字元」）→ 取出來是 `:1004`，而 `group_id` 是 `1004`
--   → **這個視圖自 2026-10-30 起一條都查不到**，而且因為 join 不中，
--   SQLite 退化成對 `statements` 的全掃（`COUNT(*)` 也超時）。
--   `v_rhyme_members` 用的是字串拼接（`'reading:guangyun:' || group_id`），
--   所以它一直是对的——**同一份偏移寫在兩個地方，錯的只有一處**。
CREATE VIEW v_rhyme_group AS
SELECT ch.head AS char, ch.char_id, ch.modern_codepoints,
       rg.group_id AS xiao_id, rg.rhyme_class, rg.fanqie,
       rg.rhyme, rg.char_count AS xiao_char_count,
       rg.fanqie AS reading,
       rg.evidence_url, rg.license, rg.source_id
FROM relation_edges e
JOIN refs fr ON fr.ref_id = e.from_id
JOIN refs rt ON rt.ref_id = e.to_id
JOIN characters ch ON ch.char_id = substr(fr.ref, 6)        -- 'char:' 5 字元
JOIN rhyme_groups rg ON rg.group_id = substr(rt.ref, 18)    -- 'reading:guangyun:' 17 字元
WHERE e.type = 'phonology' AND rt.ref LIKE 'reading:guangyun:%'
  AND rg.system = 'guangyun_xiao';                          -- 韻類層（≠ 韻目層的 206 韻）

-- 便捷視圖：某韻目（或韻類）下的全部字
CREATE VIEW v_rhyme_members AS
SELECT rg.rhyme, rg.group_id AS xiao_id, rg.rhyme_class, rg.fanqie,
       ch.head AS char, ch.char_id, ch.modern_codepoints
FROM rhyme_groups rg
JOIN relations r ON r.to_ref = 'reading:guangyun:' || rg.group_id
JOIN characters ch ON ch.char_id = substr(r.from_ref, 6)
WHERE rg.system = 'guangyun_xiao';

-- 便捷視圖：字頻排名（Jun Da《現代漢字使用頻率表》）
-- 只取「字」與「排名」兩列——上游其餘列的出處是 CC-CEDICT / Wikipedia
-- （CC BY-SA 3.0，copyleft），本庫零 copyleft 路線，**不收**。
CREATE VIEW v_char_frequency AS
SELECT ch.head AS char, ch.char_id, ch.modern_codepoints,
       json_extract(s.locator, '$.rank') AS rank,
       json_extract(s.locator, '$.简化字') AS simplified,
       json_extract(s.locator, '$.对应繁体') AS traditional,
       s.text, s.license, s.source_id
FROM statements s
JOIN characters ch ON ch.char_id = s.char_id
WHERE s.kind = 'frequency';

-- 便捷視圖：引文 ↔ 原文對照（**引文脫誤**）
-- 《康熙字典》條目裡內嵌的【說文】【廣韻】引文，與本庫的原文逐條對齊。
-- 對齊類型與具體差異片段見 locator.對照。
-- ⚠ **join 順序很重要**：從 `relations` 側起（`type='quotes'` 只有一萬多條，
-- 走 idx_rel_type），再回查兩個 statements 的主鍵。反過來寫的話
-- SQLite 會掃全表 70 萬條陳述，實測查一條要跑好幾分鐘。
CREATE VIEW v_quote_alignment AS
SELECT cit.statement_id AS cite_id, cit.text AS cite_text,
       cit.char_id, cit.edition_id AS cite_edition,
       json_extract(cit.locator, '$.cited_book') AS cited_book,
       json_extract(cit.locator, '$.對照.類型') AS align_type,
       json_extract(cit.locator, '$.對照.相似度') AS similarity,
       json_extract(cit.locator, '$.對照.差異') AS diff_ops,
       orig.statement_id AS orig_id, orig.text AS orig_text,
       orig.edition_id AS orig_edition
FROM relations r
JOIN statements cit ON cit.statement_id = substr(r.from_ref, 11)  -- 'statement:' 10 字元
JOIN statements orig ON orig.statement_id = substr(r.to_ref, 11)
WHERE r.type = 'quotes';

-- 便捷視圖：對照結果的匯總（各類型各多少條）
CREATE VIEW v_quote_align_summary AS
SELECT json_extract(locator, '$.cited_book') AS cited_book,
       json_extract(locator, '$.對照.類型') AS align_type,
       COUNT(*) AS n
FROM statements
WHERE kind = 'citation'
  AND json_extract(locator, '$.對照.類型') IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 3 DESC;

-- 便捷視圖：經史用例（字 ↔ 句）
-- 句子陳述掛在「書」的字頭上（char_id = 書名），而用字是通過 cited_in 邊連過去的。
-- 這裡直接用 relations 展開，避免在視圖裡跑重子查詢。
CREATE VIEW v_classic_usage AS
SELECT ch.head AS char, ch.char_id, ch.modern_codepoints,
       json_extract(s.locator, '$.book')    AS book,
       json_extract(s.locator, '$.chapter') AS chapter,
       json_extract(s.locator, '$.seq')     AS seq,
       s.text, s.license, s.source_id, s.evidence_url
FROM relations r
JOIN statements s ON s.statement_id = substr(r.to_ref, 11)   -- 'statement:' 是 10 字元
JOIN characters ch ON ch.char_id = substr(r.from_ref, 6)      -- 'char:' 是 5 字元
WHERE r.type = 'cited_in'
  AND s.source_id = 'classics';

-- ==================== 說文軸 ====================

-- 說文版本對照：同一字在不同本的釋文
-- 大徐本 / 段注本 / 小徐本系傳 —— 逐字對照，一行一個字的一個版本。
CREATE VIEW v_shuowen_edition AS
SELECT ch.head AS char, ch.char_id, ch.modern_codepoints,
       s.edition_id, e.name AS edition_name,
       s.text,
       -- B7（设计裁定 D53）：反切是**独立陈述**（kind=phonology，父指向本行），
       -- 不再读 `s.reading`（已停用）。
       (SELECT p.text FROM statements p
         WHERE p.kind = 'phonology' AND p.parent_statement_id = s.statement_id
         ORDER BY p.statement_id LIMIT 1) AS fanqie,
       json_extract(s.locator, '$.部')   AS radical,
       json_extract(s.locator, '$.卷')   AS juan,
       json_extract(s.locator, '$.编号') AS serial,
       s.source_id, s.license, s.evidence_url
FROM statements s
JOIN characters ch ON ch.char_id = s.char_id
LEFT JOIN editions e ON e.edition_id = s.edition_id
WHERE s.edition_id IN ('shuowen_da_xu', 'shuowen_duan_ben',
                       'shuowen_xu_kai_ben', 'shuowen_xu_shen',
                       'shuowen_xu_xuan', 'shuowen_xu_kai');

-- 版本差異：段注本 / 小徐本 與 大徐本 的逐條對照結果
CREATE VIEW v_shuowen_variant AS
SELECT ch.head AS char, ch.char_id,
       s1.edition_id AS variant_edition,
       s2.edition_id AS base_edition,
       r.kind AS variant_kind,
       substr(s1.text, 1, 160) AS variant_text,
       substr(s2.text, 1, 160) AS base_text,
       json_extract(s1.locator, '$.對照大徐.相似度') AS similarity,
       json_extract(s1.locator, '$.對照大徐.類型')   AS align_type
FROM relations r
JOIN statements s1 ON s1.statement_id = substr(r.from_ref, 11)
JOIN statements s2 ON s2.statement_id = substr(r.to_ref, 11)
JOIN characters ch ON ch.char_id = s1.char_id
WHERE r.type = 'edition_variant';

-- 說文形聲結構：字 → 构件（从X）/ 声符（X聲）
CREATE VIEW v_shuowen_structure AS
SELECT ch.head AS char, ch.char_id,
       cc.head AS component, cc.char_id AS component_id,
       r.type, r.kind,
       substr(s.text, 1, 120) AS source_text
FROM relations r
JOIN characters ch  ON ch.char_id  = substr(r.from_ref, 6)
JOIN characters cc  ON cc.char_id  = substr(r.to_ref, 6)
LEFT JOIN statements s ON s.statement_id = json_extract(r.statement_ids, '$[0]')
WHERE r.type IN ('component', 'phonetic_component');

-- 說文引書：說文原文與段注引了哪些書
--
-- 後半段的 aligned_* 是**引文 ↔ 引書原文對照層**（build_shuowen_xiaoxue_alignment）寫回的：
-- 引書名與層內書名可能不同（段注引《玉篇》→ 本庫的《重修玉篇》），
-- 所以兩個名字都留著；卷別未核 = 《爾雅》分卷但本層條目沒記卷次。
CREATE VIEW v_shuowen_cited_works AS
SELECT ch.head AS char, ch.char_id,
       json_extract(s.locator, '$.引書')   AS cited_book,
       json_extract(s.locator, '$.出處書') AS source_book,
       json_extract(s.locator, '$.對照小學書.書')         AS aligned_book,
       json_extract(s.locator, '$.對照小學書.引書名')     AS aligned_cited_book,
       json_extract(s.locator, '$.對照小學書.類型')       AS aligned_type,
       json_extract(s.locator, '$.對照小學書.相似度')     AS aligned_similarity,
       json_extract(s.locator, '$.對照小學書.加權分數')   AS aligned_weighted,
       json_extract(s.locator, '$.對照小學書.錨點')       AS aligned_anchor,
       json_extract(s.locator, '$.對照小學書.錨點長度')   AS aligned_anchor_len,
       json_extract(s.locator, '$.對照小學書.卷別未核')   AS volume_unverified,
       s.edition_id, substr(s.text, 1, 160) AS quote,
       s.source_id, s.license
FROM statements s
JOIN characters ch ON ch.char_id = s.char_id
WHERE s.kind = 'citation'
  AND json_extract(s.locator, '$.引書') IS NOT NULL;

-- 《類篇》引《說文》：《類篇》條目裏「說文…」那一段，與本庫《說文》原文的對照
--
-- **與 v_shuowen_variant 是兩件事**：那個是「段注本/小徐本 ↔ 大徐本」的版本差異，
-- 這個是「後出的字書引先出的字書」。所以單獨一視圖。
--
-- 類型與門檻見規格 D39：
--   引文一致 ≥0.95 / 引文節引 ≥0.60 / 引文改寫 ≥0.35 / 引文略異 ≥0.20（且錨點 ≥2）
CREATE VIEW v_leipian_cites_shuowen AS
SELECT ch.head AS char, ch.char_id,
       s.edition_id, substr(s.text, 1, 160) AS quote,
       json_extract(s.locator, '$.對照說文.類型')       AS align_type,
       json_extract(s.locator, '$.對照說文.相似度')     AS align_similarity,
       json_extract(s.locator, '$.對照說文.錨點')       AS align_anchor,
       json_extract(s.locator, '$.對照說文.錨點長度')   AS align_anchor_len,
       json_extract(s.locator, '$.對照說文.被引字頭')   AS align_target_head,
       json_extract(s.locator, '$.對照說文.被引陳述')   AS align_target_stmt,
       s.source_id, s.license
FROM statements s
JOIN characters ch ON ch.char_id = s.char_id
WHERE s.edition_id = 'leipian_cites_shuowen'
  AND json_extract(s.locator, '$.對照說文.被引陳述') IS NOT NULL;

-- 康熙部 → 說文部 的包含關係（1:N）
-- 每條帶字數：這個康熙部裏有多少字在《說文》裏屬該部。
CREATE VIEW v_radical_containment AS
SELECT json_extract(s.locator, '$.kangxi_no')    AS kangxi_no,
       json_extract(s.locator, '$.kangxi_radical') AS kangxi_radical,
       json_extract(s.locator, '$.shuowen_radical') AS shuowen_radical,
       json_extract(s.locator, '$.字數')          AS n_chars,
       s.text, s.license
FROM statements s
WHERE s.source_id = 'radical_map'
  AND json_extract(s.locator, '$.basis') = 'containment';

-- 康熙部 → 說文部 的包含關係（邊視圖，含雙向）
CREATE VIEW v_radical_contains AS
SELECT r.from_ref AS from_radical, r.to_ref AS to_radical, r.kind,
       r.statement_ids
FROM relations r
WHERE r.type = 'radical_contains';

-- 說文反切 ↔ 廣韻反切 的音韻互證
--
-- ⚠ **說文反切在「陳述」的 `reading` 裡，不在字頭記錄裡。**
--   說文原文陳述的 reading 形如「於悉切」；廣韻陳述的 reading 形如「於悉」。
--   而 `characters.pinyin` 裝的是**現代拼音**（一 → yī），**不是反切**——
--   早期這個視圖把 `ch.pinyin` 別名成 `shuowen_fanqie`，於是列名叫「反切」
--   卻顯示現代拼音，**是個實質錯誤**（下游據此比對音韻會全錯）。
--   修法：從說文原文陳述取 reading，並把現代拼音單獨一列 `modern_pinyin`。
--
-- 每個字可能有多條帶 reading 的說文原文陳述（實測 17 個字），
-- 這裡取 `statement_id` 最小的一條——**確定性**，不會讓視圖行數膨脹。
CREATE VIEW v_shuowen_fanqie AS
SELECT ch.head AS char, ch.char_id,
       r.kind AS compare_kind,
       sw.text AS shuowen_fanqie,             -- 「於悉切」（B7：反切是一條獨立陳述）
       replace(sw.text, '切', '') AS shuowen_cut,  -- 「於悉」（與廣韻同形）
       gy.text AS guangyun_fanqie,            -- 「於悉」（B7：值在 text，reading 欄已停用）
       ch.pinyin AS modern_pinyin,            -- 「yī」：現代拼音，**不是反切**
       json_extract(gy.locator, '$.音韻地位') AS yun_diwei
FROM relations r
JOIN characters ch ON ch.char_id = substr(r.from_ref, 6)
JOIN statements gy ON gy.statement_id = substr(r.to_ref, 11)
LEFT JOIN statements sw ON sw.statement_id = (
    -- B7（设计裁定 D53）：反切已從 statements.reading 立成**獨立陳述**，
    -- 取 kind=phonology 的 text。同一 (char, edition) 只立一條，
    -- 所以不必再「取最小 id」去重——但保留 MIN 让视图对脏数据也不膨胀。
    SELECT MIN(s2.statement_id) FROM statements s2
    WHERE s2.char_id = ch.char_id
      AND s2.edition_id = 'shuowen_xu_shen'
      AND s2.kind = 'phonology')
WHERE r.type = 'phonology'
  AND r.kind LIKE 'fanqie_%';

-- 說文反切互證匯總（按分類統計）
CREATE VIEW v_fanqie_summary AS
SELECT r.kind AS compare_kind, COUNT(*) AS n
FROM relations r
WHERE r.type = 'phonology' AND r.kind LIKE 'fanqie_%'
GROUP BY 1 ORDER BY 2 DESC;

-- ===========================================================================
-- 字單元 × 列 這一層（見規格 §4 D40）
--
-- 「一個字一行，來源是列」的心智模型在**消費層**的落法：
--   v_unit_columns —— 覆蓋矩陣：這個字單元有哪些列可用（哪些列是空的，一眼看出）
--   v_char_full    —— 字 × 列 寬表：按列展開的一條陳述一行
--   v_char_core    —— 同上，只取第一批（role='core'）的列
--   v_unit_lookup  —— **選字階段**：檢索詞 → 可達的字單元
--
-- ⚠ v_unit_lookup 用 `search_group`（**可達性**），只可用來「擴展檢索候選」，
--   **絕不可用於聚合說解**——否則「說/説/说」「協/恊/叶/葉」會被混成一鍋。
--   聚合請用 `v_char_full`（走 `canonical_id`，身份口徑）。規格 D20。
-- ===========================================================================

-- 覆蓋矩陣：單元 × 列
-- `anchor_observed` 是**派生**的：這一列實際上按什麼方式掛載
-- （self / canonical / reachability），由 unit_bindings 反算。
-- 舊版把「掛法」寫成**聲明**（anchor_policy），實測 101 列裡 79 列與實際不符——
-- 漂移的聲明等於沒有聲明。現在聲明只剩**真策略**（aggregation，二值），
-- 觀察值另存派生欄位，兩處不可能對不上。
--
-- **schema v3：從瘦身的 unit_columns 出發**（只含 n_statements）；
-- `kinds` / `source_ids` / `anchor_observed` 對 unit_bindings 逐列現算
-- （按單元 66 列 × 索引查找，實測 ~1–3 ms；不再全表 group by）。
CREATE VIEW v_unit_columns AS
SELECT u.unit_id, u.head AS unit_head, u.codepoints, u.has_shuowen,
       c.column_id, c.role, c.tier, c.bundle, c.work_id, c.axis, c.name AS column_name,
       c.provides, c.aggregation, c.license,
       uc.n_statements,
       (SELECT json_group_array(v) FROM (
            SELECT DISTINCT je.value AS v
              FROM unit_bindings b2, json_each(b2.kinds) je
             WHERE b2.unit_id = uc.unit_id AND b2.column_id = uc.column_id
             ORDER BY v)) AS kinds,
       (SELECT json_group_array(v) FROM (
            SELECT DISTINCT js.value AS v
              FROM unit_bindings b3, json_each(b3.source_ids) js
             WHERE b3.unit_id = uc.unit_id AND b3.column_id = uc.column_id
             ORDER BY v)) AS source_ids,
       (SELECT GROUP_CONCAT(DISTINCT b.method) FROM unit_bindings b
         WHERE b.unit_id = uc.unit_id AND b.column_id = uc.column_id) AS anchor_observed
FROM unit_columns uc
JOIN units u ON u.unit_id = uc.unit_id
JOIN columns c ON c.column_id = uc.column_id;

-- 字 × 列 寬表（一條陳述一行，按列展開）
-- ⚠ `locator` 原文 + 四個**抽取列**——下游要的往往不是整個 JSON，
-- 而是其中一個鍵（四刻本流水號 / 詩經篇名 / 廣韻小韻號 / 韻目）。
-- 直接塞 JSON 會逼每個消費者寫 json_extract；抽成列則「可排序、可索引」。
CREATE VIEW v_char_full AS
SELECT u.unit_id, u.head AS unit_head, u.codepoints,
       c.column_id, c.role, c.tier, c.bundle, c.work_id, c.axis, c.name AS column_name,
       c.provides, c.aggregation, b.method, b.char_id,
       s.statement_id, s.stable_id, s.kind, s.text, s.reading, s.radical,
       s.parent_statement_id, s.evidence_url, s.license, s.source_id, s.confidence,
       s.locator,
       json_extract(s.locator, '$.serial')    AS serial,
       json_extract(s.locator, '$.chapter')   AS chapter,
       json_extract(s.locator, '$."小韻號"')  AS rhyme_class,
       json_extract(s.locator, '$."韻目"')    AS rhyme_head
FROM unit_bindings b
JOIN units u ON u.unit_id = b.unit_id
JOIN columns c ON c.column_id = b.column_id
JOIN statements s ON s.char_id = b.char_id AND s.edition_id = b.column_id;

-- 便捷視圖：**邊**帶上穩定 id（規格 D45）
-- 邊表裡的 `statement:S…` 是**內部行號**——全量重建就會變。
-- 想引用某一條陳述，要用 `stable_id`（內容地址，重建不變）。
-- 這個視圖把兩邊的穩定 id 一起帶出來，下游不必自己去 join。
--
-- ⚠ **視圖不是索引**：全表掃描會做兩次 `statements` 的 join（2,370,396 行 × 2），
--   慢；按 `from_stable_id` / `to_stable_id` 查請自己 join `statements`（有唯一索引）。
CREATE VIEW v_edge_stable AS
SELECT r.relation_id, r.type, r.kind,
       r.from_ref, r.to_ref,
       fs.stable_id AS from_stable_id,
       ts.stable_id AS to_stable_id
FROM relations r
LEFT JOIN statements fs ON fs.statement_id = substr(r.from_ref, 11)
LEFT JOIN statements ts ON ts.statement_id = substr(r.to_ref, 11);

-- 第一批（core 列）：說文系 + 康熙 + 韻書/字書 + 蒙學
CREATE VIEW v_char_core AS
SELECT * FROM v_char_full WHERE role = 'core';

-- **默認視圖（设计裁定 D54）**：`tier='base'` 的四列。
--
-- 「一個字一個默認視圖」——把庫裡所有列一次全給，等於什麼都沒說；
-- 這裡只給 base 四列（許慎原文 / 段注 /《爾雅》/《康熙》），其餘按 tier 分檔展開。
--
-- ⚠ base 層恰好四列由 R26 強制（`units.BASE_COLUMNS` 是唯一事實源），
--   別在這裡寫死列名——寫死就成第二份事實源。
CREATE VIEW v_char_base AS
SELECT * FROM v_char_full WHERE tier = 'base';

-- 選字階段：檢索詞 → 可達的字單元（**僅檢索候選**）
-- ⚠ 查詢端與單元端都要去重：同一個檢索詞在多個 work 下各有字頭行，
--   同一 search_group 裡也可能有多個字頭行落在同一個單元——不去重會重複數十行。
CREATE VIEW v_unit_lookup AS
SELECT DISTINCT query_head, unit_id, unit_head, codepoints, has_shuowen, n_members, works
FROM (
    SELECT q.head AS query_head,
           u.unit_id AS unit_id, u.head AS unit_head, u.codepoints AS codepoints,
           u.has_shuowen AS has_shuowen, u.n_members AS n_members, u.works AS works
    FROM (SELECT DISTINCT head, search_group FROM characters) q
    JOIN characters t ON t.search_group = q.search_group
    JOIN units u ON u.unit_id = t.canonical_id
);

-- 單元一覽（每個字單元一行——這就是「一行一個字」）
CREATE VIEW v_units AS
SELECT u.unit_id, u.head, u.codepoints, u.primary_char_id, u.primary_work,
       u.n_members, u.works, u.has_shuowen,
       (SELECT COUNT(*) FROM unit_columns uc
         WHERE uc.unit_id = u.unit_id) AS n_columns,
       (SELECT COUNT(*) FROM unit_columns uc
          JOIN columns c ON c.column_id = uc.column_id
         WHERE uc.unit_id = u.unit_id AND c.role = 'core') AS n_core_columns
FROM units u;

-- ==================== 遷移台賬 ====================

-- 哪些一次性遷移已經應用到 data/。
-- 資料來自 data/migrations.jsonl（臺賬由一次性遷移腳本寫，未隨 v1 發布），
-- 每一條都帶一個可復跑的探針（對著 data/ 查當前狀態）。
CREATE TABLE schema_migrations (
    migration_id  TEXT PRIMARY KEY,   -- 遷移 id（M001_shards …）
    script        TEXT,               -- 對應的遷移腳本
    title         TEXT,               -- 一句話說明
    applied_at    TEXT,               -- 應用時間（UTC）
    rows_affected INTEGER,
    evidence      TEXT,               -- 探針實測結果
    note          TEXT
);
"""


def read_jsonl(name: str) -> list[dict]:
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def read_statements() -> list[dict]:
    """读全部陈述。

    陈述只存**逐源分文件**（data/provenance/<source_id>.jsonl），
    不再有全量副本 data/statements.jsonl——那样会重复一份 164 MB，
    且让「某来源授权状态变化时整块移除」变得做不到。
    """
    prov = os.path.join(DATA, "provenance")
    out: list[dict] = []
    if not os.path.isdir(prov):
        return out
    # provenance 的每個來源是一個**目錄**（內含 part-*.jsonl 分片）。
    # read_jsonl_any 兩種形態都吃，所以讀法不必關心某來源目前是哪一種。
    for name in sorted(os.listdir(prov)):
        path = os.path.join(prov, name)
        if os.path.isdir(path):
            out.extend(read_jsonl_any(path))
        elif name.endswith(".jsonl"):
            out.extend(read_jsonl(path))
    return out


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


#: 單個視圖 `COUNT(*)` 的預算（秒）。超過就是「全表用不了」，不是「慢一點」。
VIEW_COUNT_BUDGET_S = 5.0


def verify_views(con: sqlite3.Connection,
                 budget_s: float = VIEW_COUNT_BUDGET_S) -> list[str]:
    """逐個視圖驗「能不能查」，回傳問題清單（空 = 全過）。

    三層檢查，缺一層就漏一種壞法：
    1. **能不能查**——SQLite 建視圖時不檢查列是否存在，「建成功」證明不了能用。
       實測踩過：`v_reachable_shuowen` 引用了瘦身時刪掉的 `characters.radical`，
       視圖建得好好的，一查就 `no such column`，而沒有任何校驗器查視圖。
    2. **能不能返回行**——`LIMIT 1` 只證明不報錯。實測踩過：`v_rhyme_group`
       的 `substr(...,17)` 差了偏移一位（應為 18），join 永遠不中 →
       **一條都查不到**，而舊冒煙測只做 `LIMIT 1`，所以它一路綠著發了出去。
    3. **能不能掃全表**——`COUNT(*)` 有沒有在預算內跑完。實測踩過：
       `v_char_radical` 用 `'char:'||char_id` 拼字串 join 2.2M 行的邊視圖，
       `COUNT(*)` 跑 10 分鐘不返回（按 id 過濾卻只要 1 ms——正好掩蓋了這一點）。

    這一函數被單體庫與 6 個 bundle 共用：**派發件與源件用同一把尺子**。
    """
    problems: list[str] = []
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")]
    for vn in names:
        try:
            con.execute("SELECT * FROM %s LIMIT 1" % vn).fetchall()
        except sqlite3.OperationalError as exc:
            problems.append("%s：查不了（%s）" % (vn, exc))
            continue
        t0 = time.time()
        try:
            con.execute("SELECT COUNT(*) FROM %s" % vn).fetchone()
        except sqlite3.OperationalError as exc:
            problems.append("%s：COUNT 失败（%s）" % (vn, exc))
            continue
        dt = time.time() - t0
        if dt > budget_s:
            problems.append("%s：COUNT 用了 %.1fs（> %.0fs，等於全表不可用）"
                            % (vn, dt, budget_s))
    return problems


def main() -> int:
    # 中文 Windows 的默认控制台是 GBK：结尾要打印带「⚠」的 build.json，
    # 直接 print 会 UnicodeEncodeError 并以退出码 1 结束（其实 db 已经写好）。
    # 只把控制台编不出的字符替换掉，**不改文件内容**。
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="hanzi-relations.db")
    ap.add_argument("--hash-out", default="build.json")
    args = ap.parse_args()

    out_path = os.path.join(ROOT, args.out)
    if os.path.exists(out_path):
        # Windows 上别的进程（浏览页 / DB 工具 / 另一个 sqlite 连接）开着这个文件时，
        # os.remove 直接抛 PermissionError——堆栈对使用者没有意义。
        # 退避重试；仍不行就给出**能照着做**的提示。
        last: Exception | None = None
        for attempt in range(8):
            try:
                os.remove(out_path)
                break
            except PermissionError as exc:      # WinError 32
                last = exc
                time.sleep(0.5 * (attempt + 1))
        else:
            print(f"!! 无法删除 {out_path}：{last}")
            print("   多半是它正被别的程序打开（浏览页 / SQLite 工具 / 另一个连接）。")
            print("   关掉那些程序再跑一次；或用 --out 指定另一个文件名。")
            return 1

    con = sqlite3.connect(out_path)
    try:
        con.executescript(SCHEMA)

        # **schema 版本**（規格 §4 D44）：`PRAGMA user_version` 記的是
        # SQLite 的**物理結構**版本（表/列/索引的形狀），不是資料的內容。
        # 下游拿著一個庫，先看 user_version 才知道能不能用這個 schema 查。
        #
        # ⚠ 實測坑：任何碰過 schema 的操作（建表/刪表/VACUUM）都會推進
        #   SQLite 檔頭裡的 `schema_version`，於是「行數、內容全一樣，
        #   sha256 卻變了」——「同輸入必得同 sha256」只在**一次性重建**時成立。
        #   這就是為什麼資料的完整性要靠 MANIFEST.json（逐檔案 sha256），
        #   而不是只靠 DB 的一個 sha256。
        con.execute(f"PRAGMA user_version = {int(schema_mod.SQLITE_SCHEMA_VERSION)}")

        # 遷移台賬：哪些一次性遷移已經應用到 data/（腳本未隨 v1 發布）
        mig_rows = read_jsonl_any(os.path.join(DATA, "migrations.jsonl"))
        if mig_rows:
            con.executemany(
                "INSERT OR REPLACE INTO schema_migrations "
                "(migration_id, script, title, applied_at, rows_affected, evidence, note) "
                "VALUES (?,?,?,?,?,?,?)",
                [(r.get("migration_id"), r.get("script"), r.get("title"),
                  r.get("applied_at"), r.get("rows_affected"),
                  r.get("evidence"), r.get("note"))
                 for r in mig_rows])

        chars = sorted(read_jsonl_dir(os.path.join(DATA, "characters")), key=lambda r: r["char_id"])
        con.executemany(
            "INSERT INTO characters VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(r.get("char_id"), r.get("canonical_id"), r.get("search_group"),
              r.get("head"), r.get("work_id"), r.get("upstream_id"),
              r.get("volume"),
              json.dumps(r.get("modern_codepoints") or [], ensure_ascii=False),
              r.get("pinyin"), r.get("mpinyin"),
              json.dumps(r.get("source_ids") or [], ensure_ascii=False))
             for r in chars])

        stmts = sorted(read_statements(), key=lambda r: r["statement_id"])
        con.executemany(
            "INSERT INTO statements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(i, r.get("statement_id"), r.get("stable_id"), r.get("kind"), r.get("char_id"), r.get("work_id"),
              r.get("edition_id"), r.get("text"), r.get("reading"), r.get("radical"),
              r.get("parent_statement_id"),
              json.dumps(r.get("locator") or {}, ensure_ascii=False),
              r.get("evidence_url"), r.get("license"), r.get("source_id"),
              r.get("confidence"), r.get("summary_method"), r.get("reviewer"),
              r.get("review_date"))
             for i, r in enumerate(stmts, 1)])

        # ---- 壓縮倒排索引（規格 D47）----
        # **rid 必須與插入順序一致**：statements 按 statement_id 排序後插入，
        # rid = 1..N（枚舉序號），倒排表裡的 posting 就是這個 rid。
        # 索引只覆蓋 `text`（正文）——「查某句釋文」是主要用途。
        uni, bi = inverted_mod.build_postings([r.get("text") or "" for r in stmts])
        con.executemany(
            "INSERT INTO inverted_unigram VALUES (?,?)",
            [(t, inverted_mod.encode_deltas(list(uni[t]))) for t in sorted(uni)])
        con.executemany(
            "INSERT INTO inverted_bigram VALUES (?,?)",
            [(t, inverted_mod.encode_deltas(list(bi[t]))) for t in sorted(bi)])
        inv_stats = {
            "unigram_terms": len(uni),
            "bigram_terms": len(bi),
            "unigram_postings": sum(len(a) for a in uni.values()),
            "bigram_postings": sum(len(a) for a in bi.values()),
        }
        print("  倒排索引：單字 %d 詞 / %d posting；二字組 %d 詞 / %d posting"
              % (inv_stats["unigram_terms"], inv_stats["unigram_postings"],
                 inv_stats["bigram_terms"], inv_stats["bigram_postings"]))

        rels = sorted(read_jsonl_dir(os.path.join(DATA, "relations")), key=lambda r: r["relation_id"])
        # 端點去重 → refs（整數 id）；relation_edges 只存 id。
        # 這是 schema v3 的存儲瘦身：from_ref/to_ref 由 31 位元組字串變成 4 位元組整數，
        # 兩個字串索引（~126 MB）也跟著變成整數索引（規格 D50）。
        ref_ids: dict[str, int] = {}
        edge_rows = []
        for r in rels:
            frm, to = r.get("from"), r.get("to")
            fid = ref_ids.get(frm)
            if fid is None:
                fid = ref_ids[frm] = len(ref_ids) + 1
            tid = ref_ids.get(to)
            if tid is None:
                tid = ref_ids[to] = len(ref_ids) + 1
            edge_rows.append((r.get("relation_id"), r.get("type"), fid, tid,
                              r.get("kind"),
                              json.dumps(r.get("statement_ids") or [], ensure_ascii=False)))
        con.executemany(
            "INSERT INTO refs VALUES (?,?)",
            sorted(((i, ref) for ref, i in ref_ids.items()), key=lambda x: x[0]))
        con.executemany(
            "INSERT INTO relation_edges VALUES (?,?,?,?,?,?)", edge_rows)
        print("  關係：%d 條邊 / %d 個去重端點（refs）" % (len(edge_rows), len(ref_ids)))

        srcs = sorted(read_jsonl("sources.jsonl"), key=lambda r: r["source_id"])
        con.executemany(
            "INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(r.get("source_id"), r.get("name"), r.get("url"), r.get("kind"),
              r.get("license"), 1 if r.get("redistributable") else 0,
              r.get("retrieved_at"), r.get("version"), r.get("attribution"),
              r.get("notes"))
             for r in srcs])

        rads = sorted(read_jsonl("radicals.jsonl"),
                      key=lambda r: (r.get("system", ""), str(r.get("index", 0)),
                                     r.get("radical", "")))
        con.executemany(
            'INSERT OR REPLACE INTO radicals '
            '(system, radical, "index", char, strokes, source_id, license, evidence_url, note) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            [(r.get("system"), r.get("radical"), r.get("index"), r.get("char"),
              r.get("strokes"), r.get("source_id"), r.get("license"),
              r.get("evidence_url"), r.get("note"))
             for r in rads])

        rhymes = sorted(read_jsonl("rhyme_groups.jsonl"),
                        key=lambda r: (r.get("system", ""), str(r.get("group_id", ""))))
        con.executemany(
            'INSERT OR REPLACE INTO rhyme_groups '
            '(system, group_id, rhyme, rhyme_class, fanqie, xiao_index, '
            ' char_count, xiao_count, tone_classes, source_id, license, evidence_url) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            [(r.get("system"), r.get("group_id"), r.get("rhyme"), r.get("rhyme_class"),
              r.get("fanqie"), r.get("xiao_index"), r.get("char_count"),
              r.get("xiao_count"), r.get("tone_classes"), r.get("source_id"),
              r.get("license"), r.get("evidence_url"))
             for r in rhymes])

        eds = sorted(read_jsonl("editions.jsonl"), key=lambda r: r["edition_id"])
        con.executemany(
            "INSERT INTO editions VALUES (?,?,?,?,?)",
            [(r.get("edition_id"), r.get("work_id"), r.get("axis"), r.get("name"),
              r.get("note"))
             for r in eds])

        # ------------------------------------------------------------------
        # 讀音登記表（規格 §4 D42）
        # ------------------------------------------------------------------
        # 許可由「該 edition 的陳述的 source_id → sources.jsonl」反查（與列登記表同一口徑），
        # **不在讀音登記表裡另存一份許可**。
        src_lic = {s["source_id"]: s.get("license") for s in srcs}
        ed_srcs: dict[str, set[str]] = {}
        for s in stmts:
            if s.get("edition_id") and s.get("source_id"):
                ed_srcs.setdefault(s["edition_id"], set()).add(s["source_id"])
        rds = sorted(read_jsonl("readings.jsonl"), key=lambda r: r["reading_id"])
        con.executemany(
            "INSERT OR REPLACE INTO readings "
            "(reading_id, edition_id, value, slug, value_kind, normalized, tone, "
            " locations, license, derived_from, transform) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(r.get("reading_id"), r.get("edition_id"), r.get("value"),
              r.get("slug"), r.get("value_kind"), r.get("normalized"), r.get("tone"),
              json.dumps(r.get("locations") or [], ensure_ascii=False),
              src_lic.get(sorted(ed_srcs.get(r.get("edition_id"), ()))[0])
              if len(ed_srcs.get(r.get("edition_id"), ())) == 1 else None,
              r.get("derived_from"), r.get("transform"))
             for r in rds])

        # ------------------------------------------------------------------
        # 構形值域登記表（規格 §4 D43）
        # ------------------------------------------------------------------
        # `namespace` 是**來源 id**（key 是上游自己的 key），
        # 對應的版本形態由 `work_id == namespace` 查出來，许可再 join 出去。
        sts = sorted(read_jsonl("structures.jsonl"),
                     key=lambda r: r.get("structure_id", ""))
        ns_ed: dict[str, str] = {}
        for e in eds:
            ns_ed.setdefault(e.get("work_id") or "", e.get("edition_id") or "")
        con.executemany(
            "INSERT OR REPLACE INTO structures "
            "(structure_id, namespace, edition_id, key, value, value_kind, "
            " derived_from, transform) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(r.get("structure_id"), r.get("namespace"),
              ns_ed.get(r.get("namespace") or ""),
              r.get("key"), r.get("value"), r.get("value_kind"),
              r.get("derived_from"), r.get("transform"))
             for r in sts])

        # ------------------------------------------------------------------
        # 列登記表 + 字單元 / 掛載層（規格 §4 D40）
        # ------------------------------------------------------------------
        col_specs = units_mod.load_column_specs()
        problems = units_mod.validate_column_specs(col_specs)
        if problems:
            for p in problems:
                print(f"⚠ 列登記表問題：{p}", file=sys.stderr)

        ed_by_id = {e["edition_id"]: e for e in eds}
        # 每一列的許可與出處：由該列陳述的 source_id 反查（不另設事實源）
        src_lic = {s["source_id"]: s.get("license") for s in srcs}
        col_srcs: dict[str, set[str]] = {}
        col_kinds: dict[str, set[str]] = {}
        for s in stmts:
            if s.get("edition_id") and s.get("source_id"):
                col_srcs.setdefault(s["edition_id"], set()).add(s["source_id"])
            if s.get("edition_id") and s.get("kind"):
                col_kinds.setdefault(s["edition_id"], set()).add(s["kind"])

        # R13：列聲明的 provides 必須覆蓋該列實際出現的陳述 kind
        for p in units_mod.check_column_kinds(col_specs, col_kinds):
            print(f"⚠ {p}", file=sys.stderr)

        col_rows = []
        for spec in col_specs:
            cid = spec["column_id"]
            ed = ed_by_id.get(cid, {})
            src_ids = sorted(col_srcs.get(cid, ()))
            lic = src_lic.get(src_ids[0]) if len(src_ids) == 1 else None
            col_rows.append({
                "column_id": cid,
                "role": spec.get("role"),
                "provides": spec.get("provides") or [],
                "aggregation": spec.get("aggregation"),
                "tier": spec.get("tier"),
                "bundle": spec.get("bundle"),
                "work_id": ed.get("work_id"),
                "axis": ed.get("axis"),
                "name": ed.get("name"),
                "license": lic,
                "note": spec.get("note"),
            })
        con.executemany(
            "INSERT OR REPLACE INTO columns VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(r["column_id"], r["role"],
              json.dumps(r["provides"], ensure_ascii=False), r["aggregation"],
              r["tier"], r["bundle"],
              r["work_id"], r["axis"], r["name"], r["license"], r["note"])
             for r in col_rows])

        unit_rows, member_rows = units_mod.build_units(chars)
        binding_rows, coverage_rows = units_mod.build_bindings(chars, stmts, col_specs)
        con.executemany(
            "INSERT OR REPLACE INTO units VALUES (?,?,?,?,?,?,?,?)",
            [(r["unit_id"], r["head"], r["primary_char_id"], r["primary_work"],
              json.dumps(r["codepoints"], ensure_ascii=False), r["n_members"],
              json.dumps(r["works"], ensure_ascii=False), 1 if r["has_shuowen"] else 0)
             for r in unit_rows])
        con.executemany(
            "INSERT OR REPLACE INTO unit_members VALUES (?,?,?,?,?)",
            [(r["unit_id"], r["char_id"], r["work_id"], r["head"], r["is_primary"])
             for r in member_rows])
        con.executemany(
            "INSERT OR REPLACE INTO unit_bindings VALUES (?,?,?,?,?,?,?)",
            [(r["unit_id"], r["column_id"], r["char_id"], r["method"], r["n_statements"],
              json.dumps(r["kinds"], ensure_ascii=False),
              json.dumps(r["source_ids"], ensure_ascii=False))
             for r in binding_rows])
        # schema v3：覆蓋矩陣只留瘦身版 unit_columns（unit_id, column_id, n_statements）；
        # kinds / source_ids 由 v_unit_columns 現算，不再物化。
        con.executemany(
            "INSERT INTO unit_columns VALUES (?,?,?)",
            [(r["unit_id"], r["column_id"], r["n_statements"])
             for r in coverage_rows])

        con.commit()
        con.execute("VACUUM")
        con.commit()

        # ------------------------------------------------------------------
        # **視圖冒煙測**：每個視圖都要能查、且**真能掃得動**。
        # 見模組級 verify_views()（單體庫與 6 個 bundle 共用同一份判據）。
        # ------------------------------------------------------------------
        problems = verify_views(con)
        view_names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")]
        if problems:
            for msg in problems:
                print("!! 視圖 " + msg, file=sys.stderr)
            print("!! %d/%d 個視圖有問題——派發件不能帶壞視圖。"
                  % (len(problems), len(view_names)), file=sys.stderr)
            return 1
        print("視圖冒煙測：%d 個視圖全部可查、COUNT 均在 %ds 內"
              % (len(view_names), VIEW_COUNT_BUDGET_S))
    finally:
        con.close()

    sha = sha256_file(out_path)
    size = os.path.getsize(out_path)
    manifest = {
        "database": args.out,
        "bytes": size,
        "sha256": sha,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "sqlite_version": sqlite3.sqlite_version,
        "schema_version": schema_mod.SCHEMA_VERSION,
        "sqlite_schema_version": schema_mod.SQLITE_SCHEMA_VERSION,
        "row_counts": {
            "characters": len(chars), "statements": len(stmts),
            "relations": len(rels), "sources": len(srcs),
            "radicals": len(rads), "editions": len(eds),
            "columns": len(col_rows),
            "structures": len(sts),
            "readings": len(rds),
            "units": len(unit_rows), "unit_members": len(member_rows),
            "unit_bindings": len(binding_rows), "unit_columns": len(coverage_rows),
            "refs": len(ref_ids), "relation_edges": len(edge_rows),
            "schema_migrations": len(mig_rows),
            "inverted_unigram_terms": inv_stats["unigram_terms"],
            "inverted_bigram_terms": inv_stats["bigram_terms"],
        },
        "licenses": sorted({r.get("license") for r in stmts if r.get("license")}),
        "note": ("本文件是 SQLite 构建产物的校验清单。JSONL 才是事实源；"
                 "本 db 由 src/build_sqlite.py 从 data/*.jsonl 确定性构建，"
                 "同输入必得同 sha256（VACUUM 后）。"
                 "⚠ 但这只在「一次性重建」时成立：任何碰过 schema 的操作"
                 "（建表/删表/VACUUM）都会推进 SQLite 檔頭的 schema_version，"
                 "行数与内容全一样而 sha256 变了。数据的完整性请以 MANIFEST.json"
                 "（逐文件 sha256）为准。"),
    }
    # **原子寫**（硬規則 12）：先寫 `.tmp` 再 `os.replace`。
    # 這個檔案正是下游校驗 sha256 的清單——寫到一半被打斷會留下
    # **截斷的 JSON**，下游拿到半截清單還會以為自己下的是好包。
    manifest_path = os.path.join(ROOT, args.hash_out)
    tmp_path = manifest_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, manifest_path)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
