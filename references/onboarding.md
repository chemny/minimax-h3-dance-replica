# 首次接入与环境检测

首次使用先核实已有接入，沿用会话中已确认的地址和密钥位置；缺少必要信息时才提问，不索取聊天明文密钥：

- **已有工作流 API**：提供服务地址、接口说明及密钥文件或环境变量。`connection.kind=gateway`可使用统一入口的Ref2V/Character Swap适配器；先核实catalog，再核对实际prepare图。没有本机ComfyUI也可调用。深度路线没有等价gateway映射，不能静默换成`video.replace_person`，也不能把任意厂商API视为兼容。
- **本机 ComfyUI**：无需先搭建封装 API。提供 ComfyUI 根目录及实际运行它的 Python；原生 HTTP 接口一般为 `http://127.0.0.1:8188`，默认无需 Bearer 密钥。认证代理可使用 `--native-key-file`，或环境变量 `COMFY_API_KEY`。
- **远程 ComfyUI 原生服务**：提供原生地址。服务端 Python/依赖不能用客户端环境代替；在 GPU 主机运行检测器或取得管理员报告。新版深度直接通过原生 API 节点运行，无需 SSH 或服务器脚本执行权限。

## Windows 一键检查

双击技能目录 `Check-Environment.bat`。检测报告保存在桌面 `H3-Environment-Check`；无 Python 时也会保存 `bootstrap.txt` 指引。只查常用目录，不全盘扫描。未找到不等于没安装，便携版、Desktop 和自定义位置应显式指定。

自定义环境可在 PowerShell 执行（路径只是示例）：

```powershell
& '.\scripts\check_environment.ps1' -ComfyRoot 'D:\ComfyUI' -Python 'D:\ComfyUI\.venv\Scripts\python.exe'
```

已有封装 API，且可访问对应原生 ComfyUI：

```powershell
& '.\scripts\check_environment.ps1' -ApiUrl 'http://your-host:8788' -KeyFile 'D:\secrets\comfy-key' -ComfyUrl 'http://your-host:8188'
```

macOS/Linux 同样可运行标准库检测器：

```bash
python3 "$SKILL/scripts/check_environment.py" --comfy-url http://127.0.0.1:8188 --comfy-root "$COMFY_ROOT" --python "$COMFY_PYTHON" --out "$CASE/environment.json"
```

检测须选择路线：统一入口`dance.py check --config task.json`会读取任务路线；独立检测可用`--route ref2v`或`--route character_swap`，Windows用`-Route`。省略路线保留深度检测。原生Ref2V/深度可用`--steps 4`，Character Swap固定其八步组合；检查不改变默认值。

## 报告如何处理

检测包含客户端Python版本、Pillow、FFmpeg/FFprobe可执行性、libx264与所需滤镜；本机推理再检查ComfyUI目录、实际Python、按路线选择的GPU依赖、节点、模型和队列。Character Swap的kitchen配方不因未安装Sage被误报；远程API客户端不检查本机CUDA。

- `pass`：该项被观测到；模型选择器可见不等于文件完整或推理成功。
- `fail`：明确缺项或接口拒绝；先修复。
- `unknown`：证据不足，例如远端依赖或任意 API 字段映射；需补查，不能报“环境齐全”。
- `warn`：需注意，例如队列繁忙、深度执行目录尚需配置。

盘点报告保留 `ready=false`：静态盘点不能替代实际图校验和端到端验证；检测不加载模型、不运行生成、不安装、不重启、不占用 GPU 推理。报告可能包含本机路径和 GPU 型号，但不保存密钥或请求地址。

## 引导安装顺序

由 Agent 根据报告逐项给出缺项、来源、目标目录、空间需求、依赖关系和安装后验证方法，用户授权后才实施；批处理本身不安装。

1. 没有 ComfyUI：引导从 [ComfyUI 官方](https://github.com/Comfy-Org/ComfyUI) 选择适用的 Desktop/便携版/手动安装。不要在用户已有环境旁默默再装一套。
2. Python/CUDA 缺项：使用 ComfyUI 自己的解释器，根据对应版本 requirements 和启动日志解决；不把系统 Python 的包当成已装，不盲目更新 torch。24GB RTX3090 是本技能已验证环境，其他显存配置不承诺可跑。
3. 节点缺项：按所选路线检查。深度需H3/DA3/DA3Render/ControlNet/KJ Sage；原生Ref2V需H3参考节点及所选注意力节点；Character Swap需H3 Relay、SAM3和该配方的注意力节点。有文件未加载时先查启动日志，不重复安装。
4. 权重缺项：按 [routes.md](routes.md) 核对主模型、匹配LoRA、编码器/VAE及路线专用权重。不要把Character Swap的FL2V八步组合与原生Ref2V八步混用。先查已有模型路径，别名需显式映射；缺项列出来源、目录、大小及校验值，授权后才下载。
5. 首尾帧：由Codex自身图像工具编辑，Agent须检查会话是否提供该能力；检测脚本不能探测会话工具。不要求安装Qwen Image 2.1模型、编辑节点或图片API。工具不可用时使用用户提供的已检查端点，或说明缺项暂停，不自动切换模型。H3仍需要Qwen3VL文本编码器，它不是Qwen Image 2.1图片生成模型。
6. **内置客户端**：`native_client.py`只用标准库，不要求安装/复制作者的gateway工程。本机无需启动封装服务；远程用户直接配置原生API地址。`h3_run.py`和`dance.py`均使用此客户端。
7. 统一入口：按 [runtime.md](runtime.md) 选择`route`并运行check/build/validate。深度才需要anchors/depth；直接路线使用已批准人物照。`depth_remote.py`仍只作为明确选择的服务器执行方式，不属于其他两条路线的前置条件。

任意其他封装API仍需适配；本技能只支持明确声明的协议与配方，不修改用户已有公共API默认工作流。

完整来源与模型目录见 [安装清单](installation.md)。Windows也可直接运行 `Check-Environment.bat -Config task.json`；`-ClientPython`与`-Python`分别选择客户端与ComfyUI解释器。客户端依赖安装使用其解释器执行`-m pip install -r requirements.txt`，不改系统或GPU环境中的包。
