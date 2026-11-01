"""从 `src/schema.py` 生成 `schemas/*.schema.json`（**产物**）。

## 为什么 schema 文件是产物

契约的**事实源是 `src/schema.py`**（设计裁定 D44）。
手改 `schemas/*.schema.json` 会被下次生成**整表覆盖**——
这与 `data/columns.jsonl` 是产物同一个道理（硬规则 27）。

好处是枚举只可能有一份：`STATEMENT_KINDS` 改了，
`schemas/statements.schema.json` 里的 `enum` 跟着改，
不会出现「文档说 A、schema 说 B、代码说 C」。

## 用法

```bash
python src/build_schemas.py            # 生成
python src/build_schemas.py --check    # 只校验「已生成的文件 == 应生成的内容」
```
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import schema as schema_mod  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_DIR = os.path.join(ROOT, "schemas")

DRAFT = "https://json-schema.org/draft/2020-12/schema"


def build_one(name: str, body: dict) -> dict:
    """给单个记录类型组装成一份完整的 JSON Schema 文档。"""
    doc = {
        "$schema": DRAFT,
        "$id": f"https://hanzi-relations.local/schemas/{name}.schema.json",
        "title": body.get("title", name),
        "description": body.get("description", ""),
        "schema_version": schema_mod.SCHEMA_VERSION,
    }
    for k, v in body.items():
        if k in ("title", "description"):
            continue
        doc[k] = v
    return doc


def expected_files() -> dict[str, dict]:
    out = {}
    for name, body in schema_mod.RECORD_SCHEMAS.items():
        out[f"{name}.schema.json"] = build_one(name, body)
    return out


def write_all(check: bool = False) -> int:
    os.makedirs(SCHEMA_DIR, exist_ok=True)
    exp = expected_files()
    bad = 0

    # 删掉本模块不再生成的陈旧 schema 文件（避免「旧契约还在原地」）。
    for fn in sorted(os.listdir(SCHEMA_DIR)):
        if fn.endswith(".schema.json") and fn not in exp:
            os.remove(os.path.join(SCHEMA_DIR, fn))
            print(f"  删除陈旧 {fn}")

    for fn, doc in sorted(exp.items()):
        path = os.path.join(SCHEMA_DIR, fn)
        text = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if check:
            if not os.path.exists(path):
                print(f"!! 缺少 {fn}（跑 `python src/build_schemas.py` 生成）")
                bad += 1
                continue
            cur = open(path, "r", encoding="utf-8").read()
            if cur != text:
                print(f"!! {fn} 与 schema.py 不一致（跑 `python src/build_schemas.py` 重生成）")
                bad += 1
            else:
                print(f"  ok {fn}")
        else:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, path)
            print(f"  写入 {fn}")

    if not check:
        print(f"\n已生成 {len(exp)} 份 schema → schemas/")
        print(f"schema_version = {schema_mod.SCHEMA_VERSION}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    return write_all(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
