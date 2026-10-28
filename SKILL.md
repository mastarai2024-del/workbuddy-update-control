---
name: workbuddy-update-control
description: 检查或关闭 macOS WorkBuddy 自动升级、清理待安装更新包、阻止后台下载，并恢复原设置。适用于用户明确要求禁止 WorkBuddy 更新或查询是否已关闭；Windows、Linux 和技能自身更新另行诊断。
---

# WorkBuddy 更新控制

先区分用户要求：查询只运行 `inspect`；“关闭自动升级”只涉及启动升级偏好；“不自动下载”还需要下载拦截；删除更新包需要明确包含清理的授权。说明下载锁会让检查更新报错。当前辅助脚本只自动修改实机验证过的 macOS WorkBuddy 5.9.2 及指定更新器 SHA256，其他构建停在检查阶段。

## 检查

相对路径均以本 Skill 目录为起点：

```bash
python3 scripts/workbuddy_updates.py inspect
```

需要自定义安装或多账号配置时，用 `--app` 和 `--config-dir` 指向实际运行实例。先核对进程、启动方式及配置归属。默认目标为 `/Applications/WorkBuddy.app` 和 `~/.workbuddy`。

开关保存在 `~/.workbuddy/app/app-config.json` 的顶层 `startupForceAutoUpdate`，不是 `~/.workbuddy/settings.json`。字段缺失不能解释为已关闭；`false` 也只关闭启动时自动升级。需要判断运行状态时，读取 `~/.workbuddy/logs/update/update-YYYYMMDD.log` 的当前启动会话，按实际时区解释 UTC 时间。避免输出完整配置、带账号参数的 URL 或 UI 种子令牌。

## 关闭与清理

本节适用于用户同时授权关闭升级、阻止下载和清理更新。只获部分授权时，只完成对应范围；不要直接运行组合操作。

1. 用原生界面确认任务状态，正常退出目标 WorkBuddy 实例。存在运行任务或正在安装的更新时，保留现场并处理该阻塞；脚本不会自行退出或强杀软件。
2. 预览精确删除清单：

   ```bash
   python3 scripts/workbuddy_updates.py disable
   ```

   `supportedLayout` 必须为真。路径不符、安装中、符号链接、未知下载文件或损坏配置会被拒绝。不要绕过拒绝条件扩大删除范围。
3. 按已获授权应用：

   ```bash
   python3 scripts/workbuddy_updates.py disable --apply
   ```

   脚本先记录待修改字段的原值及恢复状态，再设置 `startupForceAutoUpdate=false`、删除校验过的待安装压缩包/解压目录/隐藏安装副本和记录，最后把下载目录改成带 `uchg` 标志的只读占位文件。保留输出的 `backup` 绝对路径。备份只包含变更字段，不复制完整配置或已删除安装包。
4. 重新启动 WorkBuddy，执行文件检查：

   ```bash
   python3 scripts/workbuddy_updates.py verify
   ```

   再完成下面的运行验收。文件检查不能替代运行验收。

## 运行验收

- 界面显示保留版本，原“重启更新”横幅消失，原任务列表可见。
- 当前启动会话出现 `enabled=false source=user-setting`，包括窗口创建前的早期更新检查。
- 等待启动约 30 秒后的自动检查。服务器有新版本时，应在下载目录创建阶段出现 `EEXIST`，且此会话没有 `Downloading update from:`、新增压缩包或待安装副本。
- 服务器没有更新或不可达时，只能确认偏好和文件锁，真实下载拦截记为未验证。不要为了验证下载而清除锁或安装新版。

交付写明版本、真实修改、删除范围、日志/UI 证据、备份及恢复路径。仍可能查询版本；不声称禁用了所有更新网络请求，也不承诺重装或更新器改版后继续有效。

## 恢复

先退出目标 WorkBuddy，再预览并应用原 `disable` 输出的备份目录：

```bash
python3 scripts/workbuddy_updates.py restore --backup /absolute/path/to/backup
python3 scripts/workbuddy_updates.py restore --backup /absolute/path/to/backup --apply
```

恢复只解除本 Skill 的锁并恢复原开关字段，保留其他最新设置；安装包不会恢复。自定义实例需继续传入原 `--app` / `--config-dir`。部分执行失败时，先查看脚本返回的恢复快照和 `phase`，逐项处理，不重复盲目清理。此前手工处理遗留的锁可只读识别，但恢复应使用当时生成的恢复材料。

## 未知版本

读取 [references/updater-layout.md](references/updater-layout.md)，核对本机真正的更新器和请求顺序。更新支持前需要在隔离数据上验证拒绝条件和恢复，再做获授权的本机验收。保持应用签名及网络/账号配置完整；本 Skill 不修改 `app.asar`，也不通过封禁整站阻断模型服务。
