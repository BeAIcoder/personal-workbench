"""debug_center 调试中心模块：问题记录 / E2E 测试运行归档 / 前端报错采集。

纯 JSON 文件存储（不建 SQLAlchemy 表），存储根目录默认 backend/data/debug/，
可用环境变量 DEBUG_DATA_DIR 覆盖（测试隔离）。E2E 脚本经 dev/e2e/debug_reporter.py
以同一文件格式写入运行结果，本模块负责读取展示与问题跟踪。
"""
from .manifest import manifest  # noqa: F401

__all__ = ["manifest"]
