# 已验证的更新器布局

2026-10-28 在 macOS 的 WorkBuddy 5.9.2 上核验。应用 Bundle ID 为 `com.tencent.workbuddy.mac`；`main/update-service.darwin.js` 的 SHA256：

```text
73e624fefade28999e081ce3c0d635dd3474eb82bd5485dfa52495b7ac1c4af3
```

这个摘要限制辅助脚本的自动修改范围。相同版本号仍可能有不同构建；不匹配时先检查源码，不通过改常量强行放行。

## 两条更新控制路径

`main/index.js` 的早期启动检查读取运行时 userDataDir 下的 `app-config.json`；`main/main-bootstrap.js` 的普通启动检查也使用 `startupForceAutoUpdate`。用户显式 `false` 优先于产品默认 `true`。主进程仍会在启动约 30 秒后及每小时检查版本，所以只改偏好无法阻止后台下载。

当前 Darwin 更新器的 `checkForUpdates` 调用自有更新流程。`downloadAndVerifyUpdate` 在调用 `downloadWithCandidates` 前创建 `BundleMigration/downloads`。用只读普通文件占住这个路径会使 mkdir 返回 EEXIST，流程在获取安装包前结束。`uchg` 阻止清理程序删除该占位文件。读取更新元数据的请求仍可发生。

## 文件对应关系

|用途|默认路径|
|---|---|
|应用|`/Applications/WorkBuddy.app`|
|升级偏好|`~/.workbuddy/app/app-config.json`|
|更新日志|`~/.workbuddy/logs/update/update-YYYYMMDD.log`|
|更新缓存|`~/Library/Caches/com.tencent.workbuddy.mac.BundleMigration`|
|压缩包|`downloads/WorkBuddy-<version>.zip`|
|解压目录|`extracted/<version>/WorkBuddy.app`|
|待安装副本|应用目录下的 `.WorkBuddy.app.installing.<version>`|
|待安装记录|缓存中的 `pending-update.json`|
|安装状态|缓存中的 `install-in-progress.json`|

清理范围由待安装记录及安装状态交叉校验得出。`phase=prepared` 可以清理；`installing` 应先确认安装进程和磁盘状态。路径与上述模板不符时保留现场。脚本保留历史备份及未知文件，不扫描用户项目。

## 验证边界

原始本机操作清理了待安装的 5.9.3，重启后界面仍显示 5.9.2，启动日志显示 `enabled=false source=user-setting`。之后的真实自动检查识别出新版，在创建下载目录时被 EEXIST 拦截，该会话未开始下载安装包。封装后的脚本在临时文件夹通过清理、文件锁和恢复测试，并在本机完成只读识别与状态检查；未重复更改真实软件设置来测试封装脚本。

这里只验证了该构建的 macOS 更新路径。Windows、Linux、其他 Bundle ID、改版更新器、手动下载安装和企业外部分发均未覆盖。升级包被清除后，恢复只能解除限制，不能凭空找回已删除文件。
