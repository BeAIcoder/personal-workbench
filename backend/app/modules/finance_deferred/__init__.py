"""finance_deferred（长期待摊）模块包。

范围只覆盖**租入资产改良支出**的直线摊销：项目主档、测算批次、月份级摊销行
（三表 ``mod_deferred_*``）、严格 schema、事务/状态机服务、纯计算引擎
（``calculation.py``）、统一校验（``checks.py``）、xlsx 导出（``exporter.py``）
与只读摘要工具（``tools.py``）。

本文件不 import 子模块，避免包导入即触发副作用（建表注册走 manifest 的
``from . import models``）。
"""
