"""按字形库账本重建全部 oracle 字形边（离线、可复跑）。

规则：
  - 钉账本（默认 v1.0.0），按 unicode 优先、否则 exact modern_char
  - 为指定 work_id 字头生成 uid 指针边
  - 删除旧 oracle 边；其它层边不动
  - 不把异体映射写进身份

用法：
  python src/rebuild_oracle_glyphs.py --glyph-repo ../hanzi-glyphs-svg
  python src/rebuild_oracle_glyphs.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jsonl_io import read_jsonl_dir  # noqa: E402
import glyph_pointer as gp  # noqa: E402

DATA = os.path.join(ROOT, "data")
REL_DIR = os.path.join(DATA, "relations")


def next_relation_id(relations: list[dict]) -> int:
    mx = 0
    for r in relations:
        rid = r.get("relation_id") or ""
        if rid.startswith("R") and rid[1:].isdigit():
            mx = max(mx, int(rid[1:]))
    return mx + 1


def is_oracle_edge(to: str, by_uid: dict[str, dict]) -> bool:
    parsed = gp.parse_glyph_target(to)
    if not parsed:
        return False
    _repo, uid = parsed
    row = by_uid.get(uid)
    return bool(row and row.get("layer") == "oracle")


def rewrite_parts(new_oracle: list[dict], by_uid: dict[str, dict]) -> None:
    parts = sorted(
        f for f in os.listdir(REL_DIR)
        if f.startswith("part-") and f.endswith(".jsonl")
    )
    if not parts:
        raise SystemExit("没有 relations part 文件")

    kept_by_part: list[list[dict]] = []
    for name in parts:
        path = os.path.join(REL_DIR, name)
        rows: list[dict] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                if o.get("type") == "glyph" and is_oracle_edge(o.get("to") or "", by_uid):
                    continue
                rows.append(o)
        kept_by_part.append(rows)

    kept_by_part[-1].extend(new_oracle)

    for name, rows in zip(parts, kept_by_part):
        path = os.path.join(REL_DIR, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for o in rows:
                fh.write(json.dumps(o, ensure_ascii=False) + "\n")
        os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--glyph-repo",
        default=os.environ.get("HANZI_GLYPHS_SVG")
        or os.path.join(os.path.dirname(ROOT), "hanzi-glyphs-svg"),
    )
    ap.add_argument("--glyph-ref", default=gp.DEFAULT_GLYPH_REF)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--work-id", default="shuowen",
                    help="只为该 work_id 的字头建 oracle 边")
    args = ap.parse_args()

    chars = read_jsonl_dir(os.path.join(DATA, "characters"))
    char_by_id = {c["char_id"]: c for c in chars}
    relations = read_jsonl_dir(REL_DIR)
    by_uid, _by_stem, meta = gp.load_ledger(args.glyph_repo, args.glyph_ref)
    by_uni, by_modern = gp.build_identity_lookups(by_uid, layer="oracle")

    old_oracle = [
        r for r in relations
        if r.get("type") == "glyph" and is_oracle_edge(r.get("to") or "", by_uid)
    ]

    before_mismatch = 0
    before_match = 0
    for r in old_oracle:
        parsed = gp.parse_glyph_target(r["to"])
        assert parsed
        _repo, uid = parsed
        cid = r["from"].split(":", 1)[1]
        char = char_by_id.get(cid)
        row = by_uid.get(uid)
        if not char or not row:
            before_mismatch += 1
            continue
        ok, _ = gp.identity_match(char, row)
        if ok:
            before_match += 1
        else:
            before_mismatch += 1

    targets = [c for c in chars if c.get("work_id") == args.work_id]
    targets.sort(key=lambda c: c.get("char_id") or "")

    new_oracle: list[dict] = []
    rid = next_relation_id(relations)
    method_counter = Counter()
    yi_samples = []
    for c in targets:
        uids = gp.uids_for_char(c, by_uni, by_modern)
        if not uids:
            continue
        row0 = by_uid[uids[0]]
        _ok, reason = gp.identity_match(c, row0)
        method_counter[reason] += 1
        for uid in uids:
            new_oracle.append({
                "relation_id": f"R{rid}",
                "type": "glyph",
                "from": f"char:{c['char_id']}",
                "to": gp.format_glyph_target(uid),
            })
            rid += 1
        if c.get("head") == "一":
            yi_samples.append((c["char_id"], uids))

    after_fail = 0
    for r in new_oracle:
        parsed = gp.parse_glyph_target(r["to"])
        assert parsed
        _repo, uid = parsed
        cid = r["from"].split(":", 1)[1]
        char = char_by_id[cid]
        ok, reason = gp.identity_match(char, by_uid[uid])
        if not ok:
            after_fail += 1
            if after_fail <= 5:
                print("FAIL", r, reason)

    print("=== rebuild oracle glyphs ===")
    print(f"ledger              : {meta.get('_source')} sha={meta.get('_pinned_sha')}")
    print(f"oracle uids         : {sum(1 for u,r in by_uid.items() if r['layer']=='oracle')}")
    print(f"old oracle edges    : {len(old_oracle)}")
    print(f"before match/mismatch: {before_match} / {before_mismatch}")
    print(f"new oracle edges    : {len(new_oracle)}")
    print(f"chars linked        : {sum(method_counter.values())} ({dict(method_counter)})")
    print(f"after identity fails: {after_fail}")
    print(f"sample 一           : {yi_samples}")
    if new_oracle:
        print(f"first new           : {new_oracle[0]}")
        print(f"last new            : {new_oracle[-1]}")

    if after_fail:
        print("ABORT: new edges failed G-identity", file=sys.stderr)
        return 1
    if args.dry_run:
        print("dry-run: not writing")
        return 0

    rewrite_parts(new_oracle, by_uid)
    print(f"wrote relations under {REL_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
