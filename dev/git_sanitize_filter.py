"""Git clean/smudge 过滤器：提交进库的 blob 脱敏，工作区保持真实内容。

- clean  （git add 时）    ：真实串 -> 占位符
- smudge （git checkout 时）：占位符 -> 真实串

映射表在 dev/sanitize-map.local.json（已 gitignore，不进库）。
映射表缺失或损坏时静默透传—— fresh clone 没有映射表也能正常检出（内容保持脱敏态）。
仅用标准库；任何异常都不允许让过滤器失败（失败会阻断 git 操作）。
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
    # surrogateescape：非 UTF-8 字节（如 GBK 片段）原样往返，保证不破坏任何文件
    text = raw.decode("utf-8", errors="surrogateescape")
    pairs = load_pairs()
    if pairs:
        if mode == "smudge":
            pairs = [(placeholder, real) for real, placeholder in pairs]
        for src, dst in pairs:
            if src in text:
                text = text.replace(src, dst)
    sys.stdout.buffer.write(text.encode("utf-8", errors="surrogateescape"))


if __name__ == "__main__":
    main()
