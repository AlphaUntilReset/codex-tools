# Codex Tools

**非官方 Codex 桌面增强工具：高清透明桌宠、细粒度动画、任务与套餐用量提示、背景美化。**

Unofficial desktop companion tools for Codex on Windows. High-resolution PNG animation, task status, usage pacing, and custom wallpaper. Bring your own artwork.

直接使用自己的高清透明 PNG，让角色以独立桌宠的形式出现在 Windows 桌面上。每一帧独立保存，通过 JSON 设置动作顺序和毫秒级时间；从一张静态图开始，逐步添加眨眼、挥手、工作动作和 16 方向视线，不需要把原画转换为像素风格或压进固定图集格子。

这是独立运行的 Windows 配套软件，没有注册为 Codex 原生插件，也不是浏览器扩展。代码不会修改官方客户端安装包、认证文件或更新机制。

**仓库及公开程序包不提供角色、壁纸或动画素材。** 你可以使用自己制作或有权使用的图片；代码的 MIT 许可不覆盖用户图片。

- [高清素材制作指南](docs/HIGH-RES-ART.md)：从一张 PNG 到完整动画包，包含画布、站位、帧时间和方向图示例。
- [功能与状态说明](docs/FEATURES.md)：交互、任务状态、coding plan 余额与颜色规则。
- [自备素材格式](components/desktop-pet/PUBLIC-ASSETS.md)：可直接校验的 `pack.json` 契约。
- [隐私说明](PRIVACY.md)：本地任务读取、独立套餐查询和诊断数据边界。

## 高清素材工作流

所有帧保持相同画布，每边最多 4096 像素；推荐从 768×832 或 1536×1664 起步，这两个尺寸不是硬性要求。每个动作支持 1–256 帧，可逐帧设置 16–60000 毫秒的停留时间。只有 `idle` 必须提供，其他基础动作缺失时使用待机图片；动画是否自然取决于你提供的帧与站位一致性。

本项目着重解决高清原画在固定图集或像素化制作流程中需要重新排版、压缩或重绘的问题，提供独立 PNG 与可编辑清单的接入方式。官方也提供自定义宠物：截至 2026-10-22，官方桌面文档说明可通过 Create pet 和 `hatch-pet` 创建；同一文档的**网页上传入口**要求 1536×1872 的透明 PNG/WebP 图集，大小不超过 20 MiB。这个网页要求不能直接推断为所有官方桌面素材的尺寸或风格限制。[官方 Pets 文档](https://learn.chatgpt.com/docs/pets)

## 使用桌宠

### Windows 程序包

如果 [Releases](https://github.com/AlphaUntilReset/codex-tools/releases) 中提供 `CodexTools-Windows-*.zip`，下载后解压到可写目录，双击其中的 `CodexToolsPet/CodexToolsPet.exe`。首次启动选择自己的一张透明 PNG，即可使用。请保留整个目录及依赖文件；没有现成程序包时，也可以按下面的步骤从源码启动。

图片副本与 `pack.json` 放在程序旁的 `user-assets/pet/`，不会上传。只提供一张图片时，桌宠是静态的；补齐动画后才会有相应动作。详细格式见[自备素材说明](components/desktop-pet/PUBLIC-ASSETS.md)。

### 从源码启动

需要 Windows 10/11 与 Python 3.14，安装 Python 时保留 `py` 启动器。下载本仓库 ZIP 并解压，或运行：

```powershell
git clone https://github.com/AlphaUntilReset/codex-tools.git
cd codex-tools
```

双击 `启动桌宠.cmd`。首次运行会安装锁定依赖，再让你选择一张透明 PNG；以后直接启动，已有素材包不会被覆盖。

Python 环境放在 `%USERPROFILE%\.codex-tools\v\<12位目录哈希>`，让 Qt 依赖避开深层仓库路径的长度限制，不需要修改 Windows 长路径设置。每个源码目录使用独立环境；依赖校验记录与环境放在一起。图片与 `pack.json` 仍放在仓库的 `components/desktop-pet/user-assets/pet/`，不会移入依赖环境。以前留在组件里的 `.venv` 不会被复用或删除。

**任务连接默认关闭，套餐用量查询独立且默认运行。** 需要任务状态时，在托盘设置中选择 `codex-local`；任务信息来自本机状态，演示模式单独标记。套餐查询会使用本机 Codex 登录文件中的凭据发起只读网络请求，详见[隐私说明](PRIVACY.md)。托盘可调整大小、置顶、鼠标穿透和启动选项；更多用法见[桌宠组件说明](components/desktop-pet/README.md)。

## Coding plan 余额与颜色

任务面板同时显示所读取限额窗口的剩余比例和重置时间。当前实现读取用量响应的 `primary_window`，剩余比例为 `100 − 已用百分比`，不是现金余额或所有模型限额的总和。

颜色帮助判断消耗是否跟得上时间进度：`进度差 = 已过去时间占比 × 100 − 已用百分比`。进度差达到 **+15 个百分点**显示绿色，达到 **−15 个百分点**显示红色，其余保持默认色。例如窗口已过去一半，剩余 70% 为绿色，剩余 30% 为红色。这是均匀预算进度比较，不是未来消耗速度预测。

旧数据、已到重置时间但尚未刷新、或缺少必要信息时，不继续沿用额度颜色；未登录和暂不可用也会明确显示。刷新间隔、计算细节及任务颜色的区别见[功能与状态说明](docs/FEATURES.md)。

## 使用背景美化

需要 Windows 应用包版 Codex、Node.js 22 或以上、Python 3 和 Windows PowerShell 5.1。

双击 `安装背景.cmd`，选择一张主背景 PNG、两张侧栏 JPEG。安装会先验证图片与运行文件，备份原桌面快捷方式，然后安装当前用户的背景运行副本、登录入口和带本机调试参数的 Codex 快捷方式。首次安装后完全退出 Codex，再从桌面图标打开。

已有安装时安装器会停止，避免覆盖当前设置。手动构建、只读安装预览、侧栏尺寸与恢复说明见[背景组件说明](components/codex-wallpaper/README.md)。

- `关闭自动背景.cmd` / `开启自动背景.cmd`：切换背景。
- `修复背景.cmd`：等待你完全退出 Codex 后，恢复带背景参数的启动。
- `应急原生启动.cmd`：关闭美化并尝试原生启动。
- `恢复原启动方式.cmd`：恢复本项目接管的启动入口。

背景通过 `127.0.0.1:9228` 本机调试接口注入 CSS。客户端更新可能改变界面结构或启动参数；此时先使用恢复入口。兼容性按实际客户端验证，不保证未来所有版本。

## 更新与保留素材

公开版不连接任何私人素材仓库。源码用户在退出桌宠后执行 `git pull --ff-only`，再运行启动入口；代码与依赖更新不覆盖被忽略的 `user-assets/`。不要使用 `git reset --hard` 或 `git clean` 清理自己的素材。

程序包用户把新版本解压到新目录，再把自己的 `user-assets/` 复制过去。设置保存在本机应用数据目录。保留旧目录便于回退，确认新版本可用后再自行处理旧版本。

背景运行副本不会因拉取源码而自动替换。公开初版支持首次安装、背景开关与启动恢复，尚未提供自动替换已安装背景程序的升级入口。已有安装请保留运行目录、图片、配置及备份，不要反复运行安装器或删除配置来绕过保护；“源码已更新”不代表“已安装程序已升级”。

## 开发与验证

代码和依赖锁是公开的；测试在临时目录生成简单几何图片，无需原角色素材。需要 Node.js 22 或以上。首次验证先准备依赖，从仓库根目录执行下面的 PowerShell 命令；这不会启动桌宠或要求选择图片：

```powershell
$petPython = (node tools/python-env.cjs)
$petEnvironment = Split-Path (Split-Path $petPython -Parent) -Parent
py -3.14 -m venv $petEnvironment
& $petPython -m pip install -r components/desktop-pet/requirements.lock.txt
npm ci
npx playwright install chromium
npm run check:public
npm test
npm run test:setup
npm run test:pet
npm run test:wallpaper-assets
```

`node tools/python-env.cjs` 只显示解释器路径，不创建环境。测试默认使用与双击启动相同的短路径环境。已有独立开发环境时，可设置 `$env:CODEX_TOOLS_PYTHON = "C:/path/to/python.exe"`，安装锁定依赖后直接运行测试，无需执行上面的环境创建命令；这个覆盖仅供开发工具使用，不改变双击启动器管理的环境。

无素材 Windows 程序包的构建命令仍从仓库根目录执行：

```powershell
$petPython = (node tools/python-env.cjs)
& $petPython components/desktop-pet/tools/build_public_package.py
```

无素材构建输出 `components/desktop-pet/dist-public/CodexToolsPet/`。公开发布应从干净检出构建，保留依赖许可，并复查没有用户图片、日志或本机路径。`PUBLIC-FILES.json` 是经过审核的精确源码清单，新增或修改发布文件必须重新审核；只更新清单哈希不能替代审核。

## 隐私与许可

本地连接会读取本机 Codex 活动和任务信息。分享截图或诊断前请自行去除敏感内容，详见 [PRIVACY.md](PRIVACY.md)。项目没有图片上传流程。

代码与文档使用 [MIT](LICENSE)。上游 CSS 与依赖声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目不附带、售卖或授权任何角色美术资产。项目独立维护，与 OpenAI 无隶属关系。
