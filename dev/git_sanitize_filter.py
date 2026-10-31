"""Git clean/smudge 过滤器：提交进库的 blob 脱敏，工作区保持真实内容。

- clean  （git add 时）    ：真实串 -> 占位符
- smudge （git checkout 时）：占位符 -> reverse 映射

映射表在 dev/sanitize-map.local.json（已 gitignore，不进库）。
映射表缺失或损坏时静默透传—— fresh clone 没有映射表也能正常检出（内容保持脱敏态）。
仅用标准库；任何异常都不允许让过滤器失败（失败会阻断 git 操作）。

编码说明（2026-10-31）：替换在**字节层**做，真实串/占位符分别按 UTF-8 与
GB18030 编码后各替换一次——UTF-8 文件（md/py/js…）与 GBK 文件（bat/vbs，
cscript/cmd 要求 ANSI）都能命中。旧实现只按 UTF-8 解码后匹配，GBK 文件里
的中文真实串永远匹配不上（这正是 .vbs 真实路径曾经入库的根因）。
"""
import json
import sys
from pathlib import Path

MAP_FILE = Path(__file__).resolve().parent / "sanitize-map.local.json"


def load_pairs() -> list[tuple[str, str]]:
    try:
        data = json.loads(MAP_FILE.read_text(encoding="utf-8"))
        pairs = [(m["real"], m["placeholder"]) for m in data.get("mappings", [])]
        # 长的先替换，避免短串截断长串
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        return pairs
    except Exception:
        return []


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "clean"
    raw = sys.stdin.buffer.read()
    pairs = load_pairs()
    if pairs:
        if mode == "smudge":
            pairs = [(placeholder, real) for real, placeholder in pairs]
        for src, dst in pairs:
            for encoding in ("utf-8", "gb18030"):
                try:
                    src_bytes = src.encode(encoding)
                    dst_bytes = dst.encode(encoding)
                except UnicodeEncodeError:
                    continue
                if src_bytes in raw:
                    raw = raw.replace(src_bytes, dst_bytes)
    sys.stdout.buffer.write(raw)


if __name__ == "__main__":
    main()
