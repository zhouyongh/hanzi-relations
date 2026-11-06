"""扩层 identity dry-run：按字头对齐账本，生成候选边报告（默认不写入）。

规则见 docs/glyph-layer-expand-identity.md。

用法：
  python src/expand_glyph_layers_dry_run.py
  python src/expand_glyph_layers_dry_run.py --layers bronze,clerical
  python src/expand_glyph_layers_dry_run.py --apply --layers bronze  # 小规模试写
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

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


def existing_layer_uids(relations: list[dict], by_uid: dict[str, dict]) -> dict[str, set[str]]:
    """char_id → 已有 glyph 边所在 layer 集合。"""
    out: dict[str, set[str]] = defaultdict(set)
    for r in relations:
        if r.get("type") != "glyph":
            continue
        parsed = gp.parse_glyph_target(r.get("to") or "")
        if not parsed:
            continue
        _repo, uid = parsed
        row = by_uid.get(uid)
        if not row:
            continue
        frm = r.get("from") or ""
        if frm.startswith("char:"):
            out[frm[5:]].add(row["layer"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glyph-repo", default=gp.default_glyph_repo(ROOT))
    ap.add_argument("--glyph-ref", default=gp.DEFAULT_GLYPH_REF)
    ap.add_argument("--work-id", default="shuowen")
    ap.add_argument("--all-chars", action="store_true")
    ap.add_argument(
        "--layers",
        default="",
        help="逗号分隔；默认=白名单中关系库尚无边的层",
    )
    ap.add_argument(
        "--one-per-char",
        action="store_true",
        help="每字每层只取排序后第一个 uid（默认全保留）",
    )
    ap.add_argument(
        "--out-dir",
        default=os.path.join(ROOT, "tmp", "glyph-expand-dry-run"),
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="写入候选边到 data/relations（危险；默认只报告）",
    )
    ap.add_argument(
        "--apply-max-edges",
        type=int,
        default=50000,
        help="apply 时硬顶，防止误灌超大层",
    )
    args = ap.parse_args()

    by_uid, _stem, meta = gp.load_ledger(args.glyph_repo, args.glyph_ref)
    chars = read_jsonl_dir(os.path.join(DATA, "characters"))
    relations = list(read_jsonl_dir(REL_DIR))
    have = existing_layer_uids(relations, by_uid)

    if args.all_chars:
        targets = list(chars)
    else:
        targets = [c for c in chars if c.get("work_id") == args.work_id]
    targets.sort(key=lambda c: c.get("char_id") or "")

    present_layers = set()
    for layers in have.values():
        present_layers |= layers
    default_layers = sorted(gp.PUBLISHED_LAYERS - present_layers)
    if args.layers.strip():
        layers = [x.strip() for x in args.layers.split(",") if x.strip()]
        bad = [x for x in layers if x not in gp.PUBLISHED_LAYERS]
        if bad:
            raise SystemExit(f"层不在白名单: {bad}")
    else:
        layers = default_layers

    os.makedirs(args.out_dir, exist_ok=True)
    per_layer = {}
    all_candidates: list[dict] = []

    for layer in layers:
        by_uni, by_modern = gp.build_identity_lookups(by_uid, layer=layer)
        cand: list[dict] = []
        chars_hit = 0
        chars_multi = 0
        chars_zero = 0
        reason_c = Counter()
        for c in targets:
            cid = c["char_id"]
            if layer in have.get(cid, set()):
                continue  # 该字该层已有边，跳过
            uids = gp.uids_for_char(c, by_uni, by_modern)
            if not uids:
                chars_zero += 1
                continue
            if args.one_per_char:
                uids = uids[:1]
            if len(uids) > 1:
                chars_multi += 1
            chars_hit += 1
            row0 = by_uid[uids[0]]
            _ok, reason = gp.identity_match(c, row0)
            reason_c[reason] += 1
            for uid in uids:
                cand.append({
                    "type": "glyph",
                    "from": f"char:{cid}",
                    "to": gp.format_glyph_target(uid),
                    "layer": layer,
                    "unified_id": uid,
                    "head": c.get("head") or "",
                    "match": reason,
                })
        per_layer[layer] = {
            "candidate_edges": len(cand),
            "chars_hit": chars_hit,
            "chars_multi": chars_multi,
            "chars_zero_among_targets_without_edge": chars_zero,
            "match_reasons": dict(reason_c),
            "ledger_active": sum(1 for r in by_uid.values() if r["layer"] == layer),
        }
        path = os.path.join(args.out_dir, f"candidates-{layer}.jsonl")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for row in cand:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        all_candidates.extend(cand)

    summary = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "dry_run": not args.apply,
        "glyph_ref": args.glyph_ref,
        "glyph_sha": meta.get("_pinned_sha"),
        "ledger_source": meta.get("_source"),
        "work_id": None if args.all_chars else args.work_id,
        "target_chars": len(targets),
        "one_per_char": args.one_per_char,
        "layers": layers,
        "present_layers_in_relations": sorted(present_layers),
        "per_layer": per_layer,
        "total_candidate_edges": len(all_candidates),
        "identity_rules": [
            "unicode codepoint match first",
            "else exact modern_char when glyph has no unicode",
            "no variant/tongjia folding",
            "default keep all uids per (char,layer); --one-per-char optional",
        ],
    }
    summary_path = os.path.join(args.out_dir, "summary.json")
    report_path = os.path.join(args.out_dir, "REPORT.md")
    with open(summary_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    lines = [
        "# 扩层 identity dry-run",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 账本：{summary['ledger_source']} (`{summary['glyph_sha']}`)",
        f"- 字头范围：{'ALL' if args.all_chars else args.work_id}（{len(targets)}）",
        f"- 一对多：{'每字每层 1 件' if args.one_per_char else '全保留'}",
        f"- 候选边合计：{len(all_candidates)}",
        "",
        "| layer | ledger_active | candidate_edges | chars_hit | chars_multi | chars_zero |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for layer in layers:
        s = per_layer[layer]
        lines.append(
            f"| {layer} | {s['ledger_active']} | {s['candidate_edges']} | "
            f"{s['chars_hit']} | {s['chars_multi']} | "
            f"{s['chars_zero_among_targets_without_edge']} |"
        )
    lines += [
        "",
        "## 写入策略",
        "",
        "- 默认**不写入** data/。",
        "- `--apply --layers bronze` 可小规模试写；全量新层须用户确认。",
        f"- apply 硬顶：{args.apply_max_edges} 边。",
        "",
        f"报告：`{os.path.abspath(report_path)}`",
        f"汇总：`{os.path.abspath(summary_path)}`",
        "",
    ]
    with open(report_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))

    print("=== expand glyph layers dry-run ===")
    print(f"ledger : {meta.get('_source')} sha={meta.get('_pinned_sha')}")
    print(f"targets: {len(targets)} layers={layers}")
    print(f"total candidates: {len(all_candidates)}")
    for layer in layers:
        s = per_layer[layer]
        print(
            f"  {layer}: edges={s['candidate_edges']} hit={s['chars_hit']} "
            f"multi={s['chars_multi']} zero={s['chars_zero_among_targets_without_edge']}"
        )
    print(f"report: {os.path.abspath(report_path)}")

    if not args.apply:
        print("dry-run only; not writing relations")
        return 0

    # 阶段 1 Done 口径：扩层默认只 dry-run；全量/试写须显式环境变量确认
    import os as _os
    if _os.environ.get("CONFIRM_EXPAND_APPLY") != "1":
        print(
            "ABORT: 扩层写入已关闭（阶段 1 只 dry-run）。"
            "若确需试写，设置 CONFIRM_EXPAND_APPLY=1 且限制 --layers（最多 2 层）。",
            file=sys.stderr,
        )
        return 1

    if len(all_candidates) > args.apply_max_edges:
        print(
            f"ABORT: candidates {len(all_candidates)} > apply-max-edges "
            f"{args.apply_max_edges}",
            file=sys.stderr,
        )
        return 1
    if len(layers) > 2:
        print(
            "ABORT: apply 一次最多 2 个层；全量请用户确认后分批",
            file=sys.stderr,
        )
        return 1

    # 写入：追加到最后一片，分配新 relation_id
    rid = next_relation_id(relations)
    new_edges = []
    for row in all_candidates:
        new_edges.append({
            "relation_id": f"R{rid}",
            "type": "glyph",
            "from": row["from"],
            "to": row["to"],
        })
        rid += 1

    parts = sorted(
        f for f in os.listdir(REL_DIR)
        if f.startswith("part-") and f.endswith(".jsonl")
    )
    last = os.path.join(REL_DIR, parts[-1])
    with open(last, "a", encoding="utf-8", newline="\n") as fh:
        for o in new_edges:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"APPLY wrote {len(new_edges)} edges -> {last}")
    print("PENDING USER: 是否继续其它层全量写入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
