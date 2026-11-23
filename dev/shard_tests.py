#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 backend/tests 下的测试文件按确定性负载均衡分片输出，供 CI / 本地使用。

用法（在 backend 目录）：
    python ../dev/shard_tests.py --shard 1 --total 4     # 每行一个测试文件路径
    python ../dev/shard_tests.py --mode fast             # 跳过整文件 slow 的文件
    python ../dev/shard_tests.py --count                 # 只打印将参与的文件数
    python ../dev/shard_tests.py --stats                 # 分片权重摘要（stderr）

输出约定：**每行一个相对路径**，便于 bash `mapfile -t FILES < <(...)` 与
`xargs` 直接消费（早期版本输出单行空格分隔，`mapfile` 会把它当成一个参数）。

分片规则：以文件展开后的用例数（`parametrize` 组合数 + 模块级常量解析）为权重，
按权重降序、权重相同按文件名升序，做 LPT 贪心装填到当前最轻的分片；
同一输入永远产出同一结果（不依赖哈希随机化、文件顺序或 mtime）。

fast/slow 约定（与 `backend/pytest.ini` 的 markers 登记同源）：
- 整文件慢：模块级 `pytestmark = pytest.mark.slow`，`--mode fast` 会整体跳过；
- 单例慢：函数/类级 `@pytest.mark.slow`，运行时用 `pytest -m "not slow"` 过滤；
- 本脚本只做文件级排除（不解析单个用例），所以 `--mode fast` 的结果是
  `-m "not slow"` 的父集近似：文件级 slow 一定是 slow，单例 slow 仍会被选中。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def test_", re.MULTILINE)
PYTESTMARK_RE = re.compile(r"^pytestmark\s*=", re.MULTILINE)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _element_count(node: ast.expr, consts: dict[str, ast.expr], depth: int = 0) -> int:
    """估算 parametrize 的 argvalues 展开数；解析不了按 1 算（只会低估，不会高估）。

    真实用例常写成 `@pytest.mark.parametrize("v", CASES)`，所以先解析模块级常量，
    再退到 AST 形状（List/Tuple/Dict/list()/tuple()/拼接）。
    """
    if depth > 6:
        return 1
    if isinstance(node, ast.Name) and node.id in consts:
        return _element_count(consts[node.id], consts, depth + 1)
    try:
        data = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        data = None
    if isinstance(data, (list, tuple)):
        return max(1, len(data))
    if isinstance(data, dict):
        return max(1, len(data))
    if isinstance(node, (ast.List, ast.Tuple)):
        return max(1, len(node.elts))
    if isinstance(node, ast.Dict):
        return max(1, len(node.keys))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"list", "tuple", "sorted", "set", "frozenset"}
        and node.args
    ):
        return _element_count(node.args[0], consts, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _element_count(node.left, consts, depth + 1) + _element_count(
            node.right, consts, depth + 1
        )
    return 1


def _parametrize_size(call: ast.Call, consts: dict[str, ast.expr]) -> int:
    """`@pytest.mark.parametrize("a,b", ARGVALUES)` 展开后的用例数。"""
    if len(call.args) < 2:
        return 1
    return _element_count(call.args[1], consts)


def _is_parametrize(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr == "parametrize"


def _parametrize_multiplier(decorators: list[ast.expr], consts: dict[str, ast.expr]) -> int:
    multiplier = 1
    for decorator in decorators:
        if isinstance(decorator, ast.Call) and _is_parametrize(decorator):
            multiplier *= _parametrize_size(decorator, consts)
    return multiplier


def test_weight(path: Path) -> int:
    """文件权重 ≈ 展开后的用例数（测试成本的一阶代理）；至少为 1。

    逐层累加 `parametrize` 组合数（模块级 `pytestmark` / 类装饰器 / 函数装饰器，
    argvalues 允许引用模块级常量），让分片按真实收集数均衡，而不是按文件数。
    """
    text = _read(path)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return max(1, len(TEST_DEF_RE.findall(text)))

    consts: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id != "pytestmark":
                    consts[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            if node.target.id != "pytestmark":
                consts[node.target.id] = node.value

    module_multiplier = 1
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in node.targets
        ):
            for value in ast.walk(node.value):
                if isinstance(value, ast.Call) and _is_parametrize(value):
                    module_multiplier *= _parametrize_size(value, consts)

    def count(container: list[ast.stmt], inherited: int) -> int:
        total = 0
        for node in container:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    total += inherited * _parametrize_multiplier(node.decorator_list, consts)
            elif isinstance(node, ast.ClassDef):
                total += count(
                    node.body,
                    inherited * _parametrize_multiplier(node.decorator_list, consts),
                )
        return total

    total = count(tree.body, module_multiplier)
    if not total:
        return max(1, len(TEST_DEF_RE.findall(text)))
    return max(total, 1)


def declares_file_level_slow(path: Path) -> bool:
    """模块级 `pytestmark` 语句里出现 `slow` 即视为整文件慢。

    只截取 `pytestmark =` 之后到语句结束（空行或缩进回退）的窗口，
    避免把文件后面无关的 `slow` 字样误判成本文件标记。
    """
    text = _read(path)
    for match in PYTESTMARK_RE.finditer(text):
        window = text[match.end(): match.end() + 400]
        statement = window.split("\n\n", 1)[0]
        if re.search(r"\bslow\b", statement):
            return True
    return False


def collect(root: Path, mode: str) -> list[Path]:
    files = sorted(
        (
            path for path in root.glob("test_*.py")
            if path.is_file()
        ),
        key=lambda path: path.name,
    )
    if mode == "fast":
        files = [path for path in files if not declares_file_level_slow(path)]
    return files


def assign(files: list[Path], total: int) -> list[list[Path]]:
    """LPT 贪心：重的先放，每次放进当前权重最轻的分片。"""
    weights = {path: test_weight(path) for path in files}
    buckets: list[list[Path]] = [[] for _ in range(total)]
    loads = [0] * total
    for path in sorted(files, key=lambda item: (-weights[item], item.name)):
        target = min(range(total), key=lambda index: (loads[index], len(buckets[index]), index))
        buckets[target].append(path)
        loads[target] += weights[path]
    return [sorted(bucket, key=lambda item: item.name) for bucket in buckets]


def main(argv: list[str] | None = None) -> int:
    # Windows 原生 Python 的文本模式会把 \n 翻成 \r\n，bash `mapfile -t` 只剥 \n，
    # 于是每个路径尾部残留 \r，pytest 拿到就是「文件不存在」。固定输出只用 LF。
    # 只在命令行入口改，不被 import 时也生效（测试会直接 import 本模块做均衡断言）。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")

    parser = argparse.ArgumentParser(description="按负载均衡输出 pytest 测试文件分片")
    parser.add_argument("--shard", type=int, help="1-based 分片号（与 --total 同用）")
    parser.add_argument("--total", type=int, default=4, help="分片总数，默认 4")
    parser.add_argument("--root", default="tests", help="测试目录（相对当前目录），默认 tests")
    parser.add_argument(
        "--mode",
        choices=("full", "fast"),
        default="full",
        help="full=全部文件；fast=跳过整文件声明 pytest.mark.slow 的文件",
    )
    parser.add_argument("--count", action="store_true", help="只打印文件数（判断分片是否为空）")
    parser.add_argument("--stats", action="store_true", help="把每片文件数/权重写到 stderr")
    args = parser.parse_args(argv)

    if args.total < 1:
        parser.error("--total 必须 >= 1")
    if args.shard is not None and not (1 <= args.shard <= args.total):
        parser.error("要求 1 <= --shard <= --total")

    root = Path(args.root)
    if not root.is_dir():
        parser.error(f"测试目录不存在：{root}")

    files = collect(root, args.mode)
    buckets = assign(files, args.total)

    if args.stats:
        for index, bucket in enumerate(buckets, start=1):
            weight = sum(test_weight(path) for path in bucket)
            print(
                f"[shard {index}/{args.total}] {len(bucket)} 文件 / 权重 {weight}",
                file=sys.stderr,
            )
        if args.mode == "fast" and files and not any(
            declares_file_level_slow(path) for path in collect(root, "full")
        ):
            print(
                "[fast] 当前没有整文件 pytest.mark.slow 标记，fast 子集等于全量；"
                "单例慢请用 @pytest.mark.slow + pytest -m \"not slow\"",
                file=sys.stderr,
            )

    selected = buckets if args.shard is None else [buckets[args.shard - 1]]
    if args.count:
        print(sum(len(bucket) for bucket in selected))
        return 0

    for bucket in selected:
        for path in bucket:
            print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
