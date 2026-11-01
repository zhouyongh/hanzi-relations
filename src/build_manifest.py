"""生成 `MANIFEST.json`：**每个数据文件的 sha256 + 行数 + 字节数**（设计裁定 D44）。

## 为什么要有它

`build.json` 只记了 **SQLite 那个文件**的一个 sha256。
而仓库的事实源是 `data/` 下 **40 个 JSONL 分片**——
「下游拿到的是不是这份数据」，现在**无法回答**。

更要紧的是实测踩过的一个坑：**任何碰过 SQLite schema 的操作
（建表、删表、VACUUM）都会改文件头里的 `schema_version`，
于是行数、内容全一样，sha256 却变了**——
「同输入必得同 sha256」只在「一次性重建」时成立。

`MANIFEST.json` 把这件事下沉到**逐文件**：
每个 JSONL 分片各有一个 sha256，DB 变不变不影响数据的身份。

## 覆盖范围

- `data/**`（含 `provenance/<source_id>/` 各分片）
- `schemas/**`（契约产物）

**不含** `MANIFEST.json` 自身（自引用无解）。

## 用法

```bash
python src/build_manifest.py            # 生成
python src/build_manifest.py --check    # 校验：现有文件与台账是否一致
```

`--check` 会报三种情况：**文件内容变了** / **文件不见了** / **多出没登记的文件**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import schema as schema_mod  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "MANIFEST.json")

#: 纳入 manifest 的目录（相对仓库根）。
INCLUDE = ("data", "schemas")

#: manifest 自身的文件名——排除（自引用）。
SELF = "MANIFEST.json"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_lines(path: str) -> int:
    n = 0
    with open(path, "rb") as fh:
        for _ in fh:
            n += 1
    return n


def collect() -> list[dict]:
    """遍历纳入范围的所有文件，算出 sha256 / 行数 / 字节数。"""
    out: list[dict] = []
    for top in INCLUDE:
        base = os.path.join(ROOT, top)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for fn in sorted(filenames):
                if fn == SELF or fn.endswith(".tmp"):
                    continue
                # **以 `_` 开头的文件不入清单**：data/_*.txt / _*.json / _*.jsonl
                # 是临时探查与流水线日志（`.gitignore` 里也排除它们）。
                # 收进清单的后果很直接：清单登记了一个**不进版本库**的文件，
                # 别人 clone 之后 `--check` 永远报「缺文件」。
                if fn.startswith("_"):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
                out.append({
                    "path": rel,
                    "bytes": os.path.getsize(full),
                    "lines": count_lines(full),
                    "sha256": sha256_file(full),
                })
    out.sort(key=lambda r: r["path"])
    return out


def build() -> dict:
    files = collect()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": schema_mod.SCHEMA_VERSION,
        "sqlite_schema_version": schema_mod.SQLITE_SCHEMA_VERSION,
        "scope": list(INCLUDE),
        "totals": {
            "files": len(files),
            "bytes": sum(f["bytes"] for f in files),
            "lines": sum(f["lines"] for f in files),
        },
        "files": files,
    }


def check() -> int:
    if not os.path.exists(MANIFEST):
        print(f"!! 缺少 {os.path.relpath(MANIFEST, ROOT)}——跑 `python src/build_manifest.py` 生成")
        return 1
    with open(MANIFEST, "r", encoding="utf-8") as fh:
        old = json.load(fh)
    old_map = {r["path"]: r for r in old.get("files", [])}
    new_map = {r["path"]: r for r in collect()}

    bad = 0
    for path, rec in sorted(new_map.items()):
        prev = old_map.get(path)
        if prev is None:
            print(f"!! 多出未登记的文件：{path}")
            bad += 1
        elif prev.get("sha256") != rec["sha256"]:
            print(f"!! 内容已变：{path}")
            print(f"     登记 {prev.get('sha256')}  实测 {rec['sha256']}")
            bad += 1
    for path in sorted(old_map):
        if path not in new_map:
            print(f"!! 登记了但文件已不在：{path}")
            bad += 1

    print("-" * 60)
    if bad:
        print(f"manifest 校验失败：{bad} 处不一致")
        return 1
    print(f"manifest 校验通过：{len(new_map)} 个文件与登记一致")
    print(f"  合计 {sum(f['bytes'] for f in new_map.values()) / 1048576:.1f} MiB / "
          f"{sum(f['lines'] for f in new_map.values()):,} 行")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if args.check:
        return check()

    doc = build()
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, MANIFEST)
    t = doc["totals"]
    print(f"已写入 {os.path.relpath(MANIFEST, ROOT)}")
    print(f"  文件 {t['files']} 个 / {t['bytes'] / 1048576:.1f} MiB / {t['lines']:,} 行")
    print(f"  schema_version = {doc['schema_version']}  "
          f"sqlite_schema_version = {doc['sqlite_schema_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
