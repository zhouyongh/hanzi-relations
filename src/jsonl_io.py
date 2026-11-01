"""JSONL 读写：单一事实源的分片支持。

## 为什么要分片

**GitHub 的硬限制：单个文件超过 100 MiB 会被直接拒绝推送。**
本库的 `data/relations.jsonl` 实测已到 **143 MB**——也就是说
**仓库照现在的样子是推不上去的**，而用户的部署计划就是推到 GitHub。

所以大表改成**分片目录**：

```
data/relations/part-00001.jsonl   ← 按 relation_id 顺序切块，每块 ≤ 40 MB
data/relations/part-00002.jsonl
data/characters/part-00001.jsonl
```

*为什么按顺序切*：分片后 `cat data/relations/part-*.jsonl` 仍然得到
**与原文件完全一致的顺序与内容**——`relation_id` 全局单调递增，
边表的时间顺序、可 diff 性都不受影响。

*为什么 40 MB 而不是贴着 100 MB*：留足余量。JSONL 里一条记录的大小会变
（加字段、加长文本），贴着上限切的话，下次构建就可能有一片超限。

*小表不分片*：`sources.jsonl`、`editions.jsonl`、`radicals.jsonl`、
`rhyme_groups.jsonl`、`source_tags.jsonl` 与 `data/provenance/*.jsonl`
都是小文件，保持单文件最好用。

## 用法

```python
from jsonl_io import write_jsonl, write_jsonl_sharded, read_jsonl, read_jsonl_dir

write_jsonl("data/sources.jsonl", records)            # 小表：单文件
write_jsonl_sharded("data/relations", records)        # 大表：分片目录
records = list(read_jsonl_dir("data/relations"))      # 读回来，顺序不变
```
"""
from __future__ import annotations

import json
import os
import time

# 分片大小上限：40 MB（远低于 GitHub 的 100 MB 硬限制）
SHARD_MAX_BYTES = 40_000_000


def write_jsonl(path: str, records: list[dict]) -> None:
    """原子写单个 JSONL 文件。

    **原子写**（`.tmp` + `os.replace`）：构建被打断（Ctrl-C / 超时 / 进程被杀）时，
    直接 `open(path, "w")` 会留下一个**截断的文件**。
    实测踩过：`relations.jsonl` 被截断在中间，而按 relation_id 排在**末尾**的
    21,404 条 glyph 边正好被截掉、一度全丢。
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # Windows 上 os.replace 会因目标文件被其他进程打开而失败（WinError 5）——
    # 扫描器、索引服务、IDE 都可能短暂持有文件。退避重试即可。
    last: Exception | None = None
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:      # WinError 5
            last = exc
            time.sleep(0.25 * (attempt + 1))
    raise last if last else OSError(f"无法取代 {path}")


def write_jsonl_sharded(dir_path: str, records: list[dict],
                        max_bytes: int = SHARD_MAX_BYTES) -> list[str]:
    """把记录写成**分片目录**（按给定顺序切块，每块 ≤ max_bytes）。

    返回写出的文件名列表。目录里旧的 `part-*.jsonl` 会先清掉——
    否则重跑后残留的旧分片会混进来（正是「少了条/多了条」那类问题）。
    """
    os.makedirs(dir_path, exist_ok=True)
    for name in sorted(os.listdir(dir_path)):
        if name.startswith("part-") and name.endswith(".jsonl"):
            os.remove(os.path.join(dir_path, name))

    parts: list[list[dict]] = [[]]
    size = 0
    for rec in records:
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        n = len(line.encode("utf-8"))
        if parts[-1] and size + n > max_bytes:
            parts.append([])
            size = 0
        parts[-1].append(rec)
        size += n

    written: list[str] = []
    for i, part in enumerate(parts, 1):
        name = f"part-{i:05d}.jsonl"
        write_jsonl(os.path.join(dir_path, name), part)
        written.append(name)
    return written


def append_jsonl_sharded(dir_path: str, records: list[dict],
                         max_bytes: int = SHARD_MAX_BYTES) -> list[str]:
    """把 records **追加**到分片目录末尾（**不重切既有分片**）。

    与 `write_jsonl_sharded` 的区别：后者会**整表重切**（既有分片边界全变），
    前者只在最后一片放不下时**新开一片**。

    *前提*：records 的 id 顺序在既有记录之后（调用方负责保证）——
    否则 `cat part-*.jsonl` 的顺序就不再是全局 id 顺序了。

    返回最后一片的文件名。
    """
    os.makedirs(dir_path, exist_ok=True)
    parts = sorted(name for name in os.listdir(dir_path)
                   if name.startswith("part-") and name.endswith(".jsonl"))
    if not parts:
        write_jsonl_sharded(dir_path, records, max_bytes)
        return parts[-1] if parts else ""
    last = os.path.join(dir_path, parts[-1])
    size = os.path.getsize(last) if os.path.exists(last) else 0
    payload = [json.dumps(rec, ensure_ascii=False) + "\n" for rec in records]
    need = sum(len(line.encode("utf-8")) for line in payload)
    if size and size + need > max_bytes:
        idx = len(parts) + 1
        name = f"part-{idx:05d}.jsonl"
        write_jsonl(os.path.join(dir_path, name), records)
        return name
    # 追加到最後一片（原子性：先寫臨時檔再換）
    tmp = last + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        with open(last, "r", encoding="utf-8") as src:
            for line in src:
                fh.write(line)
        for line in payload:
            fh.write(line)
    os.replace(tmp, last)
    return parts[-1]


def read_jsonl(path: str) -> list[dict]:
    """读单个 JSONL 文件。"""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def read_jsonl_dir(dir_path: str, pattern: str = "part-*.jsonl") -> list[dict]:
    """读分片目录里的所有分片（按文件名排序 → 与写入顺序一致）。"""
    if not os.path.isdir(dir_path):
        return []
    out = []
    for name in sorted(os.listdir(dir_path)):
        if not (name.startswith("part-") and name.endswith(".jsonl")):
            continue
        with open(os.path.join(dir_path, name), "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(json.loads(line))
    return out


def read_jsonl_any(path: str) -> list[dict]:
    """读一个**可能是文件、也可能是分片目录**的路径。

    存在的理由：分片是**渐进**的——小的表保持单文件（最好用），
    大的表才分片。读者不该关心某个表当前是哪一种。
    """
    if os.path.isdir(path):
        return read_jsonl_dir(path)
    return read_jsonl(path)


def is_sharded(path: str) -> bool:
    """路径是分片目录？"""
    return os.path.isdir(path)
