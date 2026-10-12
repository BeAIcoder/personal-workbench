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

## 注意事项

- 新增敏感串时，**先加映射再提交**；映射表本身永远不许入库。
- 占位符一旦入库就不要再改（改了等于换钥匙，历史 blob 无法对应）。
- 过滤器需要 `python` 在 PATH 上（项目本身的前置要求）。
- 本机制只保证**当前 HEAD 起**的内容脱敏；更老的提交历史里的字符串不在处理范围（如需清洗历史，用 git filter-repo + 强推，属破坏性操作，单独评估）。
