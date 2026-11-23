# 发布与 PII 门禁 Playbook

> 适用：打开发布包、推送前检查、换机/恢复、公开源码分支。
> 铁律：**真实 Key 只经界面/环境变量录入，绝不写入文档、日志、提交、截图。**

## 1. 发布前

```bash
# 1) 工作区/暂存区 PII
python dev/check_pii.py --tracked --terms dev/pii-terms.local.txt
python dev/check_pii.py --staged --terms dev/pii-terms.local.txt

# 2) 归档（发布前必跑；含 xlsx 单元格）
python dev/check_pii.py --archive HEAD --terms dev/pii-terms.local.txt

# 3) 版本/契约
python dev/check_contracts.py
python dev/check_architecture.py

# 4) 测试与构建
cd backend && PYTHONIOENCODING=utf-8 venv/Scripts/python.exe -m pytest tests -q -n 4
cd ../frontend && npm test && npm run build
```

要求：工作区干净（`git status --porcelain` 为空）；版本以 `backend/app/config.py` 为单一源；`frontend/dist` 有构建印章。

## 2. 打包

```bash
# Windows 双击或命令行
打包发布.bat
```

脚本会：

1. 预检（git 干净/node+npm/映射表/venv）。
2. `check_pii --archive HEAD`（含本地词表）。
3. `git archive HEAD`（禁用 clone/bundle）。
4. 剔除 `AGENTS.md`/dated 报告/`20261012New/`/`dev/`/tests/`docs/`。
5. `backend/data` 空骨架 + staging 内 `npm ci + build`。
6. 三重扫描（映射表真实值反查 + 密钥正则 + 结构红线 + 二进制白名单）。
7. ZIP 到 `backups/releases/` + 解压冒烟。

## 3. 推送前

- 安装 pre-push 钩子：`dev/install_hooks.bat`。
- 推送前确认分支/领先数：
  ```bash
  git rev-list --left-right --count origin/master...HEAD
  git status --short
  ```
- 远端历史清理/force push 属于高影响操作，必须用户本机授权；不要在无凭据环境执行。

## 4. 换机/恢复

- 用整包 ZIP（DB + 附件/技能/专家包/财务目录）；只带 `.db` 会丢数据。
- 恢复脚本 `restore_backup.py` 是原子化的：临时目录 → `integrity_check` → 旧数据快照 → 换入；失败回滚。
- 本机配置四件套（`.env`、银行映射、`sanitize-map.local.json`、PII 词表）只由 CLI 收集，不随包。
- DPAPI 密钥换机后需重录（预期设计）。

## 5. 失败处理

| 现象 | 原因 | 处理 |
|---|---|---|
| PII 命中 | 真实路径/公司名/Key/wxid | 改占位符/本地配置；不要只改扫描器 |
| 版本不一致 | config/package/lock 不同 | 以 config.py 为准同步；跑契约检查 |
| 打包预检失败 | 工作区脏/映射表坏/venv 缺 | 先修前置，不要绕过预检 |
| 冒烟失败 | 解压后启动/`/docs`/静态页 | 看 staging 日志；不要在仓库 venv 里“证明”干净包可用 |
| lock 缺依赖 | 锁文件过期 | 重新生成或删除只留 requirements.txt；干净 venv 验证 |
