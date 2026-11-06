# 字形指针契约（v2）

本仓库**不存字形文件**，只存指向兄弟仓 [`hanzi-glyphs-svg`](https://github.com/zhouyongh/hanzi-glyphs-svg) 的指针边。

> **v2（硬切）**：指针主键改为字形库 `unified_id`（6-4-6）。  
> 旧格式 `glyph:<repo>:<layer>:<key>` **立即失效**，校验器 G1 报错，不做回落。

## 指针格式

```
glyph:<repo>:uid:<unified_id>
```

例：

- `glyph:hanzi-glyphs-svg:uid:004E00-0E00-000001`（「一」小篆）
- `glyph:hanzi-glyphs-svg:uid:004E00-0A00-000001`（「一」甲骨某件）

`unified_id` 正则（字形库 `ID-FORMAT.md`）：

```text
^[0-9A-F]{6}-[0-9A-Z]{4}-[0-9]{6}$
```

非法格式（含旧 `layer:key`）由校验器报 **G1**。

层内文件路径**不进边**：运行时 / 校验时用账本解析：

```text
unified_id → (layer, file)   # glyph_pointer.resolve_layer_file
```

账本默认钉字形库 git tag **`v1.0.0`**（`git show v1.0.0:glyphs/glyph-ids.csv`），
可用 `--glyph-ref` / `glyph_pointer.load_ledger(..., glyph_ref=)` 覆盖；
正式校验不要盲跟会挪生成物的 `main` 工作树。

## 已发布层白名单

单一事实源：`src/glyph_pointer.py` 的 `PUBLISHED_LAYERS`（与 v1.0.0 账本 13 层对齐）：

`oracle` `bronze` `chu` `qinwenzi` `seal` `clerical` `caoshu` `kaishu` `xingshu` `chuanchao` `songti` `songti-tc` `heiti-hk`

未在白名单的层出现在边上 → **G2**。  
**不**把未入库半层写进白名单。

> 白名单放开 ≠ 自动灌边。扩层须按 G-identity 对齐字头后再写入（见
> `docs/glyph-layer-expand-identity.md`）；全量灌边需显式确认。

## G-identity（字头必须对上字形）

校验器在账本可得时强制（字段取自账本行；也可对照层内 `index.csv`）：

1. **优先 unicode**：把字形行 `unicode` 与字头 `modern_codepoints` / `head` 的码位规范化后比较，必须命中。
2. **否则 modern_char**：仅当该字形行**没有** unicode 时，要求 `modern_char == head`（完全一致）。

**禁止**在 G-identity 里做异体/通假映射——异体是 `variant_of` 等边类型的事。

失败码：**G4**。故意把「一」指到「㐁」的 uid 必须红；冒烟见 `python src/test_glyph_identity.py`。

**G3**：账本解析出 `(layer, file)` 后，兄弟仓工作树存在 `glyphs/<layer>/<file>`。  
兄弟仓或账本不可得时：G3/G4 **跳过并在报告里写明**，不得假绿。

## 重建与迁移

- 现有边迁移（layer:key → uid）：`tmp/glyph-uid-dry-run/mapping.jsonl` +
  `python src/apply_glyph_uid_mapping.py`
- 甲骨边按 identity 重建：`python src/rebuild_oracle_glyphs.py --glyph-repo ../hanzi-glyphs-svg`
  （写 uid 指针；删旧 oracle 边，不动其它层）
- 扩层 dry-run：`python src/expand_glyph_layers_dry_run.py`

## 破坏性变更

改指针四段格式、或从白名单移除已发布层，视为破坏性变更：bump 本文件版本说明，
并确保消费方改读契约而非硬编码层名或旧 `layer:key`。
