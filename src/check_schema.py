"""按生成的 JSON Schema 校验 `data/` 里的实际记录（设计裁定 D44）。

## 为什么要有这个校验器

`AGENTS.md` 写着「契约」，但契约是 markdown——**改代码的人不一定看**。
上一轮就因此踩过：`verify_relations.py` 抄了一份 `STMT_KINDS`，
与 `units.KIND_DIMENSION` 漂移，R13 变成「橡皮图章」
（校验器拿自己抄的枚举去对账自己的数据）。

现在有了 `src/schema.py`（单一真源）+ `schemas/*.schema.json`（产物），
**字段级漂移由本脚本兜住**：

- 有人给字头表加了字段但没登记 → `additionalProperties: false` 报错；
- 有人改了枚举但没改 schema → `enum` 报错；
- 有人手改了 `schemas/*.schema.json` → `build_schemas.py --check` 报错。

## 附带检查

`relations` 的 `kind` 受 `schema.RELATION_KINDS` 约束：
- 不在表里的 `type` **不许带 `kind`**（带 kind 是例外，不是常态）；
- 在表里的 `type`，`kind` 必须在该类型的取值集内。

## 用法

```bash
python src/check_schema.py             # 全量
python src/check_schema.py --sample 5  # 每类抽 5% 记录（快速自查）
```

**退出码 1 = 有记录不符合契约。**
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import schema as schema_mod  # noqa: E402
from jsonschema_mini import compile_schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SCHEMA_DIR = os.path.join(ROOT, "schemas")

MAX_REPORT = 40          # 每类最多打印多少条错误
MAX_PER_FILE = 20        # 每个文件最多打印多少条错误


def data_files(kind: str) -> list[str]:
    """某类记录对应的数据文件（分片目录展开成多个文件）。"""
    out: list[str] = []
    for pat in schema_mod.RECORD_FILES[kind]:
        if "*" in pat:
            for d in sorted(glob.glob(os.path.join(DATA, pat))):
                out.extend(sorted(glob.glob(os.path.join(d, "part-*.jsonl"))))
        else:
            p = os.path.join(DATA, pat)
            if os.path.isdir(p):
                out.extend(sorted(glob.glob(os.path.join(p, "part-*.jsonl"))))
            elif os.path.exists(p):
                out.append(p)
    return out


def load_schema(name: str) -> dict:
    path = os.path.join(SCHEMA_DIR, f"{name}.schema.json")
    if not os.path.exists(path):
        raise SystemExit(
            f"缺少 {path}\n先跑 `python src/build_schemas.py` 生成 schema 文件。")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check_kind_semantics(rec: dict, errs: list[str]) -> None:
    """`variant_of` 等边的 `kind` 必须落在该类型的取值集内。"""
    rtype = rec.get("type")
    kind = rec.get("kind")
    allowed = schema_mod.RELATION_KINDS.get(rtype)
    if allowed is None:
        if kind:
            errs.append(f"type={rtype} 不允许带 kind（{kind!r}）")
    elif kind is not None and kind not in allowed:
        errs.append(f"type={rtype} 的 kind={kind!r} 不在允许集 {list(allowed)} 内")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=float, default=None,
                    help="每类只校验这个百分比的记录（1~99），用于快速自查；"
                         "不传则全量校验")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    total_checked = 0
    total_errors = 0
    print("=" * 68)
    print("契约校验（schema → data）")
    print("=" * 68)
    print(f"schema_version = {schema_mod.SCHEMA_VERSION}")
    print()

    for kind in sorted(schema_mod.RECORD_SCHEMAS):
        raw = load_schema(kind)
        check = compile_schema(raw)
        files = data_files(kind)
        if not files:
            print(f"[{kind}] !! 找不到数据文件")
            total_errors += 1
            continue

        n_checked = 0
        n_err = 0
        samples: list[str] = []
        for fp in files:
            rel = os.path.relpath(fp, ROOT).replace("\\", "/")
            with open(fp, "r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    if args.sample is not None and args.sample < 100:
                        # 确定性抽样：按行号取模，不用随机数（同输入必得同结果）。
                        if ((lineno * 7919) % 1000) >= args.sample * 10:
                            continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError as exc:
                        n_err += 1
                        if len(samples) < MAX_PER_FILE:
                            samples.append(f"{rel}:{lineno} JSON 解析失败：{exc}")
                        continue
                    errs = check(rec, f"{rel}:{lineno}")
                    if kind == "relations":
                        sem: list[str] = []
                        check_kind_semantics(rec, sem)
                        errs.extend(sem)
                    n_checked += 1
                    if errs:
                        n_err += 1
                        if len(samples) < MAX_PER_FILE:
                            samples.extend(errs[:4])
            if len(samples) >= MAX_REPORT:
                break

        total_checked += n_checked
        total_errors += n_err
        flag = "OK " if n_err == 0 else "!! "
        print(f"{flag}{kind:<14} 校验 {n_checked:>9,} 条 / 文件 {len(files):>2} 个 / 错误 {n_err:,}")
        for s in samples[:MAX_REPORT]:
            print(f"      {s}")
        if n_err > len(samples):
            print(f"      …… 另有 {n_err - len(samples):,} 条错误未打印")

    print()
    print("-" * 68)
    print(f"合计校验 {total_checked:,} 条，错误 {total_errors:,} 条")
    if total_errors:
        print("契约与数据不一致——改 data 要改 src/schema.py 并重生成 schema。")
        return 1
    print("契约与数据一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
