"""生成零构建的只读浏览页（单文件内嵌 gzip 数据版）。

为什么是单文件 + gzip：
- 初版把数据明文内联 → 12 MB HTML，浏览器解析与交互都很慢（实测点击超时）；
- 分片版（data/index.js + data/char/*.js）依赖动态 <script src>，
  在 file:// 下被 Chrome 的本地文件策略拦住，双击打不开；
- 现在：数据 gzip 后 2.29 MB，base64 内嵌进单个 HTML（约 3.1 MB），
  页面用浏览器原生 DecompressionStream 解压——**无外部请求、无依赖、file:// 可用**。

数据只含「说文系」核心（原文 + 徐铉注 + 徐锴注 + 段注 + 重文 + 字形指针）。
Unihan 的读音/异体等 21 万条在 data/*.jsonl 与 SQLite 里，供程序查。
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# JSONL 讀寫（分片支持）：見 src/jsonl_io.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jsonl_io import read_jsonl, read_jsonl_any, read_jsonl_dir  # noqa: E402
# 顯示層分詞（斷句讀 + 分詞；不進 data/，jieba 缺席時自動退化）
from display_text import wordize  # noqa: E402
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "viewer", "index.html")

# `--check`：只比对、不写盘（回归用）。
#
# 为什么需要：三页 HTML 是**产物**，data/ 一变它们就该跟着变，但没有任何校验器管这件事——
# 上一次「字头表瘦身」改了字头字段，页面里还嵌着旧结构，只有人点开才看得出。
# 有了 --check，CI 与 run_pipeline 都能问一句「这三页还是当前数据生成的吗」。
CHECK_ONLY = "--check" in sys.argv

_written = 0
_stale: list[str] = []


# ⚠ `mtime=0`：`gzip.compress` 默认把**当前时刻**写进 gzip 頭，
# 于是同一份 data/ 每次生成的页面字节都不同（内容一样、sha256 不同）。
# 这既违反硬规则 11（同输入必得同输出），也让 `--check` 永远判「过期」。
_B64 = lambda b: base64.b64encode(gzip.compress(b, 9, mtime=0)).decode("ascii")

def _size(path: str) -> int:
    """--check 下页面可能根本不存在（还没生成过），别让一句 print 把校验搞崩。"""
    return os.path.getsize(path) if os.path.exists(path) else 0


_B64_LINE = re.compile(r'const B64 = "([^"]*)";')


def emit(path: str, html: str) -> None:
    """写盘；`--check` 下只比对。

    ⚠ **比的是「骨架 + 解压后的 payload」，不是整页字节**。
    原先按整页字节比（`gzip mtime=0` 让同一台机器上确实逐字节可复现），
    但 **deflate 的输出取决于 zlib 的构建**：同一份输入在 Windows CPython
    与 GitHub Actions 的 Linux CPython 上压出的字节不同 → base64 不同 →
    整页不同。于是本地 `--check` 通过、CI 三个页面全红，而**数据一个字节都没错**。
    现在：① 页面骨架（把 base64 换成占位符后）必须逐字节相同；
    ② 内嵌 payload **解压后**必须逐字节相同。两者都比的是数据，不是压缩器。
    """
    global _written
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if CHECK_ONLY:
        try:
            with open(path, "r", encoding="utf-8", newline="\n") as fh:
                have = fh.read()
        except FileNotFoundError:
            _stale.append(f"{os.path.relpath(path, ROOT)}（缺文件）")
            return
        if _B64_LINE.sub('const B64 = "__DATA_B64__";', have) != \
                _B64_LINE.sub('const B64 = "__DATA_B64__";', html):
            _stale.append(f"{os.path.relpath(path, ROOT)}（骨架变了）")
            return
        m_have = _B64_LINE.search(have)
        m_new = _B64_LINE.search(html)
        if m_have and m_new:
            try:
                if gzip.decompress(base64.b64decode(m_have.group(1))) != \
                        gzip.decompress(base64.b64decode(m_new.group(1))):
                    _stale.append(f"{os.path.relpath(path, ROOT)}（内嵌数据过期）")
            except (OSError, ValueError):
                _stale.append(f"{os.path.relpath(path, ROOT)}（内嵌数据解不开）")
        return
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    _written += 1


# 康熙条目正文很长（46,827 条，平均数百字）。浏览页截断显示——
# 全文在 data/provenance/kangxi_dict.jsonl 与 SQLite 里，浏览页只作人工核验用。
KANGXI_TEXT_LIMIT = 260

EDITION_LABELS = {
    "shuowen_xu_shen": ["許慎《說文解字》原文", "大徐本正文"],
    "shuowen_xu_xuan": ["徐鉉校語", "大徐本校定之語"],
    "shuowen_xu_kai": ["徐鍇《說文解字系傳》", "小徐本"],
    "shuowen_duan_yucai": ["段玉裁《說文解字注》", "清·經韻樓本系統"],
    # 說文版本對照層（殆知閣來源的三本正文）
    "shuowen_da_xu": ["大徐本《說文解字》", "說文解字.md·逐字一行"],
    "shuowen_duan_ben": ["段玉裁本《說文解字注》", "上海古籍 81 年本"],
    "shuowen_xu_kai_ben": ["小徐本《說文解字系傳》", "臣鍇曰"],
    "leipian_cites_shuowen": ["《類篇》引《說文》", "宋·司馬光等"],
    "kangxi_dict": ["《康熙字典》", "清·張玉書等編"],
    "guangyun": ["《廣韻》", "中古音"],
    "cjk_decomp_structure": ["cjk-decomp 構形分解", ""],
    # Unicode 暫定欄位（unicode-org/unihan-database）
    "unihan_wip_definition": ["Unicode 字義釋文", "暫定欄位"],
    "unihan_wip_phonetic": ["Unicode 聲母/韻母編碼", "暫定欄位"],
    "unihan_wip_meyerwempe": ["Meyer-Empey 羅馬字讀音", "1941·歷史讀音"],
    "unihan_wip_alt_radicals": ["Unicode 異體部首檢字", "康熙檢字法"],
}

# ---- 对比页的「书族」与拟处置 ----
# 这是**评审用的展示分组**，不是数据契约：真正的族登记要等整理落地后
# 进 src/build_columns.py（硬规则 27）。这里先让对比页能按「一部书一族」并排，
# 好让人在同一屏里看出「哪一列是多余的」。
FAMILY_ORDER = ["說文系", "爾雅學", "韻書", "字書", "訓詁方言", "雜學",
                "蒙學詩文", "康熙", "Unihan", "工具"]
FAMILY_OF = {
    "xiaoxue_尔雅": "爾雅學", "xiaoxue_尔雅注": "爾雅學",
    "xiaoxue_尔雅注疏": "爾雅學", "xiaoxue_尔雅翼": "爾雅學", "xiaoxue_埤雅": "爾雅學",
    "guangyun": "韻書", "xiaoxue_重修广韵": "韻書", "xiaoxue_原本广韵": "韻書",
    "xiaoxue_集韵": "韻書", "xiaoxue_古今韵会举要": "韻書", "xiaoxue_洪武正韵": "韻書",
    "xiaoxue_孙氏唐韵考": "韻書", "xiaoxue_唐韵正": "韻書", "xiaoxue_切韵指掌图": "韻書",
    "xiaoxue_重修玉篇": "字書", "xiaoxue_类篇": "字書", "xiaoxue_龙龛手鉴": "字書",
    "xiaoxue_五经文字": "字書", "xiaoxue_佩觿": "字書", "xiaoxue_字鉴": "字書",
    "xiaoxue_九经字样": "字書", "xiaoxue_字防": "字書", "xiaoxue_字诂": "字書",
    "xiaoxue_释名": "訓詁方言", "xiaoxue_广雅": "訓詁方言",
    "xiaoxue_輶轩使者绝代语释别国方言": "訓詁方言", "xiaoxue_方言": "訓詁方言",
    "xiaoxue_续方言": "訓詁方言", "xiaoxue_别雅": "訓詁方言", "xiaoxue_骈雅": "訓詁方言",
    "xiaoxue_匡谬正俗": "雜學", "xiaoxue_班马字类": "雜學",
    "xiaoxue_急就篇": "雜學", "xiaoxue_六艺纲目": "雜學",
    "cjk_decomp_structure": "工具", "hanzi_frequency_rank": "工具",
    "opencc_s2t": "工具", "radical_map": "工具",
    "kangxi_dict": "康熙",
}


def family_of(e: str) -> str:
    if e in FAMILY_OF:
        return FAMILY_OF[e]
    if e.startswith("shuowen") or e == "leipian_cites_shuowen":
        return "說文系"
    if e.startswith("classic_"):
        return "蒙學詩文"
    if e.startswith("unihan"):
        return "Unihan"
    return "工具"


# 拟处置（同 `列整理 - 保留清单.md` §10/§12.3）：drop=下架 / demote=降级 / hold=待拍板
# ⚠ 已经执行过的处置不在这里留键：`shuowen_qjz`/`shuowen_dyc`（M014 删）、
# `xiaoxue_方言`/`classic_yudingquantshi`/`unihan_simplification`（M015 删）——
# 列都没了，留一条 verdict 只会让人以为它还挂着「待下架」。
VERDICT_OF = {
    "xiaoxue_尔雅注": "hold", "xiaoxue_埤雅": "hold", "xiaoxue_原本广韵": "hold",
    "shuowen_duan_ben": "hold",
}



def read_jsonl(name: str) -> list[dict]:
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def read_statements() -> list[dict]:
    """读全部陈述。

    陈述只存**逐源分文件**（data/provenance/<source_id>.jsonl），
    不再有全量副本 data/statements.jsonl——那样会重复一份 164 MB，
    且让「某来源授权状态变化时整块移除」变得做不到。
    """
    prov = os.path.join(DATA, "provenance")
    out: list[dict] = []
    if not os.path.isdir(prov):
        return out
    # provenance 的每個來源是一個**目錄**（內含 part-*.jsonl 分片）。
    # read_jsonl_any 兩種形態都吃，所以讀法不必關心某來源目前是哪一種。
    for name in sorted(os.listdir(prov)):
        path = os.path.join(prov, name)
        if os.path.isdir(path):
            out.extend(read_jsonl_any(path))
        elif name.endswith(".jsonl"):
            out.extend(read_jsonl(path))
    return out


def _all_statements() -> dict[str, dict]:
    """statement_id → 陈述（只读一次，供多处引用）。"""
    return {s["statement_id"]: s for s in read_statements()}


def main() -> int:
    characters = read_jsonl_dir(os.path.join(DATA, "characters"))
    statements = read_statements()
    relations = read_jsonl_dir(os.path.join(DATA, "relations"))
    sources = read_jsonl("sources.jsonl")
    editions = read_jsonl("editions.jsonl")
    radicals = read_jsonl("radicals.jsonl")

    # 收录范围：说文系（字形演变 + 版本对照）
    # 康熙字典有 46,815 条字头，内联后浏览页达 8.9 MB、实测加载超时，
    # 故**另做一页**（viewer/kangxi.html），主页面只收说文系。
    VIEW_WORKS = ("shuowen",)
    shuowen_chars = [c for c in characters if c.get("work_id") in VIEW_WORKS]
    sw_ids = {c["char_id"] for c in shuowen_chars}

    # ---- 跨来源同字：康熙的字头行与说文行共享 canonical_id（实测 10,697 / 11,129）----
    # 心智模型是「一个字一行、来源是列」：点开一个说文收的字，康熙那一列该在同一页上。
    # 但康熙条目挂在**它自己的字头行**上（work_id=kangxi），不并进来页面上就只剩
    # 说文系——用户会以为库里有康熙却显示不出来。
    # 只并 `kind=gloss`（說解正文）：康熙的内嵌引文有 10 万条，并进来页面直接超时；
    # 引文只给条数，正文去 kangxi.html / SQLite 看。
    xsrc_alias: dict[str, str] = {}
    for c in characters:
        cid = c["char_id"]
        if cid in sw_ids:
            continue
        cn = c.get("canonical_id") or cid
        if cn in sw_ids and c.get("work_id") not in VIEW_WORKS:
            xsrc_alias[cid] = cn

    stmts_by_char: dict[str, list[dict]] = defaultdict(list)
    xsrc_gloss: dict[str, list[dict]] = defaultdict(list)
    xsrc_cites: dict[str, int] = defaultdict(int)
    for s in statements:
        cid = s.get("char_id")
        if cid in sw_ids:
            stmts_by_char[cid].append(s)
        elif cid in xsrc_alias:
            if s.get("kind") == "gloss":
                xsrc_gloss[xsrc_alias[cid]].append(s)
            elif s.get("kind") == "citation":
                xsrc_cites[xsrc_alias[cid]] += 1

    glyph_by_char: dict[str, list[str]] = defaultdict(list)
    variant_of: dict[str, list[str]] = defaultdict(list)
    for r in relations:
        frm = r.get("from", "")
        if r.get("type") == "glyph" and frm.startswith("char:"):
            glyph_by_char[frm[5:]].append(r["to"])
        elif r.get("type") == "variant_of" and frm.startswith("char:"):
            # 变体边：from 是变体字头，to 是正字头
            variant_of[frm[5:]].append(r["to"])

    # ---- 字 → 部（D41.4 瘦身後，部首歸屬只走 `radical` 邊）----
    #
    # 字頭表已刪掉 `radical` 列，所以「這個字屬哪部」從邊裡取：
    #   radical:shuowen:<部名>  → 顯示部名（說文）
    #   radical:kangxi:<部號>   → 顯示**部首字**（康熙，部號 → 部首字查部表）
    kx_radical_char: dict[str, str] = {}
    for r in radicals:
        if r.get("system") == "kangxi" and r.get("radical"):
            kx_radical_char[str(r["radical"])] = r.get("char") or ""
    radical_by_char: dict[str, str] = {}
    for r in relations:
        if r.get("type") != "radical":
            continue
        frm, to = r.get("from", ""), r.get("to", "")
        if not (frm.startswith("char:") and to.startswith("radical:")):
            continue
        parts = to.split(":", 2)
        if len(parts) != 3:
            continue
        if parts[1] == "shuowen":
            radical_by_char[frm[5:]] = parts[2]
        elif parts[1] == "kangxi":
            radical_by_char[frm[5:]] = kx_radical_char.get(parts[2], parts[2])

    # 变体关系（古文/重文）→ 同一个字的不同写法
    # 例：《说文》「朙」是「明」的古文，两者是两个字头但指同一个字。
    #     搜「明」时必须能把「朙」的原文与段注带出来，否则看到的是空的。
    # 边是单向的（from=变体，to=正字），这里建成**双向**表。
    variant_pair: dict[str, set[str]] = defaultdict(set)
    for r in relations:
        if r.get("type") != "variant_of":
            continue
        frm = r.get("from", "")
        to = r.get("to", "")
        if not (frm.startswith("char:") and to.startswith("char:")):
            continue
        a, b = frm.split(":")[-1], to.split(":")[-1]
        variant_pair[a].add(b)
        variant_pair[b].add(a)

    # ---- 字头索引 ----
    # char_id → 字头（含 canonical_id：跨来源归并键）
    char_by_id = {c["char_id"]: c for c in characters}

    index = []
    for c in shuowen_chars:
        cid = c["char_id"]
        sts = (stmts_by_char.get(cid) or []) + xsrc_gloss.get(cid, [])
        if not sts:
            continue
        index.append({
            "id": cid, "h": c.get("head"), "v": c.get("volume"),
            "r": radical_by_char.get(cid, ""),
            "cp": c.get("modern_codepoints") or [],
            "py": c.get("pinyin") or "", "mp": c.get("mpinyin") or "",
            "n": len(sts), "w": c.get("work_id"),
            "cn": c.get("canonical_id") or cid,
            "sg": c.get("search_group") or cid,   # 检索可达组（异体/简繁 连通分量）
            # 与本字头互为古文/重文的字头（双向）
            "var": sorted(variant_pair.get(cid, set())),
        })

    # ---- 每字的陈述 ----
    # 康熙条目里的「内嵌引文」有 10 万条，全量内联会让页面到 11 MB（实测加载超时）。
    # 浏览页只存**被引书名与条数**（聚合），引文全文留给 SQLite 与 JSONL。
    detail: dict[str, list[dict]] = {}
    # **顺带带上「引文 ↔ 原文」的对照结果**：每条引文与今本原文对不对得上、
    # 差在哪里（节引/异文/反切不合…）——这是「引文脱误」的可实证材料。
    cite_books: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(dict))
    # ---- 说文轴的三样新东西 ----
    # (1) 版本对照：段注/小徐 与 大徐 的逐条差异类型
    variant_map: dict[str, list[dict]] = defaultdict(list)
    # (2) 形声结构：字 → 构件 / 声符
    comp_map: dict[str, list[dict]] = defaultdict(list)
    # (3) 说文/段注 引了哪些书
    sw_cites: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # (4) 引文 ↔ 引书原文 的对照结果（小学书正文层建的）
    sw_cite_align: dict[str, list[dict]] = defaultdict(list)
    # (5) 《類篇》引《說文》的对照结果（**与「版本对照」分开**——
    #     兩者都用 locator.對照說文/對照大徐 兩個鍵，但語義完全不同：
    #     版本對照是「段注本 vs 大徐本」，這裡是「《類篇》的引文 vs 《說文》原文」）
    leipian_align: dict[str, list[dict]] = defaultdict(list)
    # 对比页（compare.html）的正文：**不截断**、含反切行。
    # 浏览页按 KANGXI_TEXT_LIMIT 截断是为了体积；比对「哪一列更全」必须看全文本，
    # 否则长条被截成一様长，比出来的结论是反的。
    cmp_detail: dict[str, list[dict]] = defaultdict(list)
    for c in shuowen_chars:
        cid = c["char_id"]
        sts = (stmts_by_char.get(cid) or []) + xsrc_gloss.get(cid, [])
        if not sts:
            continue
        rows = []
        # B7（设计裁定 D53）：反切是一条**独立陈述**（kind=phonology，父指针回指说解）。
        # 详情行原来从 `s["reading"]` 取反切；现在 reading 欄已清空，
        # 反切改挂到**父陈述那一行**上显示——页面上一张卡片就是一个条目，
        # 反切是该条目的一个标记，不是另起一张卡（另起会让人以为有两处说解）。
        fq_by_parent = {s.get("parent_statement_id"): (s.get("text") or "")
                        for s in sts
                        if s.get("kind") == "phonology" and s.get("parent_statement_id")}
        fq_own = {s.get("statement_id") for s in sts
                  if s.get("kind") == "phonology" and s.get("parent_statement_id")}
        for s in sts:
            # 对比页取**完整正文**（浏览页按 KANGXI_TEXT_LIMIT 截断）：
            # 「哪一列更全」靠字数比，截断会把长条拉成一样长，比出来的结论是反的。
            # 康熙的**引书**正文不进（那是按次出现各一条，几十万条且与「列的全不全」无关）；
            # 其余列的 citation 要留——《全唐詩》《御定全唐詩》这些列的正文本身就是
            # citation（「《全唐詩》中出現 N 次」），跳过就等于把要比较的两列抹掉。
            _e = s.get("edition_id")
            if s.get("kind") == "citation" and _e == "kangxi_dict":
                pass
            elif s.get("char_id") != cid:
                # 跨来源行（康熙字头行并过来的 gloss）**不进对比页**：
                # 对比页比的是「同一字、说文系各本子谁更全」，康熙在 kangxi.html 有整列。
                pass
            else:
                _t = s.get("text") or ""
                _o = s.get("locator") or {}
                _lp = [f"{k}:{_o[k]}" for k in
                       ("volume", "radical", "seq", "编号", "編號", "page", "頁")
                       if _o.get(k) not in (None, "")]
                _cap = 200 if s.get("kind") == "citation" else 8000
                cmp_detail[cid].append({
                    "e": _e, "k": s.get("kind"),
                    "t": wordize(_t[:_cap]), "z": len(_t), "L": " · ".join(_lp[:3]),
                })
            if s.get("statement_id") in fq_own:
                continue        # 反切并到父行显示（下面 rows 的 "r"）
            if s.get("kind") == "citation":
                loc = s.get("locator") or {}
                book = loc.get("cited_book") or loc.get("引書") or "?"
                e = cite_books[cid][book]
                e["n"] = e.get("n", 0) + 1
                al = (loc.get("對照") or {}).get("類型")
                if al:
                    e.setdefault("al", {})
                    e["al"][al] = e["al"].get(al, 0) + 1
                # **说文/段注引书**（《方言》《釋名》等）也聚合进去
                src_book = loc.get("出處書")
                if loc.get("引書"):
                    sw_cites[cid][f"{loc['引書']}"] = \
                        sw_cites[cid].get(loc["引書"], 0) + 1
                    # 引文 ↔ 引书原文的对照结果（小学书正文层建的）
                    xa = loc.get("對照小學書")
                    if xa:
                        sw_cite_align[cid].append({
                            "bk": xa.get("書") or "",
                            "cb": xa.get("引書名") or "",
                            "mp": xa.get("書名映射") or "",
                            "an": xa.get("錨點") or "",
                            "al": xa.get("錨點長度") or 0,
                            "w": xa.get("加權分數") or 0,
                            "cv": bool(xa.get("卷別未核")),
                            "ty": xa.get("類型") or "",
                            "r": xa.get("相似度") or 0,
                            "q": (s.get("text") or "")[:70],
                            "o": (xa.get("原文") or "")[:90],
                        })
                # 《類篇》引《說文》的對照（含證據：相似度/錨點/錨點長度）
                # **必須在這個分支裏取**——這些陳述的 kind 是 citation，
                # 下面那個分支走不到（否則整塊永遠顯示不出來）。
                lp = loc.get("對照說文")
                if lp and lp.get("被引陳述"):
                    leipian_align[cid].append({
                        "i": s["statement_id"], "e": s.get("edition_id"),
                        "t": lp.get("類型") or "",
                        "r": lp.get("相似度") or 0,
                        "an": lp.get("錨點") or "",
                        "al": lp.get("錨點長度") or 0,
                        "h": lp.get("被引字頭") or "",
                    })
                continue        # 引文正文不入浏览页
            loc = s.get("locator") or {}
            # 版本对照结果（**只有** `對照大徐` 才是版本对照：
            # 段注本/小徐本 → 大徐本。`對照說文` 是《類篇》引《說文》的對照，
            # 語義完全不同，**不能混進同一個區塊**。）
            cmp_da = loc.get("對照大徐")
            if cmp_da:
                variant_map[cid].append({
                    "i": s["statement_id"], "e": s.get("edition_id"),
                    "t": cmp_da.get("類型") or "",
                    "r": cmp_da.get("相似度") or 0,
                    "b": cmp_da.get("大徐釋文") or "",
                })
            rows.append({
                "i": s["statement_id"], "k": s.get("kind"), "e": s.get("edition_id"),
                "t": wordize((s.get("text") or "")[:KANGXI_TEXT_LIMIT]),
                "r": fq_by_parent.get(s["statement_id"]) or s.get("reading"),
                "p": s.get("parent_statement_id"), "u": s.get("evidence_url"),
                "L": s.get("license"),
                "trunc": len(s.get("text") or "") > KANGXI_TEXT_LIMIT,
            })
        detail[cid] = rows
    cite_books = {k: dict(v) for k, v in cite_books.items() if v}

    # 形声结构：从 component / phonetic_component 边来
    for r in relations:
        if r.get("type") not in ("component", "phonetic_component"):
            continue
        frm, to = r.get("from", ""), r.get("to", "")
        if not (frm.startswith("char:") and to.startswith("char:")):
            continue
        a, b = frm.split(":")[-1], to.split(":")[-1]
        comp_map[a].append({
            "h": (char_by_id.get(b) or {}).get("head", ""),
            "t": r.get("type"), "k": r.get("kind"),
        })
    comp_map = {k: v for k, v in comp_map.items() if v}

    # ---- 说文 540 部 ↔ 康熙 214 部 的对照 ----
    # **这是独立于字头的一层**（挂在 radical_map work 下的字头上），
    # 所以单独收集：部首名 → {說文部, 康熙部號, 依據}。
    radical_map_data: list[dict] = []
    for s in statements:
        if s.get("source_id") != "radical_map":
            continue
        loc = s.get("locator") or {}
        radical_map_data.append({
            "sw": loc.get("shuowen_radical") or "",
            "kn": loc.get("kangxi_no") or "",
            "kr": loc.get("kangxi_radical") or "",
            "basis": loc.get("basis") or "",
            "n": loc.get("字數") or 0,
            "t": s.get("text") or "",
        })
    # 按「說文部」索引，便于页面上按说文部查
    radical_by_sw: dict[str, list[dict]] = defaultdict(list)
    for r in radical_map_data:
        if r["sw"]:
            radical_by_sw[r["sw"]].append(r)
    # **包含關係要反向整理**：一個說文部會被多個康熙部包含
    # （說文「水」部被康熙 85 部、2 部…都包含）。
    # 頁面上要顯示的是「本字頭所屬的說文部，被哪些康熙部包含」。
    contain_by_sw: dict[str, list[dict]] = defaultdict(list)
    for r in radical_map_data:
        if r["basis"] == "containment" and r["sw"]:
            contain_by_sw[r["sw"]].append(r)

    glyphs = {k: v for k, v in glyph_by_char.items() if v}

    src_meta = {s["source_id"]: {"n": s.get("name"), "l": s.get("license"),
                                 "u": s.get("url")} for s in sources}
    ed_meta = {e["edition_id"]: {"n": e.get("name"), "t": e.get("note"),
                                 "a": e.get("axis")} for e in editions}

    stats = {
        "characters": len(characters), "statements": len(statements),
        "relations": len(relations), "sources": len(sources),
        "radicals": len(radicals), "editions": len(editions),
        "viewChars": len(index),
    }

    # ---- 字例：經史用例（詩經、論語、孟子、大學、中庸、楚辭）----
    # 這一層體量大（8.5 萬條邊），只收「字 → 句子」的聚合與前若干條用例，
    # 完整內容在 data/provenance/classics.jsonl 與 SQLite 裡。
    classic_chars: list[dict] = []
    classic_by_head: dict[str, list[dict]] = defaultdict(list)
    classic_stat: dict[str, dict] = {}
    classic_rels: dict[str, list[str]] = defaultdict(list)

    cls_ids = {c["char_id"] for c in characters if c.get("work_id") == "classics"}
    for c in characters:
        if c.get("work_id") != "classics":
            continue
        h = c.get("head") or ""
        if not h or len(h) != 1:
            continue
        classic_by_head[h].append(c)
    classic_stat = {sid: s for sid, s in
                     _all_statements().items()
                     if s.get("source_id") == "classics"}

    # 字 → 句子（cited_in 边）
    for r in relations:
        if r.get("type") != "cited_in":
            continue
        frm = r.get("from", "")
        if frm.startswith("char:"):
            classic_rels[frm[5:]].append(r["to"].split(":")[-1])

    # ---- 简繁互查索引 ----
    # 说文系字头是繁体（說、東、車、馬），用户输入的是简体。
    # 实测 28 个常见简体字只有 1 个能直接命中说文字头——不补这层基本不可用。
    # 边（简体 → 繁体）建在各自来源的字头上，跨来源要靠 canonical_id 串联：
    # 说文的「說」与 OpenCC 的「說」是两个 char_id，但 canonical_id 相同。
    simp_index: dict[str, list[str]] = defaultdict(list)
    for r in relations:
        if r.get("type") != "variant_of":
            continue
        frm = r.get("from", "")
        to = r.get("to", "")
        if not (frm.startswith("char:") and to.startswith("char:")):
            continue
        simp_index[frm[5:]].append(to[5:])

    # 字头 → 可搜的简体形。
    # **按 search_group（检索可达组）归并，不是按 canonical_id**：
    # canonical 是「身份」，说文的「閒」(U+9592) 与 OpenCC 的「間」(U+9593)
    # 码位不同、身份不同，但 Unihan 的 kSpecializedSemanticVariant 把二者连成一组，
    # 搜简体「间」就应当能查到说文的「閒」。可达组已经把这条链算好了。
    simp_of: dict[str, list[str]] = defaultdict(list)
    for simp_cid, trad_cids in simp_index.items():
        sc = char_by_id.get(simp_cid)
        if not sc or not sc.get("head"):
            continue
        for tid in trad_cids:
            tc = char_by_id.get(tid)
            if not tc or not tc.get("head"):
                continue
            key = tc.get("search_group") or tc.get("canonical_id") or tid
            simp_of[key].append(sc["head"])

    out_lines = []
    for h, cl in sorted(classic_by_head.items()):
        n_sent = 0
        sample = []
        for c in cl:
            for sid in classic_rels.get(c["char_id"], []):
                s = classic_stat.get(sid)
                if not s:
                    continue
                n_sent += 1
                if len(sample) < 6:
                    loc = s.get("locator") or {}
                    sample.append({
                        "b": loc.get("book", ""), "c": loc.get("chapter", ""),
                        "t": s.get("text", ""),
                    })
        if n_sent:
            out_lines.append({
                "h": h, "n": n_sent, "s": sample,
                "ids": [c["char_id"] for c in cl],
            })
    classic_chars = out_lines

    # 把字例挂到单独一页（classics.html）——与说文系合并后 7.9 MB、加载超时
    classic_index = []
    seen_cls = set()
    for k in classic_chars:
        for cid in k.get("ids", []):
            if cid in seen_cls:
                continue
            seen_cls.add(cid)
            classic_index.append({
                "id": cid, "h": k["h"], "n": k["n"], "s": k["s"],
            })

    # 把「可搜的简体形」挂到字头上：c.sp = ["说","説",...]
    for c in index:
        sp = simp_of.get(c.get("sg") or c.get("cn") or c["id"])
        if sp:
            c["sp"] = sorted(set(sp))

    # ---- 列登记的「层级」：界面默认只展开 base 4 列（设计裁定 D54）----
    # 浏览页只收说文系，但列的层级是**全库口径**——直接从 data/columns.jsonl 读，
    # 不在这里另立一份（那是第二份事实源）。
    col_meta: dict[str, dict] = {}
    for spec in read_jsonl("columns.jsonl"):
        cid = spec.get("column_id")
        if not cid:
            continue
        col_meta[cid] = {
            "t": spec.get("tier"),                       # base / standard / extended
            # 分库名当「書系」分组：勾選欄按它排、按它分節（說文系/小學書/蒙學…）。
            # 这里只读登记表，不在界面另立一份分组规则（那是第二份事实源）。
            "b": spec.get("bundle") or "misc",
            "n": (ed_meta.get(cid) or {}).get("n") or cid,
        }

    payload = {
        "c": index,          # chars
        "s": detail,         # statements by char_id（引文正文已剥离）
        "g": glyphs,         # glyph pointers
        "b": cite_books,     # char_id -> {被引书名: 条数}
        "va": variant_map,   # char_id -> 版本对照差异（大徐 vs 段注/小徐）
        "cp": comp_map,      # char_id -> 构件/声符
        "sc": {k: dict(v) for k, v in sw_cites.items() if v},  # char_id -> {所引之书: 次数}
        "sa": {k: v for k, v in sw_cite_align.items() if v},  # 引文 ↔ 引书原文 对照
        "lp": {k: v for k, v in leipian_align.items() if v},  # 《類篇》引《說文》对照
        "rb": {k: v for k, v in radical_by_sw.items() if v},  # 说文部 -> 康熙部对照
        "rc": {k: v for k, v in contain_by_sw.items() if v},  # 说文部 -> 包含它的康熙部
        "src": src_meta,
        "ed": ed_meta,
        "cm": col_meta,      # 列层级：edition_id -> {t: tier, n: 列名}
        # 跨来源同字的内嵌引文条数（康熙那一列只并 gloss，引文只给条数）
        "xc": {k: v for k, v in xsrc_cites.items() if v},
        "st": stats,
        "lb": EDITION_LABELS,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(raw, 9, mtime=0)
    b64 = base64.b64encode(gz).decode("ascii")

    html = TEMPLATE.replace("__DATA_B64__", b64)
    emit(OUT, html)

    print(f"viewer: {OUT}")
    print(f"  HTML {_size(OUT)/1e6:.2f} MB "
          f"(原始 {len(raw)/1e6:.2f} MB → gzip {len(gz)/1e6:.2f} MB → base64 {len(b64)/1e6:.2f} MB)")
    print(f"  字頭 {len(index)}，字形指针 {len(glyphs)}")

    # ---- 对比页：同一字，各部书/各本子的正文并排（compare.html）----
    # 为什么单独一页：这是**评审工具**，不是给人日常查字的。
    # 要回答的是「同一部书的两列（原本/重修廣韻、爾雅/爾雅注疏）到底差多少」，
    # 并排才看得出，串在一个页面里上下翻是看不出的。
    cmp_payload = {
        "c": [{"id": c["id"], "h": c["h"], "r": c["r"], "v": c["v"], "n": c["n"]}
              for c in index],
        "s": {k: v for k, v in cmp_detail.items() if v},
        "cm": {e: {"n": m["n"], "t": m["t"], "f": family_of(e),
                   "d": VERDICT_OF.get(e, "keep")} for e, m in col_meta.items()},
        "fo": FAMILY_ORDER,
        "st": stats,
    }
    cp_raw = json.dumps(cmp_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    cp_gz = gzip.compress(cp_raw, 9, mtime=0)
    cp_b64 = base64.b64encode(cp_gz).decode("ascii")
    cp_out = os.path.join(ROOT, "viewer", "compare.html")
    emit(cp_out, COMPARE_TEMPLATE.replace("__DATA_B64__", cp_b64))
    print(f"viewer: {cp_out}")
    print(f"  HTML {_size(cp_out)/1e6:.2f} MB "
          f"(原始 {len(cp_raw)/1e6:.2f} MB → gzip {len(cp_gz)/1e6:.2f} MB)")
    print(f"  可比的字頭 {len(index)}")

    # ---- 字例页：单独一页（classics.html）----
    # 与说文系合并后 7.9 MB、浏览器加载直接超时（实测 >275 s），故独立成页。
    if not classic_index:
        # base 版没有诗文列：不生成空页（也清掉旧产物，别让 0 字頭的页面留在仓库里）
        stale = os.path.join(ROOT, "viewer", "classics.html")
        if os.path.exists(stale) and not CHECK_ONLY:
            os.remove(stale)
        print("viewer: 跳过 classics.html（本版没有诗文列，0 字頭）")
    else:
        cl_payload = {"c": classic_index, "st": stats}
        cl_raw = json.dumps(cl_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        cl_gz = gzip.compress(cl_raw, 9, mtime=0)
        cl_b64 = base64.b64encode(cl_gz).decode("ascii")
        cl_out = os.path.join(ROOT, "viewer", "classics.html")
        emit(cl_out, CLASSICS_TEMPLATE.replace("__DATA_B64__", cl_b64))
        print(f"viewer: {cl_out}")
        print(f"  HTML {_size(cl_out)/1e6:.2f} MB "
              f"(原始 {len(cl_raw)/1e6:.2f} MB → gzip {len(cl_gz)/1e6:.2f} MB)")
        print(f"  用字字頭 {len(classic_index)}")

    # ---- 康熙页：单独一页，只收康熙数据 ----
    kx_chars = [c for c in characters if c.get("work_id") == "kangxi"]
    kx_ids = {c["char_id"] for c in kx_chars}
    kx_stmts: dict[str, list[dict]] = defaultdict(list)
    # 康熙頁的被引書統計，**順帶帶上「引文 ↔ 原文」的對照結果**
    # （節引/異文/反切不合…）——引文脫誤的可證實材料。
    kx_books: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(dict))
    for st in statements:
        cid = st.get("char_id")
        if cid not in kx_ids:
            continue
        if st.get("kind") == "citation":
            loc = st.get("locator") or {}
            book = loc.get("cited_book") or "?"
            e = kx_books[cid][book]
            e["n"] = e.get("n", 0) + 1
            al = (loc.get("對照") or {}).get("類型")
            if al:
                e.setdefault("al", {})
                e["al"][al] = e["al"].get(al, 0) + 1
            continue
        kx_stmts[cid].append({
            "i": st["statement_id"], "k": st.get("kind"), "e": st.get("edition_id"),
            "t": wordize((st.get("text") or "")[:KANGXI_TEXT_LIMIT]),
            "r": st.get("reading"), "u": st.get("evidence_url"),
            "L": st.get("license"),
            "trunc": len(st.get("text") or "") > KANGXI_TEXT_LIMIT,
            "loc": st.get("locator") or {},
        })

    kx_index = []
    for c in kx_chars:
        cid = c["char_id"]
        if cid not in kx_stmts:
            continue
        kx_index.append({
            "id": cid, "h": c.get("head"), "v": c.get("volume"),
            "r": radical_by_char.get(cid, ""),
            "cp": c.get("modern_codepoints") or [],
            "n": len(kx_stmts[cid]),
        })
    kx_payload = {
        "c": kx_index,
        "s": {k: v for k, v in kx_stmts.items()},
        "b": {k: dict(v) for k, v in kx_books.items() if v},
        "src": src_meta, "ed": ed_meta, "st": stats, "lb": EDITION_LABELS,
    }
    kx_raw = json.dumps(kx_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    kx_gz = gzip.compress(kx_raw, 9, mtime=0)
    kx_b64 = base64.b64encode(kx_gz).decode("ascii")
    kx_html = KANGXI_TEMPLATE.replace("__DATA_B64__", kx_b64)
    kx_out = os.path.join(ROOT, "viewer", "kangxi.html")
    emit(kx_out, kx_html)
    print(f"viewer: {kx_out}")
    print(f"  HTML {_size(kx_out)/1e6:.2f} MB "
          f"(原始 {len(kx_raw)/1e6:.2f} MB → gzip {len(kx_gz)/1e6:.2f} MB)")
    print(f"  字頭 {len(kx_index)}")
    if CHECK_ONLY:
        if _stale:
            print("\n[FAIL] 以下页面与当前 data/ 不一致（跑 python src/build_viewer.py 重新生成）：")
            for p in _stale:
                print(f"  - {p}")
            return 1
        print("\n所有页面均为当前数据生成，未写盘。")
    return 0


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>漢字引用關係庫 · hanzi-relations</title>
<style>
:root{
  --bg:#fbfaf7; --panel:#fff; --ink:#1c1b18; --mute:#6b6862; --line:#e3ded4;
  --accent:#8a6d3b; --accent2:#3f5d52; --warn:#a1442f;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.75 "Noto Serif CJK SC","Source Han Serif SC","Songti SC",Georgia,serif}
header{background:var(--panel);border-bottom:1px solid var(--line);padding:18px 24px;
  position:sticky;top:0;z-index:10;box-shadow:0 1px 3px rgba(0,0,0,.04)}
h1{margin:0 0 4px;font-size:20px;letter-spacing:.02em}
.sub{color:var(--mute);font-size:13px}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;align-items:center}
input[type=search]{flex:1;min-width:220px;padding:9px 12px;font:inherit;font-size:15px;
  border:1px solid var(--line);border-radius:6px;background:#fff}
input[type=search]:focus{outline:2px solid var(--accent);outline-offset:-1px}
button{padding:9px 14px;font:inherit;font-size:14px;border:1px solid var(--line);
  border-radius:6px;background:#fff;cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}
button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
main{display:grid;grid-template-columns:minmax(280px,340px) 1fr;gap:0;
  min-height:calc(100vh - 108px)}
aside{border-right:1px solid var(--line);background:var(--panel);
  max-height:calc(100vh - 108px);overflow-y:auto;position:sticky;top:108px}
#list{padding:8px}
.item{padding:9px 12px;border-radius:6px;cursor:pointer;display:flex;
  justify-content:space-between;gap:8px;align-items:baseline}
.item:hover{background:#f3efe6}
.item.sel{background:var(--accent);color:#fff}
.item .m{font-size:11px;opacity:.65}
#detail{padding:24px 28px;max-width:1000px}
#detail h2{margin:0 0 4px;font-size:30px}
.meta{color:var(--mute);font-size:13px;margin-bottom:18px}
.sec{margin:22px 0 10px;padding-bottom:5px;border-bottom:1px solid var(--line);
  font-size:14px;color:var(--accent);letter-spacing:.06em}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:13px 15px;margin:9px 0}
.card .who{font-size:12px;color:var(--mute);margin-bottom:5px;
  display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tag{font-size:10.5px;padding:1.5px 6px;border-radius:3px;background:#efe9dc;
  color:var(--accent);border:1px solid #e2d9c6;white-space:nowrap}
.tag.duan{background:#e8f0ec;color:var(--accent2);border-color:#d5e2dc}
.tag.xuan{background:#f2ebf2;color:#6b4a72;border-color:#e2d5e2}
.tag.g{background:#eef2f7;color:#3b5670;border-color:#d6e0ec}
/* 同一张卡里若有多个 .txt（如原文 + 校语），靠 5px 间距分开，类别块都在行首同一处。 */
.txt + .txt{margin-top:5px}
.cite{margin:9px 0;padding:9px 12px;border-left:3px solid #d8cdb4;
  background:#fbf8f1;border-radius:0 6px 6px 0}
.cite .txt{font-size:15px;line-height:1.8;color:var(--ink);display:block;margin-top:3px;
  white-space:pre-wrap}
.txt{font-size:16px;line-height:1.85;white-space:pre-wrap}
.lnk{font-size:12px;color:var(--mute);margin-top:7px}
.lnk a{color:var(--accent2)}
.empty{color:var(--mute);padding:40px 0;text-align:center}
.kv{display:grid;grid-template-columns:88px 1fr;gap:4px 12px;font-size:14px}
.kv dt{color:var(--mute)}
.kv dd{margin:0}
.pill{display:inline-block;font-size:12px;padding:2px 8px;border-radius:10px;
  background:#f0ebe0;color:var(--accent);margin:2px 4px 2px 0}
.pill.g{background:#eef2f7;color:#3b5670}
/* 陳述 kind 徽章：一列裏混着釋文、反切、校語，不標類別就分不清
   「哪條是釋義、哪條是音」。配色只為分組，不是裝飾。 */
.kb{font-size:10.5px;padding:1px 5px;border-radius:3px;background:#5c7a68;color:#fff;
  white-space:nowrap;letter-spacing:.04em;vertical-align:2px}
.kb.phon{background:#8a6d3b}
.kb.note{background:#8d8578}
.kb.cite{background:#6b7f8a}
.kb.var{background:#7b6a8a}
.kb.st{background:#4f6b7a}
.kb.lk{background:#7c7768}
.kb.fq{background:#a08a6a}
.rdg{font-size:13px;color:#8a6d3b;margin-right:2px}
/* 正文起頭的類別標籤（「注」「釋義」「音」…）：**小色塊**，一個類別詞一塊。
   尺寸 / 配色沿用 4e90712 的 `.kb` 徽章（10.5px、八種顏色）——讀者認的就是這套；
   位置仍在正文開頭（62f6984 的改进：類別必須是正文的第一個詞）。 */
.lbl{font-size:10.5px;line-height:1.5;padding:1px 5px;border-radius:3px;
  letter-spacing:.04em;background:#5c7a68;color:#fff;vertical-align:2px;
  margin-right:5px;white-space:nowrap}
.lbl.note{background:#8d8578}
.lbl.cite{background:#6b7f8a}
.lbl.var{background:#7b6a8a}
.lbl.st{background:#4f6b7a}
.lbl.lk{background:#7c7768}
.lbl.fq{background:#a08a6a}
.lbl.phon{background:#8a6d3b}
/* 康熙條目分段（kwHtml）：一義一段，「又」做義項標記，【書證】做小色塊。
   條目正文中位 26 字、最長 2,084 字，不分段就是「讀到第 300 字還在同一段裏」。 */
.sense{margin:3px 0}
.sense + .sense{margin-top:5px}
.y{font-weight:700;color:#8a6d3b;margin-right:4px}
.bk{font-size:.85em;padding:0 4px;border-radius:3px;background:#f1f1ef;color:#5f5f5c}
.bk.m{background:#f1f1ef;color:#5f5f5c}
/* 來源列開關（規格 D54）：常駐展開時 89 個 chip 一屏放不下，看着就是一團。
   收成一條「來源列 · 已開 5 / 可選 N」，點開才按 tier × bundle 成表：
   左邊一欄書系名（說文系/小學書/蒙學與詩文/Unihan/其他），右邊是 chip。 */
.cols{margin:4px 0 6px;padding:0;background:#f7f4ec;border:1px solid var(--line);
  border-radius:9px;font-size:13px;color:var(--mute);overflow:hidden}
.cols .hd{display:flex;gap:9px;align-items:center;padding:9px 13px;font-size:12.5px;
  color:var(--accent);letter-spacing:.05em;cursor:pointer;user-select:none}
.cols .hd .sum{color:var(--mute);letter-spacing:0;font-size:12px}
.cols .hd .tog{margin-left:auto;font-size:12px;color:var(--accent);background:#fff;
  border:1px solid #e2d9c6;border-radius:6px;padding:2px 9px}
.cols .body{padding:2px 13px 11px;border-top:1px solid var(--line)}
.cols .body[hidden]{display:none}
.cols .tier{font-size:11px;color:#8a8071;letter-spacing:.08em;margin:11px 0 5px;
  display:flex;gap:8px;align-items:center}
.cols .tier .n{color:#b0a89d;letter-spacing:0}
.cols .grid{display:grid;grid-template-columns:58px 1fr;gap:6px 10px;align-items:start}
.cols .grp{font-size:11px;color:#b0a89d;padding-top:5px;letter-spacing:.05em;
  text-align:right;white-space:nowrap}
.cols .line{display:flex;flex-wrap:wrap;gap:5px 6px;align-items:center}
.chip{display:inline-flex;gap:5px;align-items:center;cursor:pointer;padding:2px 9px;
  border-radius:12px;background:#fff;border:1px solid var(--line);font-size:12.5px;
  color:var(--ink)}
.chip:hover{border-color:var(--accent)}
.chip input{margin:0;cursor:pointer}
.chip.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.chip.on .n{color:#fff;opacity:.7}
.chip.all{background:#efe9dc;color:var(--accent);font-weight:600;border-radius:6px;
  font-size:11.5px;padding:1px 8px}
.chip .n{font-size:10.5px;color:#a9a29a}
/* 左側：不是「一列 300 個字」，而是同部 / 異體 / 同聲符三組字格 */
#list .sec2{font-size:11px;color:#9a938a;letter-spacing:.08em;margin:14px 6px 7px}
#list .sec2:first-child{margin-top:6px}
.chips{display:flex;flex-wrap:wrap;gap:5px;padding:0 4px}
.gchip{min-width:38px;text-align:center;padding:5px 7px 3px;border-radius:7px;background:#fff;
  border:1px solid var(--line);cursor:pointer;font-size:17px;line-height:1.15}
.gchip:hover{border-color:var(--accent);background:#f7f4ec}
.gchip.sel{background:var(--accent);color:#fff;border-color:var(--accent)}
.gchip .k{display:block;font-size:9.5px;opacity:.55;font-weight:400}
.gchip.sel .k{opacity:.75}
.colblock[hidden]{display:none}
.stat{font-size:12.5px;color:var(--mute);margin-left:auto}
@media(max-width:860px){main{grid-template-columns:1fr}
  aside{position:static;max-height:230px;border-right:0;border-bottom:1px solid var(--line)}}
</style>
</head>
<body>
<header>
  <h1>漢字引用關係庫 <span style="font-size:13px;color:var(--mute)">hanzi-relations</span></h1>
  <div class="sub">以字為索引，把各部典籍對同一個字的說解、版本差異、字形/部首/音韻/異體的關係，結構化成可追溯的引用關係。</div>
  <div class="bar">
    <input type="search" id="q" placeholder="輸入漢字、拼音、部首…（如：明、月、ming、肉）" autocomplete="off">
    <button id="btnAll" class="on">全部</button>
    <button id="btnDuan">有段注</button>
    <span class="stat" id="stat">解壓中…</span>
  </div>
</header>
<main>
  <aside><div id="list"></div></aside>
  <section id="detail"><div class="empty">在上方輸入一個字（如：明、月、說），或點左側同部字。</div></section>
</main>
<script>
const B64 = "__DATA_B64__";
const el = id => document.getElementById(id);
let D = null, sel = null;

function b64ToBytes(b64){
  const bin = atob(b64);
  const n = bin.length;
  const bytes = new Uint8Array(n);
  for (let i = 0; i < n; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}
async function load(){
  const bytes = b64ToBytes(B64);
  const ds = new DecompressionStream('gzip');
  const stream = new Blob([bytes]).stream().pipeThrough(ds);
  const text = await new Response(stream).text();
  D = JSON.parse(text);
  build();
  el('stat').textContent = '字頭 ' + D.st.viewChars + ' · 陳述 ' +
    D.st.statements.toLocaleString() + ' · 邊 ' + D.st.relations.toLocaleString();
  renderList(D.c);
}

/* 拼音去聲調：上游寫 yī / yuán / shànɡ（ɡ=U+0261），
   使用者搜 ming / yue / shang，索引與查詢都要去聲調。 */
const TONE = {'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e',
  'ī':'i','í':'i','ǐ':'i','ì':'i','ō':'o','ó':'o','ǒ':'o','ò':'o',
  'ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'v','ǘ':'v','ǚ':'v','ǜ':'v','ü':'v',
  'ń':'n','ň':'n','ǹ':'n','ḿ':'m','ẑ':'z','ɡ':'g'};
function strip(s){
  let o = '';
  for (const ch of s) o += TONE[ch.toLowerCase()] || ch.toLowerCase();
  return o;
}

const byHead = new Map(), byPinyin = new Map(), byRadical = new Map(), byCp = new Map();
const byText = new Map();   // 说文原文里出现该字的条目（互训、连文）
const byVar = new Map();    // 变体（古文/重文）→ 正字头
const byClassic = new Map();// 字例：字 → 用例信息
const bySimp = new Map();   // 简体 → 繁体字头（说文字头是繁体，用户输入是简体）
const byCanon = new Map();  // canonical_id → 所有该字的字头（跨来源归并）
const byGroup = new Map();  // search_group → 该组内所有字头（检索可达组）
const duanSet = new Set();
function push(map, k, c){
  if (!k) return;
  if (!map.has(k)) map.set(k, []);
  const a = map.get(k);
  if (a.indexOf(c) < 0) a.push(c);
}
function build(){
  for (const c of D.c){
    push(byHead, c.h, c);
    push(byPinyin, strip(c.py), c);
    push(byPinyin, strip(c.mp), c);
    push(byRadical, c.r, c);
    /* canonical：跨来源归并键。同一现代字在各个来源各有一个字头，
       靠它串起来——否则「说 → 說」的边连不到说文的字头上。 */
    if (c.cn) push(byCanon, c.cn, c);
    /* search_group：检索可达组——「同一字的各种写法」的连通分量。
       canonical 是**身份**（按码位），search_group 是**可达性**（异体/简繁连起来）。
       例：说文的「閒」(U+9592) 与「間」(U+9593) 是两个身份、两个码位，
       但 Unicode 的专义异体把它们连成一组——搜简体「间」要能查到说文的「閒」。 */
    if (c.sg) push(byGroup, c.sg, c);
    /* 简体 → 繁体字头。反查时必须走这条：
       《说文》的字头是繁体（說、東、車），用户输入简体。
       不建这张表，搜「说」根本找不到「說」。 */
    for (const sp of (c.sp || [])) push(bySimp, sp, c);
    /* 现代码位 → 字头。反查时必须走这条：
       《说文》的字头与现代字**不是一一对应**——如「明」在《说文》里是「朙」，
       只按字头字面匹配会找不到。 */
    for (const cp of c.cp || []) push(byCp, cp, c);
    /* 原文互训：如「朙」条下说「明也」，搜「明」应能命中「朙」。 */
    for (const s of (D.s[c.id] || [])){
      if (s.k === 'gloss' && s.e === 'shuowen_xu_shen' && s.t){
        for (const ch of s.t){
          if (ch >= '一' && ch <= '鿿') push(byText, ch, c);
        }
      }
    }
    /* 变体：本字头与 c.var 里那些字头互为古文/重文。
       搜「明」时要把正字头「朙」一起带出来——否则用户只看到一条重文说明。 */
    for (const v of (c.var || [])){
      const other = D.c.find(x => x.id === v);
      if (other){ push(byVar, other.h, c); push(byVar, c.h, other); }
    }
  }
  /* 字例索引：字 → 用例信息 */
  for (const k of (D.k || [])) byClassic.set(k.h, k);
  for (const id in D.s){
    for (const s of D.s[id]){
      if (s.e === 'shuowen_duan_yucai'){ duanSet.add(id); break; }
    }
  }
}
function search(q){
  if (!q) return D.c;
  /* 《说文》的字头与现代字不一一对应，「明」这一组尤其典型：
       - 「朙」是 U+6719，《说文》「朙」部说「明也」
       - 「明」是 U+660E，是「朙」的古文，单独一个字头，只有一条重文说明
     所以搜「明」时，字面命中、码位反查、部首同名、原文互训**都要收**。
     互训命中（原文里出现该字）相关度最低，排最后。 */
  const out = [], weak = [];
  const add = (arr, w) => {
    if (!arr) return;
    for (const c of arr) {
      const list = w ? weak : out;
      if (list.indexOf(c) < 0) list.push(c);
    }
  };
  add(byHead.get(q), 0);
  add(byRadical.get(q), 0);
  add(byCp.get(cpOf(q), 0), 0);
  /* 简体 → 繁体字头：说文字头是繁体（說、東、車），用户输入简体。
     不建这张表，搜「说」找不到「說」——实测 28 个常见简体字只有 1 个能直接命中。 */
  add(bySimp.get(q), 0);
  /* 变体（古文/重文）：「明」↔「朙」是同一个字，两个方向都收 */
  add(byVar.get(q), 0);
  /* 部首名与查询词相同的字（如查「明」部） */
  for (const c of D.c) if (c.r === q && out.indexOf(c) < 0) out.push(c);
  /* 跨来源归并：命中任何一个字头，把同一 canonical 的其他来源字头也带出来。
     否则搜「说」只命中 OpenCC 的字头，看不到说文的「說」条目。 */
  for (const c of out.slice()){
    const same = byCanon.get(c.cn);
    if (same) add(same, 0);
  }
  /* 检索可达组：把同组（同字的不同写法）的字头都带出来。
     这一步让「简 → 繁 → 说文古文/专义异体」的多跳链能走通：
     搜简体「间」→ 繁「間」→（专义异体）→ 说文「閒」。 */
  for (const c of out.slice()){
    const same = byGroup.get(c.sg);
    if (same) add(same, 0);
  }
  /* 原文互训：如「朙」条下说「明也」，搜「明」应能命中「朙」。 */
  add(byText.get(q), 1);
  if (out.length || weak.length) return out.concat(weak);
  const s = strip(q);
  for (const [k, arr] of byPinyin) if (k.indexOf(s) >= 0) add(arr, 0);
  return out;
}
/* 一个字符 → 它的 Unicode 码位（用于按码位反查字头） */
function cpOf(ch){
  if (!ch || ch.length !== 1) return null;
  const o = ch.codePointAt(0);
  if (o < 0x10000) return o.toString(16).toUpperCase().padStart(4,'0');
  return o.toString(16).toUpperCase();
}
function esc(s){ return String(s==null?'':s).replace(/[&<>]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function edLabel(eid){
  const L = D.lb[eid];
  if (L) return L[0];
  return (D.ed[eid] && D.ed[eid].n) || eid;
}
function tagCls(eid){
  if (eid === 'shuowen_duan_yucai') return 'duan';
  if (eid === 'shuowen_xu_xuan' || eid === 'shuowen_xu_kai') return 'xuan';
  return '';
}
/* 陳述 kind 的徽章（釋義 / 注 / 音 / 校語 / 引文…）。
   一列裏的條目不是同一類東西：說文條下既有釋文也有反切與徐鉉校語，
   不把類別標出來，讀起來就是一串長短不一的字，分不清哪條是「誰說的什麼」。
   與 compare.html 用同一套叫法（那邊是並排比列，這邊是逐條讀）。 */
const KIND = {gloss:'義', note:'校語', commentary:'注', phonology:'音',
  citation:'引文', variant_note:'異體', structure:'構形', radical_lookup:'檢字',
  radical_compare:'部對照', frequency:'字頻', radical_alt:'部首', serial:'刻本號'};
const KCLS = {phonology:'phon', commentary:'note', note:'note', citation:'cite',
  variant_note:'var', structure:'st', radical_lookup:'lk', radical_compare:'lk',
  frequency:'fq', radical_alt:'lk', serial:'note'};
/* 正文前的类别标签：「注」「釋義」「音」……
   类别必须是正文的**第一个词**（读者原话：「注：xxx / 釋義：xxx」），
   但只上色不留底色，扫读时还是看不出分段——所以套成**一块底色**（.lbl），
   一个类别词一块，段注卡读起来就是「[釋義] 密也。从用、口。／[注] 密也。 密、山部曰…」。 */
function klab(k){
  const t = KIND[k];
  if (!t) return '';
  return '<span class="lbl ' + (KCLS[k] || '') + '" title="kind=' + esc(k) + '">' +
         esc(t) + '</span>';
}
/* 康熙條目是**一坨**：反切、義項、書證連排（中位 26 字、最長 2,084 字），不分段就是
   「讀到第 300 字還在同一段裏」。兩刀：
   ① 以「　又」分義項——「又至也」「又忠信也」是這類字書的義項起頭，各成一段；
   ② 【書名·篇名】做成小色塊（有的很長，如【左傳·昭二十年】），一眼看出是書證、
      不是正文；註／傳／箋／疏這類單字標記換一色，免得把「註」當成書名。 */
const KW_MARK = new Set(['註', '注', '傳', '箋', '疏', '正義', '釋文', '說文', '案', '集解']);
function kwHtml(t){
  return String(t || '').split(/\u3000(?=又)/).map(function (seg, i) {
    let s = esc(seg);
    s = s.replace(/【([^】]{1,60})】/g, function (m, name) {
      return '<span class="bk' + (KW_MARK.has(name) ? ' m' : '') + '">' + name + '</span>';
    });
    if (i > 0) s = '<span class="y">（又）</span>' + s.replace(/^又/, '');
    return '<div class="sense">' + s + '</div>';
  }).join('');
}
/* 反切的「音」也放正文开头（与 義 / 注 同一个位置）：先一行 [音] 五結切，再正文。
   读者原话：「就是那个地方，釋義 和 音 都在那个地方」——类别徽章只在正文左侧出现一处，
   不再散在头部那行（头部只留书名与许可）。 */
function ktxt(s){
  if (/^kangxi/.test(s.e || '')) return klab(s.k) + kwHtml(s.t);
  const rdg = s.r
    ? '<span class="lbl phon" title="反切（本條的音）">音</span>' +
      '<span class="rdg">' + esc(s.r) + '</span><br>'
    : '';
  return rdg + klab(s.k) + esc(s.t);
}
let onlyDuan = false;
function rows(q){
  const r = search(q).slice();
  /* 排序优先级：
       1. 变体直系（古文/重文）——「明」与「朙」是同一个字，必须排最前
       2. 有许慎原文的字头
       3. 陈述多者
     《说文》里「明」是「朙」的古文，两个字头各立一条；若不排序，
     搜「明」先看到的是只有一条重文说明的「明」，看不到真正的释义。 */
  r.sort((a, b) => {
    const va = isVarPair(a, q) ? 1 : 0, vb = isVarPair(b, q) ? 1 : 0;
    if (va !== vb) return vb - va;
    const ga = hasGloss(a) ? 1 : 0, gb = hasGloss(b) ? 1 : 0;
    if (ga !== gb) return gb - ga;
    return (D.s[b.id]||[]).length - (D.s[a.id]||[]).length;
  });
  return onlyDuan ? r.filter(c => duanSet.has(c.id)) : r;
}
/* c 与查询词是否构成变体关系（古文/重文），**且 c 本身有许慎原文**。
   「明」是「朙」的古文，两个字头都算直系；但「明」自己只有一条重文说明，
   没有原文——真正能代表这个字的是「朙」。所以只有「有原文」的那个才优先。 */
function isVarPair(c, q){
  if (!q) return false;
  if (!hasGloss(c)) return false;
  if (c.h === q) return true;
  for (const v of (c.var || [])){
    const t = D.c.find(x => x.id === v);
    if (t && t.h === q) return true;
  }
  return false;
}function hasGloss(c){
  for (const s of (D.s[c.id] || [])){
    if (s.k === 'gloss' && s.e === 'shuowen_xu_shen') return true;
  }
  return false;
}
/* 左側不是「一列 300 個字」——那是資料傾倒，不是導覽。
   改成幾組字格：檢索結果（有查詢時）/ 同部 / 異體·重文 / 同聲符。
   每格只放字與條數（部首與卷次在 hover），一屏能掃完，點一下就換字。 */
let PHO = null;                       /* 聲符 -> [字頭 id]，首次用到時建 */
function buildPho(){
  PHO = new Map();
  for (const cid in (D.cp || {})){
    for (const x of D.cp[cid]){
      if (x.t !== 'phonetic_component' || !x.h) continue;
      if (!PHO.has(x.h)) PHO.set(x.h, []);
      PHO.get(x.h).push(cid);
    }
  }
}
function pick(c){
  sel = c.id;
  /* 用 replaceState 而不是改 location.hash：同一頁連點十個字不該留十條歷史 */
  try { history.replaceState(null, '', '#' + encodeURIComponent(c.h || '')); } catch (e) {}
  renderSide(rows((el('q').value || '').trim()), c);
  renderDetail(c);
}
function renderSide(rs, c){
  const list = el('list'); list.innerHTML = '';
  const byId = new Map(D.c.map(x => [x.id, x]));
  const q = (el('q').value || '').trim();
  /* 字格只放**单字**：像「□」这种占位字头、或带前缀的伪字头不进導覽。
     （它们在数据里是已知缺口 G001，正文照样能查，但不该占着字格第一位。） */
  const ONE = /^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u{20000}-\u{3ffff}]$/u;
  const sect = (title, chars, more) => {
    chars = (chars || []).filter(x => ONE.test(x.h || ''));
    if (!chars.length) return;
    const t = document.createElement('div'); t.className = 'sec2'; t.textContent = title;
    list.appendChild(t);
    const box = document.createElement('div'); box.className = 'chips';
    for (const x of chars){
      const d = document.createElement('div');
      d.className = 'gchip' + (c && x.id === c.id ? ' sel' : '');
      d.title = (x.r ? '《說文》' + x.r + '部 · ' : '') + (x.n || 0) + ' 條' +
                (x.v ? ' · ' + x.v : '');
      d.innerHTML = esc(x.h) + '<span class="k">' + (x.n || 0) + '</span>';
      d.onclick = () => pick(x);
      box.appendChild(d);
    }
    list.appendChild(box);
    if (more){
      const m = document.createElement('div'); m.className = 'empty';
      m.style.fontSize = '11.5px'; m.style.padding = '8px 0 0'; m.textContent = more;
      list.appendChild(m);
    }
  };

  if (q){
    sect('檢索 · ' + rs.length + ' 個字頭', rs.slice(0, 60),
         rs.length > 60 ? '僅列前 60，請縮小範圍（或加部首、拼音）' : '');
  }
  if (c){
    const same = D.c.filter(x => x.id !== c.id && x.r && x.r === c.r)
                    .sort((a, b) => String(a.v || '').localeCompare(String(b.v || '')) ||
                                    (b.n || 0) - (a.n || 0));
    sect('同部 · ' + (c.r || '—') + '部 ' + same.length + ' 字', same.slice(0, 48),
         same.length > 48 ? '另有 ' + (same.length - 48) + ' 字' : '');
    const vars = (c.var || []).map(id => byId.get(id)).filter(Boolean);
    sect('異體 · 重文 · 古文 ' + vars.length, vars.slice(0, 40));
    if (!PHO) buildPho();
    const sibIds = new Set();
    for (const x of (D.cp || {})[c.id] || []){
      if (x.t !== 'phonetic_component' || !x.h) continue;
      for (const id of (PHO.get(x.h) || [])) if (id !== c.id) sibIds.add(id);
    }
    const sibs = [...sibIds].map(id => byId.get(id)).filter(Boolean)
                            .sort((a, b) => (b.n || 0) - (a.n || 0));
    sect('同聲符 ' + sibs.length, sibs.slice(0, 40),
         sibs.length > 40 ? '另有 ' + (sibs.length - 40) + ' 字' : '');
  }
  if (!list.children.length){
    list.innerHTML = '<div class="empty" style="font-size:13px;padding:24px 10px">' +
      '在上方輸入一個字、部首或拼音。<br>選中一個字之後，這裡給出同部、異體與同聲符的字。</div>';
  }
}
function renderList(rs){ renderSide(rs, null); }
function renderDetail(c){
  const sts = D.s[c.id] || [];
  /* 說文原文：默认视图只放 base 层的「許慎原文」一列。
     大徐本不在 base（它与許慎原文是同一部书的另一个转录本，73% 逐字相同，
     并排显示就是复制），以后按需作标准列加入。 */
  const GLOSS_EDS = ['shuowen_xu_shen'];
  const gloss = sts.filter(s => s.k === 'gloss' && GLOSS_EDS.indexOf(s.e) >= 0);
  /* 段注区**只放段玉裁注一列**（`shuowen_duan_yucai`）。
     段注本（`shuowen_duan_ben`，殆知閣本）是**校勘对照本**，按「分得清」这条判据
     它不该进默认区（列整理 §12.2：默认显示用段注，它按需勾出）：
     实测它 43.0% 的字头有一条 >120 字连排，而且**条目会挤在一起**
     （周 那一条把「周」「唐」两条并成一段，反切「徒郞切」也留在正文里）。
     把两列都塞进「段注」区，读到的就是一坨看不出是两条的正文——
     那正是「分類不清」的来源。所以 duan_ben 归到下面的勾选栏（tier=standard）。 */
  const duan  = sts.filter(s => s.e === 'shuowen_duan_yucai');
  const other = sts.filter(s => GLOSS_EDS.indexOf(s.e) < 0
                              && s.e !== 'shuowen_duan_yucai');
  const gb = new Map(gloss.map(s => [s.i, s]));

  let h = '<h2>' + esc(c.h) + '</h2>';
  h += '<div class="meta"><dl class="kv">';
  h += '<dt>說文部</dt><dd>' + esc(c.r || '—');
  /* 康熙 214 部對照：本字頭屬的說文部對應到康熙第幾部。
     依據有三種——同名、同名（簡繁）、部首歸屬（康熙部首字在說文裏屬哪一部）。 */
  const rb = (D.rb || {})[c.r];
  if (rb && rb.length){
    /* 兩種關係分開顯示：
       - same_name / same_name_variant：部首等同（康熙第 N 部＝說文「X」部）
       - radical_owner：部首歸屬（康熙部首字在說文裏屬哪一部） */
    const eq = rb.filter(x => x.basis === 'same_name' || x.basis === 'same_name_variant');
    const ow = rb.filter(x => x.basis === 'radical_owner');
    if (eq.length)
      h += '　<span style="color:var(--mute);font-size:12px">康熙第 ' + esc(eq[0].kn) + ' 部（部首「' + esc(eq[0].kr) + '」）</span>';
    if (ow.length)
      h += '　<span style="color:var(--mute);font-size:12px">該部首字在《說文》屬「' + esc(ow[0].sw) + '」部</span>';
  }
  /* 包含關係：本字頭所屬的說文部，被哪些康熙部包含 */
  const rc = (D.rc || {})[c.r];
  if (rc && rc.length){
    const ks = {};
    for (const x of rc) ks[x.kn] = {kn: x.kn, kr: x.kr, n: (ks[x.kn] ? ks[x.kn].n : 0) + (x.n || 0)};
    const arr = Object.values(ks).sort((a,b) => b.n - a.n);
    h += '<div class="lnk">本《說文》部被康熙 ' + arr.length + ' 部包含：' +
         arr.slice(0,6).map(x => '第'+esc(x.kn)+'部（'+esc(x.kr)+'，'+x.n+'字）').join('、') +
         (arr.length > 6 ? ' 等' : '') + '</div>';
  }
  h += '</dd>';
  h += '<dt>卷次</dt><dd>' + esc(c.v || '—') + '</dd>';
  h += '<dt>現代碼位</dt><dd>' + ((c.cp||[]).map(x=>'U+'+x).join('、') || '—') + '</dd>';
  if (c.py) h += '<dt>說文音</dt><dd>' + esc(c.py) + '</dd>';
  if (c.mp && c.mp !== c.py) h += '<dt>普通話</dt><dd>' + esc(c.mp) + '</dd>';
  h += '</dl></div>';

  /* 來源列開關放在這裡：標題與碼位之下、說文原文之上。
     讀的順序是「這字是誰、屬哪部、什麼音」→「我要看哪幾家的說法」→「原文」。
     **默認收起**：這個字在多少列裏有內容，是十幾到八十九個 chip 的差別，
     全部攤開等於把正文擠到第二屏以下。收起時只留一行「已開 4 列 · 可選 N」，
     點開才按 tier × bundle 成表（左邊書系名、右邊 chip），同組按條數降序。 */
  {
    const cm = D.cm || {};
    const tierOf = e => (cm[e] || {}).t || 'extended';
    const colName = e => (cm[e] || {}).n || edLabel(e);
    const bundleOf = e => (cm[e] || {}).b || 'misc';
    const BNAME = {shuowen:'說文系', xiaoxue:'小學書', classics:'蒙學與詩文',
                   unihan:'Unihan', misc:'其他', base:'預設'};
    const BORDER = ['shuowen','xiaoxue','classics','unihan','misc','base'];
    const picked = {};
    for (const s of other){
      if (tierOf(s.e) === 'base') continue;
      (picked[s.e] = picked[s.e] || []).push(s);
    }
    const eds = Object.keys(picked);
    const baseEds = Object.keys(cm).filter(e => cm[e].t === 'base');
    const nBase = baseEds.length;
    if (eds.length){
      const rank = e => { const i = BORDER.indexOf(bundleOf(e)); return i < 0 ? 99 : i; };
      /* 列名从 `cm` 取，不写死：写死的名单在加/减列时必然漂移
         （base 从 5 列减到 4 列，这行就曾把「大徐本」写成一个不存在的列）。 */
      const baseNames = baseEds.map(e => (cm[e] || {}).n || edLabel(e)).join(' · ');
      h += '<div class="cols"><div class="hd" data-tog="cols">' +
           '來源列<span class="sum">已展開 ' + nBase + ' 列：' + esc(baseNames) + '；' +
           '另有 ' + eds.length + ' 列可勾</span>' +
           '<span class="tog">展開 ▾</span></div><div class="body" hidden>';
      for (const tier of ['standard', 'extended']){
        const row = eds.filter(e => tierOf(e) === tier)
                       .sort((a, b) => rank(a) - rank(b) ||
                                       picked[b].length - picked[a].length ||
                                       colName(a).localeCompare(colName(b), 'zh'));
        if (!row.length) continue;
        h += '<div class="tier">' + (tier === 'standard' ? '標準' : '擴展') +
             '<span class="n">' + row.length + ' 列</span>' +
             '<label class="chip all"><input type="checkbox" data-all="' + tier +
             '">全選</label></div><div class="grid">';
        const byBundle = {};
        for (const e of row){ (byBundle[bundleOf(e)] = byBundle[bundleOf(e)] || []).push(e); }
        for (const b of BORDER){
          if (!byBundle[b]) continue;
          h += '<div class="grp">' + esc(BNAME[b] || b) + '</div><div class="line">';
          for (const e of byBundle[b]){
            h += '<label class="chip"><input type="checkbox" data-col="' + esc(e) + '">' +
                 esc(colName(e)) + '<span class="n">' + picked[e].length + '</span></label>';
          }
          h += '</div>';
        }
        h += '</div>';
      }
      h += '</div></div>';
    }
  }

  h += '<div class="sec">說文原文</div>';
  for (const s of gloss){
    h += '<div class="card"><div class="who">' +
         '<span class="tag">'+esc(edLabel(s.e))+'</span>'+
         '<span class="tag '+tagCls(s.e)+'">'+esc(s.L||'')+'</span></div>'+
         '<div class="txt">'+ktxt(s)+'</div>';
    if (s.u) h += '<div class="lnk">出处：<a href="'+esc(s.u)+'" target="_blank" rel="noopener">'+esc(s.u)+'</a></div>';
    h += '</div>';
  }
  if (duan.length){
    /* 一个字的段注合并成一张卡：头部（段注/书名/许可）只出现一次，出处只留一条。
       音提到最前，注按原顺序排；引文（citation）已嵌在注文里，不再单独占一行。 */
    let duanLines = duan.filter(s => s.k !== 'citation');
    if (!duanLines.length) duanLines = duan.slice();
    const first = duanLines[0];
    const duanU = (duanLines.find(s => s.u) || {}).u;
    const ph = duanLines.filter(s => s.k === 'phonology');
    const rest = duanLines.filter(s => s.k !== 'phonology');
    h += '<div class="sec">段玉裁《說文解字注》</div>';
    h += '<div class="card"><div class="who">' +
         '<span class="tag duan">段注</span>'+
         '<span class="tag">'+esc(edLabel(first.e))+'</span>'+
         '<span class="tag">'+esc(first.L||'')+'</span></div>';
    for (const s of ph) h += '<div class="txt">' + klab(s.k) + esc(s.t) + '</div>';
    for (const s of rest) h += '<div class="txt">' + ktxt(s) + '</div>';
    if (duanU) h += '<div class="lnk">出处：<a href="'+esc(duanU)+'" target="_blank" rel="noopener">'+esc(duanU)+'</a></div>';
    h += '</div>';
  }
  if (other.length){
    /* 列分档（设计裁定 D54）：**一个字默认只看 base 4 列**，其余列折叠、逐列勾选。
       勾选栏已经移到上面的「來源列」区（标题之下、原文之上）；这里只负责
       把 base 列的正文展开，以及每一列自己的折叠块——**块首带列名与条数**，
       因为勾选栏和正文之间隔着原文与段注，滚下来得能认出这是哪一列。
       base 里除說文原文与段注之外的（爾雅 / 康熙）**也按列分块**：
       把爾雅的「月在甲曰畢…」与康熙的长条目混排在一起，读的人分不清哪句是哪本书。 */
    const cm = D.cm || {};
    const tierOf = e => (cm[e] || {}).t || 'extended';
    const colName = e => (cm[e] || {}).n || edLabel(e);
    const baseRows = other.filter(s => tierOf(s.e) === 'base');
    const byBase = new Map();
    for (const s of baseRows){
      if (!byBase.has(s.e)) byBase.set(s.e, []);
      byBase.get(s.e).push(s);
    }
    for (const e of [...byBase.keys()].sort((a,b) => byBase.get(b).length - byBase.get(a).length)){
      const nCite = ((D.xc || {})[c.id] || 0);
      h += '<div class="sec">'+esc(colName(e))+' · '+byBase.get(e).length+' 條'+
           (e === 'kangxi_dict' && nCite ? '　<span class="n">另有 '+nCite+' 條內嵌引文，見康熙頁</span>' : '')+
           '</div>';
      for (const s of byBase.get(e)){
        h += '<div class="card"><div class="who">' +
             '<span class="tag '+tagCls(s.e)+'">'+esc(edLabel(s.e))+'</span>'+
             '<span class="tag">'+esc(s.L||'')+'</span></div><div class="txt">'+
             ktxt(s)+'</div>';
        if (s.u) h += '<div class="lnk">出处：<a href="'+esc(s.u)+'" target="_blank" rel="noopener">'+esc(s.u)+'</a></div>';
        h += '</div>';
      }
    }
    /* base 列的「未收」要显式说一句：《爾雅》只有 1,388 条、康熙也不是每个字都收
       （能按 canonical_id 对上的 10,697 / 11,129）。不写就是「查了没反应」，
       看着像坏了——「默认 4 列」这个心智模型必须有反馈。 */
    {
      const shown = new Set([...byBase.keys()]);
      const missing = [['xiaoxue_尔雅', '《爾雅》'], ['kangxi_dict', '《康熙字典》']]
        .filter(([e]) => !shown.has(e));
      if (missing.length)
        h += '<div class="empty" style="padding:8px 2px">' +
             missing.map(([, nm]) => nm + ' 未收此字').join('　·　') + '</div>';
    }
    for (const tier of ['standard', 'extended']){
      const rowsT = other.filter(s => tierOf(s.e) === tier);
      if (!rowsT.length) continue;
      /* 按列分组（同一列的多条陈述放一块） */
      const byCol = new Map();
      for (const s of rowsT){
        if (!byCol.has(s.e)) byCol.set(s.e, []);
        byCol.get(s.e).push(s);
      }
      const cols = [...byCol.keys()].sort((a,b) => byCol.get(b).length - byCol.get(a).length ||
                                                  colName(a).localeCompare(colName(b), 'zh'));
      for (const e of cols){
        h += '<div class="colblock" data-block="'+esc(e)+'" hidden>';
        h += '<div class="sec">'+esc(colName(e))+' · '+byCol.get(e).length+' 條' +
             (tier === 'standard' ? '（標準列）' : '（擴展列）') + '</div>';
        for (const s of byCol.get(e)){
          h += '<div class="card"><div class="who">' +
               '<span class="tag '+tagCls(s.e)+'">'+esc(edLabel(s.e))+'</span>'+
               '<span class="tag">'+esc(s.L||'')+'</span></div><div class="txt">'+
               ktxt(s)+'</div>';
          if (s.trunc) h += '<div class="lnk">（此处截断，全文见 data/provenance/kangxi_dict.jsonl）</div>';
          if (s.u) h += '<div class="lnk">出处：<a href="'+esc(s.u)+'" target="_blank" rel="noopener">'+esc(s.u)+'</a></div>';
          h += '</div>';
        }
        h += '</div>';
      }
    }
  }
  const gl = D.g[c.id];
  if (gl && gl.length){
    h += '<div class="sec">字形（指向 hanzi-glyphs-svg）</div><div class="card">';
    for (const g of gl){
      const p = g.split(':');
      h += '<span class="pill g">'+esc(p[2])+' · '+esc(p[3])+'</span>';
    }
    const first = gl[0].split(':');
    h += '<div class="lnk">字形文件在 <code>hanzi-glyphs-svg/glyphs/'+esc(first[2])+'/'+
         '</code>，本库只存指针不复制。</div></div>';
  }
  /* 引文：康熙条目里内嵌的他书引文（浏览页只列书名与条数，正文在 SQLite/JSONL） */
  const cb = (D.b || {})[c.id];
  if (cb && Object.keys(cb).length){
    h += '<div class="sec">康熙條目內引用的書</div><div class="card">';
    const books = Object.keys(cb).sort((a,b) => cb[b].n - cb[a].n);
    for (const b of books){
      const e = cb[b];
      h += '<span class="pill">《'+esc(b)+'》 '+e.n+' 条</span>';
      if (e.al){
        const parts = Object.keys(e.al).map(k => k+' '+e.al[k]).join('、');
        h += '<div class="lnk">引文對照今本：'+esc(parts)+'</div>';
      }
    }
    h += '<div class="lnk">引文正文在 <code>data/provenance/kangxi_dict.jsonl</code>' +
         '（kind=citation），浏览页只列书名与条数以控制体积。</div></div>';
  }
  /* 版本對照：大徐本 vs 段注本 / 小徐本 的逐條差異 */
  const va = (D.va || {})[c.id];
  if (va && va.length){
    h += '<div class="sec">版本對照 · 大徐本 ↔ 段注本／小徐本</div>';
    const byKind = {};
    for (const v of va){ (byKind[v.t] = byKind[v.t] || []).push(v); }
    h += '<div class="card">';
    for (const k in byKind){
      h += '<div class="lnk"><b>'+esc(k)+'</b>：'+byKind[k].length+' 條</div>';
    }
    h += '</div>';
    for (const v of va.slice(0, 12)){
      h += '<div class="card"><div class="who"><span class="tag">'+esc(edLabel(v.e))+'</span>'+
           '<span class="tag">'+esc(v.t)+'</span>'+
           (v.r ? '<span class="tag">相似度 '+v.r+'</span>' : '')+'</div>';
      if (v.b) h += '<div class="quote">大徐本：'+esc(v.b)+'</div>';
      h += '</div>';
    }
    if (va.length > 12) h += '<div class="lnk">（另有 '+(va.length-12)+' 條，見 SQLite 視圖 v_shuowen_variant）</div>';
  }
  /* 《類篇》引《說文》：後出的字書引先出的字書（與上面的版本對照是**兩件事**） */
  const lp = (D.lp || {})[c.id];
  if (lp && lp.length){
    h += '<div class="sec">《類篇》引《說文》</div>';
    const byKind = {};
    for (const v of lp){ (byKind[v.t] = byKind[v.t] || []).push(v); }
    h += '<div class="card">';
    for (const k in byKind){
      h += '<div class="lnk"><b>'+esc(k)+'</b>：'+byKind[k].length+' 條</div>';
    }
    h += '</div>';
    for (const v of lp.slice(0, 8)){
      h += '<div class="card"><div class="who"><span class="tag">《類篇》</span>'+
           '<span class="tag">'+esc(v.t)+'</span>'+
           (v.r ? '<span class="tag">相似度 '+v.r+'</span>' : '')+
           (v.al ? '<span class="tag">錨點 '+v.al+' 字</span>' : '')+'</div>'+
           (v.h ? '<div class="lnk">被引《說文》字頭：'+esc(v.h)+'</div>' : '')+
           (v.an ? '<div class="lnk">共同片段：'+esc(v.an)+'</div>' : '')+
           '</div>';
    }
    if (lp.length > 8) h += '<div class="lnk">（另有 '+(lp.length-8)+' 條，見 SQLite 視圖 v_shuowen_cited_works）</div>';
  }
  /* 形聲結構：從X（義符）與 X聲（聲符） */
  const cp = (D.cp || {})[c.id];
  if (cp && cp.length){
    h += '<div class="sec">形聲結構（許慎自述）</div><div class="card">';
    const sem = cp.filter(x => x.t === 'component');
    const pho = cp.filter(x => x.t === 'phonetic_component');
    if (sem.length) h += '<div class="txt">從：' + sem.map(x=>'<span class="pill">'+esc(x.h)+'</span>').join('') + '</div>';
    if (pho.length) h += '<div class="txt">聲：' + pho.map(x=>'<span class="pill">'+esc(x.h)+'</span>').join('') + '</div>';
    h += '</div>';
  }
  /* 說文與段注引了哪些書 */
  const sc = (D.sc || {})[c.id];
  if (sc && Object.keys(sc).length){
    h += '<div class="sec">《說文》與段注所引之書</div><div class="card">';
    const bs = Object.keys(sc).sort((a,b) => sc[b]-sc[a]);
    for (const b of bs) h += '<span class="pill">《'+esc(b)+'》 '+sc[b]+' 處</span>';
    h += '<div class="lnk">引文正文在 <code>data/provenance/shuowenjiezi_shuowen.jsonl</code>。</div></div>';
  }
  /* 引文 ↔ 引書原文 的對照（說文/段注引了某書，能對上原文哪一條） */
  const sa = (D.sa || {})[c.id];
  if (sa && sa.length){
    h += '<div class="sec">引文 ↔ 引書原文</div>';
    for (const x of sa.slice(0, 8)){
      h += '<div class="card"><div class="who"><span class="tag">《'+esc(x.bk)+'》</span>'+
           '<span class="tag">'+esc(x.ty)+'</span>'+
           (x.r ? '<span class="tag">相似度 '+x.r+'</span>' : '')+
           (x.w ? '<span class="tag">加權 '+x.w+'</span>' : '')+
           (x.al ? '<span class="tag">錨點 '+x.al+' 字</span>' : '')+'</div>'+
           (x.cb ? '<div class="lnk">段注引的是《'+esc(x.cb)+'》'+
                  (x.mp ? '，本庫對應到《'+esc(x.bk)+'》' : '')+
                  (x.cv ? '；卷別未核' : '')+'</div>' : '')+
           (x.an ? '<div class="lnk">共同片段：'+esc(x.an)+'</div>' : '')+
           '<div class="quote">引文：'+esc(x.q)+'</div>'+
           '<div class="txt">原文：'+esc(x.o)+'</div></div>';
    }
  }
  /* 变体：本字头是谁的古文/重文，以及本字头有哪些变体 */
  const vars = (c.var || []).map(v => {
    const t = D.c.find(x => x.id === v);
    return t ? t.h : v;
  });
  if (vars.length){
    h += '<div class="sec">古文 · 重文</div><div class="card">';
    h += '<div class="txt">本字頭是下列字頭的古文／重文：' +
         vars.map(v => '<span class="pill">'+esc(v)+'</span>').join('') + '</div>';
    h += '<div class="lnk">《說文》裏古文與正字各立字頭，' +
         '它們的說解並列存，點上方字頭可切換。</div></div>';
  }
  el('detail').innerHTML = h;
  /* 勾选栏的绑定：勾上哪一列，就显示哪一列的那一块（默认全折起）。
     「全选」把同一档的所有列一起勾上。
     勾上的 chip 变色（不然白底一片，看不出自己勾了哪些）；
     新显示出来的块如果不在视口里，滚到它附近——checkbox 在顶上、正文在下面。 */
  const reveal = (blk, on) => {
    if (!blk) return;
    blk.hidden = !on;
    if (!on) return;
    const r = blk.getBoundingClientRect();
    if (r.top < 0 || r.top > innerHeight - 60) blk.scrollIntoView({behavior: 'smooth', block: 'start'});
  };
  /* 來源列面板的展開／收起：默認收起（89 個 chip 攤開會把正文擠到第二屏）。 */
  const togEl = el('detail').querySelector('.cols .hd[data-tog]');
  if (togEl){
    const body = togEl.parentNode.querySelector('.body');
    const lab = togEl.querySelector('.tog');
    togEl.onclick = ev => {
      if (ev.target.closest('input')) return;
      const open = body.hidden;
      body.hidden = !open;
      lab.textContent = open ? '收起 ▴' : '展開 ▾';
    };
  }
  for (const cb of el('detail').querySelectorAll('.cols input[data-col]')){
    const lab = cb.closest('.chip');
    cb.onchange = () => {
      if (lab) lab.classList.toggle('on', cb.checked);
      reveal(el('detail').querySelector('.colblock[data-block="' + cb.dataset.col + '"]'), cb.checked);
    };
  }
  for (const cb of el('detail').querySelectorAll('.cols input[data-all]')){
    cb.onchange = () => {
      const tier = cb.dataset.all;
      for (const one of el('detail').querySelectorAll('.cols input[data-col]')){
        const t = (D.cm || {})[one.dataset.col] || {};
        if ((t.t || 'extended') !== tier) continue;
        one.checked = cb.checked;
        const lab = one.closest('.chip');
        if (lab) lab.classList.toggle('on', cb.checked);
        const blk = el('detail').querySelector('.colblock[data-block="' + one.dataset.col + '"]');
        if (blk) blk.hidden = !cb.checked;
      }
    };
  }
  /* 自检标记：把当前选中的字头与陈述数写进 title 属性，
     便于人工核验（以及自动化测试读取）。 */
  el('detail').setAttribute('data-char-id', c.id || '');
  el('detail').setAttribute('data-statements', String(sts.length));
  el('detail').setAttribute('data-gloss', String(gloss.length));
  el('detail').setAttribute('data-duan', String(duan.length));
}
let timer = null;
el('q').addEventListener('input', e => {
  const v = e.target.value.trim();
  clearTimeout(timer);
  timer = setTimeout(() => { const r = rows(v); renderList(r);
    if (r.length === 1){ sel = r[0].id; renderDetail(r[0]); } }, 150);
});
el('btnAll').onclick = () => { onlyDuan = false; el('btnAll').classList.add('on');
  el('btnDuan').classList.remove('on'); renderList(rows(el('q').value.trim())); };
el('btnDuan').onclick = () => { onlyDuan = true; el('btnDuan').classList.add('on');
  el('btnAll').classList.remove('on'); renderList(rows(el('q').value.trim())); };
load().then(() => {
  /* 支持 #字 形式的深链：打开 viewer/index.html#明 直接看该字。
     注意《说文》字头与现代字不一一对应：「明」在《说文》里是「朙」的古文，
     两者是两个字头（各有各的陈述）。深链应优先选**有许慎原文**的那个字头，
     否则会落到只有一条重文说明的「明」上。 */
  /* 默认落在一个字上：首屏空白不像「能查」的页。用「和」——它有許慎原文、
     段注与反切，四条陈述，正好展示正文的层次。 */
  const hash = decodeURIComponent(location.hash.replace(/^#/, '')).trim() || '和';
  if (hash){
    /* 走 rows()（含排序）而不是 search()：
       「明」在《说文》里有两个字头——「明」（朙的古文，只有 1 条重文说明）
       与「朙」（有原文 + 3 条段注）。排序让有原文的那个排前面。 */
    const hits = rows(hash);
    if (hits.length){
      sel = hits[0].id;
      renderSide(rows(''), hits[0]);
      renderDetail(hits[0]);
    }
  }
  /* 深鏈換字：**頁面已經開著**時改 hash 也要生效。
     只讀一次 hash 的話，在地址欄把 `#周` 改成 `#唐` 頁面**什麼都不會發生**
     ——看起來像「深鏈壞了」（瀏覽器改 hash 不重載頁面，只派發 hashchange）。 */
  window.addEventListener('hashchange', () => {
    const h = decodeURIComponent(location.hash.replace(/^#/, '')).trim();
    if (!h) return;
    const hits = rows(h);
    if (!hits.length) return;
    if (sel === hits[0].id) return;       // 自己寫 hash 引起的那次，不重繪
    sel = hits[0].id;
    renderSide(rows(''), hits[0]);
    renderDetail(hits[0]);
  });
});
</script>
</body>
</html>
"""


CLASSICS_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>經史用例檢索 · hanzi-relations</title>
<style>
:root{
  --bg:#fbfaf7; --panel:#fff; --ink:#1c1b18; --mute:#6b6862; --line:#e3ded4;
  --accent:#8a6d3b; --accent2:#3f5d52;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.75 "Noto Serif CJK SC","Source Han Serif SC","Songti SC",Georgia,serif}
header{background:var(--panel);border-bottom:1px solid var(--line);padding:18px 24px;
  position:sticky;top:0;z-index:10}
h1{margin:0 0 4px;font-size:20px}
.sub{color:var(--mute);font-size:13px}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;align-items:center}
input[type=search]{flex:1;min-width:220px;padding:9px 12px;font:inherit;font-size:15px;
  border:1px solid var(--line);border-radius:6px;background:#fff}
input[type=search]:focus{outline:2px solid var(--accent);outline-offset:-1px}
a{color:var(--accent2)}
main{display:grid;grid-template-columns:minmax(260px,320px) 1fr;
  min-height:calc(100vh - 108px)}
aside{border-right:1px solid var(--line);background:var(--panel);
  max-height:calc(100vh - 108px);overflow-y:auto;position:sticky;top:108px}
#list{padding:8px}
.item{padding:8px 12px;border-radius:6px;cursor:pointer;display:flex;
  justify-content:space-between;gap:8px;align-items:baseline}
.item:hover{background:#f3efe6}
.item.sel{background:var(--accent);color:#fff}
.item .m{font-size:11px;opacity:.65}
#detail{padding:24px 28px;max-width:1000px}
#detail h2{margin:0 0 4px;font-size:30px}
.sec{margin:22px 0 10px;padding-bottom:5px;border-bottom:1px solid var(--line);
  font-size:14px;color:var(--accent);letter-spacing:.06em}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:13px 15px;margin:9px 0}
.cite{margin:9px 0;padding:9px 12px;border-left:3px solid #d8cdb4;
  background:#fbf8f1;border-radius:0 6px 6px 0}
.cite .txt{font-size:15px;line-height:1.8;color:var(--ink);display:block;margin-top:3px}
.lnk{font-size:12px;color:var(--mute);margin-top:7px}
.empty{color:var(--mute);padding:40px 0;text-align:center}
.stat{font-size:12.5px;color:var(--mute);margin-left:auto}
@media(max-width:860px){main{grid-template-columns:1fr}
  aside{position:static;max-height:230px;border-right:0;border-bottom:1px solid var(--line)}}
</style>
</head>
<body>
<header>
  <h1>經史用例檢索 <span style="font-size:13px;color:var(--mute)">hanzi-relations</span></h1>
  <div class="sub">《詩經》《論語》《孟子》《大學》《中庸》《楚辭》與《蒙學》十二種
    （三字經/百家姓/千字文/弟子規/朱子家訓/聲律啓蒙/幼學瓊林/文字蒙求/增廣賢文/千家詩/唐詩三百首/古文觀止）
    等 26 部書裏的用字例句。
    說文系（字形演變與版本對照）見 <a href="index.html">主頁</a>，
    康熙字典見 <a href="kangxi.html">康熙頁</a>。</div>
  <div class="bar">
    <input type="search" id="q" placeholder="輸入漢字…（如：明、月、學、仁）" autocomplete="off">
    <span class="stat" id="stat">解壓中…</span>
  </div>
</header>
<main>
  <aside><div id="list"></div></aside>
  <section id="detail"><div class="empty">從左側選一個字，或在上方搜尋。</div></section>
</main>
<script>
const B64 = "__DATA_B64__";
const el = id => document.getElementById(id);
let D = null, sel = null;

function b64ToBytes(b64){
  const bin = atob(b64);
  const n = bin.length;
  const bytes = new Uint8Array(n);
  for (let i = 0; i < n; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}
async function load(){
  const bytes = b64ToBytes(B64);
  const ds = new DecompressionStream('gzip');
  const stream = new Blob([bytes]).stream().pipeThrough(ds);
  const text = await new Response(stream).text();
  D = JSON.parse(text);
  el('stat').textContent = '用字 ' + D.c.length.toLocaleString() + ' 個';
  renderList(D.c);
}
function esc(s){ return String(s==null?'':s).replace(/[&<>]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function renderList(rows){
  const list = el('list'); list.innerHTML = '';
  const frag = document.createDocumentFragment();
  let shown = 0;
  for (const c of rows){
    if (shown >= 400) break;
    const d = document.createElement('div');
    d.className = 'item' + (sel === c.id ? ' sel' : '');
    d.innerHTML = '<span>'+esc(c.h)+'</span><span class="m">'+c.n+' 句</span>';
    d.onclick = () => { sel = c.id; renderList(rows); renderDetail(c); };
    frag.appendChild(d); shown++;
  }
  if (!shown) frag.innerHTML = '<div class="empty">无匹配</div>';
  list.appendChild(frag);
}
function renderDetail(c){
  let h = '<h2>' + esc(c.h) + '</h2>';
  h += '<div class="sec">用例 ' + c.n + ' 句</div><div class="card">';
  for (const s of (c.s || [])){
    h += '<div class="cite">《' + esc(s.b) + (s.c && s.c !== s.b ? '·' + esc(s.c) : '') +
         '》<br><span class="txt">' + esc(s.t) + '</span></div>';
  }
  h += '<div class="lnk">完整用例在 <code>data/provenance/classics.jsonl</code>' +
       '（kind=citation 且 locator 含 chapter）与 SQLite 的 <code>v_classic_usage</code> 视图。</div></div>';
  el('detail').innerHTML = h;
  el('detail').setAttribute('data-char-id', c.id || '');
}
let timer = null;
el('q').addEventListener('input', e => {
  const v = e.target.value.trim();
  clearTimeout(timer);
  timer = setTimeout(() => {
    const hits = v ? D.c.filter(c => c.h === v) : D.c;
    renderList(hits);
    if (hits.length === 1){ sel = hits[0].id; renderDetail(hits[0]); }
  }, 140);
});
load().then(() => {
  const hash = decodeURIComponent(location.hash.replace(/^#/, '')).trim();
  if (hash){
    const hit = D.c.find(c => c.h === hash);
    if (hit){ sel = hit.id; renderList([hit]); renderDetail(hit); }
  }
  /* 深鏈換字：**頁面已經開著**時改 hash 也要生效。
     之前只在 load() 裏讀一次 hash，用戶在地址欄換深鏈（或頁內鏈接改 hash）
     **什麼都不會發生**——看起來像「深鏈壞了」。 */
  window.addEventListener('hashchange', () => {
    const h = decodeURIComponent(location.hash.replace(/^#/, '')).trim();
    if (!h) return;
    const hit = D.c.find(c => c.h === h);
    if (hit){ sel = hit.id; renderList([hit]); renderDetail(hit); }
  });
});
</script>
</body>
</html>
"""


KANGXI_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>康熙字典檢索 · hanzi-relations</title>
<style>
:root{
  --bg:#fbfaf7; --panel:#fff; --ink:#1c1b18; --mute:#6b6862; --line:#e3ded4;
  --accent:#8a6d3b; --accent2:#3f5d52;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.75 "Noto Serif CJK SC","Source Han Serif SC","Songti SC",Georgia,serif}
header{background:var(--panel);border-bottom:1px solid var(--line);padding:18px 24px;
  position:sticky;top:0;z-index:10}
h1{margin:0 0 4px;font-size:20px}
.sub{color:var(--mute);font-size:13px}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;align-items:center}
input[type=search]{flex:1;min-width:220px;padding:9px 12px;font:inherit;font-size:15px;
  border:1px solid var(--line);border-radius:6px;background:#fff}
input[type=search]:focus{outline:2px solid var(--accent);outline-offset:-1px}
a{color:var(--accent2)}
main{display:grid;grid-template-columns:minmax(280px,340px) 1fr;
  min-height:calc(100vh - 108px)}
aside{border-right:1px solid var(--line);background:var(--panel);
  max-height:calc(100vh - 108px);overflow-y:auto;position:sticky;top:108px}
#list{padding:8px}
.item{padding:8px 12px;border-radius:6px;cursor:pointer;display:flex;
  justify-content:space-between;gap:8px;align-items:baseline}
.item:hover{background:#f3efe6}
.item.sel{background:var(--accent);color:#fff}
.item .m{font-size:11px;opacity:.65}
#detail{padding:24px 28px;max-width:1000px}
#detail h2{margin:0 0 4px;font-size:30px}
.meta{color:var(--mute);font-size:13px;margin-bottom:18px}
.sec{margin:22px 0 10px;padding-bottom:5px;border-bottom:1px solid var(--line);
  font-size:14px;color:var(--accent);letter-spacing:.06em}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:13px 15px;margin:9px 0}
.card .who{font-size:12px;color:var(--mute);margin-bottom:5px;display:flex;gap:8px;
  align-items:center;flex-wrap:wrap}
.tag{font-size:10.5px;padding:1.5px 6px;border-radius:3px;background:#efe9dc;
  color:var(--accent);border:1px solid #e2d9c6;white-space:nowrap}
.txt{font-size:15.5px;line-height:1.8;white-space:pre-wrap}
.lbl{font-size:10.5px;line-height:1.5;padding:1px 5px;border-radius:3px;
  letter-spacing:.04em;background:#5c7a68;color:#fff;vertical-align:2px;
  margin-right:5px;white-space:nowrap}
.lbl.note{background:#8d8578}
.lbl.cite{background:#6b7f8a}
.lbl.var{background:#7b6a8a}
.lbl.st{background:#4f6b7a}
.lbl.lk{background:#7c7768}
.lbl.fq{background:#a08a6a}
.lbl.phon{background:#8a6d3b}
/* 康熙条目分段（kwHtml）：一义一段，「又」作义项标记，【書證】作小色块。 */
.sense{margin:3px 0}
.sense + .sense{margin-top:5px}
.y{font-weight:700;color:#8a6d3b;margin-right:4px}
.bk{font-size:.85em;padding:0 4px;border-radius:3px;background:#f1f1ef;color:#5f5f5c}
.bk.m{background:#f1f1ef;color:#5f5f5c}
.lnk{font-size:12px;color:var(--mute);margin-top:7px}
.empty{color:var(--mute);padding:40px 0;text-align:center}
.kv{display:grid;grid-template-columns:88px 1fr;gap:4px 12px;font-size:14px}
.kv dt{color:var(--mute)}
.kv dd{margin:0}
.pill{display:inline-block;font-size:12px;padding:2px 8px;border-radius:10px;
  background:#f0ebe0;color:var(--accent);margin:2px 4px 2px 0}
.stat{font-size:12.5px;color:var(--mute);margin-left:auto}
@media(max-width:860px){main{grid-template-columns:1fr}
  aside{position:static;max-height:230px;border-right:0;border-bottom:1px solid var(--line)}}
</style>
</head>
<body>
<header>
  <h1>康熙字典檢索 <span style="font-size:13px;color:var(--mute)">hanzi-relations</span></h1>
  <div class="sub">《康熙字典》46,827 條說解，含條目內引用的《集韻》《廣韻》《說文》《玉篇》等他書引文。
    說文系（字形演變與版本對照）見 <a href="index.html">主頁</a>。</div>
  <div class="bar">
    <input type="search" id="q" placeholder="輸入漢字…（如：明、月、朙）" autocomplete="off">
    <span class="stat" id="stat">解壓中…</span>
  </div>
</header>
<main>
  <aside><div id="list"></div></aside>
  <section id="detail"><div class="empty">從左側選一個字，或在上方搜尋。</div></section>
</main>
<script>
const B64 = "__DATA_B64__";
const el = id => document.getElementById(id);
let D = null, sel = null;

function b64ToBytes(b64){
  const bin = atob(b64);
  const n = bin.length;
  const bytes = new Uint8Array(n);
  for (let i = 0; i < n; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}
async function load(){
  const bytes = b64ToBytes(B64);
  const ds = new DecompressionStream('gzip');
  const stream = new Blob([bytes]).stream().pipeThrough(ds);
  const text = await new Response(stream).text();
  D = JSON.parse(text);
  build();
  el('stat').textContent = '康熙字頭 ' + D.c.length.toLocaleString() +
    ' · 說解 ' + D.st.statements.toLocaleString() + ' 條';
  renderList(D.c);
}
const byHead = new Map();
function build(){
  for (const c of D.c){
    if (!byHead.has(c.h)) byHead.set(c.h, []);
    byHead.get(c.h).push(c);
  }
}
function esc(s){ return String(s==null?'':s).replace(/[&<>]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function edLabel(eid){
  const L = D.lb[eid];
  if (L) return L[0];
  return (D.ed[eid] && D.ed[eid].n) || eid;
}
function renderList(rs){
  const list = el('list'); list.innerHTML = '';
  const frag = document.createDocumentFragment();
  let shown = 0;
  for (const c of rs){
    if (shown >= 300) break;
    const d = document.createElement('div');
    d.className = 'item' + (sel === c.id ? ' sel' : '');
    d.innerHTML = '<span>'+esc(c.h)+'</span><span class="m">'+esc(c.r||'')+' '+c.n+'条</span>';
    d.onclick = () => { sel = c.id; renderList(rs); renderDetail(c); };
    frag.appendChild(d); shown++;
  }
  if (!shown) frag.innerHTML = '<div class="empty">无匹配</div>';
  list.appendChild(frag);
  if (rs.length > shown){
    const m = document.createElement('div');
    m.className = 'empty'; m.style.fontSize = '12px';
    m.textContent = '仅显示前 '+shown+' / '+rs.length+'，请缩小搜索范围';
    list.appendChild(m);
  }
}
/* 类别标签（与 index 同一套叫法）：康熙一条目里既有说解、也有引书与校语，
   正文起头一个**类别色块**（「義」「引文」），读者才分得清哪句是康熙自己说的。 */
const KIND = {gloss:'義', note:'校語', commentary:'注', phonology:'音',
  citation:'引文', variant_note:'異體', serial:'刻本號', structure:'構形',
  radical_lookup:'檢字', radical_compare:'部對照', frequency:'字頻', radical_alt:'部首'};
const KCLS = {phonology:'phon', commentary:'note', note:'note', citation:'cite',
  variant_note:'var', structure:'st', radical_lookup:'lk', radical_compare:'lk',
  frequency:'fq', radical_alt:'lk', serial:'note'};
function klab(k){
  const t = KIND[k];
  if (!t) return '';
  return '<span class="lbl ' + (KCLS[k] || '') + '" title="kind=' + esc(k) + '">' +
         esc(t) + '</span>';
}
/* 康熙条目分段：以「　又」分义项，【書名·篇名】做成小色块（与正页同一套）。 */
const KW_MARK = new Set(['註', '注', '傳', '箋', '疏', '正義', '釋文', '說文', '案', '集解']);
function kwHtml(t){
  return String(t || '').split(/\u3000(?=又)/).map(function (seg, i) {
    let s = esc(seg);
    s = s.replace(/【([^】]{1,60})】/g, function (m, name) {
      return '<span class="bk' + (KW_MARK.has(name) ? ' m' : '') + '">' + name + '</span>';
    });
    if (i > 0) s = '<span class="y">（又）</span>' + s.replace(/^又/, '');
    return '<div class="sense">' + s + '</div>';
  }).join('');
}
function renderDetail(c){
  const sts = D.s[c.id] || [];
  let h = '<h2>' + esc(c.h) + '</h2>';
  h += '<div class="meta"><dl class="kv">';
  h += '<dt>部首</dt><dd>' + esc(c.r || '—') + '</dd>';
  h += '<dt>集卷</dt><dd>' + esc(c.v || '—') + '</dd>';
  h += '<dt>現代碼位</dt><dd>' + ((c.cp||[]).map(x=>'U+'+x).join('、') || '—') + '</dd>';
  h += '</dl></div>';

  h += '<div class="sec">康熙字典解釋</div>';
  for (const s of sts){
    h += '<div class="card"><div class="who"><span class="tag">'+esc(edLabel(s.e))+'</span>'+
         '<span class="tag">'+esc(s.L||'')+'</span></div><div class="txt">'+
         klab(s.k)+kwHtml(s.t)+'</div>';
    if (s.trunc) h += '<div class="lnk">（此处截断，全文见 data/provenance/kangxi_dict.jsonl）</div>';
    h += '</div>';
  }

  const cb = (D.b || {})[c.id];
  if (cb && Object.keys(cb).length){
    h += '<div class="sec">條目內引用的書</div><div class="card">';
    const books = Object.keys(cb).sort((a,b) => cb[b].n - cb[a].n);
    for (const b of books){
      const e = cb[b];
      h += '<span class="pill">《'+esc(b)+'》 '+e.n+' 条</span>';
      if (e.al){
        const parts = Object.keys(e.al).map(k => k+' '+e.al[k]).join('、');
        h += '<div class="lnk">引文對照今本：'+esc(parts)+'</div>';
      }
    }
    h += '<div class="lnk">引文正文在 <code>data/provenance/kangxi_dict.jsonl</code>' +
         '（kind=citation），此处只列书名与条数以控制体积。</div></div>';
  }
  el('detail').innerHTML = h;
  el('detail').setAttribute('data-char-id', c.id || '');
  el('detail').setAttribute('data-statements', String(sts.length));
}
let timer = null;
el('q').addEventListener('input', e => {
  const v = e.target.value.trim();
  clearTimeout(timer);
  timer = setTimeout(() => {
    const hits = (byHead.get(v) || []);
    renderList(hits);
    if (hits.length === 1){ sel = hits[0].id; renderDetail(hits[0]); }
  }, 150);
});
load().then(() => {
  const hash = decodeURIComponent(location.hash.replace(/^#/, '')).trim();
  if (hash){
    const hits = byHead.get(hash) || [];
    if (hits.length){ sel = hits[0].id; renderList(hits); renderDetail(hits[0]); }
  }
  /* 頁面已開著時改 hash 也要換字（瀏覽器改 hash 不重載，只派發 hashchange）。 */
  window.addEventListener('hashchange', () => {
    const h = decodeURIComponent(location.hash.replace(/^#/, '')).trim();
    const hits = byHead.get(h) || [];
    if (!hits.length || sel === hits[0].id) return;
    sel = hits[0].id; renderList(hits); renderDetail(hits[0]);
  });
});
</script>
</body>
</html>
"""


COMPARE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>列對比 · hanzi-relations</title>
<style>
:root{--bg:#fbfaf7;--panel:#fff;--ink:#1c1b18;--mute:#6b6862;--line:#e3ded4;
  --accent:#8a6d3b;--accent2:#3f5d52;--warn:#a1442f;--ok:#3f7d5a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.7 "Noto Serif CJK SC","Source Han Serif SC","Songti SC",Georgia,serif}
header{background:var(--panel);border-bottom:1px solid var(--line);padding:12px 22px;
  position:sticky;top:0;z-index:10;box-shadow:0 1px 3px rgba(0,0,0,.04)}
h1{margin:0;font-size:18px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
h1 .sub{color:var(--mute);font-size:12.5px;font-weight:400}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px;align-items:center}
input[type=search]{flex:1;min-width:220px;padding:8px 11px;font:inherit;font-size:15px;
  border:1px solid var(--line);border-radius:6px;background:#fff}
input[type=search]:focus{outline:2px solid var(--accent);outline-offset:-1px}
.chip{padding:3px 9px;border:1px solid var(--line);border-radius:999px;background:#fff;
  cursor:pointer;font-size:13px;user-select:none}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.legend{font-size:11.5px;color:var(--mute);margin-top:7px;line-height:1.6}
main{padding:14px 22px 60px;max-width:1900px}
.who{padding:6px 0 2px}
.who h2{font-size:30px;margin:0 6px 0 0;display:inline-block}
.who .m{color:var(--mute);font-size:13px}
.famh{margin:26px 0 9px;font-size:15px;color:var(--accent);letter-spacing:.08em;
  border-bottom:1px solid var(--line);padding-bottom:5px;display:flex;gap:10px;align-items:baseline}
.famh .c{color:var(--mute);font-size:12px;letter-spacing:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:12px;align-items:start}
.col{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.col.drop{border-color:#e0bdb4;background:#fdf8f6}
.col.demote{border-color:#e3d6b4}
.col.hold{border-color:#c9d3e3}
.ch{display:flex;gap:6px;align-items:baseline;padding:8px 12px;
  border-bottom:1px solid var(--line);flex-wrap:wrap}
.ch b{font-weight:600;font-size:14px}
.bd{font-size:10.5px;padding:1px 6px;border-radius:3px;border:1px solid #d9d2c4;color:var(--mute)}
.bd.keep{background:#eaf2ec;color:var(--ok);border-color:#cfe0d4}
.bd.drop{background:#f7e7e2;color:var(--warn);border-color:#e8c9c0}
.bd.demote{background:#f6f0df;color:#8a6d3b;border-color:#e6d9b8}
.bd.hold{background:#eaf0f7;color:#3b5670;border-color:#d6e0ec}
.best{font-size:10.5px;padding:1px 6px;border-radius:3px;background:var(--accent);color:#fff}
.cm{padding:4px 12px;color:var(--mute);font-size:12px;border-bottom:1px dashed var(--line);
  background:#fdfcf9}
.stn{padding:9px 12px;border-bottom:1px dashed #efe9dd}
.stn:last-child{border-bottom:0}
.stn .k{font-size:10.5px;color:#fff;background:var(--accent);border-radius:3px;
  padding:1px 5px;margin-right:6px;white-space:nowrap;vertical-align:2px}
.stn .k.phon{background:#8a6d3b}
.stn .k.cite{background:#6b7f8a}
.stn .k.ser{background:#b9b1a4}
.stn .t{white-space:pre-wrap;font-size:14.5px;line-height:1.85}
.stn .t.long{font-size:13px;line-height:1.75}
.stn .l{font-size:10.5px;color:var(--mute);margin-top:2px}
.seg{display:block;padding-left:12px;text-indent:-12px}
.seg::before{content:"·";color:#c9c0ae;margin-right:6px}
.ser{padding:7px 12px;font-size:11.5px;color:var(--mute);border-bottom:1px dashed #efe9dd;
  background:#fbfaf7}
.ser b{font-weight:600;color:#8a8071}
.runon{font-size:10.5px;padding:0 5px;border-radius:3px;background:#f7e7e2;color:var(--warn)}
.empty{color:var(--mute);padding:50px;text-align:center}
.hint{font-size:12.5px;color:var(--mute);margin-top:6px}
.hint .chip{margin:2px 3px 0 0;display:inline-block}
</style>
</head>
<body>
<header>
  <h1>列對比<span class="sub">同一個字 · 各部書各本子並排 · 看誰更全 / 誰在重複 / 誰該下架</span></h1>
  <div class="bar">
    <input type="search" id="q" placeholder="輸入一個字（如：月、雉、羌、防）" autocomplete="off">
    <span id="quick"></span>
  </div>
  <div class="hint" id="hint"></div>
  <div class="legend">
    <b>最全</b>＝本族內字數最多（不代表該保留：本子重複時字多也會下架）；
    <b>保留</b>／<b>擬下架</b>／<b>降級</b>／<b>待定</b>＝按
    <code>列整理 - 保留清单.md</code> 的擬處置；
    每格下方的 <b>條 · 字 · 均 · 最長</b> 就是「全不全」的硬指標；
    <span class="runon">連排</span>＝這一列有一條正文超過 120 字還沒分條，讀起來最亂，
    通常不是本字真有這麼多話，而是解析器沒認出條目邊界。
    <b>刻本號</b>（C-01350 / TH-01306）不是釋義，是刻本掃描的條目流水號，折成灰線不佔版位。
    長條正文按書自帶的「○」「又…」「 X切」斷行——**顯示層**，不改數據。深鏈：<code>#月</code>。
    本頁只收**挂在《說文》字頭上的列**；《康熙字典》與詩文集各自掛在自己的字頭
    （見 <code>kangxi.html</code> / <code>classics.html</code>），故不出現在此頁。
  </div>
</header>
<main id="box"><div class="empty">正在載入…</div></main>
<script>
const B64 = "__DATA_B64__";
function el(i){ return document.getElementById(i); }
function esc(s){ return String(s==null?"":s).replace(/[&<>]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
async function load(){
  const bin = atob(B64);
  const u = new Uint8Array(bin.length);
  for (let i=0;i<bin.length;i++) u[i] = bin.charCodeAt(i);
  const ds = new DecompressionStream("gzip");
  const ab = await new Response(new Blob([u]).stream().pipeThrough(ds)).arrayBuffer();
  return JSON.parse(new TextDecoder().decode(ab));
}
let D = null;
let CUR = null;                    // 当前渲染的字头（展开「其餘 N 條」要重渲染）
const byHead = new Map();
const EXPAND = new Set();          // `${charId}|${edition}`：这一格已展开全部
const LIMIT = 120;                 // 一列默认最多渲染多少条（「防」在集韻有 5,072 条）
const KIND = {gloss:"義", note:"校語", commentary:"注", phonology:"音",
  citation:"引文", variant_note:"異體", serial:"刻本號", structure:"構形",
  radical_lookup:"檢字", radical_compare:"部對照", frequency:"字頻", radical_alt:"部首"};
const KCLS = {phonology:"phon", citation:"cite", serial:"ser", note:"ser"};
const VERDICT = {keep:"保留", drop:"擬下架", demote:"降級", hold:"待定"};

function perCol(cid){
  const m = new Map();
  for (const s of (D.s[cid] || [])){
    const e = s.e || "";
    const o = m.get(e) || {n:0, z:0, mx:0, sn:0, srl:[], kinds:new Set()};
    o.n++; o.z += (s.z || 0); o.mx = Math.max(o.mx, s.z || 0);
    if (s.k === "serial"){ o.sn++; if (o.srl.length < 8) o.srl.push(s.t || ""); }
    o.kinds.add(s.k || ""); m.set(e, o);
  }
  return m;
}

// 显示层分词已在构建期做完（`src/display_text.py`）：正文里带 `\n`（句读边界）
// 与半角空格（词界），这里只管按原样排出来，不再二次切分。

function render(c){
  CUR = c;
  const sts = D.s[c.id] || [];
  const m = perCol(c.id);
  let h = '<div class="who"><h2>' + esc(c.h) + '</h2><span class="m">' +
          esc(c.r || "") + ' · 卷 ' + esc(c.v || "—") + ' · ' +
          sts.length + ' 條 / ' + m.size + ' 列</span></div>';
  for (const fam of D.fo){
    const cols = [...m.entries()].filter(([e]) => ((D.cm[e] || {}).f || "工具") === fam);
    if (!cols.length) continue;
    cols.sort((a,b) => b[1].z - a[1].z);      // 全的排前面
    h += '<div class="famh">' + esc(fam) + '<span class="c">' + cols.length +
         ' 列</span></div><div class="grid">';
    cols.forEach(([e, o], i) => {
      const meta = D.cm[e] || {}, vd = meta.d || "keep";
      const key = c.id + "|" + e;
      const all = sts.filter(s => s.e === e && s.k !== "serial");
      const ser = sts.filter(s => s.e === e && s.k === "serial");
      const open = EXPAND.has(key) || all.length <= LIMIT;
      const shown = open ? all : all.slice(0, LIMIT);
      const avg = o.n ? Math.round(o.z / o.n) : 0;
      h += '<div class="col ' + vd + '"><div class="ch"><b>' + esc(meta.n || e) + '</b>' +
           '<span class="bd ' + vd + '">' + (VERDICT[vd] || vd) + '</span>' +
           (i === 0 ? '<span class="best">最全</span>' : '') + '</div>' +
           '<div class="cm">' + o.n + ' 條 · ' + o.z + ' 字 · 均 ' + avg +
           ' 字 · 最長 ' + o.mx + ' 字' +
           (o.mx >= 120 ? ' <span class="runon" title="一整段幾百字沒有分條：這樣的列看着最亂，也最難比">連排</span>' : '') +
           '</div>';
      for (const s of shown){
        const t = s.t || "";
        h += '<div class="stn"><span class="k ' + (KCLS[s.k] || "") + '">' +
             esc(KIND[s.k] || s.k || "") + '</span>' +
             '<span class="t' + (t.length > 160 ? " long" : "") + '">' + esc(t) + '</span>' +
             (s.L ? '<div class="l">' + esc(s.L) + '</div>' : "") + '</div>';
      }
      if (ser.length){
        // 刻本條目號不是釋義：C-01350 / TH-01306 這類是刻本掃描的條目流水號，
        // 用來指回 hanzi-glyphs-svg 的篆書字形（C=陳昌治、TH=藤花榭、K=祁寯藻、D=段玉裁本）。
        // 對「這個字怎麼說」零信息，折成一條灰線，不佔版位。
        h += '<div class="ser"><b>刻本條目號 · ' + ser.length + ' 條</b>（' +
             esc(ser.slice(0, 6).map(s => s.t).join(" · ")) +
             (ser.length > 6 ? " …" : "") +
             '）——不是釋義：刻本掃描的條目流水號，指回篆書字形（C 陳昌治 / TH 藤花榭 / K 祁寯藻 / D 段玉裁本）</div>';
      }
      if (!open){
        h += '<div class="stn"><span class="chip" data-more="' + esc(key) + '">' +
             '展開其餘 ' + (all.length - LIMIT) + ' 條（共 ' + all.length + ' 條）</span>' +
             '<div class="l">這一列在本字頭上堆了 ' + all.length + ' 條正文——' +
             '通常不是本字真有这么多说解，而是解析器把整卷倒进了一个字头。</div></div>';
      }
      h += '</div>';
    });
    h += '</div>';
  }
  el("box").innerHTML = h;
  if (decodeURIComponent(location.hash.replace(/^#/, "")) !== c.h){
    location.hash = encodeURIComponent(c.h);
  }
}

function pick(v){
  const hits = byHead.get(v.trim()) || [];
  if (!hits.length){ el("box").innerHTML = '<div class="empty">沒有這個字：' + esc(v) + '</div>'; return; }
  render(hits[0]);
}

function suggest(v){
  const out = [];
  for (const [h] of byHead){ if (v && h.includes(v)) out.push(h); if (out.length >= 24) break; }
  el("hint").innerHTML = v && !byHead.has(v) && out.length
    ? '相近：' + out.map(x => '<span class="chip" data-go="' + esc(x) + '">' + esc(x) + '</span>').join("")
    : "";
}

load().then(d => {
  D = d;
  for (const c of D.c){
    if (!byHead.has(c.h)) byHead.set(c.h, []);
    byHead.get(c.h).push(c);          // 一个字头可能有多条（异体/重出），pick 按数组取
  }
  const quick = ["月","雉","羌","防","江","說","禮","网","箕","時","學","道","一","上"];
  el("quick").innerHTML = quick.filter(x => byHead.has(x))
    .map(x => '<span class="chip" data-go="' + x + '">' + x + '</span>').join("");
  document.addEventListener("click", ev => {
    const mo = ev.target.closest("[data-more]");
    if (mo){ EXPAND.add(mo.getAttribute("data-more")); if (CUR) render(CUR); return; }
    const g = ev.target.closest("[data-go]");
    if (g) pick(g.getAttribute("data-go"));
  });
  el("q").addEventListener("input", ev => {
    const v = ev.target.value.trim();
    if (byHead.has(v)) pick(v); else suggest(v);
  });
  el("q").addEventListener("keydown", ev => {
    if (ev.key === "Enter") pick(ev.target.value.trim());
  });
  const hash = decodeURIComponent(location.hash.replace(/^#/, "")).trim();
  pick(hash && byHead.has(hash) ? hash : "月");
  /* 頁面已開著時改 hash 也要換字（同 index / kangxi）。 */
  window.addEventListener("hashchange", () => {
    const h = decodeURIComponent(location.hash.replace(/^#/, "")).trim();
    if (!h || !byHead.has(h)) return;
    if (CUR && CUR.h === h) return;      // render() 自己寫的 hash，不重繪
    pick(h);
  });
}).catch(err => {
  el("box").innerHTML = '<div class="empty">載入失敗：' + esc(err && err.message) + '</div>';
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
