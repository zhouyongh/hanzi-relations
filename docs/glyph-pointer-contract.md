# 字形指针契约（v1）

本仓库**不存字形文件**，只存指向兄弟仓 [`hanzi-glyphs-svg`](https://github.com/zhouyongh/hanzi-glyphs-svg) 的指针边。

## 指针格式

```
glyph:<repo>:<layer>:<key>
```

例：

- `glyph:hanzi-glyphs-svg:oracle:0386`
- `glyph:hanzi-glyphs-svg:seal:3D000`

非法格式由校验器报 **G1**。

## 已发布层白名单（v1）

单一事实源：`src/glyph_pointer.py` 的 `PUBLISHED_LAYERS`。

| 层 | 含义 | 键 |
|---|---|---|
| `oracle` | 甲骨文 | `glyphs/oracle/index.csv` 的文件名 stem（如 `0386`） |
| `seal` | 篆书（漢典《說文》系） | `glyphs/seal/` 文件名 stem（篆书区块码位，如 `3D000`） |

未在白名单的层出现在边上 → **G2**。  
**不**把未入库的 `xiaozhuan` 或其他半层写进白名单。

## G-identity（字头必须对上字形）

校验器在兄弟仓可得且能读到对应层 `index.csv` 时强制：

1. **优先 unicode**：把 `index.unicode` 与字头 `modern_codepoints` / `head` 的码位规范化后比较，必须命中。
2. **否则 modern_char**：仅当该字形行**没有** unicode 时，要求 `index.modern_char == head`（完全一致）。

**禁止**在 G-identity 里做异体/通假映射——异体是 `variant_of` 等边类型的事，与「指针是否指到正确文件」是两层问题。

失败码：**G4**。故意把「一」指到 `oracle:0001`（实为「㐁」）必须红；冒烟见 `python src/test_glyph_identity.py`。

兄弟仓或 `index.csv` 不可得时：G3/G4 **跳过并在报告里写明**，不得当成「已核过身份」的假绿。

## 重建

甲骨边若曾按条目号错配，用：

```
python src/rebuild_oracle_glyphs.py --glyph-repo ../hanzi-glyphs-svg
```

该脚本按本契约离线全量生成 oracle 边，删除旧 oracle 边，**不动 seal**。应与校验同批合入。

## 破坏性变更

改指针三段格式、或从白名单移除已发布层，视为破坏性变更：bump 本文件版本说明，并确保消费方改读契约而非硬编码层名。
