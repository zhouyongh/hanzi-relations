"""按 bundle 把单体 DB 拆成可装卸的列库（v1 只有 base）。

## 为什么要有这个脚本

「加一列就多一个库文件，勾上再合并查」这条设计要求**物理上可装卸**。
v1 只有一捆：

- `db/bundle-base.db` —— 骨架 + **base 四列**（說文原文 / 段注 / 爾雅 / 康熙），
  这是默认下载件。

`shuowen` / `xiaoxue` / `classics` / `unihan` / `misc` 五个名字留给扩展包；
v1 没有列落进去，**默认不建**（建了也是空库）。要建别的捆用 `--only`。

合并查询由 `src/query_bundles.py` 用 `ATTACH` + `TEMP VIEW` 做，
**不需要先合并成一个大库**。

## 每个 bundle 里放什么

| 表 | 放什么 | 为什么 |
|---|---|---|
| `columns` | 只有本 bundle 的列 | 列登记跟着列走 |
| `statements` | 本 bundle 各列的陈述，**保留全局 `rid`** | 合并查时 rid 必须一致（否则边对不上） |
| `refs` | 本 bundle 的字头 / 陈述 / 它们指向的实体 | 边要能指向端点 |
| `relation_edges` | **有一端在本库**的边（跨 bundle 的边也留） | 只留「两端都在」会整族丢掉跨库边 |
| `unit_bindings` / `unit_columns` | 本 bundle 的列 | 覆盖矩阵按列切 |
| `characters` / `units` / `unit_members` | **只装本 bundle 涉及的字头** | 全量骨架是纯负担：实测会让包大出数倍 |
| `readings` / `structures` / `radicals` / `sources` / `editions` / `schema_migrations` | 全量 | 小表；合并不需要它们参与切分 |

⚠ **不复制倒排索引**（`inverted_*`）：它是对**全库正文**建的，
只对某个 bundle 建一份会给出「查得到这个词、但缺一半结果」的索引——
比没有索引更糟。单库（`hanzi-relations.db`）里才有它。

## 用法

```bash
python src/build_bundles.py                 # v1 只建 base → db/
python src/build_bundles.py --only base     # 同上（显式）
python src/build_bundles.py --check         # 只核对：bundle 行数之和 == 单体库
```
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from units import COLUMN_BUNDLES  # noqa: E402
from build_sqlite import verify_views  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "hanzi-relations.db")
OUTDIR = os.path.join(ROOT, "db")

#: 每个 bundle 都要**全量**复制的骨架表（行身份必须一致）。
#: `rhyme_groups` 只有 4,090 行（< 1 MiB），但 `v_rhyme_group` / `v_rhyme_members`
#: 两个视图要 join 它——不放进来，bundle 里这两个视图一查就 `no such table`。
SKELETON = ("characters", "units", "unit_members", "radicals", "sources",
            "editions", "readings", "structures", "rhyme_groups", "schema_migrations")

COLUMN_SCOPED = ("columns", "statements", "refs", "relation_edges",
                 "unit_bindings", "unit_columns")


def table_ddl(con: sqlite3.Connection, name: str) -> str:
    """从**已 ATTACH 为 src** 的单体库取建表语句（照着建，形状一致）。"""
    row = con.execute("SELECT sql FROM src.sqlite_master WHERE name=? AND type='table'",
                      (name,)).fetchone()
    if not row or not row[0]:
        raise SystemExit(f"!! 单体库里没有表 {name}")
    return row[0]


def build_one(name: str) -> dict:
    out_path = os.path.join(OUTDIR, f"bundle-{name}.db")
    if os.path.exists(out_path):
        os.remove(out_path)
    con = sqlite3.connect(out_path)
    try:
        con.execute(f"ATTACH DATABASE ? AS src", (DB,))
        cur = con.cursor()
        cols = [r[0] for r in cur.execute(
            "SELECT column_id FROM src.columns WHERE bundle=?", (name,))]
        if not cols:
            print(f"!! bundle {name} 没有任何列（取值域 {COLUMN_BUNDLES}）")
            return {}
        q = ",".join("?" * len(cols))

        # ---- 这一捆涉及的字头 ----
        # 只装「本 bundle 的陈述挂到的字头」+「挂载矩阵里出现的字头」——
        # 全量 33 万字头对 base 是纯负担（实测全量骨架让 base 从 100 MiB 涨到 550 MiB）。
        con.execute("CREATE TEMP TABLE b_chars (char_id TEXT PRIMARY KEY)")
        con.executemany("INSERT OR IGNORE INTO b_chars "
                        "SELECT DISTINCT char_id FROM src.statements "
                        "WHERE edition_id=? AND char_id IS NOT NULL", [(c,) for c in cols])
        con.executemany("INSERT OR IGNORE INTO b_chars "
                        "SELECT DISTINCT char_id FROM src.unit_bindings "
                        "WHERE column_id=?", [(c,) for c in cols])
        if name == "base":
            # 一条陈述都没有的字头：它们不属于任何一捆，但可能带着
            # 字形指针（`glyph` 边）——不放进 base，合并查里就永远查不到。
            # base 是「骨架 + 默认四列」那一包，每次查询都会 ATTACH。
            con.execute("INSERT OR IGNORE INTO b_chars "
                        "SELECT char_id FROM src.characters "
                        "WHERE char_id NOT IN (SELECT char_id FROM src.statements)")

        # ---- 骨架（只装本 bundle 的字头）----
        con.execute(table_ddl(con, "characters"))
        con.execute("INSERT INTO characters SELECT * FROM src.characters "
                    "WHERE char_id IN (SELECT char_id FROM b_chars)")
        con.execute(table_ddl(con, "units"))
        con.execute("INSERT INTO units SELECT * FROM src.units WHERE unit_id IN "
                    "(SELECT DISTINCT canonical_id FROM src.characters "
                    " WHERE char_id IN (SELECT char_id FROM b_chars))")
        con.execute(table_ddl(con, "unit_members"))
        con.execute("INSERT INTO unit_members SELECT * FROM src.unit_members "
                    "WHERE char_id IN (SELECT char_id FROM b_chars)")

        # ---- 小表全量 ----
        for t in ("radicals", "sources", "editions", "readings", "structures",
                  "rhyme_groups", "schema_migrations"):
            con.execute(table_ddl(con, t))
            con.execute(f"INSERT INTO {t} SELECT * FROM src.{t}")

        # ---- 列登记 ----
        con.execute(table_ddl(con, "columns"))
        con.executemany("INSERT INTO columns SELECT * FROM src.columns "
                        "WHERE column_id=?", [(c,) for c in cols])

        # ---- 陈述（rid / statement_id 原样保留，合并查要靠它对齐边）----
        con.execute(table_ddl(con, "statements"))
        con.execute(f"INSERT INTO statements SELECT * FROM src.statements "
                    f"WHERE edition_id IN ({q})", cols)

        # ---- 引用 ----
        # ① 本 bundle 的字头 ② 本 bundle 的陈述 ③ 上面两者指向的实体
        #    （work / edition / reading / structure / glyph / radical）
        con.execute(table_ddl(con, "refs"))
        con.execute("INSERT INTO refs SELECT * FROM src.refs WHERE ref IN "
                    "(SELECT 'char:'||char_id FROM b_chars)")
        con.execute("INSERT INTO refs SELECT * FROM src.refs WHERE ref LIKE "
                    "'statement:%' AND substr(ref, 11) IN "
                    "(SELECT statement_id FROM statements)")

        # ---- 边 ----
        # 只要**有一端在本库**就留下（跨 bundle 的那几族边：commentary_on 545 /
        # quotes 8,758 / edition_variant 10,621——按「两端都在」筛会整族丢掉，
        # 而单库查不出问题、合并查永远看不到）。
        #
        # 另有**两端都是实体**的边（与某个字头无关，只挂在部首层上）：
        # 这类边**只在 base 库**里存一份——base 必然随每次查询加载，
        # 放进每个 bundle 只是重复。
        con.execute(table_ddl(con, "relation_edges"))
        con.execute("CREATE TEMP TABLE keep_edges AS "
                    "SELECT * FROM src.relation_edges "
                    "WHERE from_id IN (SELECT ref_id FROM refs) "
                    "   OR to_id   IN (SELECT ref_id FROM refs)")
        if name == "base":
            con.execute("INSERT INTO keep_edges "
                        "SELECT * FROM src.relation_edges WHERE type IN "
                        "('radical_contains','radical_owner')")
        con.execute("INSERT INTO refs SELECT * FROM src.refs "
                    "WHERE ref_id IN (SELECT from_id FROM keep_edges "
                    "                 UNION SELECT to_id FROM keep_edges) "
                    "  AND ref_id NOT IN (SELECT ref_id FROM refs)")
        con.execute("INSERT INTO relation_edges SELECT * FROM keep_edges")
        con.execute("DROP TABLE keep_edges")

        # ---- 覆盖矩阵 / 挂载（本 bundle 的列）----
        con.execute(table_ddl(con, "unit_bindings"))
        con.execute(f"INSERT INTO unit_bindings SELECT * FROM src.unit_bindings "
                    f"WHERE column_id IN ({q})", cols)
        con.execute(table_ddl(con, "unit_columns"))
        con.execute(f"INSERT INTO unit_columns SELECT * FROM src.unit_columns "
                    f"WHERE column_id IN ({q})", cols)

        # 索引：合并查的 join 键
        con.executescript("""
CREATE UNIQUE INDEX idx_b_refs_id   ON refs(ref_id);
CREATE UNIQUE INDEX idx_b_refs_ref  ON refs(ref);
CREATE INDEX idx_b_stmt_char   ON statements(char_id);
CREATE INDEX idx_b_stmt_edit   ON statements(edition_id);
CREATE INDEX idx_b_stmt_cedit  ON statements(char_id, edition_id);
CREATE INDEX idx_b_stmt_par    ON statements(parent_statement_id);
CREATE INDEX idx_b_edge_from   ON relation_edges(from_id);
CREATE INDEX idx_b_edge_to     ON relation_edges(to_id);
CREATE INDEX idx_b_edge_type   ON relation_edges(type);
CREATE INDEX idx_b_ub_unit     ON unit_bindings(unit_id, column_id);
CREATE INDEX idx_b_uc_unit     ON unit_columns(unit_id);
CREATE INDEX idx_b_uc_col      ON unit_columns(column_id);
""")

        # ---- 视图：照单体库的实现复制一份（不另写一份 SQL）----
        # ⚠ 为什么 bundle 也必须带视图：README 承诺「先下 `bundle-base.db` 就够用」，
        #   而 2026-11-01 实测早期 bundle **一个视图都没有** → 用户照 README 敲
        #   `SELECT * FROM v_char_base` 直接 `no such table`。默认下载件是用户
        #   第一分钟接触的东西，它没有视图，等于「试试看」那一步就断了。
        #   视图 SQL 全部从单体库的 `sqlite_master` 取（**两份 SQL 必然漂移**）；
        #   每个视图只管本库的行——跨 bundle 的合并仍由 `query_bundles.py` 的 TEMP VIEW 做。
        for (view_sql,) in con.execute(
                "SELECT sql FROM src.sqlite_master WHERE type='view' ORDER BY name"):
            con.execute(view_sql)
        problems = verify_views(con)
        if problems:
            for msg in problems:
                print(f"!! bundle {name} 的视图 {msg}", file=sys.stderr)
            print(f"!! {len(problems)} 个视图有问题——派发件不能带坏视图。",
                  file=sys.stderr)
            raise SystemExit(1)
        con.commit()
        counts = {}
        for t in SKELETON + COLUMN_SCOPED:
            counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        con.execute("DETACH DATABASE src")
        con.commit()
        con.execute("VACUUM")
        con.commit()
    finally:
        con.close()
    size = os.path.getsize(out_path)
    return {"bundle": name, "path": out_path, "bytes": size,
            "columns": len(cols), "counts": counts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=None,
                    help="只建这些 bundle（可重复）")
    ap.add_argument("--check", action="store_true", help="只核对行数，不建库")
    args = ap.parse_args()

    if not os.path.exists(DB):
        print(f"!! 找不到 {DB}——先跑 python src/build_sqlite.py")
        return 1
    os.makedirs(OUTDIR, exist_ok=True)

    # v1 只有 base；其余 bundle 名留给扩展包，默认不建空库。
    names = args.only or ["base"]
    if args.check:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        total = con.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
        got = 0
        for n in names:
            p = os.path.join(OUTDIR, f"bundle-{n}.db")
            if not os.path.exists(p):
                print(f"-- {n}: 未建")
                continue
            c2 = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            k = c2.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
            c2.close()
            print("-- %-9s %8d 条陈述  %7.1f MiB" % (n, k, os.path.getsize(p) / 1048576))
            got += k
        print(f"合计 {got} / 单体 {total}  ->", "一致" if got == total else "不一致！")
        return 0 if got == total else 1

    t0 = time.time()
    results = []
    for n in names:
        r = build_one(n)
        if not r:
            return 1
        results.append(r)
        print("%-9s 列 %2d  陈述 %7d  边 %7d  %6.1f MiB  %.1fs"
              % (n, r["columns"], r["counts"]["statements"],
                 r["counts"]["relation_edges"], r["bytes"] / 1048576,
                 time.time() - t0))
    total_mib = sum(r["bytes"] for r in results) / 1048576
    print("\n合计 %.1f MiB（%d 个 bundle，单体库 %.1f MiB）"
          % (total_mib, len(results), os.path.getsize(DB) / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
