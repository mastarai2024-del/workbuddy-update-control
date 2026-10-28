# WorkBuddy Update Control

用于 macOS WorkBuddy 的 Codex Skill：检查自动升级状态、清理待安装更新包、阻止后台下载，并恢复原设置。

当前自动修改范围为 WorkBuddy 5.9.2，且更新器源码摘要必须匹配。其他版本可以检查，但不会自动修改。此工具与 WorkBuddy 官方无关。

## 安装为 Skill

将仓库克隆到 Codex Skills 目录：

```bash
git clone https://github.com/mastarai2024-del/workbuddy-update-control.git ~/.codex/skills/workbuddy-update-control
```

目标目录已存在时，先确认归属与未提交修改。重新打开 Codex 聊天后，可输入：

```text
使用 $workbuddy-update-control 检查 WorkBuddy 自动更新状态。
```

要同时关闭启动升级、清理安装包并阻止下载：

```text
使用 $workbuddy-update-control 关闭 WorkBuddy 自动升级，删除待安装更新包，并阻止后续自动下载。
```

完整操作和验收要求见 [SKILL.md](SKILL.md)。

## 直接使用脚本

需要 macOS 和 Python 3.9 或更高版本，无第三方依赖。脚本自身不联网、不请求管理员权限、不修改应用包。执行修改前需正常退出 WorkBuddy。

```bash
# 只读检查，可在 WorkBuddy 运行时执行
python3 scripts/workbuddy_updates.py inspect

# 预览组合操作；不写入文件
python3 scripts/workbuddy_updates.py disable

# 关闭启动升级、删除已校验的待安装文件并设置下载锁
python3 scripts/workbuddy_updates.py disable --apply

# 重新打开 WorkBuddy 后进行文件检查，运行日志和界面另行核验
python3 scripts/workbuddy_updates.py verify
```

修改后会返回备份目录，默认位于 `~/Library/Application Support/workbuddy-update-control/backups/`。备份只保存目标字段及操作状态，不复制完整配置或安装包。

恢复时先退出 WorkBuddy，并使用此前输出的备份目录：

```bash
python3 scripts/workbuddy_updates.py restore --backup /absolute/path/to/backup
python3 scripts/workbuddy_updates.py restore --backup /absolute/path/to/backup --apply
```

恢复会保留其他最新设置。已清理的更新包不会恢复。

自定义实例可用 `--app /path/to/WorkBuddy.app`、`--config-dir /path/to/config-root`；备份位置可通过 `--backup-root` 指定。路径含符号链接、未知下载文件、运行中进程、正在安装的更新或源码不匹配时，脚本会停止。

## 工作方式与限制

`startupForceAutoUpdate=false` 只关闭启动时自动升级，配置实际位于 `~/.workbuddy/app/app-config.json`。当前更新器仍会自动检查和下载。因此本工具还在它的固定下载路径放置带 `uchg` 标志的只读占位文件，使安装包下载前的目录创建失败。

软件仍可能查询新版本，手动检查更新也可能显示错误。重新安装、更新器改版或外部分发可能使用不同路径，届时需要重新核验。使用此工具也会阻止后续安全修复的自动安装；需要更新时先恢复限制，再使用官方安装程序。

2026-10-28 的本机验证覆盖 macOS WorkBuddy 5.9.2：清理待安装 5.9.3 后，重启版本保持不变；日志确认启动自动升级已关闭，后续真实自动检查在下载前被拦截。封装脚本另在隔离临时数据上验证，未重新执行本机清理。[源码布局与验证范围](references/updater-layout.md)

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试只使用临时目录和可丢弃文件，覆盖清理边界、拒绝条件、文件锁、重复执行及保留其他配置的恢复流程。macOS 之外会跳过文件锁集成测试。

MIT License，见 [LICENSE](LICENSE)。
