# 脱敏过滤机制（git clean/smudge filter）

## 原理

提交进 git 的内容经 **clean 过滤器**脱敏（真实串 → 占位符）；检出时经 **smudge 过滤器**反向恢复（占位符 → 真实串）。

- 工作区文件**始终是真实内容**，本地运行完全不受影响；
- git 仓库（含远端）里的 blob **始终是脱敏内容**；
- 映射表 `dev/sanitize-map.local.json` **不进库**（已 gitignore），是反向恢复的唯一钥匙，请妥善保管（建议随每周备份一起拷到 `backups/`）。

## 涉及文件

| 文件 | 入库？ | 作用 |
| --- | --- | --- |
| `dev/git_sanitize_filter.py` | ✅ | 过滤器本体（clean/smudge 双模式，映射缺失时透传） |
| `dev/sanitize-map.local.json` | ❌（gitignore） | 真实串 ↔ 占位符映射表（私有） |
| `dev/install_filter.bat` | ✅ | 克隆后一键重挂过滤器配置 |
| `dev/check_pii.py` | ✅ | PII 门禁：指定路径 / tracked / staged / `git archive`，文本 + xlsx 单元格 |
| `dev/desensitize.local.json` | ❌（gitignore） | 脱敏脚本的真实目录与公司映射（与反向钥匙分开保管） |
| `.gitattributes` | ✅ | 声明哪些文本文件走过滤器（md/py/js/vue/json/yml/txt） |

## 日常（本机）无需任何操作

过滤器已在本仓库配置好（`.git/config`）。`git add` / `git commit` 自动脱敏，`git diff` 不受影响。

## 从 git 还原后恢复真实内容

1. 正常 `git clone`（此时文件是脱敏态，但代码可以完整运行——占位符都是可运行的安全值）；
2. 把备份的 `sanitize-map.local.json` 放回 `dev/` 目录；
3. 双击 `dev/install_filter.bat`——它会自动完成过滤器配置 + 全量重检出（smudge 反向恢复），一步到位。

还原命令的手动等价形式（install_filter.bat 的内容）：

```bat
git config filter.sanitize.clean "python dev/git_sanitize_filter.py clean"
git config filter.sanitize.smudge "python dev/git_sanitize_filter.py smudge"
git config filter.sanitize.required false
git checkout-index -f -- dev/git_sanitize_filter.py
git rm -r --cached .
git read-tree HEAD
git checkout-index -f -a
```

> 过程中如出现两行关于 `dev/git_sanitize_filter.py` 自身的 `external filter failed` 提示，属无害噪音：smudge 处理过滤器脚本自己时文件正被覆写，而脚本不含任何敏感串，过滤与否内容相同。

## 脱敏脚本本地配置（gitignore）

`dev/desensitize.local.json` 与反向钥匙分开保存，代码只留合成示例。字段（均可选）：

```json
{
  "wechat_dir": "<合成目录>",
  "attach_dir": "<合成附件目录>",
  "pos_dir": "<合成目录>",
  "cash_dir": "<合成目录>",
  "manage_attach_dir": "<合成目录>",
  "company_tokens": {"合成公司甲": "合成公司乙"},
  "cash_files": ["合成文件1.xlsx", "合成文件2.xlsx"],
  "manage_reports": {
    "project_company": [["合成公司甲", "合成公司乙"]],
    "law_firms": [["合成律所甲", "合成律所乙"]],
    "staff": ["合成人员甲"],
    "defendants": ["合成被告甲"],
    "lawyers": ["合成律师甲"],
    "others": ["合成其他甲"],
    "brands": ["合成品牌甲"]
  }
}
```

环境变量优先级更高：`VERIFY_WECHAT_DIR` / `VERIFY_ATTACH_DIR` / `POS_SRC_DIR` / `CASH_SRC_DIR` / `MANAGE_ATTACH_DIR`。

## 通用模式防护（2026-11-11）

clean 模式在映射表之外，再对两类高置信模式做**不可逆**遮蔽（不依赖映射表，fresh clone 同样生效）：

| 规则 | 处理 | 说明 |
| --- | --- | --- |
| 微信 ID（`wxid_` + 8 位以上字母数字） | → `wxid_***` | 遮蔽后无法反向恢复，属有意设计 |
| 常见云令牌（`sk-…` / `sk-ant-…` / `ghp_…` / `github_pat_…` / `AKIA…` / `AIza…` / `xox…`） | → `***REDACTED***` | 匹配 20 位以上高熵串，覆盖 `sk-ant-*` 与连字符/下划线形态；`sk-synthetic-*` 等测试值豁免 |
| PEM 私钥头 | 仅检测，不替换 | 只遮头会留下正文；`check` 模式直接非零退出 |

过滤器还提供 `check` 模式：从 stdin 读入 blob，命中通用模式时打印**规则名**并退出 1（不回显值）：

```sh
git show :dev/example.py | python dev/git_sanitize_filter.py check
```

路径、公司全称、xlsx 单元格与本地词表由 `dev/check_pii.py` 负责（过滤器只处理文本流）；
两者互补，不能互相替代。

## PII 门禁（pre-push / 发布预检）

```sh
python dev/check_pii.py --tracked   # 工作树
python dev/check_pii.py --staged    # 暂存区（pre-commit）
python dev/check_pii.py --archive HEAD   # 某个提交的完整树
python dev/check_pii.py --repo <克隆目录> --archive HEAD   # 扫描克隆副本
python dev/check_pii.py --path dev --path BUGfix.md
```

pre-push 钩子：**完整脚本以 `dev/hooks/pre-push` 为准**（此处不再复制，避免与实现漂移）；安装 `dev/install_hooks.bat` 或手动复制到 `.git/hooks/pre-push`。脚本行为：

- `git rev-list` 遍历**待推送范围内的每个提交**，不只 tip；
- 新分支（`remote_sha` 全零）用 `--not --remotes` 扫本地新增提交；删除分支（`local_sha` 全零）跳过；
- 本机词表存在时显式 `--terms`；不存在时打印警告，通用规则仍执行；
- 无 stdin（手动运行）回退 `--archive HEAD`。
- 不要用 `--staged` 代替 pre-push：push 时暂存区通常为空，不能代表待推送提交。
- 输出只有「文件:行号/单元格 + 规则名」，不回显命中值；退出码 0=干净、1=命中、2=扫描失败。
- 本地词表只通过 `--terms` / `PII_TERMS_FILE` / `PII_TERMS` 加载，**不读取映射钥匙**。
- `--archive` 扫描提交树，`--staged` 扫描即将提交的 blob，`--tracked` 扫描工作区内容；`--repo` 可指向克隆副本。
- 文本按 UTF-8 → GB18030 → UTF-16 解码；`.xlsx/.xlsm` 逐个单元格扫描；损坏 xlsx/无法解码按 2 退出。
- `.pdf`/`.zip`/`.7z`/`.rar`/`.gz`/`.tar` **不解析内容**：扫描时在 stderr 明确提示并跳过；需要覆盖时先解包/转文本，不能当作“已扫干净”。

## 历史清理注意（git filter-repo）

1. 先保全：按 `backups/20261111-pre-remediation/` 的 bundle + 未提交补丁 + 未跟踪 ZIP 验证可还原。
2. 替换规则放**本机 gitignore 目录**（如 `backups/20261111-pre-remediation/replace-text-*.txt`），每行 `真实值==>占位符`，**绝不提交、绝不贴进报告**。
3. `git filter-repo --replace-text <规则文件>` 会重写全部提交；先在全量克隆副本演练，重写后对 `git log --all -p` 与 `git archive <rev>` 做敏感串反查，确认 0 命中。
4. 强推只能 `git push --force-with-lease`；旧对象、GitHub 缓存、fork/第三方镜像无法保证清除，必须在报告中如实标注。
5. 历史重写后协作者需重新克隆；本地 `sanitize-map.local.json` 仍是 smudge 钥匙，重写不改变工作区真实内容。

## 注意事项

- 新增敏感串时，**先加映射再提交**；映射表本身永远不许入库。
- 脱敏脚本的真实目录/公司映射放 `dev/desensitize.local.json`（gitignore），代码只留合成示例；该文件与反向钥匙同等敏感，随每周备份保存。
- 占位符一旦入库就不要再改（改了等于换钥匙，历史 blob 无法对应）。
- 过滤器需要 `python` 在 PATH 上（项目本身的前置要求）。
- **编码覆盖（2026-10-31）**：替换在字节层按 UTF-8 与 GB18030 双编码各做一次，
  GBK 文件（bat/vbs，cmd/cscript 要求 ANSI）同样被脱敏；`.gitattributes` 已把
  `*.example/*.vbs/*.bat/*.html/*.css/*.sh/*.mjs` 纳入过滤。历史 blob 中的残留
  （如旧版 `backend/.env.example`、`开机自启动.vbs` 里的真实路径）需单独清洗。
- 本机制只保证**当前 HEAD 起**的内容脱敏；更老的提交历史需按「历史清理注意」用 git filter-repo + 强推（破坏性操作，先备份、先演练、后 `--force-with-lease`）。
