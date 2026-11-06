"""按 dry-run mapping.jsonl 原地改写 glyph 边 to（保留 relation_id）。

默认读 tmp/glyph-uid-dry-run/mapping.jsonl。
**会改 data/relations/**；不 commit/push。

用法：
  python src/apply_glyph_uid_mapping.py --dry-run
  python src/apply_glyph_uid_mapping.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import glyph_pointer as gp  # noqa: E402

REL_DIR = os.path.join(ROOT, "data", "relations")
DEFAULT_MAP = os.path.join(ROOT, "tmp", "glyph-uid-dry-run", "mapping.jsonl")


def load_mapping(path: str) -> dict[str, str]:
    """relation_id → new_to；同时校验 new_to 可解析。"""
    out: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            rid = o.get("relation_id") or ""
            new_to = o.get("new_to") or ""
            old_to = o.get("old_to") or ""
            if not rid or not new_to:
                raise SystemExit(f"mapping 行缺字段: {o!r}")
            parsed = gp.parse_glyph_target(new_to)
            if not parsed:
                raise SystemExit(f"new_to 非法: {new_to} ({rid})")
            if rid in out:
                raise SystemExit(f"mapping 重复 relation_id: {rid}")
            out[rid] = new_to
            # old_to 仅作审计；允许缺省
            _ = old_to
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", default=DEFAULT_MAP)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mapping = load_mapping(args.mapping)
    parts = sorted(
        f for f in os.listdir(REL_DIR)
        if f.startswith("part-") and f.endswith(".jsonl")
    )
    if not parts:
        raise SystemExit(f"无 relations part：{REL_DIR}")

    seen = 0
    changed = 0
    missing_map = 0
    already = 0
    bad = 0
    leftover_legacy = 0

    for name in parts:
        path = os.path.join(REL_DIR, name)
        rows: list[dict] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                if o.get("type") == "glyph":
                    seen += 1
                    rid = o.get("relation_id") or ""
                    to = o.get("to") or ""
                    if gp.parse_glyph_target(to):
                        already += 1
                        # 若 mapping 也有，仍以 mapping 为准（应相同）
                        if rid in mapping and mapping[rid] != to:
                            o = dict(o)
                            o["to"] = mapping[rid]
                            changed += 1
                    elif rid in mapping:
                        o = dict(o)
                        o["to"] = mapping[rid]
                        changed += 1
                    else:
                        missing_map += 1
                        if ":" in to and ":uid:" not in to:
                            leftover_legacy += 1
                rows.append(o)

        if args.dry_run:
            continue
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for o in rows:
                fh.write(json.dumps(o, ensure_ascii=False) + "\n")
        os.replace(tmp, path)

    print("=== apply glyph uid mapping ===")
    print(f"mapping rows : {len(mapping)}")
    print(f"glyph edges  : {seen}")
    print(f"changed      : {changed}")
    print(f"already uid  : {already}")
    print(f"missing map  : {missing_map}")
    print(f"legacy left  : {leftover_legacy}")
    print(f"bad          : {bad}")
    if args.dry_run:
        print("dry-run: not writing")
        return 0 if missing_map == 0 and leftover_legacy == 0 else 2

    if missing_map or leftover_legacy:
        print("ERROR: 仍有未映射/旧格式边", file=sys.stderr)
        return 1
    if changed != len(mapping) and already != len(mapping):
        # 允许 already==len（重跑幂等）或 changed==len（首次）
        if changed + already < len(mapping):
            print("ERROR: 应用条数与 mapping 不一致", file=sys.stderr)
            return 1
    print(f"wrote parts under {REL_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
