"""讀音值域的登記與歸一（規格 §4 D42）。

## 這個模組解決什麼

`type=phonology` 的邊有兩種目標形態，**混在一個命名空間裡**：

- **賦值邊**（240,175 條）：目標 `phonology:<edition_id>:<value>`，
  說的是「這個字有這個讀音/韻類/反切」。
- **比對邊**（8,875 條）：目標 `statement:S…`，
  `kind` 是 `fanqie_same` / `fanqie_homophone` / …，說的是說文反切與廣韻反切的**比對結論**。

本模組處理**賦值邊那一側**：把 `<value>` 變成**可解析、可查詢的讀音登記項**。

## 為什麼要拆多值

實測值域（87,585 個 `(edition, value)`）裡有兩類退化：

| edition | 邊數 | distinct 值 | 問題 |
|---|---|---|---|
| `unihan_khanyupinyin` | 34,130 | **34,130** | 每個值都唯一 → 「哪些字讀同一個音」**答不了** |
| `unihan_kxhc1983` | 11,072 | **11,072** | 同上 |
| `unihan_kmandarin` | 44,355 | 1,492 | 正常（值空間小，可同音查詢） |

原因是 Unihan 的《漢語拼音》字段是 **`定位碼-讀音`** 的形式：
`10019.020-tiàn` 裡 `10019.020` 是**字典頁碼與序號**（不是讀音的一部分），
真正要查的只有 `tiàn`。所以**必須剝掉定位碼**，讀音值空間才會收斂。

## 值域裡的幾個坑（都實測過）

1. **`_` 不是多值分隔符**。它是 `reading_slug()` 把空白換成的下劃線——
   羅馬字裡的**詞界**：`TAKAI_AGAMERU`（滝 トラウ）、`TO_THAK`。
   只有 `kHanyuPinyin` / `kXHC1983` 這兩個 edition 的 `_` 才是**讀音組**的分隔。
2. **`,` 才是多值分隔符**（8,188 個值），
   但 `0685.060,0685.091-léi` 裡的逗號分隔的是**兩個定位碼**、共用一個讀音——
   位置在**橫線之前**、讀音在**之後**。
3. **`guangyun` 的值不是讀音，是小韻號**（`939`、`301`…）→ `value_kind=rhyme_class`。
4. **`unihan_kfanqie` 的值是反切**（`郎丁`、`他紺_他念`）→ `value_kind=fanqie`。
5. **`unihan_wip_phonetic` / `unihan_wip_meyerwempe` 的值是編碼**（`780*`、`24a`）→ `value_kind=code`。
6. **`unihan_khanyupinlu` 的值帶括號頁碼**（`è(14)`、`wú(1997)`）——括號裡是頁碼**不是聲調**，
   別把它當聲調號讀。

## 派生字段

`normalized` 與 `tone` 是**派生**的（硬規則 10 要求記 `derived_from` + `transform`）：
聲調符號 → 數字聲調，字母小寫，`ü` → `v`。
"""
from __future__ import annotations

import re
from typing import Iterable

#: 讀音登記項的 `value_kind`（**封閉枚舉**，新增需改本文件與規格）。
VALUE_KINDS = (
    "reading",          # 單個讀音（普通話/粵拼/日/韓/古唐）
    "reading_list",     # 逗號分隔的多個讀音（已拆分，每個讀音一條登記）
    "location_reading", # 帶定位碼的讀音（Unihan《漢語拼音》《xiandai 漢語拼音》）
    "fanqie",           # 反切（切腳字對）
    "rhyme_class",      # 韻類/小韻號（《廣韻》）
    "code",             # 編碼（聲母韻母碼、Meyer-Empepe 歷史讀音編碼）
)

#: 哪些 edition 的值裡 `_` 是**讀音組**分隔（其餘是羅馬字詞界）。
#:
#: ⚠ **這三個 edition 的值可能是「多組」**——上游用**空格**分隔各組，
#: `reading_slug()` 把空格換成 `_` 之後就是 slug 形式。**兩種分隔符都要認**，
#: 否則**重建會漂移**（實測：只認 `_` 時，`10019.020:tiàn` 這種**原值**
#: （冒號分隔）整串不會被拆，邊目標會帶上定位碼，與庫裡現有的乾淨目標對不上）。
_UNDERSCORE_IS_GROUP = {"unihan_khanyupinyin", "unihan_kxhc1983",
                        "unihan_khanyupinlu"}

#: 哪些 edition 的值是反切。
_FANQIE_EDITIONS = {"unihan_kfanqie"}

#: 哪些 edition 的值是韻類（小韻號）。
_RHYME_EDITIONS = {"guangyun"}

#: 哪些 edition 的值是編碼。
_CODE_EDITIONS = {"unihan_wip_phonetic", "unihan_wip_meyerwempe"}

#: 哪些 `value_kind` **會解析聲調**。
#:
#: 只有真正的讀音才解析。韻類（小韻號）、反切、編碼的值裡���結尾數字
#: **不是聲調**——解析出來是**假值**（實測 6,173 行）。
_TONE_BEARING_KINDS = frozenset({"reading", "reading_list", "location_reading"})

#: 哪些 edition 的值帶定位碼。
_LOCATION_EDITIONS = {"unihan_khanyupinyin", "unihan_kxhc1983", "unihan_khanyupinlu"}

#: 聲調符號 → (基字母, 聲調)。覆蓋普通話與漢語拼音方案裡常見的。
_TONE_MARKS = {
    "ā": ("a", 1), "á": ("a", 2), "ǎ": ("a", 3), "à": ("a", 4),
    "ē": ("e", 1), "é": ("e", 2), "ě": ("e", 3), "è": ("e", 4),
    "ī": ("i", 1), "í": ("i", 2), "ǐ": ("i", 3), "ì": ("i", 4),
    "ō": ("o", 1), "ó": ("o", 2), "ǒ": ("o", 3), "ò": ("o", 4),
    "ū": ("u", 1), "ú": ("u", 2), "ǔ": ("u", 3), "ù": ("u", 4),
    "ǖ": ("v", 1), "ǘ": ("v", 2), "ǚ": ("v", 3), "ǜ": ("v", 4),
    "ń": ("n", 2), "ň": ("n", 3), "ǹ": ("n", 4),
    "ḿ": ("m", 2),
    "ê̄": ("e", 1), "ế": ("e", 2), "ê̌": ("e", 3), "ề": ("e", 4),
    "ɑ̄": ("a", 1), "ɑ́": ("a", 2), "ɑ̌": ("a", 3), "ɑ̀": ("a", 4),
    "ế": ("e", 2),
}

#: 定位碼前綴。
#:
#: - **原值**（上游 Unihan_Readings.txt）：`1234.567:tiàn` —— 分隔符是**冒號**
#: - **slug 形式**（`reading_slug()` 把冒號換成連字號）：`1234.567-tiàn`
#: - 多個定位碼用逗號分隔，其中任一個可帶 `*`（Unihan 的「首選讀音」標記）：
#:   `0161.040,0161.041*:chù`、`0500.031*,0500.032-huī`
#:
#: ⚠ **不容忍 `*` 就會整串失配**——實測 7 條《現代漢語詞典》讀音因此
#: 沒被拆開，定位碼殘留在 `normalized` 裡，跨 edition 同音連接失效。
_LOC_TOKEN = r"[0-9]+(?:\.[0-9]+)?\*{0,2}"
_LOC_RE = re.compile(rf"^({_LOC_TOKEN}(?:,{_LOC_TOKEN})*)[:\-](.*)$")

#: `unihan_khanyupinlu` 的括號頁碼：`è(14)`
#:
#: ⚠ 必須用**貪婪**的 `(.*)`——非貪婪的 `(.*?)` 遇到 `a(1102)_ā(404)` 這種
#: 多組值會把括號**留在中間**（`a(1102)_a1`），歸一值就殘留頁碼。
_PAREN_RE = re.compile(r"^(.*)\((\d+)\)$")


def reading_slug(value: str) -> str:
    """把讀音值變成可放在關係目標裡的短標識。

    ⚠ **与构建脚本里的同名函数保持一致**——
    邊目標的 slug 規則不能有第二套（否則遷移時目標會對不上）。
    只做空白/冒號的替換，**其餘字元原樣保留**。
    """
    out = []
    for ch in (value or "").strip():
        if ch.isspace():
            out.append("_")
        elif ch == ":":
            out.append("-")
        else:
            out.append(ch)
    return "".join(out)


def classify_value_kind(edition_id: str, value: str) -> str:
    """讀音值的**語義類別**（封閉枚舉）。

    沒有這一欄，`normalized` / `tone` 對 `80044.210-zhāi` 這種值就毫無意義。
    """
    v = (value or "").strip()
    if edition_id in _RHYME_EDITIONS:
        return "rhyme_class"
    if edition_id in _FANQIE_EDITIONS:
        return "fanqie"
    if edition_id in _CODE_EDITIONS:
        return "code"
    if edition_id in _LOCATION_EDITIONS:
        return "location_reading"
    if "," in v:
        return "reading_list"
    return "reading"


def split_readings(edition_id: str, value: str) -> list[tuple[str, str]]:
    """把一個讀音值拆成**若干個 (讀音, 定位碼)**。

    *輸入*既可以是**上游原值**，也可以是**slug 形式**
    （`reading_slug()` 把空白換成 `_`、冒號換成 `-`）——**兩者都要認**，
    否則 builder（喂原值）與遷移腳本（喂 slug）會得出不同的邊目標。

    拆分規則（自定，登記在規格 D42/D43）：

    1. **分組**：`_UNDERSCORE_IS_GROUP` 裡的 edition 用**空白或 `_`** 分組；
       其餘 edition 的值**原樣返回一條**（羅馬字的 `_` 是詞界，不是分隔符）。
    2. 每組兩種形態：
       - `定位碼:讀音`（kHanyuPinyin / kXHC1983）——定位碼可含逗號與 `*`；
         讀音部分可含逗號（`10263.070:dān,qiú` → 兩個讀音，共用定位碼）。
       - `讀音(頁碼)`（kHanyuPinLu）——括號裡是**頁碼不是聲調**。
    3. 其餘 edition 一律不拆。
    """
    v = (value or "").strip()
    if not v:
        return []
    if edition_id not in _UNDERSCORE_IS_GROUP:
        return [(v, "")]

    out: list[tuple[str, str]] = []
    for group in re.split(r"[\s_]+", v):
        group = group.strip()
        if not group:
            continue
        m = _LOC_RE.match(group)
        if m:
            locs, readings = m.group(1), m.group(2)
            for r in readings.split(","):
                r = r.strip()
                if r:
                    out.append((r, locs))
            continue
        m2 = _PAREN_RE.match(group)
        if m2:
            out.append((m2.group(1).strip(), m2.group(2)))
            continue
        out.append((group, ""))
    return out or [(v, "")]


def normalize(reading: str, value_kind: str = "reading") -> tuple[str, int | None]:
    """讀音 → (標準化值, 聲調)。

    *標準化值*：字母小寫、聲調符號轉成**數字聲調**綴在後面（`tiàn` → `tian4`）、
    `ü` → `v`。
    *聲調*：1–5；**判不出來就是 `None`**（不是 0——「沒有聲調」與「聲調未知」是兩回事）。

    粵拼與日韓羅馬字的**數字聲調後綴**（`jyu4`、`KOU`）也認：
    結尾的 1–6 數字算聲調，**但**前面的字母裡沒有聲調符號時才認。

    ## ⚠ `value_kind` 必須分流（實測 6,173 行假聲調）

    `rhyme_class`（《廣韻》**小韻號** `1` / `1004`）、`fanqie`（反切 `一丸`）、
    `code`（編碼 `1001` / `1*`）**不是讀音**——結尾的數字**不是聲調**。
    舊版無差別解析，於是 **2,321 行**小韻號與 **3,852 行**編碼帶上了**假聲調**
    （小韻號 1 → tone 1、編碼 1004 → tone 4）。

    假值比「未知」更糟：查「聲調是 4 的字」會把編碼 1004 撈進來。
    這三類一律 `tone=None`。
    """
    s = (reading or "").strip()
    if not s:
        return "", None
    if value_kind not in _TONE_BEARING_KINDS:
        return s.lower(), None

    # 括號頁碼（`è(14)`）先剝掉——**括號裡是頁碼不是聲調**
    m = _PAREN_RE.match(s)
    if m:
        s = m.group(1).strip()

    # 結尾數字聲調（粵拼 / 帶調號的羅馬字）
    tone: int | None = None
    tail = re.search(r"([1-6])$", s)
    if tail and not any(ch in _TONE_MARKS for ch in s):
        tone = int(tail.group(1))
        s = s[: tail.start()]

    base = []
    for ch in s:
        if ch in _TONE_MARKS:
            letter, t = _TONE_MARKS[ch]
            base.append(letter)
            tone = t
        elif ch == "ü":
            base.append("v")
        else:
            base.append(ch)
    norm = "".join(base).lower()
    if tone is not None:
        norm = f"{norm}{tone}"
    return norm, tone


def reading_id(edition_id: str, bare_reading: str) -> str:
    """讀音的穩定 id：`reading:<edition_id>:<slug>`。

    **不含許可、不含來源**——那些從 `edition_id` join 出去
    （硬規則 2：許可的唯一事實源是 `data/sources.jsonl`）。
    """
    return f"reading:{edition_id}:{reading_slug(bare_reading)}"


def build_reading_rows(edges: Iterable[dict],
                        locations: dict[tuple[str, str], set[str]] | None = None
                        ) -> list[dict]:
    """由讀音邊推出 `data/readings.jsonl` 的登記行。

    接受**兩種**目標前綴：`reading:<ed>:<slug>`（遷移後）
    與 `phonology:<ed>:<value>`（遷移前）——遷移腳本在改名前後都用得上它。

    *每個 `(edition_id, 裸讀音)` 一行*；多值已拆開，
    所以 `kHanyuPinyin` 的 34,130 條邊會產生**收斂後**的讀音值空間
    （同音字共用一條登記）。

    `locations` 是可選的「`(edition_id, 裸讀音)` → 定位碼集合」對照
    （由**陳述裡的上游原值**反查）。邊上只有 slug，定位碼得從原值找回來——
    不給這個參數，`locations` 就是空的（欄位形同虛設）。

    行裡**沒有 `license`、沒有 `source_id`**：那是第二份許可載體。
    """
    acc: dict[tuple[str, str], dict] = {}
    for e in edges:
        to = e.get("to") or ""
        ed = bare = raw = None
        if to.startswith("reading:"):
            parts = to.split(":", 2)
            if len(parts) != 3:
                continue
            ed, bare = parts[1], parts[2]
            raw = bare
        elif to.startswith("phonology:"):
            parts = to.split(":", 2)
            if len(parts) != 3:
                continue
            ed, raw = parts[1], parts[2]
            pieces = split_readings(ed, raw)
            for b, loc in pieces:
                _acc_one(acc, ed, b, loc, raw, locations)
            continue
        else:
            continue
        _acc_one(acc, ed, bare, "", raw, locations)

    rows = []
    for key in sorted(acc):
        rec = acc[key]
        rec["locations"] = sorted(rec["locations"])
        rows.append(rec)
    return rows


def _acc_one(acc: dict, ed: str, bare: str, loc: str, raw: str,
             locations: dict[tuple[str, str], set[str]] | None = None) -> None:
    """把一條 (edition, 裸讀音) 併進登記表。"""
    key = (ed, bare)
    rec = acc.get(key)
    if rec is None:
        vkind = classify_value_kind(ed, raw)
        norm, tone = normalize(bare, vkind)
        rec = acc[key] = {
            "reading_id": reading_id(ed, bare),
            "edition_id": ed,
            "value": bare,
            "slug": reading_slug(bare),
            "value_kind": vkind,
            "normalized": norm,
            "tone": tone,
            "locations": [],
            "derived_from": "data/relations/",
            "transform": "strip_location_prefix+normalize_tone",
        }
    if loc and loc not in rec["locations"]:
        rec["locations"].append(loc)
    if locations:
        for extra in locations.get(key, ()):  # 由陳述原值反查的定位碼
            if extra not in rec["locations"]:
                rec["locations"].append(extra)
