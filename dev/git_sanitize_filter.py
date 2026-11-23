"""Git clean/smudge 过滤器：提交进库的 blob 脱敏，工作区保持真实内容。

- clean  （git add 时）    ：真实串 -> 占位符；再叠加通用模式（微信 ID / 常见令牌）
- smudge （git checkout 时）：占位符 -> reverse 映射
- check  （pre-push 等）   ：只读 stdin，命中通用模式时非零退出，不回显命中值

映射表在 dev/sanitize-map.local.json（已 gitignore，不进库）。
映射表缺失或损坏时静默透传—— fresh clone 没有映射表也能正常检出（内容保持脱敏态）。
仅用标准库；任何异常都不允许让过滤器失败（失败会阻断 git 操作）。

编码说明（2026-10-31）：替换在**字节层**做，真实串/占位符分别按 UTF-8 与
GB18030 编码后各替换一次——UTF-8 文件（md/py/js…）与 GBK 文件（bat/vbs，
cscript/cmd 要求 ANSI）都能命中。旧实现只按 UTF-8 解码后匹配，GBK 文件里
的中文真实串永远匹配不上（这正是 .vbs 真实路径曾经入库的根因）。

通用模式（2026-11-11）：路径/公司名仍需映射表登记；微信 ID 与常见云令牌
改为 clean 时无条件遮蔽（不可逆），并在 check 模式报规则名。完整 PII 门禁
（路径、公司全称、xlsx 单元格、本地词表）由 dev/check_pii.py 负责。
"""
import json
import re
import sys
from pathlib import Path

MAP_FILE = Path(__file__).resolve().parent / "sanitize-map.local.json"

GENERIC_RULES = (
    ("wechat_id", re.compile(rb"\bwxid_[A-Za-z0-9_-]{8,}"), b"wxid_***"),
    (
        "token",
        re.compile(
            rb"\b(?:"
            rb"sk-(?:ant-|proj-|svcacct-)?[A-Za-z0-9_-]{20,}"
            rb"|gh[pousr]_[A-Za-z0-9]{30,}"
            rb"|github_pat_[A-Za-z0-9_]{40,}"
            rb"|AKIA[0-9A-Z]{16}"
            rb"|AIza[0-9A-Za-z_-]{35}"
            rb"|xox[baprs]-[A-Za-z0-9-]{10,}"
            rb")(?![A-Za-z0-9_-])",
        ),
        b"***REDACTED***",
    ),
    # 私钥块只检测不替换：只遮头会留下密钥正文，反而制造“已处理”的假象。
    ("private_key", re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"), None),
)


def load_pairs() -> list[tuple[str, str]]:
    try:
        data = json.loads(MAP_FILE.read_text(encoding="utf-8"))
        pairs = [(m["real"], m["placeholder"]) for m in data.get("mappings", [])]
        # 长的先替换，避免短串截断长串
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        return pairs
    except Exception:
        return []


def sanitize_bytes(
    raw: bytes, mode: str = "clean", pairs: list[tuple[str, str]] | None = None
) -> bytes:
    """按映射表 + 通用模式处理字节；``pairs`` 显式传入时便于合成测试。"""
    if pairs is None:
        pairs = load_pairs()
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
    if mode == "clean":
        for _rule, pattern, replacement in GENERIC_RULES:
            if replacement is not None:
                raw = pattern.sub(replacement, raw)
    return raw


def check_bytes(raw: bytes) -> list[str]:
    """返回命中的规则名（不返回值），供 pre-push 检查。"""
    return [rule for rule, pattern, _replacement in GENERIC_RULES if pattern.search(raw)]


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "clean"
    raw = sys.stdin.buffer.read()
    if mode == "check":
        hits = check_bytes(raw)
        if hits:
            print("sanitize check 命中：" + ", ".join(hits), file=sys.stderr)
            raise SystemExit(1)
        return
    sys.stdout.buffer.write(sanitize_bytes(raw, mode))


if __name__ == "__main__":
    main()
