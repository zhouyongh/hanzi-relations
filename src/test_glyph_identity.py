"""G-identity 负例：错配指针必须被判定失败（给 CI/本地冒烟用）。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glyph_pointer as gp  # noqa: E402


def main() -> int:
    char_yi = {"char_id": "C000001", "head": "一", "modern_codepoints": ["4E00"]}
    # 旧错边：oracle:0001 实际是 㐁
    bad_row = {"modern_char": "㐁", "unicode": "003401", "file": "0001.svg"}
    ok, reason = gp.identity_match(char_yi, bad_row)
    if ok:
        print("NEGATIVE TEST FAILED: mismatched oracle:0001 was accepted")
        return 1
    print(f"negative reject OK ({reason})")

    good_row = {"modern_char": "一", "unicode": "004E00", "file": "0386.svg"}
    ok2, reason2 = gp.identity_match(char_yi, good_row)
    if not ok2:
        print(f"POSITIVE TEST FAILED: correct 0386 rejected ({reason2})")
        return 1
    print(f"positive accept OK ({reason2})")

    # modern-only fallback when unicode absent
    ok3, reason3 = gp.identity_match(char_yi, {"modern_char": "一", "unicode": "", "file": "x.svg"})
    ok4, reason4 = gp.identity_match(char_yi, {"modern_char": "元", "unicode": "", "file": "y.svg"})
    if not ok3 or ok4:
        print("modern fallback tests failed", reason3, reason4)
        return 1
    print("modern fallback OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
