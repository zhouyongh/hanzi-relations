# 扩层 G-identity 方案

## 目标

在指针已硬切为 `glyph:hanzi-glyphs-svg:uid:<unified_id>` 的前提下，
按字头身份从字形库账本（钉 `v1.0.0`）为**尚无边的层**生成候选边。
**禁止**把账本某层 active 全量灌进关系库。

## 身份规则（与契约 G-identity 一致）

对每个候选字头 `char_row`、每个目标层 `layer`：

1. 从账本取出该层全部 active 行，建倒排：
   - `unicode → [uid…]`（有 unicode 的行）
   - `modern_char → [uid…]`（**仅**无 unicode 的行）
2. 命中顺序：
   - 先用字头 `modern_codepoints` ∪ `head` 码位查 unicode 倒排；
   - 若一个都没有，再用 `head` 精确匹配 modern_char 倒排。
3. **禁止**异体 / 通假 / 简繁折叠（那是 `variant_of` / `search_group` 的事）。

## 一对多策略

- **默认（dry-run / 建议 apply）**：同一 `(char, layer)` 命中多个 uid 时，
  **全部保留**为多条 glyph 边（与现有 oracle 多件行为一致）。
- **可选 `--one-per-char`**：每字每层只取排序后第一个 uid（字典序），用于浏览页瘦身试验；
  全量生产默认不要开。

## 字头范围

- 默认：`work_id=shuowen` 的字头（与现有 oracle/seal 边口径一致）。
- 可用 `--work-id` 覆盖；`--all-chars` 扩到全部字头（数字会大很多，须另确认）。

## 与现有边的关系

- 扩层脚本**不删除**已有边；只对「该字头在该层尚无 glyph 边」的缺口补候选。
- 已对 uid 迁移后的 oracle/seal 边保持不动。

## 输出

`tmp/glyph-expand-dry-run/`：

- `summary.json` / `REPORT.md`：每层候选边数、命中字头数、一对多字头数、零命中字头数
- `candidates-<layer>.jsonl`：候选边（未写入 data/）

## 写入门槛

- dry-run 数字清晰且抽检无误后，可对 **1–2 个层** 试写（建议先 `bronze`、`clerical`）。
- **全部新层全量写入**须用户显式确认；本仓库默认只出报告。
