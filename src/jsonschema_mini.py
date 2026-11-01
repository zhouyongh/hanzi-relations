"""最小 JSON Schema 校验器（**只用标准库**）。

## 为什么自己写

`verify.yml` 的三个校验 job 全是「只用标准库」的——这是仓库的既有约定，
也是 CI 能不装任何东西就跑的原因。引入 `jsonschema`（PyPI）会破坏它：
pip install 失败就是 CI 红，而 JSON Schema 校验**不是**这个仓库的核心风险。

本库需要的 JSON Schema 能力只有一小片：类型、必填、枚举、正则、
数组项、取值范围、`$ref`、对象封闭（`additionalProperties: false`）。
够用就够——**校验器不是产品，是护栏**。

## 支持的关键字

| 关键字 | 支持 |
|---|---|
| `type` | string / integer / number / boolean / object / array / null；也可写成数组表示「或」 |
| `enum` / `const` | ✅ |
| `required` / `properties` / `additionalProperties` | ✅（`additionalProperties` 支持布尔与 schema） |
| `pattern` / `minLength` / `maxLength` | ✅ |
| `items` / `minItems` / `maxItems` / `uniqueItems` | ✅ |
| `minimum` / `maximum` | ✅ |
| `$ref` / `$defs` | ✅（仅本文件内的 `#/$defs/NAME`） |
| `oneOf` / `anyOf` / `allOf` | ✅ |
| `if` / `then` / `else` / `dependentSchemas` | ❌（本库用不到） |
| `format` | ❌（**不做格式断言**——格式是提示，不是约束） |

## 用法

```python
from jsonschema_mini import compile_schema

check = compile_schema(schema_dict)     # 返回 (instance, path) -> list[str]
errors = check(record, "data/x.jsonl:12")
```
"""
from __future__ import annotations

import re
from typing import Any, Callable

Check = Callable[[Any, str], list[str]]

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


class SchemaError(Exception):
    """schema 本身写错了（不是数据不合法）。"""


def _resolve(ref: str, root: dict) -> dict:
    """解析 `#/$defs/NAME` 形式的本地引用。"""
    if not ref.startswith("#/"):
        raise SchemaError(f"只支持本地引用，收到 {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            raise SchemaError(f"引用解析失败：{ref!r}")
        node = node[part]
    if not isinstance(node, dict):
        raise SchemaError(f"引用指向的不是 schema：{ref!r}")
    return node


def compile_schema(schema: dict, root: dict | None = None) -> Check:
    """把 schema 编译成一个校验函数。

    **编译一次、复用多次**：3,900,000 条记录要逐条校验，
    每次重新解析 schema 会慢一个数量级。
    """
    if root is None:
        root = schema
    return _compile(schema, root)


def _compile(schema: Any, root: dict) -> Check:
    if not isinstance(schema, dict):
        raise SchemaError(f"schema 必须是对象，收到 {type(schema).__name__}")

    if "$ref" in schema:
        target = _resolve(schema["$ref"], root)
        inner = _compile(target, root)
        ref = schema["$ref"]

        def check_ref(inst: Any, path: str, _i=inner, _r=ref) -> list[str]:
            errs = _i(inst, path)
            if errs:
                return [f"{path}: 不满足 {_r}"] + errs
            return []
        return check_ref

    checks: list[Check] = []

    # --- type ---
    if "type" in schema:
        t = schema["type"]
        types = [t] if isinstance(t, str) else list(t)
        for name in types:
            if name not in _TYPE_CHECKS:
                raise SchemaError(f"未知的 type：{name!r}")
        fns = [_TYPE_CHECKS[n] for n in types]

        def check_type(inst: Any, path: str, _f=fns, _n=types) -> list[str]:
            if any(f(inst) for f in _f):
                return []
            return [f"{path}: 类型应为 {'/'.join(_n)}，实为 {type(inst).__name__}"]
        checks.append(check_type)

    # --- enum / const ---
    if "enum" in schema:
        allowed = list(schema["enum"])

        def check_enum(inst: Any, path: str, _a=allowed) -> list[str]:
            if inst in _a:
                return []
            return [f"{path}: 取值 {inst!r} 不在枚举 {sorted(map(str, _a))} 内"]
        checks.append(check_enum)

    if "const" in schema:
        want = schema["const"]

        def check_const(inst: Any, path: str, _w=want) -> list[str]:
            if inst == _w:
                return []
            return [f"{path}: 应恒为 {_w!r}，实为 {inst!r}"]
        checks.append(check_const)

    # --- 字符串 ---
    if "pattern" in schema:
        rx = re.compile(schema["pattern"])

        def check_pattern(inst: Any, path: str, _r=rx) -> list[str]:
            if not isinstance(inst, str):
                return []
            if _r.search(inst):
                return []
            return [f"{path}: {_r.pattern} 不匹配 {inst!r}"]
        checks.append(check_pattern)

    if "minLength" in schema:
        n = schema["minLength"]

        def check_minlen(inst: Any, path: str, _n=n) -> list[str]:
            if isinstance(inst, str) and len(inst) < _n:
                return [f"{path}: 长度 {len(inst)} < {_n}"]
            return []
        checks.append(check_minlen)

    if "maxLength" in schema:
        n = schema["maxLength"]

        def check_maxlen(inst: Any, path: str, _n=n) -> list[str]:
            if isinstance(inst, str) and len(inst) > _n:
                return [f"{path}: 长度 {len(inst)} > {_n}"]
            return []
        checks.append(check_maxlen)

    # --- 数值 ---
    if "minimum" in schema:
        n = schema["minimum"]

        def check_min(inst: Any, path: str, _n=n) -> list[str]:
            if isinstance(inst, (int, float)) and not isinstance(inst, bool) and inst < _n:
                return [f"{path}: {inst} < {_n}"]
            return []
        checks.append(check_min)

    if "maximum" in schema:
        n = schema["maximum"]

        def check_max(inst: Any, path: str, _n=n) -> list[str]:
            if isinstance(inst, (int, float)) and not isinstance(inst, bool) and inst > _n:
                return [f"{path}: {inst} > {_n}"]
            return []
        checks.append(check_max)

    # --- 数组 ---
    if "items" in schema:
        item_check = _compile(schema["items"], root)

        def check_items(inst: Any, path: str, _c=item_check) -> list[str]:
            if not isinstance(inst, list):
                return []
            errs: list[str] = []
            for i, v in enumerate(inst):
                errs.extend(_c(v, f"{path}[{i}]"))
            return errs
        checks.append(check_items)

    if "minItems" in schema:
        n = schema["minItems"]

        def check_minitems(inst: Any, path: str, _n=n) -> list[str]:
            if isinstance(inst, list) and len(inst) < _n:
                return [f"{path}: 元素数 {len(inst)} < {_n}"]
            return []
        checks.append(check_minitems)

    if "maxItems" in schema:
        n = schema["maxItems"]

        def check_maxitems(inst: Any, path: str, _n=n) -> list[str]:
            if isinstance(inst, list) and len(inst) > _n:
                return [f"{path}: 元素数 {len(inst)} > {_n}"]
            return []
        checks.append(check_maxitems)

    if schema.get("uniqueItems"):
        def check_unique(inst: Any, path: str) -> list[str]:
            if not isinstance(inst, list):
                return []
            seen: list[Any] = []
            for v in inst:
                if v in seen:
                    return [f"{path}: 元素重复 {v!r}"]
                seen.append(v)
            return []
        checks.append(check_unique)

    # --- 对象 ---
    props = schema.get("properties")
    required = list(schema.get("required", []))
    addl = schema.get("additionalProperties", True)
    if props is not None or required or addl is not False:
        compiled_props = {k: _compile(v, root) for k, v in (props or {}).items()}
        addl_check = _compile(addl, root) if not isinstance(addl, bool) else None
        req = set(required)

        def check_object(inst: Any, path: str, _p=compiled_props, _r=req,
                         _a=addl, _ac=addl_check) -> list[str]:
            if not isinstance(inst, dict):
                return []
            errs: list[str] = []
            for k in _r:
                if k not in inst:
                    errs.append(f"{path}: 缺少必填字段 {k!r}")
            for k, v in inst.items():
                sub = _p.get(k)
                if sub is not None:
                    errs.extend(sub(v, f"{path}.{k}"))
                elif _a is False:
                    errs.append(f"{path}: 出现未登记字段 {k!r}")
                elif _ac is not None:
                    errs.extend(_ac(v, f"{path}.{k}"))
            return errs
        checks.append(check_object)

    # --- 组合 ---
    if "allOf" in schema:
        subs = [_compile(s, root) for s in schema["allOf"]]

        def check_allof(inst: Any, path: str, _s=subs) -> list[str]:
            errs: list[str] = []
            for c in _s:
                errs.extend(c(inst, path))
            return errs
        checks.append(check_allof)

    if "anyOf" in schema:
        subs = [_compile(s, root) for s in schema["anyOf"]]

        def check_anyof(inst: Any, path: str, _s=subs) -> list[str]:
            for c in _s:
                if not c(inst, path):
                    return []
            return [f"{path}: 不满足 anyOf 的任何一个分支"]
        checks.append(check_anyof)

    if "oneOf" in schema:
        subs = [_compile(s, root) for s in schema["oneOf"]]

        def check_oneof(inst: Any, path: str, _s=subs) -> list[str]:
            hits = sum(1 for c in _s if not c(inst, path))
            if hits == 1:
                return []
            return [f"{path}: 应恰好满足 oneOf 的 1 个分支，实为 {hits} 个"]
        checks.append(check_oneof)

    if not checks:
        def check_any(inst: Any, path: str) -> list[str]:
            return []
        return check_any

    if len(checks) == 1:
        return checks[0]

    def check_all(inst: Any, path: str, _c=checks) -> list[str]:
        errs: list[str] = []
        for c in _c:
            errs.extend(c(inst, path))
        return errs
    return check_all
