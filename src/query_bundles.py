"""在多个 bundle 上「合并查」——不先合并成一个库。

v1 只有 `base` 一个 bundle；扩展包加入后才会有多个可合并。

```bash
python src/query_bundles.py --char 月
python src/query_bundles.py --char 月 --bundle base
python src/query_bundles.py --char 月 --col xiaoxue_尔雅
python src/query_bundles.py --char 月 --tier base          # 只要 base 四列
```

## 做法

每个 `db/bundle-*.db` 都带**本 bundle 涉及的字头与骨架表**
（`characters` / `units`）与**自己那几列**的陈述、边、覆盖矩阵。合并查就是：

1. `ATTACH` 用户勾选的那几个 bundle；
2. 建一个 **TEMP VIEW** `v_char_full`，对每个 attached 库做
   `statements ⋈ columns ⋈ unit_bindings ⋈ units`，再 `UNION ALL`；
3. 查询照常在 `v_char_full` 上写。

为什么不用 `VIEW`：视图会被写进库文件，而「合哪几个库」是**查询时**才决定的；
写进文件就把某一次的勾选固化了。`TEMP VIEW` 只活在这次连接里。

⚠ bundle 数上限 8（SQLite 默认 `SQLITE_MAX_ATTACHED = 10`，留两个余量）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from units import COLUMN_BUNDLES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBDIR = os.path.join(ROOT, "db")

#: 一个 bundle 库能提供的「字 × 列」宽表（与 build_sqlite 的 v_char_full 同形，
#: 少掉只有单体库才有的派生视图列）。
PER_BUNDLE_SQL = """
SELECT u.unit_id, u.head AS unit_head,
       c.column_id, c.role, c.tier, c.bundle, c.work_id, c.name AS column_name,
       c.provides, c.aggregation, b.method, b.char_id,
       s.rid, s.statement_id, s.stable_id, s.kind, s.text, s.edition_id,
       s.parent_statement_id, s.evidence_url, s.license, s.source_id, s.confidence,
       s.locator
FROM {alias}.unit_bindings b
JOIN {alias}.units u     ON u.unit_id = b.unit_id
JOIN {alias}.columns c   ON c.column_id = b.column_id
JOIN {alias}.statements s ON s.char_id = b.char_id AND s.edition_id = b.column_id
"""


def connect(bundles: list[str]) -> sqlite3.Connection:
    missing = [b for b in bundles
               if not os.path.exists(os.path.join(DBDIR, f"bundle-{b}.db"))]
    if missing:
        raise SystemExit("!! 还没建这些 bundle：%s\n   先跑 python src/build_bundles.py --only base"
                         % ", ".join(missing))
    if len(bundles) > 8:
        raise SystemExit(f"!! 一次最多 attach 8 个库（SQLite 默认上限 10，留两个余量）："
                         f"{len(bundles)} 个")
    con = sqlite3.connect(":memory:")
    parts = []
    for i, b in enumerate(bundles):
        alias = f"b{i}"
        path = os.path.join(DBDIR, f"bundle-{b}.db")
        con.execute(f"ATTACH DATABASE ? AS {alias}", (path,))
        parts.append(PER_BUNDLE_SQL.format(alias=alias))
    con.execute("CREATE TEMP VIEW v_char_full AS " + "\nUNION ALL\n".join(parts))
    con.execute("CREATE TEMP VIEW v_char_base AS SELECT * FROM v_char_full "
                "WHERE tier='base'")
    con.row_factory = sqlite3.Row
    return con


def main() -> int:
    # 中文 Windows 的默认控制台是 GBK：正文里有扩展区汉字（𢇏 之类），
    # 直接 print 会 UnicodeEncodeError。只替换控制台编不出的字符。
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--char", default="月", help="要查的字头")
    ap.add_argument("--bundle", action="append", default=None,
                    help="要合并的 bundle（可重复；v1 默认只有 base）")
    ap.add_argument("--col", action="append", default=None,
                    help="只看这些列（可重复）")
    ap.add_argument("--tier", default=None, choices=("base", "standard", "extended"))
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    bundles = args.bundle or ["base"]
    con = connect(bundles)
    print("合并查：%s" % " + ".join(f"bundle-{b}.db" for b in bundles))

    sql = ("SELECT unit_id, unit_head, column_id, tier, column_name, kind, "
           "substr(text,1,60) AS text FROM v_char_full WHERE unit_head=?")
    params: list = [args.char]
    if args.col:
        sql += " AND column_id IN (%s)" % ",".join("?" * len(args.col))
        params += args.col
    if args.tier:
        sql += " AND tier = ?"
        params.append(args.tier)
    sql += " ORDER BY CASE tier WHEN 'base' THEN 0 WHEN 'standard' THEN 1 ELSE 2 END," \
           " column_id LIMIT ?"
    params.append(args.limit)

    rows = con.execute(sql, params).fetchall()
    print("命中 %d 行（每行 = 一条陈述）" % len(rows))
    for r in rows:
        print("  [%-8s] %-26s %-10s %s" % (r["tier"], r["column_id"], r["kind"],
                                           (r["text"] or "").replace("\n", " ")))
    # 覆盖矩阵：这个字在各 bundle 里各有多少列
    cov = con.execute(
        "SELECT bundle, COUNT(DISTINCT column_id) n, COUNT(*) rows_ FROM v_char_full "
        "WHERE unit_head=? GROUP BY 1 ORDER BY 1", (args.char,)).fetchall()
    print("\n这个字在各 bundle 的列数：",
          {r["bundle"]: r["n"] for r in cov})
    return 0


if __name__ == "__main__":
    sys.exit(main())
