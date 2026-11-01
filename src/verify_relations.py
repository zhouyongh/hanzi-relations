"""校验本仓库的数据：格式、引用完整性、许可闸门。

退出码 0 = 全部通过；1 = 有问题（可直接用于 CI）。

检查项：
  F. 格式       —— JSON 合法、必填字段齐全、枚举值合法
  R. 引用完整性 —— 边的两端都能解析；陈述的字头/上位陈述都存在
  L. 许可闸门   —— 陈述的 license 必须等于其来源登记的 license；
                    redistributable=false 的来源不得有内容入库；
                    指针来源的摘要必须填 summary_method
  G. 字形指针   —— glyph 边的目标文件存在（只对已发布层）
  P. provenance —— data/provenance/<source_id>.jsonl 与 statements 一致
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# JSONL 讀寫（分片支持）：見 src/jsonl_io.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jsonl_io import read_jsonl, read_jsonl_any, read_jsonl_dir  # noqa: E402
import units as units_mod  # noqa: E402
import readings as readings_mod  # noqa: E402
import structures as structures_mod  # noqa: E402
import schema as schema_mod  # noqa: E402
import stable_id as stable_id_mod  # noqa: E402
import known_gaps as gaps_mod  # noqa: E402

DATA = os.path.join(ROOT, "data")
PROV = os.path.join(DATA, "provenance")

# 封闭枚举一律取自 `src/schema.py`（**契约的单一事实源**）。
#
# 这里原来抄了一份 `REL_TYPES` / `STMT_KINDS` / `CONFIDENCE` / `ALLOWED_LICENSES`
# ——那正是「第二份事实源」：校验器拿自己抄的枚举去对账自己的数据，
# 枚举漂移了校验器还照样绿（上一轮的 R13「橡皮图章」就是这么来的）。
REL_TYPES = set(schema_mod.RELATION_TYPES)
STMT_KINDS = set(schema_mod.STATEMENT_KINDS)
CONFIDENCE = set(schema_mod.CONFIDENCE)
SUMMARY_METHODS = set(schema_mod.SUMMARY_METHODS)

# 许可白名单与 Public-Domain 判据都在 `schema.ALLOWED_LICENSES` 的注释里
# （本文件不再抄一份规则，避免第二份事实源）。
ALLOWED_LICENSES = set(schema_mod.ALLOWED_LICENSES)
# 指针型来源允许的许可（内容不入库，只存指针）
POINTER_ONLY_LICENSES = set(schema_mod.POINTER_ONLY_LICENSES)


def license_parts(license_id: str) -> list[str]:
    """把 "A+B" 拆成 ["A", "B"]；单个许可就返回一个元素的列表。

    派生来源（多来源合成）用 "+" 连接的组合许可——
    每一项都必须在白名单内。例：radical_map = Unihan(Unicode-3.0) × 說文(Apache-2.0)
    → "Unicode-3.0+Apache-2.0"。只登记其中一项会**低报**实际义务。
    """
    return [p.strip() for p in (license_id or "").split("+") if p.strip()]

errors: list[str] = []
warnings: list[str] = []


def err(code: str, msg: str) -> None:
    errors.append(f"[{code}] {msg}")


def warn(code: str, msg: str) -> None:
    warnings.append(f"[{code}] {msg}")


def read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        err("F0", f"缺少文件：{os.path.relpath(path, ROOT)}")
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                err("F1", f"{os.path.basename(path)} 第 {i} 行 JSON 非法：{exc}")
    return out


def read_statements_by_source() -> dict[str, list[dict]]:
    """按 provenance 的**来源目录**分组读全部陈述。

    逐源分文件才能在授权状态变化时整块移除某来源，也避免与全量副本重复 164 MB。

    *为什么按目录分组*：R23 重算 `stable_id` 时，组内序号（内容完全重复时
    才需要）必须与迁移脚本的分组口径**完全一致**——迁移按目录分，
    这里就按目录分。
    """
    prov = os.path.join(PROV, "")
    out: dict[str, list[dict]] = {}
    if not os.path.isdir(prov):
        err("F0", "缺少目录 data/provenance/")
        return out
    for name in sorted(os.listdir(prov)):
        path = os.path.join(PROV, name)
        if os.path.isdir(path):
            # provenance 的每个来源是一个**目录**（内含 part-*.jsonl 分片）
            out[name] = read_jsonl_any(path)
        elif name.endswith(".jsonl"):
            out[name] = read_jsonl(path)
    return out


def read_statements() -> list[dict]:
    """读全部陈述（不分组的便捷视图）。"""
    out: list[dict] = []
    for stmts in read_statements_by_source().values():
        out.extend(stmts)
    return out


def ngram_overlap(a: str, b: str, n: int = 8) -> float:
    """a 的 n-gram 集合有多大比例出现在 b 中。用于「摘要不得是原文摘抄」自检。"""
    if len(a) < n:
        return 0.0
    ga = {a[i:i + n] for i in range(len(a) - n + 1)}
    if not ga:
        return 0.0
    gb = {b[i:i + n] for i in range(len(b) - n + 1)} if len(b) >= n else set()
    if not gb:
        return 0.0
    return len(ga & gb) / len(ga)


def main() -> int:
    ap = argparse.ArgumentParser(description="校验关系与出处（退出码 1 = 有问题）")
    ap.add_argument("--glyph-repo", default=None,
                    help="字形仓库路径（兄弟仓库 hanzi-glyphs-svg）。"
                         "缺省依次取：命令行参数 → 环境变量 HANZI_GLYPHS_SVG → "
                         "本仓库的同级目录。找不到就整体跳过 G3（不是报错）。")
    args = ap.parse_args()

    # ---------- 载入 ----------
    sources = read_jsonl(os.path.join(DATA, "sources.jsonl"))
    characters = read_jsonl_dir(os.path.join(DATA, "characters"))
    stmts_by_source = read_statements_by_source()
    statements = [st for stmts in stmts_by_source.values() for st in stmts]
    relations = read_jsonl_dir(os.path.join(DATA, "relations"))
    radicals = read_jsonl(os.path.join(DATA, "radicals.jsonl"))
    rhymes = read_jsonl(os.path.join(DATA, "rhyme_groups.jsonl"))
    editions = read_jsonl(os.path.join(DATA, "editions.jsonl"))
    readings = read_jsonl(os.path.join(DATA, "readings.jsonl"))
    structures = read_jsonl(os.path.join(DATA, "structures.jsonl"))

    src_by_id = {s.get("source_id"): s for s in sources}
    char_by_id = {c.get("char_id"): c for c in characters}
    stmt_by_id = {s.get("statement_id"): s for s in statements}
    edition_ids = {e.get("edition_id") for e in editions}
    rhyme_ids = {(r.get("system"), r.get("group_id")) for r in rhymes}
    # 讀音登記表（規格 D42）：讀音邊的目標必須在這裡找得到
    reading_ids = {r.get("reading_id") for r in readings if r.get("reading_id")}
    # 部首表的鍵：(system, radical 名)——radical 邊的目標必須在這裏找得到
    radical_keys = {(r.get("system"), r.get("radical")) for r in radicals
                    if r.get("radical")}

    # ---------- F. 格式 ----------
    for s in sources:
        sid = s.get("source_id", "?")
        for f in ("name", "url", "kind", "license", "redistributable",
                  "retrieved_at", "version", "attribution"):
            if f not in s or s[f] in (None, ""):
                if f == "redistributable":
                    if "redistributable" not in s:
                        err("F2", f"来源 {sid} 缺字段 redistributable")
                    continue
                err("F2", f"来源 {sid} 缺字段 {f}")
        if "redistributable" in s and not isinstance(s["redistributable"], bool):
            err("F2", f"来源 {sid} 的 redistributable 必须是布尔值")
        if s.get("kind") not in ("dataset", "text", "pointer"):
            err("F2", f"来源 {sid} 的 kind 非法：{s.get('kind')}")
        if s.get("kind") == "pointer" and s.get("redistributable") is True:
            err("F2", f"来源 {sid} 是 pointer 却标了 redistributable=true")
        if s.get("redistributable") is True:
            bad = [p for p in license_parts(s.get("license", "")) if p not in ALLOWED_LICENSES]
            if bad or not license_parts(s.get("license", "")):
                err("L1", f"来源 {sid} 标为可再分发，但许可 {s.get('license')} 不在白名单内"
                          f"（本库禁止 ND/NC/copyleft 入库）；非法项={bad}")
        # L5：Public-Domain 来源必须带**逐书核对记录**。
        #
        # 「公有领域古籍的忠实录入」这个判据，只有在**逐本书确认过来源**时才成立：
        # 上游仓库里有一部分书是从维基文库（CC BY-SA 4.0）导入的，
        # 那些不能算 Public-Domain。所以要求来源登记里写清核对方式与结果，
        # 并留下可复跑的核对脚本。
        if "Public-Domain" in license_parts(s.get("license", "")):
            notes = s.get("notes") or ""
            if "逐书核对" not in notes and "逐書核對" not in notes:
                err("L5", f"来源 {sid} 标为 Public-Domain，但 notes 里没有逐书核对的说明"
                          f"（必须写明哪些书、怎么确认不是从 copyleft 源导入的）")
            if not s.get("attribution"):
                err("L5", f"来源 {sid} 标为 Public-Domain，缺 attribution"
                          f"（公有领域古籍也要署名作者与录入者）")

    for c in characters:
        cid = c.get("char_id", "?")
        for f in ("char_id", "head", "work_id", "source_ids"):
            if f not in c or c[f] in (None, "", []):
                err("F2", f"字头 {cid} 缺字段 {f}")
        for sid in c.get("source_ids", []):
            if sid not in src_by_id:
                err("R1", f"字头 {cid} 引用了不存在的来源 {sid}")
        # canonical_id：跨来源归并键（身份），必须指向存在的字头
        canon = c.get("canonical_id")
        if canon is None:
            err("F2", f"字头 {cid} 缺 canonical_id")
        elif canon != cid and canon not in char_by_id:
            err("R6", f"字头 {cid} 的 canonical_id 指向不存在的字头 {canon}")
        # search_group：检索可达组（可达性），同样必须指向存在的字头
        grp = c.get("search_group")
        if grp is None:
            err("F2", f"字头 {cid} 缺 search_group")
        elif grp != cid and grp not in char_by_id:
            err("R7", f"字头 {cid} 的 search_group 指向不存在的字头 {grp}")

    for st in statements:
        sid = st.get("statement_id", "?")
        for f in ("statement_id", "kind", "char_id", "work_id", "edition_id",
                  "text", "locator", "evidence_url", "license", "source_id",
                  "confidence"):
            if f not in st or st[f] in (None, ""):
                if f == "evidence_url" and st.get("summary_method"):
                    pass  # 指针源的摘要允许无 evidence_url？——不，仍要求有
                err("F2", f"陈述 {sid} 缺字段 {f}")
        if st.get("kind") not in STMT_KINDS:
            err("F2", f"陈述 {sid} 的 kind 非法：{st.get('kind')}")
        if st.get("confidence") not in CONFIDENCE:
            err("F2", f"陈述 {sid} 的 confidence 非法：{st.get('confidence')}")
        if st.get("edition_id") not in edition_ids:
            err("R2", f"陈述 {sid} 引用了不存在的 edition {st.get('edition_id')}")
        if st.get("parent_statement_id") and st["parent_statement_id"] not in stmt_by_id:
            err("R3", f"陈述 {sid} 的 parent_statement_id 指向不存在的陈述")

    for r in relations:
        rid = r.get("relation_id", "?")
        if r.get("type") not in REL_TYPES:
            err("F2", f"边 {rid} 的 type 非法：{r.get('type')}")
        for side in ("from", "to"):
            if not r.get(side):
                err("F2", f"边 {rid} 缺字段 {side}")
                continue
            val = r[side]
            kind, _, ident = val.partition(":")
            if kind == "char" and ident not in char_by_id:
                err("R4", f"边 {rid} 的 {side} 指向不存在的字头 {ident}")
            elif kind == "statement" and ident not in stmt_by_id:
                err("R5", f"边 {rid} 的 {side} 指向不存在的陈述 {ident}")
            elif kind == "work":
                # 典籍实体：康熙条目里内嵌的「【說文】…」等引文所引之书。
                # 这些书名来自受引文本本身，无需外部登记即可解析。
                if not ident:
                    err("R6", f"边 {rid} 的 {side} 指向空的 work")
            elif kind == "edition":
                # **edition 目标必须真的存在**。
                #
                # 独立审查抓到：说文引书层写了一张 30 條的「书名 → edition_id」映射表，
                # 其中 20 多條指向**根本沒建過的 edition**（如 `xiaoxue_fangyan`、
                # `classic_zhouli`）——那是**指向虛構實體的邊**。
                # R2 只檢查「陳述的 edition_id」，不檢查「邊的 edition: 目標」，
                # 所以這類錯誤此前能過校驗。
                if ident not in edition_ids:
                    err("R9", f"边 {rid} 的 {side} 指向不存在的 edition {ident}")
            elif kind == "radical":
                # **radical 目标必须真的存在**——目标格式是
                # `radical:<system>:<name>`（如 `radical:shuowen:一`、
                # `radical:kangxi:1`），要在 radicals 表裡找得到。
                # 這與 R9 同類：**指向虛構實體的邊**是本庫紅線要防的。
                parts = ident.split(":")
                if len(parts) == 1:
                    err("R10", f"边 {rid} 的 {side} 指向空的 radical {ident}")
                else:
                    sysname, name = parts[0], ":".join(parts[1:])
                    if (sysname, name) not in radical_keys:
                        err("R10", f"边 {rid} 的 {side} 指向不存在的部首 "
                                   f"{sysname}:{name}")
            elif kind == "phonology":
                # 讀音的 phonology 邊有**兩種目標形態**（規格 D42）：
                #   reading:<edition_id>:<slug>  —— **賦值**：「這個字有這個讀音/韻類」
                #   statement:S…                 —— **比對**：說文反切 ↔ 廣韻反切的結論
                #                                   （kind 是 fanqie_same / fanqie_homophone…）
                #
                # 賦值邊的目標必須在**讀音登記表**裡（否則就是指向虛構實體，
                # 與 R9/R10 同類）；比對邊的目標必須是**真實陳述**。
                if ident.startswith("statement:"):
                    pass                    # 比對邊：目標的合法性由下面 R8 的通用校驗管
                elif ident.startswith("reading:"):
                    if ident not in reading_ids:
                        err("R8", f"边 {rid} 的 {side} 指向未登記的讀音 {ident}")
                elif ident.startswith("phonology:"):
                    # 舊格式：**硬遷移已完成**（D42.3），不該再有。
                    err("R17", f"边 {rid} 的 {side} 還指向舊格式 {ident}"
                              f"（讀音邊的目標已遷移到 reading:<edition>:<slug>）")
                else:
                    parts = ident.split(":")
                    if len(parts) == 1:
                        err("R8", f"边 {rid} 的 {side} 指向泛目標 phonology:{ident}，"
                                  f"應指向具體讀音/韻類")
                    elif parts[0] == "guangyun" and len(parts) >= 2:
                        if ("guangyun_xiao", parts[1]) not in rhyme_ids:
                            err("R8", f"边 {rid} 的 {side} 指向不存在的韻類 {parts[1]}")
                    elif parts[0] not in edition_ids:
                        err("R8", f"边 {rid} 的 {side} 指向未登記的讀音來源 {parts[0]}")

    # ---------- 韻類表 ----------
    for rg in rhymes:
        key = f"({rg.get('system')}, {rg.get('group_id')})"
        for f in ("system", "group_id", "source_id", "license"):
            if not rg.get(f):
                err("F2", f"韻類 {key} 缺字段 {f}")
        if rg.get("source_id") and rg["source_id"] not in src_by_id:
            err("R1", f"韻類 {key} 引用了不存在的来源 {rg['source_id']}")

    # ---------- 部首表（規格 §4 D41）----------
    # R14：部首表的鍵必須**唯一**，且每行的來源與許可必須可核。
    #
    # *為什麼要有這條*：`radicals.jsonl` 之前有 **13 個重複的 `(system, radical)` 鍵**
    # （同一個康熙部兩行：一行是 Unihan 的殘筆數、一行是部首字與筆畫）。
    # SQLite 的 `PRIMARY KEY (system, radical)` + `INSERT OR REPLACE` 把它們
    # **靜默折疊**——1,077 行進去，1,064 行出來，13 條無聲無息地沒了。
    # 校驗器之前只把 radicals 讀成一個鍵**集合**，重複被集合吃掉，**一條都沒報出來**。
    rad_seen: dict[tuple, dict] = {}
    for r in radicals:
        sysname = r.get("system")
        name = r.get("radical")
        if not sysname or not name:
            err("R14", f"部首行缺 system 或 radical：{r}")
            continue
        key = (sysname, name)
        if key in rad_seen:
            err("R14", f"部首表有重複的鍵 ({sysname}, {name})——"
                      f"SQLite 的 INSERT OR REPLACE 會靜默折疊掉其中一條")
        else:
            rad_seen[key] = r
        sid = r.get("source_id")
        if sid and sid not in src_by_id:
            err("R14", f"部首 ({sysname}, {name}) 引用了不存在的来源 {sid}")
        lic = r.get("license") or ""
        bad = [p for p in license_parts(lic) if p not in ALLOWED_LICENSES]
        if bad or not license_parts(lic):
            err("R14", f"部首 ({sysname}, {name}) 的许可 {lic!r} 不在白名单内"
                      f"（本庫禁止 ND/NC/copyleft 入庫）；非法项={bad}")

    # R15：部首系裡「部↔部」的邊必須有出處。
    #
    # *為什麼要有這條*：`radical_contains` / `radical_owner` 這兩類邊
    # 說的是**部與部**的關係，背後的證據是 `radical_map` 陳述
    # （含 `basis`、`字數`、`evidence_url`）。沒有出處指針，
    # 「這條對照是誰說的」就無從查證——和 `variant_of` 是同一種病。
    #
    # （`radical` 邊不要求 `statement_ids`：它的出處是**字頭行**的
    #   `source_ids`——康熙部來自 kangxi_dict、說文部來自《說文》檢索資料，
    #   邊是從字頭的 `radical` 列推出來的，本來就沒有對應的單條陳述。）
    for r in relations:
        if r.get("type") not in ("radical_contains", "radical_owner"):
            continue
        sids = r.get("statement_ids") or []
        if not sids:
            err("R15", f"边 {r.get('relation_id')}（{r.get('type')}）"
                       f"沒有出處指針 statement_ids")
        for sid in sids:
            if sid not in stmt_by_id:
                err("R15", f"边 {r.get('relation_id')} 的 statement_ids 指向不存在的陳述 {sid}")

    # R16：不得有**偽字頭**。
    #
    # *為什麼要有這條*：`radical_map` 這個 work 當初造了 1,946 個偽字頭，
    # `head` 形如 `kx1_sw𧮫`——那是**「康熙第 1 部 × 說文『𧮫』部」的合成標籤**，
    # 不是字。其中 1,740 個 `modern_codepoints` 為空、**自成幽靈字單元**，
    # 讓 `v_units` 里出現頭顯示為 `kx1_sw𧮫` 的「字」。
    #
    # 判據：head 帶 ASCII 下劃線 = 合成標籤（真字與書名都不會有）。
    # 該 work 已整體註銷（陳述改掛到真實字頭），所以這裡應該是 0。
    pseudo = 0
    for c in characters:
        head = c.get("head") or ""
        if "_" in head:
            pseudo += 1
            if pseudo <= 5:
                err("R16", f"字头 {c.get('char_id')} 的 head {head!r} 是合成標籤"
                           f"（偽字頭），不是字")
    if pseudo > 5:
        err("R16", f"……另有 {pseudo - 5} 個偽字頭")

    # R16-b：**占位符字頭**（上游有損）——登記在冊的已知缺口。
    #
    # *為什麼不直接報錯*：缺口是**已知的**（上游把《說文》的一部分字頭寫成 `□`，
    # 規格 D46）。直接報錯會讓 CI 永久紅——紅到人就習慣了，等於沒有。
    # 所以：**登記在冊的缺口不報錯，但每次都必須在輸出裡出現**；
    #       重測結果與登記不一致（變大 = 又壞了 / 變小 = 修好了）→ 報錯。
    #
    # 登记表：`data/known_gaps.jsonl`（探针在 src/known_gaps.py）。
    for p in gaps_mod.check_all():
        err("R16", p)
    for gap in gaps_mod.load_gaps():
        ok, detail = gaps_mod.probe(gap)
        print(f"已知缺口          : {gap.get('gap_id')} — "
              f"{'與登記一致' if ok else '★不一致'}：{detail}")

    # R19：字頭表**不得**再有部首相關的四列（規格 D41.4 瘦身）。
    #
    # *為什麼要有這條*：部首資訊一度有**四處**表示——
    #   字頭表 `radical` 列 + `radical` 邊（康熙 46,815 / 說文 11,260 行）；
    #   字頭表 `radical_kangxi` / `radical_kangxi_simplified` / `residual_strokes`
    #   三列 + `radical_lookup` 陳述。
    # 四處表示 = 兩處事實源 = 必然漂移。收斂後：歸部全走**邊**，
    # 檢字法全走**陳述**，字頭表只留身份字段。
    #
    # 邊與陳述都是**派生**的——但它們**可查證**（有出處指針、有 edition 登記），
    # 而字頭表的列只是**轉錄**，一旦上游改了列而邊沒改，就查不出來。
    for c in characters:
        bad = [k for k in ("radical", "radical_kangxi",
                           "radical_kangxi_simplified", "residual_strokes")
               if k in c]
        if bad:
            err("R19", f"字头 {c.get('char_id')} 仍帶部首列 {bad}"
                       f"——部首資訊應走 `radical` 邊與 `radical_lookup` 陳述")
            break

    # R20：`radical_lookup` 陳述必須**掛在真實字頭**上，且 edition 正確。
    #
    # *為什麼要有這條*：康熙 214 部是「**檢字法**」（便於查字），
    # 說文 540 部是「**造字法**」（從某字）。兩者是不同的維度，
    # 混進 `radical` 邊就說不清「這個字在《說文》屬哪部」還是
    # 「按康熙檢字法歸第幾部」。
    lookup_n = 0
    for st in statements:
        if st.get("kind") != "radical_lookup":
            continue
        lookup_n += 1
        if st.get("edition_id") != "unihan_krsunicode":
            err("R20", f"陳述 {st.get('statement_id')} 的 edition 不是 "
                       f"unihan_krsunicode：{st.get('edition_id')!r}")
            break
        loc = st.get("locator") or {}
        if not loc.get("radical"):
            err("R20", f"陳述 {st.get('statement_id')} 的 locator 沒有 radical")
            break
        if st.get("char_id") not in char_by_id:
            err("R20", f"陳述 {st.get('statement_id')} 掛在不存在的字頭 "
                       f"{st.get('char_id')!r}")
            break
    print(f"radical_lookup 陳述  : {lookup_n:,}")

    # ---------- 讀音登記表（規格 §4 D42）----------
    # R18：登記表本身完整——id 唯一、edition 已登記、value_kind 合法、
    #      韻類型的值必須在韻類表裡。
    #
    # （編號說明：這條原本也編 R16，與上面「偽字頭」那條**撞號**——
    #   規則號是排查問題時的抓手，撞號就抓不住。已順延到 R18。）
    #
    # *為什麼要有這條*：讀音值域原本**沒有登記表**——87,585 個 distinct 目標值
    # **無人校驗**，R8 只看 edition 前綴。登記表補上後，「指向虛構讀音」
    # 這種邊才查得出來。
    seen_rid: set[str] = set()
    for r in readings:
        rid = r.get("reading_id")
        if not rid:
            err("R18", "讀音登記行缺 reading_id")
            continue
        if rid in seen_rid:
            err("R18", f"讀音登記表有重複的 reading_id {rid}")
        seen_rid.add(rid)
        ed = r.get("edition_id")
        if ed not in edition_ids:
            err("R18", f"讀音 {rid} 引用了未登記的 edition {ed}")
        kind = r.get("value_kind")
        if kind not in readings_mod.VALUE_KINDS:
            err("R18", f"讀音 {rid} 的 value_kind 非法：{kind!r}"
                       f"（取值域 {readings_mod.VALUE_KINDS}）")
        if kind == "rhyme_class":
            xiao = r.get("value")
            if ("guangyun_xiao", xiao) not in rhyme_ids:
                err("R18", f"讀音 {rid} 聲稱是韻類，但 {xiao} 不在韻類表裡")
        if not r.get("value"):
            err("R18", f"讀音 {rid} 缺 value")

    # ---------- 列登記表（規格 §4 D40）----------
    # 「一個字一行，來源是列」這一層的登記表。三條規則各管一件事：
    #   R11 登記表本身完整，且 column_id 與 editions.jsonl **雙向一致**
    #   R12 每條陳述的 edition_id 都必須登記成列——否則這條內容**掛不到任何一列**，
    #      下游按列取數時會**靜默丟失**（這正是「少了條」那類問題）
    #   R13 列聲明的 provides 必須覆蓋該列實際出現的陳述 kind——把登記表釘在內容上
    col_specs = units_mod.load_column_specs()
    for p in units_mod.validate_column_specs(col_specs):
        err("R11", p)
    col_ids = {s.get("column_id") for s in col_specs}
    col_specs_dict = {s.get("column_id"): s for s in col_specs}
    for cid in sorted(col_ids - edition_ids):
        err("R11", f"列 {cid} 在 editions.jsonl 裡沒有對應的版本形態")
    for eid in sorted(edition_ids - col_ids):
        err("R11", f"版本形態 {eid} 沒有登記成列（它的內容會掛不到任何一列）")

    kinds_by_column: dict[str, set[str]] = defaultdict(set)
    for st in statements:
        eid = st.get("edition_id")
        if eid:
            kinds_by_column[eid].add(st.get("kind") or "")
    for eid in sorted(kinds_by_column):
        if eid not in col_ids:
            err("R12", f"陳述的 edition_id {eid} 未登記成列——該列內容會被按列查詢丟棄")
    for p in units_mod.check_column_kinds(col_specs, dict(kinds_by_column)):
        err("R13", p)

    # ---------- R26：列的層級與物理分庫（規格 §4 D54）----------
    # 「一個字一個默認視圖」是**裁定**，不是從數據裡算出來的——所以它必須被釘住：
    #   R26.1 base 層**恰好**是 `units.BASE_COLUMNS` 那 4 列（多一列少一列都報）
    #   R26.2 每列的 bundle 必須等於 `units.bundle_of(column_id)`（純函數派生）
    #   R26.3 bundle 數 ≤ 8（SQLite 默認 SQLITE_MAX_ATTACHED = 10，留兩個餘量）
    #   R26.4 base 層每列都必須有陳述（默認視圖不能有空列——那等於默認就缺一塊）
    #
    # ⚠ 為什麼單獨立一條而不是並進 R11：R11 管「登記表與 editions 雙向一致」，
    #   是**完整性**；R26 管「默認視圖是哪四列」，是**裁定**。
    #   混在一起，改裁定與修數據就分不清了。
    n_base = [s for s in col_specs if s.get("tier") == "base"]
    base_ids = tuple(sorted(s.get("column_id") for s in n_base))
    if base_ids != tuple(sorted(units_mod.BASE_COLUMNS)):
        err("R26", f"base 層與 BASE_COLUMNS 不一致：登記 {base_ids}，"
                   f"裁定 {tuple(sorted(units_mod.BASE_COLUMNS))}")
    bundles_used = sorted({s.get("bundle") for s in col_specs})
    if len(bundles_used) > 8:
        err("R26", f"bundle 數 {len(bundles_used)} > 8（{bundles_used}）——"
                   f"SQLite 默認最多 10 個 ATTACH，留不出餘量")
    for s in col_specs:
        cid = s.get("column_id")
        if s.get("bundle") != units_mod.bundle_of(cid):
            err("R26", f"列 {cid} 的 bundle={s.get('bundle')!r} 與分庫規則不符"
                       f"（應為 {units_mod.bundle_of(cid)!r}）")
        if s.get("tier") == "base" and cid not in kinds_by_column:
            err("R26", f"base 列 {cid} 沒有任何陳述——默認視圖會缺一整塊")

    # ---------- R21：列不能是空列 ----------
    # 一列登記了 `provides`（= 宣稱「我能提供這種東西」）卻**一條陳述都沒有**，
    # 下游按列取數時會拿到空集——而按列覆蓋矩陣看，該列還「存在」。
    # 這正是「少了條」那類問題：欄位齊全、值全空。
    #
    # 判據是「**宣告了能力卻沒有內容**」，不是「role 是不是 core」——
    # 六經裡有些列本來就只覆蓋少數字，不能因為內容少就報。
    for cid in sorted(col_ids):
        if cid not in kinds_by_column:
            err("R21", f"列 {cid} 宣告了 provides="
                       f"{sorted(col_specs_dict.get(cid, {}).get('provides') or [])}"
                       f" 卻沒有任何陳述（空列）")

    # ---------- R22：構形值域登記（規格 §4 D43）----------
    # `type=structure` 邊的目標 `structure:<source>:<key>` 必須在
    # `data/structures.jsonl` 裡有登記項——否則「這條邊指向什麼構形」
    # 無人校驗（與 D42 修掉的「讀音值域無登記」同型）。
    struct_ids: set[str] = set()
    for r in structures:
        sid = r.get("structure_id")
        if not sid:
            err("R22", "構形登記行缺 structure_id")
            continue
        if sid in struct_ids:
            err("R22", f"構形登記表有重複的 structure_id {sid}")
        struct_ids.add(sid)
        if structures_mod.split_structure_target(sid) is None:
            err("R22", f"構形登記行的 id 格式非法：{sid!r}"
                       f"（應為 structure:<namespace>:<key>）")
        if not r.get("key"):
            err("R22", f"構形登記行 {sid} 缺 key")
        if not r.get("value"):
            err("R22", f"構形登記行 {sid} 缺 value（構形分解式）")
        if r.get("value_kind") not in structures_mod.STRUCTURE_KINDS:
            err("R22", f"構形登記行 {sid} 的 value_kind 非法："
                       f"{r.get('value_kind')!r}")
    struct_edge_targets: set[str] = set()
    for r in relations:
        if r.get("type") != "structure":
            continue
        t = r.get("to") or ""
        struct_edge_targets.add(t)
        if t not in struct_ids:
            err("R22", f"structure 邊 {r.get('relation_id')} 的目標 {t!r} "
                       f"不在構形登記表裡")
    for sid in sorted(struct_ids - struct_edge_targets):
        err("R22", f"構形登記行 {sid} 沒有任何邊指向（孤兒登記項）")
    print(f"構形登記項        : {len(struct_ids):,}（邊目標 {len(struct_edge_targets):,}）")

    # ---------- R23：穩定 id（內容尋址，規格 §4 D45）----------
    # `stable_id` 是「外部引用某一條陳述」的公共鑰匙。它必須：
    #   ① 每條陳述都有；
    #   ② **能從內容重算出來**——否則它就不是內容地址，只是另一個隨便的 id；
    #   ③ 全域唯一。
    #
    # ② 用的是 `src/stable_id.py` 這**同一個模組**（不抄第二份算法）：
    # 內容地址 = sha256(身份欄位 + **父的內容地址**) 的規範化 JSON。
    # 組內序號按「同一來源內的出現次序」——與遷移／建庫的口徑一致。
    #
    # ⚠ **父指針哈希的是「父的內容地址」，不是父的 S 號**（規格 D45）：
    #   S 號是順序號，重建就會變；放進哈希 = 用會變的號去算不變的號。
    #   實測 131,343 條（13.5%）陳述有父指針——這條規則就是為它們準備的。
    seen_stable: dict[str, str] = {}
    n_missing_stable = 0
    n_mismatch = 0
    n_dup_addr = 0
    n_dup_groups = 0
    for src_id, stmts in stmts_by_source.items():
        occ_counter: dict[str, int] = {}
        addr_of: dict[str, str] = {}
        for st in stmts:
            if st.get("source_id") != src_id:
                err("R23", f"provenance/{src_id}/ 裡的陳述 {st.get('statement_id')} "
                           f"的 source_id 欄位是 {st.get('source_id')!r}，與目錄名不符")
            pid = st.get("parent_statement_id")
            if pid:
                paddr = addr_of.get(pid)
                if paddr is None:
                    err("R23", f"陳述 {st.get('statement_id')} 的父 {pid} "
                               f"不在同一來源內、或沒先出現——無法算內容地址")
                    paddr = None
            else:
                paddr = None
            addr = stable_id_mod.content_address(st, paddr)
            addr_of[st.get("statement_id")] = addr
            occ = occ_counter.get(addr, 0)
            occ_counter[addr] = occ + 1
            if occ > 0:
                n_dup_addr += 1
            expect = stable_id_mod.stable_id_for(addr, occ)
            got = st.get("stable_id")
            if not got:
                n_missing_stable += 1
                if n_missing_stable <= 5:
                    err("R23", f"陳述 {st.get('statement_id')} 缺 stable_id")
                continue
            if got != expect:
                n_mismatch += 1
                if n_mismatch <= 5:
                    err("R23", f"陳述 {st.get('statement_id')} 的 stable_id 與內容重算不符："
                              f"存 {got}，重算 {expect}")
            prev = seen_stable.get(got)
            if prev is not None:
                err("R23", f"stable_id {got} 重複（陳述 {prev} 與 "
                           f"{st.get('statement_id')}）")
            else:
                seen_stable[got] = st.get("statement_id")
        # **组数** = 该来源内「出现次数 ≥ 2」的内容地址个数
        # （不是重复记录数，也不是来源数——那两个口径都会把问题说小/说大）。
        n_dup_groups += sum(1 for c in occ_counter.values() if c >= 2)
    print(f"穩定 id           : {len(seen_stable):,} 個（{len(statements):,} 條陳述）")
    print(f"                    內容完全相同的組 {n_dup_groups:,} 個 / "
          f"{n_dup_addr:,} 條（組內序號決定 id）")
    print(f"                    其餘 {len(statements) - n_dup_addr:,} 條 id 只由內容決定")

    # ---------- R24：說文系「反切列」的 reading 形狀 ----------
    #
    # *為什麼要有這條*：小徐本（系傳）的條目正則寫成了 `([一-鿿]{1,3}反)`——
    # `{1,3}` **貪婪**，而反切固定是「上字 + 下字 + 反」＝ 3 個字，
    # 於是**每條多吃 1 個字**（正文末字），把它塞進反切前面。
    # 實測 **8,394 條**系傳的反切因此變成 4 字（`意冝袁反`），正文也少末字。
    #
    # **為什麼原來查不出來**：
    #   ① `reading` 是自由字符串 → 形狀沒人管；
    #   ② `readings.jsonl` 裡**根本沒有說文系的反切** → 讀音值域兜不住；
    #   ③ R8 只看 `phonology` 邊的目標前綴 → 更查不出來。
    # 只有**拿同庫其它本子的 reading 長度做橫向對照**才看得出來：
    # 大徐 9,749 條全是 3、說文原文 9,812 條全是 3、段注 22,858 條全是 3，
    # **只有系傳整列偏長**——這種病必須靠「同列橫向對照」才抓得住。
    #
    # 適用列：reading 就是「一條反切」的列。
    #   `shuowen_xu_shen` / `shuowen_duan_yucai` **不適用**——
    #   它們的 reading 是**上游原文形態**：可能含多條反切（`陟鄰切、待秊切`）
    #   或帶校語（`莫交切（當作里之切）`），或整條是聲符單字（`良`）。
    _FANQIE_COLS = ("shuowen_da_xu", "shuowen_xu_kai_ben",
                    "shuowen_xu_xuan", "shuowen_xu_kai")
    # 含多條反切的列（說文原文 / 段注引）——R25 用；**定義在這裡**，
    # 因為 R24 的「反向護欄」也要用它（兩條規則覆蓋同一批列）。
    _FQ_MULTI_COLS = ("shuowen_xu_shen", "shuowen_duan_yucai")
    # ⚠ **字類要含擴展區**：《說文》的聲符/構件大量在 Ext-A / Ext-B
    #   （𤥎、𢾭、𡕢…）——只寫 `[一-鿿]` 會把**合法的**反切判成非法
    #   （實測首版就誤報了 12 條）。字類需含 Ext-A / Ext-B。
    _FQ_CJK = "㐀-䶿一-鿿\\U00020000-\\U0002A6DF"
    _FQ_RE = re.compile(r"^[%s]{2}(反|切)$" % _FQ_CJK)
    _FQ_SHORT_RE = re.compile(r"^[%s](反|切)$" % _FQ_CJK)   # 缺上字 → 登記在 G004
    _fq_len: dict[str, Counter] = defaultdict(Counter)
    n_fq_bad = 0
    n_fq_short = 0
    for st in statements:
        ed = st.get("edition_id")
        if ed not in _FANQIE_COLS:
            continue
        # B7：反切已陈述化——判据对象从 statements.reading
        # 改成 **kind=phonology 陈述的 text**。reading 欄现在必须是空的。
        if st.get("kind") != "phonology":
            continue
        rd = (st.get("text") or "").strip()
        if not rd:
            continue
        _fq_len[ed][len(rd)] += 1
        if _FQ_RE.match(rd):
            continue
        if _FQ_SHORT_RE.match(rd):
            n_fq_short += 1      # 缺上字：上游有損，登記在 G004
            continue
        n_fq_bad += 1
        if n_fq_bad <= 8:
            err("R24", f"說文系反切 {ed} 的陳述 {st.get('statement_id')} "
                       f"讀音形狀非法：{rd!r}（應為「2 字 + 反/切」）")
    # ⚠ **反向护栏**：反切不能再回到「reading 欄」。
    # 陈述化只是「把值搬到独立陈述」，只要有一处「reading=<反切>」漏改，
    # 同一事实就又有两处表示——而这一条**镜像检查**只有在这里才做得到：
    # 形状检查（上面）只看新位置，看不出旧位置还在漏。
    # 放宽到**全库**：B7 之后 `reading` 欄整体停用（值一律只留在
    # `kind=phonology` 陈述的 `text` 里），所以任何一条非空 `reading` 都是
    # 「同一事实的两处表示」回潮。
    n_fq_field = sum(1 for st in statements if (st.get("reading") or "").strip())
    if n_fq_field:
        err("R24", f"陈述的 reading 欄非空（B7/D53 要求停用该欄）："
                    f"{n_fq_field} 條")

    if n_fq_bad:
        err("R24", f"說文系反切形狀非法的陳述共 {n_fq_bad} 條")
    for ed in sorted(_fq_len):
        dist = "、".join("%d 字 %d 條" % (k, v) for k, v in sorted(_fq_len[ed].items()))
        print(f"說文系反切 {ed:<20}: {dist}")
    print(f"                    缺上字（1 字 + 反）{n_fq_short} 條（登記在 G004）")

    # ---------- R25：說文系「含多條反切」的列，reading 必須以合法反切開頭 ----------
    #
    # `shuowen_xu_shen` / `shuowen_duan_yucai` 的 reading 是上游原文形態：
    # 可能是一條反切、可能多條（`陟鄰切、待秊切`）、可能帶校語
    # （`莫交切（當作里之切）`）、也可能整條是聲符單字（`良` / `犯`）。
    # 判據：**首條必須是合法反切**，或**整條是單字**。
    _FQ_HEAD_RE = re.compile(r"^[%s]{2}(反|切)" % _FQ_CJK)
    n_multi_bad = 0
    for st in statements:
        ed = st.get("edition_id")
        if ed not in _FQ_MULTI_COLS:
            continue
        if st.get("kind") != "phonology":
            continue
        rd = (st.get("text") or "").strip()
        if not rd:
            continue
        if _FQ_HEAD_RE.match(rd) or len(rd) == 1:
            continue
        n_multi_bad += 1
        if n_multi_bad <= 8:
            err("R25", f"說文系讀音 {ed} 的陳述 {st.get('statement_id')} "
                       f"形狀非法：{rd!r}（應以「2 字 + 反/切」開頭，或整條是單字）")
    if n_multi_bad:
        err("R25", f"說文系讀音形狀非法的陳述共 {n_multi_bad} 條")
    print(f"說文系多反切列 {len(_FQ_MULTI_COLS)} 個：形狀非法 {n_multi_bad} 條")

    # ---------- L. 许可闸门 ----------
    for st in statements:
        sid = st.get("statement_id", "?")
        ssrc = src_by_id.get(st.get("source_id"))
        if ssrc is None:
            err("L2", f"陈述 {sid} 的 source_id {st.get('source_id')} 未登记")
            continue
        if st.get("license") != ssrc.get("license"):
            err("L3", f"陈述 {sid} 的 license={st.get('license')} 与来源 "
                      f"{ssrc['source_id']} 登记的 license={ssrc.get('license')} 不一致")
        if ssrc.get("redistributable") is False:
            # 指针型来源：原文不得入库
            if st.get("text"):
                err("L4", f"陈述 {sid} 来自不可再分发的来源 {ssrc['source_id']}，"
                          f"却含有正文内容（只允许自撰摘要）")
            if not st.get("summary_method"):
                err("L5", f"陈述 {sid} 来自指针型来源，summary_method 未填")
            elif st["summary_method"] not in SUMMARY_METHODS:
                err("L5", f"陈述 {sid} 的 summary_method 非法：{st['summary_method']}")
            if not st.get("reviewer") or not st.get("review_date"):
                err("L5", f"陈述 {sid} 来自指针型来源，缺 reviewer / review_date")

    # 摘要不得是原文摘抄：对指针源的摘要做 n-gram 重叠自检
    pointer_texts: list[tuple[str, str]] = []
    for st in statements:
        ssrc = src_by_id.get(st.get("source_id"))
        if ssrc and ssrc.get("redistributable") is False and st.get("text"):
            pointer_texts.append((st["statement_id"], st["text"]))
    # 只在同字头内比对（避免跨字误报）
    by_char: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for st in statements:
        ssrc = src_by_id.get(st.get("source_id"))
        if ssrc and ssrc.get("redistributable") is False and st.get("text"):
            by_char[st.get("char_id", "")].append((st["statement_id"], st["text"]))
    for cid, items in by_char.items():
        for sid_a, text_a in items:
            for sid_b, text_b in items:
                if sid_a >= sid_b:
                    continue
                ov = ngram_overlap(text_a, text_b)
                if ov > 0.5:
                    err("L6", f"陈述 {sid_a} 与 {sid_b} 疑似原文照抄"
                              f"（8-gram 重叠 {ov:.0%}）")

    # ---------- P. provenance 分文件 ----------
    prov_files = sorted(f for f in os.listdir(PROV)
                        if f.endswith(".jsonl") or os.path.isdir(os.path.join(PROV, f))) \
        if os.path.isdir(PROV) else []
    prov_counts = {f[:-6] if f.endswith(".jsonl") else f:
                   len(read_jsonl_any(os.path.join(PROV, f))) for f in prov_files}
    stmt_counts = Counter(st.get("source_id") for st in statements)
    for sid, n in stmt_counts.items():
        if sid not in prov_counts:
            err("P1", f"来源 {sid} 有 {n} 条陈述，但缺 data/provenance/{sid}/")
        elif prov_counts[sid] != n:
            err("P2", f"来源 {sid} 的 provenance 文件有 {prov_counts[sid]} 条，"
                      f"statements 里有 {n} 条，不一致")
    for sid in prov_counts:
        if sid not in stmt_counts:
            err("P3", f"provenance/{sid}.jsonl 存在但 statements 里没有该来源的陈述")

    # ---------- G. 字形指针 ----------
    # 字形文件在**兄弟仓库** `hanzi-glyphs-svg`（公开：zhouyongh/hanzi-glyphs-svg）。
    # 本仓库只存指针：指针的**格式**与**层是否已发布**在这里核；
    # 「文件在不在」要拿到兄弟仓库才谈得上——拿不到就**整体跳过**，不是报错。
    #
    # 为什么可以跳过：兄弟仓库不在本仓库的控制范围内，它的缺席不是本仓库的
    # 数据错误。**但必须说清楚跳过了多少条**，否则「校验通过」会被误读成
    # 「21,508 条指针都核过了」——那才是真的误导。
    glyph_repo = (args.glyph_repo
                  or os.environ.get("HANZI_GLYPHS_SVG")
                  or os.path.join(os.path.dirname(ROOT), "hanzi-glyphs-svg"))
    glyph_checked = glyph_missing = glyph_total = 0
    published_layers = {"oracle", "seal"}   # v1 只对已公开发布的两层建边
    glyph_avail = os.path.isdir(os.path.join(glyph_repo, "glyphs"))
    for r in relations:
        if r.get("type") != "glyph":
            continue
        glyph_total += 1
        _, _, payload = r["to"].partition(":")
        parts = payload.split(":")
        if len(parts) != 3:
            err("G1", f"边 {r.get('relation_id')} 的 glyph 目标格式非法：{r['to']}")
            continue
        repo, layer, key = parts
        if layer not in published_layers:
            err("G2", f"边 {r.get('relation_id')} 指向未发布的字形层 {layer}"
                      f"（v1 只允许 {sorted(published_layers)}）")
            continue
        if not glyph_avail:
            continue          # 兄弟仓库不在 → 这一段整体跳过（见上）
        path = os.path.join(glyph_repo, "glyphs", layer, f"{key}.svg")
        if not os.path.exists(path):
            glyph_missing += 1
            if glyph_missing <= 5:
                err("G3", f"边 {r.get('relation_id')} 的字形文件不存在：{path}")
        else:
            glyph_checked += 1

    # ---------- 报告 ----------
    print("=" * 62)
    print("hanzi-relations 校验报告")
    print("=" * 62)
    print(f"来源 sources      : {len(sources)}")
    print(f"字头 characters   : {len(characters)}")
    print(f"陈述 statements   : {len(statements)}")
    print(f"边   relations    : {len(relations)}")
    print(f"部首 radicals     : {len(radicals)}")
    print(f"注家 editions     : {len(editions)}")
    print(f"provenance 文件   : {len(prov_files)}")
    if glyph_avail:
        print(f"字形指针已核      : {glyph_checked} / {glyph_total}（缺失 {glyph_missing}）")
    else:
        print(f"字形指针          : **跳过**（{glyph_total} 条未核验）")
        print(f"                    字形仓库不可得：{glyph_repo}")
        print("                    指针的格式与层是否已发布仍已校验；"
              "「文件是否存在」要拿到兄弟仓库才谈得上。")
        print("                    CI 里用 sparse-checkout 取 zhouyongh/hanzi-glyphs-svg 即可恢复。")
    print()
    if warnings:
        print(f"警告 {len(warnings)} 条：")
        for w in warnings[:20]:
            print("  " + w)
        print()
    if errors:
        print(f"错误 {len(errors)} 条：")
        counts = Counter(e.split("]")[0][1:] for e in errors)
        for code, n in counts.most_common():
            print(f"  {code}: {n}")
        print()
        for e in errors[:40]:
            print("  " + e)
        if len(errors) > 40:
            print(f"  ... 还有 {len(errors) - 40} 条")
        print()
        print("校验未通过。")
        return 1
    print("校验通过：0 问题。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
