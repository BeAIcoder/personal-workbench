# -*- coding: utf-8 -*-
"""真实客户/合作方/员工名称词表（仅用于脱敏校验与分发扫描）。

真实名称**不写入仓库**（否则仓库本身成为泄露源）。运行时按以下顺序合并：
1. 本文件 `PII_TERMS`（默认空）；
2. `dev/pii-terms.local.txt`：每行一个名称，已在 .gitignore 中排除；
3. 环境变量 `PII_TERMS`：英文逗号分隔；
4. `dev/sanitize-map.local.json` 中 mappings[].real（脱敏映射表，gitignore）。

打包时若词表为空，`package_release.py` 会输出警告「PII 词表为空」，
但映射表反查仍然生效；建议本机保留 `pii-terms.local.txt`。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PII_TERMS: tuple[str, ...] = ()

DEV_DIR = Path(__file__).resolve().parent
LOCAL_FILE = DEV_DIR / "pii-terms.local.txt"
SANITIZE_MAP = DEV_DIR / "sanitize-map.local.json"


def _from_local_file() -> list[str]:
    if not LOCAL_FILE.is_file():
        return []
    terms: list[str] = []
    for line in LOCAL_FILE.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            terms.append(text)
    return terms


def _from_mapping() -> list[str]:
    if not SANITIZE_MAP.is_file():
        return []
    try:
        data = json.loads(SANITIZE_MAP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [
        str(item.get("real", "")).strip()
        for item in data.get("mappings") or []
        if isinstance(item, dict) and len(str(item.get("real", "")).strip()) >= 2
    ]


def load_pii_terms() -> list[str]:
    """合并本地词表/环境变量/映射表，去重后返回。"""
    terms = list(PII_TERMS)
    terms += _from_local_file()
    terms += [t.strip() for t in os.environ.get("PII_TERMS", "").split(",")]
    terms += _from_mapping()
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            result.append(term)
    return result
