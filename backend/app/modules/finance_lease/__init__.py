"""finance_lease（租赁核算）模块包。

承租人租赁核算：五表模型（``models.py``）、严格 schema（``schemas.py``）、
事务/状态机/快照服务（``service.py``）、支持分录（``entries.py``）、只读摘要工具
（``tools.py``）与 ``manifest.py`` / ``router.py`` 注册接口。

计算引擎 ``calculation.py`` 与校验 ``checks.py`` 由计算范围实现；导入 ``importer.py``、
导出 ``exporter.py`` 由 IO 范围实现。本文件不 import 子模块，避免包导入即触发副作用。
"""
