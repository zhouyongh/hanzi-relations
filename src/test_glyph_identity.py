"""G-identity / 指针格式冒烟（给 CI/本地用）。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glyph_pointer as gp  # noqa: E402


def main() -> int:
    # 新格式解析
    ok_to = "glyph:hanzi-glyphs-svg:uid:004E00-0E00-000001"
    parsed = gp.parse_glyph_target(ok_to)
    if parsed != ("hanzi-glyphs-svg", "004E00-0E00-000001"):
        print("parse uid failed", parsed)
        return 1
    # 旧格式必须拒绝
    legacy = "glyph:hanzi-glyphs-svg:seal:3D000"
    if gp.parse_glyph_target(legacy) is not None:
        print("NEGATIVE: legacy layer:key was accepted")
        return 1
    print("format hard-cut OK")

    char_yi = {"char_id": "C000001", "head": "一", "modern_codepoints": ["4E00"]}
    bad_row = {"modern_char": "㐁", "unicode": "003401", "file": "0001.svg"}
    ok, reason = gp.identity_match(char_yi, bad_row)
    if ok:
        print("NEGATIVE TEST FAILED: mismatched glyph accepted")
        return 1
    print(f"negative reject OK ({reason})")

    good_row = {"modern_char": "一", "unicode": "004E00", "file": "3D000.svg"}
    ok2, reason2 = gp.identity_match(char_yi, good_row)
    if not ok2:
        print(f"POSITIVE TEST FAILED: correct seal row rejected ({reason2})")
        return 1
    print(f"positive accept OK ({reason2})")

    ok3, reason3 = gp.identity_match(
        char_yi, {"modern_char": "一", "unicode": "", "file": "x.svg"}
    )
    ok4, reason4 = gp.identity_match(
        char_yi, {"modern_char": "元", "unicode": "", "file": "y.svg"}
    )
    if not ok3 or ok4:
        print("modern fallback tests failed", reason3, reason4)
        return 1
    print("modern fallback OK")

    formatted = gp.format_glyph_target("004E00-0E00-000001")
    if formatted != ok_to:
        print("format_glyph_target mismatch", formatted)
        return 1
    print("format_glyph_target OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
