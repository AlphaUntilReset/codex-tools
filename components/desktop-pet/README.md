# Desktop pet

Codex Tools 的独立 Windows 桌宠组件。支持透明窗口、拖动缩放、置顶、鼠标穿透、托盘设置、任务状态面板、自然动作、鼠标方向跟随和进程退出记录。

公开发行只含代码。首次使用请按 [PUBLIC-ASSETS.md](PUBLIC-ASSETS.md) 提供透明 PNG 或完整动画包。没有动画文件时会使用待机图片，没有方向图片时保持同一朝向；程序不会生成、下载或修改你的角色美术。

从仓库根目录双击 `启动桌宠.cmd`，会在 `%USERPROFILE%\.codex-tools\v\<12位目录哈希>` 准备独立依赖环境；图片仍在本组件 `user-assets/pet/`。手动运行方式见[根目录说明](../../README.md#开发与验证)。`run_pet.py --asset-pack <pack.json>` 可指定素材包。长期使用、尤其启用自启时，请使用程序旁的 `user-assets/pet/pack.json`。

任务连接默认关闭。托盘设置里的 `codex-local` 读取当前用户本机 Codex 状态；`demo` 仅展示标记为演示的事件。套餐用量查询独立且默认运行，凭据与请求说明见[隐私说明](../../PRIVACY.md)。常见参数可运行 `run_pet.py --help` 查看。

自启仅在用户明确开启时配置。退出记录器与桌宠一起运行，用来记录真实退出证据；它不会额外创建计划任务或服务。设置、日志和缓存留在本机应用数据目录。测试用 `CODEXTOOLS_PET_DATA_DIR` 指定临时目录，并设置 `QT_QPA_PLATFORM=offscreen`。

## 可选请求提示

公开 Windows 程序包包含 `hooks/CodexToolsHook.exe`。若需要 Codex 的审批/输入请求提示，先准备源码依赖，再从仓库根目录运行：

```powershell
$petPython = (node tools/python-env.cjs)
& $petPython components/desktop-pet/tools/setup_request_hooks.py --executable "D:/Apps/CodexToolsPet/hooks/CodexToolsHook.exe"
```

默认只生成预览。明确需要安装时添加 `--install`；工具先备份并合并已有 hooks 配置，遇到并行更改会停止。移除用同样的可执行文件路径加 `--remove`。是否信任 hooks 仍由 Codex 的审批机制决定，提示工具不替用户授权操作。

## 构建

`tools/build_public_package.py` 是公开无素材构建入口，输出包含主程序、独立退出记录器和可选钩子工具。请始终分发完整依赖目录及许可文件，不要只复制主 EXE。不要将运行后补入个人图片的目录重新上传为公共包。
