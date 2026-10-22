# 自备美术资产

开源代码和无素材 Windows 程序包不包含角色原图、图集、动画帧、预览或私用素材。代码许可证不授予任何角色美术资产的使用权。请只使用自己制作或已获相应授权的图片，不要把仅限个人使用的素材提交到仓库、Issue、截图或 Release。

## 最快启动

Windows 程序包解压后，双击 `CodexToolsPet.exe`，第一次启动会让你选择一张透明 PNG。原图保持不变，副本保存到程序旁的 `user-assets/pet/idle.png`，并生成 `pack.json`。一张图片即可使用透明桌宠、拖动缩放、托盘设置、任务状态面板、Codex 本地状态连接和退出诊断；未提供的动作会显示这张图。

公开源码版先按[根目录说明](../../README.md#开发与验证)准备依赖，再从仓库根目录执行：

```powershell
$petPython = (node tools/python-env.cjs)
& $petPython components/desktop-pet/tools/asset_pack.py init --image "D:/MyArt/idle.png"
& $petPython components/desktop-pet/tools/asset_pack.py validate
& $petPython components/desktop-pet/run_pet.py
```

`init` 不覆盖已有 `idle.png` 或 `pack.json`。用 `--output <目录>` 创建另一个素材包，用 `run_pet.py --asset-pack <目录>/pack.json` 临时选择。自动启动默认读取程序旁的 `user-assets/pet/pack.json`，因此长期使用时请把最终包放到这个位置。源码版的 Python 依赖位于用户目录下的短路径环境，素材仍位于 `components/desktop-pet/user-assets/pet/`，二者分开保存。

## 添加动画

所有图片使用同样的画布宽高，保留透明通道；每边最多 4096 像素，不必采用任何特定角色尺寸。程序保持源图宽高比。路径相对于 `pack.json`，使用 `/`，不能指向包外，不能使用网络地址。每个动作支持 1–256 帧。

```json
{
  "schema_version": 1,
  "animations": {
    "idle": {
      "frames": ["idle/open.png", "idle/blink.png"],
      "durations_ms": [1800, 120]
    },
    "waving": {
      "frames": ["wave/1.png", "wave/2.png"],
      "durations_ms": [180, 180],
      "loop": false
    },
    "running": {
      "frames": ["working/1.png", "working/2.png"],
      "durations_ms": [300, 300]
    }
  }
}
```

只强制要求 `idle`。可提供的其余动作：

| 动作名称 | 使用位置 |
| --- | --- |
| `running-right`、`running-left` | 向右、向左拖动 |
| `waving` | 点击、任务完成或手动动作 |
| `jumping` | 手动动作 |
| `failed` | 任务阻塞 |
| `waiting` | 等待输入 |
| `running`、`review` | 工作中与自然动作切换 |
| `yawn`、`groom`、`thinking` | 可选生活与思考动作 |

`durations_ms` 与帧数一一对应，每项为 16–60000 毫秒；省略时每帧 150 毫秒。`loop` 必须是布尔值；挥手、跳跃和可选动作默认只播一次，其余默认循环。自然动作模式会在声明的片段之间加入停留；自备待机片段不会套用原角色的六帧节奏或颜色/眼睛修改。

可在 JSON 顶层添加 `look` 数组，填入 16 个 PNG 相对路径，顺序为正上方开始、顺时针每 22.5° 一个方向。开启鼠标跟随后使用这些方向图；省略时所有方向显示待机首帧。自备素材不会应用私用原画的眼睛位置、哈希或校准合同。

每次更换图片或编辑清单后运行 `tools/asset_pack.py validate`，再重启桌宠。校验检查图片、尺寸、帧时间和路径边界；它不判断图片版权，也不代表主观动画效果已经验收。

## 无素材构建

在 Windows 干净的公开源码副本中准备依赖，再从仓库根目录运行：

```powershell
$petPython = (node tools/python-env.cjs)
& $petPython components/desktop-pet/tools/build_public_package.py
```

输出为 `components/desktop-pet/dist-public/CodexToolsPet/`，包含程序、退出记录器和可选请求钩子工具，不收集任何本地素材。发布时优先使用仓库的干净 CI 环境，避免本机构建路径进入编译文件。完整保留 `_internal/`、`CodexToolsMonitor.exe` 和 `hooks/`。这个构建入口与私用版本的原画校验、打包入口相互独立。

`user-assets/`、设置、诊断记录、缓存与构建结果均应留在自己的电脑；请勿把已补齐个人素材的运行目录作为公共 Release 上传。
