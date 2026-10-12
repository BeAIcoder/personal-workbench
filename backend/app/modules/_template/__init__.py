"""模块模板：复制本目录为新模块目录（如 finance_expense），按下述步骤改成自己的模块。

新建一个模块的步骤：
1. 复制 `_template` 整个目录，重命名为 `finance_<模块名>`（目录名即模块唯一标识）；
2. 改 manifest.py：name 改成目录名，prefix 改成 `/api/finance/<模块名>`；
3. models.py 里定义模块表（表名必须 `mod_` 开头，建议 `mod_<模块名去finance前缀>_xxx`）；
4. schemas.py 定义请求/响应模型，service.py 写业务逻辑与 Agent 工具函数；
5. router.py 定义接口（router 不带 prefix，由注册器按 manifest.prefix 挂载）；
6. 重启服务即自动加载；无需重启的检查：`pytest tests/test_modules.py`。

注意：本目录名以 `_` 开头，注册器会跳过，模板代码不会被加载。
"""
