"""字形指针 dry-run：layer:key → glyph:hanzi-glyphs-svg:uid:<unified_id>

按拍板（A 硬切）：
  新格式  glyph:hanzi-glyphs-svg:uid:<unified_id>
  旧格式  glyph:hanzi-glyphs-svg:<layer>:<key>  迁移后立刻失效
  账本钉  字形库 git ref（默认 v1.0.0），不盲跟 working tree / main
  本脚本  只读现有边 + 账本，写报告；**不改 JSONL / DB / viewer**

用法：
  python src/migrate_glyph_pointers_dry_run.py
  python src/migrate_glyph_pointers_dry_run.py --glyph-repo ../hanzi-glyphs-svg --glyph-ref v1.0.0
  python src/migrate_glyph_pointers_dry_run.py --out-dir tmp/glyph-uid-dry-run

后续真正改数据时，可复用本文件的：
  - load_ledger_map / format_uid_target / parse_legacy_target
  - resolve_layer_file_for_uid（viewer 运行时查账本用）
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jsonl_io import read_jsonl_dir  # noqa: E402

REPO_NAME = "hanzi-glyphs-svg"
UID_RE = re.compile(r"^[0-9A-F]{6}-[0-9A-Z]{4}-[0-9]{6}$")
LEGACY_PREFIX = f"glyph:{REPO_NAME}:"
NEW_PREFIX = f"glyph:{REPO_NAME}:uid:"


def _default_glyph_repo() -> str:
    env = os.environ.get("HANZI_GLYPHS_SVG")
    if env:
        return env
    return os.path.join(os.path.dirname(ROOT), "hanzi-glyphs-svg")


def parse_legacy_target(to: str) -> tuple[str, str, str] | None:
    """旧格式 glyph:<repo>:<layer>:<key>；非法或不含 uid 段才走这里。"""
    if not to or not to.startswith("glyph:"):
        return None
    parts = to.split(":")
    if len(parts) != 4:
        return None
    _, repo, layer, key = parts
    if layer == "uid":
        return None
    if not repo or not layer or not key:
        return None
    return repo, layer, key


def parse_uid_target(to: str) -> tuple[str, str] | None:
    """新格式 glyph:<repo>:uid:<unified_id>。"""
    if not to or not to.startswith("glyph:"):
        return None
    parts = to.split(":")
    if len(parts) != 4:
        return None
    _, repo, marker, uid = parts
    if marker != "uid" or not repo or not uid:
        return None
    return repo, uid


def format_uid_target(unified_id: str, repo: str = REPO_NAME) -> str:
    return f"glyph:{repo}:uid:{unified_id}"


def _git_show(repo: str, ref: str, path: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", repo, "show", f"{ref}:{path}"],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace")
        raise SystemExit(
            f"无法读取 {repo} @ {ref}:{path}\n{err.strip() or exc}"
        ) from exc
    return out.decode("utf-8")


def _git_rev_parse(repo: str, ref: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", repo, "rev-parse", ref],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or "").strip()
        raise SystemExit(f"无法解析 git ref {ref!r} @ {repo}\n{err}") from exc


def load_ledger_map(
    glyph_repo: str,
    glyph_ref: str | None,
) -> tuple[dict[tuple[str, str], list[str]], dict[str, dict], dict]:
    """返回 ((layer, stem)→[uid…], uid→row, meta)。

    glyph_ref 非空时用 `git show <ref>:glyphs/glyph-ids.csv` 钉版本；
    为空时读工作树文件（仅调试用，正式 dry-run 应钉 v1.0.0）。
    """
    if glyph_ref:
        raw = _git_show(glyph_repo, glyph_ref, "glyphs/glyph-ids.csv")
        try:
            meta_raw = _git_show(glyph_repo, glyph_ref, "glyphs/glyph-ids.meta.json")
            meta = json.loads(meta_raw)
        except SystemExit:
            meta = {}
        sha = _git_rev_parse(glyph_repo, glyph_ref)
        meta = dict(meta)
        meta["_pinned_ref"] = glyph_ref
        meta["_pinned_sha"] = sha
        meta["_source"] = f"git:{glyph_ref}:glyphs/glyph-ids.csv"
    else:
        path = os.path.join(glyph_repo, "glyphs", "glyph-ids.csv")
        if not os.path.isfile(path):
            raise SystemExit(f"找不到账本：{path}")
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        meta_path = os.path.join(glyph_repo, "glyphs", "glyph-ids.meta.json")
        meta = {}
        if os.path.isfile(meta_path):
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        meta = dict(meta)
        meta["_pinned_ref"] = None
        meta["_pinned_sha"] = None
        meta["_source"] = path

    by_layer_stem: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_uid: dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(raw))
    for row in reader:
        if (row.get("status") or "").strip() != "active":
            continue
        uid = (row.get("unified_id") or "").strip()
        layer = (row.get("layer") or "").strip()
        file_name = (row.get("file") or "").strip()
        if not uid or not layer or not file_name:
            continue
        if not UID_RE.match(uid):
            continue
        stem = Path(file_name).stem
        by_layer_stem[(layer, stem)].append(uid)
        by_uid[uid] = {
            "unified_id": uid,
            "layer": layer,
            "file": file_name,
            "stem": stem,
            "modern_char": row.get("modern_char") or "",
            "unicode": row.get("unicode") or "",
            "source_id": row.get("source_id") or "",
            "release": row.get("release") or "",
        }
    return dict(by_layer_stem), by_uid, meta


def resolve_layer_file_for_uid(
    uid: str,
    by_uid: dict[str, dict],
) -> tuple[str, str] | None:
    """viewer 后续接口：unified_id → (layer, file stem)。本 dry-run 只暴露不写入。"""
    row = by_uid.get(uid)
    if not row:
        return None
    return row["layer"], row["stem"]


def scan_relations(rel_dir: str) -> list[dict]:
    return [r for r in read_jsonl_dir(rel_dir) if r.get("type") == "glyph"]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="字形指针 dry-run：layer:key → uid（不改数据）"
    )
    ap.add_argument(
        "--glyph-repo",
        default=_default_glyph_repo(),
        help="字形库路径（默认兄弟目录 ../hanzi-glyphs-svg 或 $HANZI_GLYPHS_SVG）",
    )
    ap.add_argument(
        "--glyph-ref",
        default="v1.0.0",
        help="钉账本的 git ref（默认 v1.0.0；传空字符串则读工作树）",
    )
    ap.add_argument(
        "--relations-dir",
        default=os.path.join(ROOT, "data", "relations"),
        help="关系边分片目录",
    )
    ap.add_argument(
        "--out-dir",
        default=os.path.join(ROOT, "tmp", "glyph-uid-dry-run"),
        help="报告输出目录（默认 tmp/glyph-uid-dry-run）",
    )
    args = ap.parse_args()

    glyph_ref = args.glyph_ref if args.glyph_ref != "" else None
    if not os.path.isdir(args.glyph_repo):
        raise SystemExit(f"字形库不存在：{args.glyph_repo}")
    if not os.path.isdir(args.relations_dir):
        raise SystemExit(f"关系目录不存在：{args.relations_dir}")

    by_layer_stem, by_uid, meta = load_ledger_map(args.glyph_repo, glyph_ref)
    edges = scan_relations(args.relations_dir)

    layer_edge_c: Counter[str] = Counter()
    ok_rows: list[dict] = []
    missing_rows: list[dict] = []
    ambiguous_rows: list[dict] = []
    already_uid_rows: list[dict] = []
    bad_format_rows: list[dict] = []
    wrong_repo_rows: list[dict] = []

    for edge in edges:
        to = edge.get("to") or ""
        rid = edge.get("relation_id") or ""
        frm = edge.get("from") or ""

        uid_parsed = parse_uid_target(to)
        if uid_parsed:
            repo, uid = uid_parsed
            already_uid_rows.append(
                {
                    "relation_id": rid,
                    "from": frm,
                    "to": to,
                    "repo": repo,
                    "unified_id": uid,
                    "in_ledger": uid in by_uid,
                }
            )
            continue

        legacy = parse_legacy_target(to)
        if not legacy:
            bad_format_rows.append(
                {"relation_id": rid, "from": frm, "to": to, "reason": "bad-format"}
            )
            continue

        repo, layer, key = legacy
        layer_edge_c[layer] += 1
        if repo != REPO_NAME:
            wrong_repo_rows.append(
                {
                    "relation_id": rid,
                    "from": frm,
                    "to": to,
                    "repo": repo,
                    "layer": layer,
                    "key": key,
                }
            )
            continue

        ids = by_layer_stem.get((layer, key), [])
        if len(ids) == 1:
            new_to = format_uid_target(ids[0])
            row = by_uid[ids[0]]
            ok_rows.append(
                {
                    "relation_id": rid,
                    "from": frm,
                    "old_to": to,
                    "new_to": new_to,
                    "layer": layer,
                    "key": key,
                    "unified_id": ids[0],
                    "file": row["file"],
                    "modern_char": row["modern_char"],
                    "unicode": row["unicode"],
                }
            )
        elif len(ids) == 0:
            missing_rows.append(
                {
                    "relation_id": rid,
                    "from": frm,
                    "old_to": to,
                    "layer": layer,
                    "key": key,
                    "reason": "no-ledger-row",
                }
            )
        else:
            ambiguous_rows.append(
                {
                    "relation_id": rid,
                    "from": frm,
                    "old_to": to,
                    "layer": layer,
                    "key": key,
                    "unified_ids": ids,
                    "count": len(ids),
                }
            )

    # 字形库已有层 vs 关系库现有边层
    ledger_layer_c: Counter[str] = Counter()
    for (_layer, _stem), uids in by_layer_stem.items():
        ledger_layer_c[_layer] += len(uids)
    relation_layers = set(layer_edge_c)
    ledger_layers = set(ledger_layer_c)
    absent_layers = sorted(ledger_layers - relation_layers)
    present_layers = sorted(ledger_layers & relation_layers)
    unknown_edge_layers = sorted(relation_layers - ledger_layers)

    os.makedirs(args.out_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S%z")

    summary = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "dry_run": True,
        "writes_data": False,
        "new_pointer_format": "glyph:hanzi-glyphs-svg:uid:<unified_id>",
        "cutover": "hard-cut (legacy layer:key will be invalid after apply)",
        "viewer_plan": "store uid only; resolve layer/file via ledger at runtime",
        "glyph_repo": os.path.abspath(args.glyph_repo),
        "glyph_ref": glyph_ref,
        "glyph_sha": meta.get("_pinned_sha"),
        "ledger_source": meta.get("_source"),
        "ledger_id_format": meta.get("id_format"),
        "ledger_schema_version": meta.get("schema_version"),
        "ledger_active_count": meta.get("counts", {}).get("active")
        if isinstance(meta.get("counts"), dict)
        else len(by_uid),
        "relations_dir": os.path.abspath(args.relations_dir),
        "totals": {
            "glyph_edges": len(edges),
            "mapped_ok": len(ok_rows),
            "missing": len(missing_rows),
            "ambiguous": len(ambiguous_rows),
            "already_uid": len(already_uid_rows),
            "bad_format": len(bad_format_rows),
            "wrong_repo": len(wrong_repo_rows),
        },
        "by_layer_edges": dict(sorted(layer_edge_c.items())),
        "ledger_layers_active_counts": dict(sorted(ledger_layer_c.items())),
        "layers_present_in_relations": present_layers,
        "layers_in_ledger_absent_from_relations": absent_layers,
        "layers_in_relations_absent_from_ledger": unknown_edge_layers,
        "expand_estimate": {
            layer: {
                "ledger_active": ledger_layer_c[layer],
                "note": "粗估：账本该层 active 件数；真正扩边还受字头 identity 匹配约束，会小于此数",
            }
            for layer in absent_layers
        },
        "samples_ok": ok_rows[:8],
        "samples_missing": missing_rows[:20],
        "samples_ambiguous": ambiguous_rows[:20],
        "runtime_hook": {
            "resolve_layer_file_for_uid": "src/migrate_glyph_pointers_dry_run.py::resolve_layer_file_for_uid",
            "format_uid_target": "src/migrate_glyph_pointers_dry_run.py::format_uid_target",
        },
    }

    summary_path = os.path.join(args.out_dir, "summary.json")
    map_path = os.path.join(args.out_dir, "mapping.jsonl")
    missing_path = os.path.join(args.out_dir, "missing.jsonl")
    ambiguous_path = os.path.join(args.out_dir, "ambiguous.jsonl")
    report_path = os.path.join(args.out_dir, "REPORT.md")

    with open(summary_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    with open(map_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in ok_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    with open(missing_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in missing_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    with open(ambiguous_path, "w", encoding="utf-8", newline="\n") as fh:
        for row in ambiguous_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    lines = []
    lines.append("# 字形指针迁移 dry-run 报告")
    lines.append("")
    lines.append(f"- 生成时间：{summary['generated_at']}")
    lines.append(f"- 字形库：`{summary['glyph_repo']}`")
    lines.append(f"- 钉 ref：`{glyph_ref}` → `{summary['glyph_sha']}`")
    lines.append(f"- 账本：`{summary['ledger_source']}`（id_format={summary['ledger_id_format']}）")
    lines.append(f"- 关系边目录：`{summary['relations_dir']}`")
    lines.append(f"- **本报告为 dry-run，未改任何 JSONL/DB/viewer**")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 项 | 数 |")
    lines.append("|---|---:|")
    for k, v in summary["totals"].items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## 现有边按 layer")
    lines.append("")
    lines.append("| layer | edges |")
    lines.append("|---|---:|")
    for layer, n in sorted(layer_edge_c.items()):
        lines.append(f"| {layer} | {n} |")
    lines.append("")
    lines.append("## 字形库账本层（active）与关系库对照")
    lines.append("")
    lines.append("| layer | ledger_active | in_relations |")
    lines.append("|---|---:|:---:|")
    for layer in sorted(ledger_layer_c):
        flag = "yes" if layer in relation_layers else "NO"
        lines.append(f"| {layer} | {ledger_layer_c[layer]} | {flag} |")
    lines.append("")
    lines.append("### 扩层粗估（账本有、关系库尚无边）")
    lines.append("")
    if not absent_layers:
        lines.append("（无）")
    else:
        lines.append("| layer | ledger_active（上界粗估） |")
        lines.append("|---|---:|")
        for layer in absent_layers:
            lines.append(f"| {layer} | {ledger_layer_c[layer]} |")
        lines.append("")
        lines.append(
            "> 粗估=该层账本 active 件数；真正按字头 G-identity 扩边会更少。"
        )
    lines.append("")
    lines.append("## 成功映射样例")
    lines.append("")
    for row in ok_rows[:8]:
        lines.append(f"- `{row['old_to']}` → `{row['new_to']}`")
    lines.append("")
    if missing_rows:
        lines.append("## 缺失样例")
        lines.append("")
        for row in missing_rows[:10]:
            lines.append(f"- `{row['old_to']}` ({row['relation_id']})")
        lines.append("")
    if ambiguous_rows:
        lines.append("## 歧义样例")
        lines.append("")
        for row in ambiguous_rows[:10]:
            lines.append(
                f"- `{row['old_to']}` → {row['count']} ids: {row['unified_ids'][:5]}"
            )
        lines.append("")
    lines.append("## 输出文件")
    lines.append("")
    lines.append(f"- `{os.path.abspath(summary_path)}`")
    lines.append(f"- `{os.path.abspath(map_path)}`")
    lines.append(f"- `{os.path.abspath(missing_path)}`")
    lines.append(f"- `{os.path.abspath(ambiguous_path)}`")
    lines.append(f"- `{os.path.abspath(report_path)}`")
    lines.append("")
    lines.append("## 后续 apply 前仍缺")
    lines.append("")
    lines.append("1. 改 `docs/glyph-pointer-contract.md` + `src/glyph_pointer.py`（硬切只认 uid）")
    lines.append("2. 更新 `verify_relations.py` G1–G4、重建脚本、单测")
    lines.append("3. apply 脚本：原地改写 `to`，保留 `relation_id`")
    lines.append("4. viewer：边只存 uid，运行时用账本 `resolve_layer_file_for_uid`")
    lines.append("5. 扩层（若做）：按字头 identity 从缺层账本生成新边，另开任务")
    lines.append("6. 重建 sqlite / bundles / viewer / MANIFEST；仍不自动 commit/push")
    lines.append("")

    with open(report_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))

    # stdout 摘要
    print("=== glyph pointer dry-run ===")
    print(f"glyph_repo : {os.path.abspath(args.glyph_repo)}")
    print(f"glyph_ref  : {glyph_ref} -> {summary['glyph_sha']}")
    print(f"ledger     : {summary['ledger_source']}")
    print(f"edges      : {len(edges)}")
    print(f"mapped_ok  : {len(ok_rows)}")
    print(f"missing    : {len(missing_rows)}")
    print(f"ambiguous  : {len(ambiguous_rows)}")
    print(f"already_uid: {len(already_uid_rows)}")
    print(f"bad_format : {len(bad_format_rows)}")
    print(f"wrong_repo : {len(wrong_repo_rows)}")
    print(f"by_layer   : {dict(sorted(layer_edge_c.items()))}")
    print(f"absent_layers (ledger yes / relations no): {absent_layers}")
    print(f"expand_upper_bound: { {k: ledger_layer_c[k] for k in absent_layers} }")
    print(f"report     : {os.path.abspath(report_path)}")
    print(f"mapping    : {os.path.abspath(map_path)}")
    print(f"summary    : {os.path.abspath(summary_path)}")
    if ok_rows:
        print("sample_ok  :", ok_rows[0]["old_to"], "->", ok_rows[0]["new_to"])
    if missing_rows:
        print("sample_miss:", missing_rows[0]["old_to"])
    if ambiguous_rows:
        print("sample_amb :", ambiguous_rows[0]["old_to"], ambiguous_rows[0]["unified_ids"][:3])

    # dry-run 成功退出：有缺失/歧义用 2 提示，但不算脚本失败逻辑错误
    if missing_rows or ambiguous_rows or bad_format_rows or wrong_repo_rows:
        return 2
    if len(ok_rows) != len(edges):
        # 还有 already_uid 等
        if len(ok_rows) + len(already_uid_rows) == len(edges) and not (
            missing_rows or ambiguous_rows or bad_format_rows or wrong_repo_rows
        ):
            return 0
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
