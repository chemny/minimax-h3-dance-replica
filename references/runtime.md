# 统一运行入口

## 接入方式

运行端需要 Python≥3.10、FFmpeg/FFprobe，以及素材预览所需 Pillow≥10。ComfyUI、PyTorch、CUDA、DA3/H3 模型均在推理主机；远程用户不在本机安装这些组件。首次使用按 [onboarding.md](onboarding.md) 检测。

`scripts/native_client.py`提供原生ComfyUI客户端，`workflow_client.py`适配已存在的工作流API；均只用标准库，不依赖作者的gateway工程或服务器SSH。本机与远程共用`dance.py`。原生无认证服务可省略密钥；认证接口使用`connection.key_file`或`connection.key_env`，不把密钥值写入任务记录。

任务必须区分`route`和`connection.kind`：前者选择生成算法，后者选择接口。`native`直接提交所选路线的节点图；`gateway`把`ref2v`映射到`video.reference`、`character_swap`映射到`video.character_swap`。`depth`只使用原生接口，不映射到`video.replace_person`。精确模型/LoRA、采样器、shift及注意力实现见 [routes.md](routes.md)。

### 封装 API 的可选能力边界

封装 API 的 `/v1/prepare` 可返回素材角色、画幅和路线说明，但不等于本技能的原生图预检。`framing=contain/cover` 仅适用于封装接口 `video.reference`、`video.replace_person`；`prompt_mode=supplement` 仅适用于封装接口 `video.replace_person`、`video.character_swap`，其中 `frame_instruction` 仅供前者补充首尾帧图片编辑。省略新字段保持封装 API 原默认行为。

这些是封装服务的独立可选能力，统一适配器没有自动透传它们，不能直接塞入原生图或任务配置。当前入口不使用Qwen首尾帧链，不自动改动服务端配方。封装路线先读实时catalog/OpenAPI，再用`/v1/prepare`核对实际图；与任务选定模型/LoRA或采样参数不一致时停止，不能把预检当成视觉效果通过。

## 创建任务

`SKILL` 为技能绝对目录；`CONFIG` 为本任务配置文件。Windows 使用对应的 Python 路径及 PowerShell 参数写法。

```bash
python3 "$SKILL/scripts/dance.py" routes
python3 "$SKILL/scripts/dance.py" init --route ref2v --config "$CONFIG"
```

`--route`可选`ref2v`、`character_swap`或`depth`。省略时保留历史深度行为；新任务应明确指定。相对素材路径以配置文件目录为基准。原生Ref2V示例：

```json
{
  "route": "ref2v",
  "connection": {
    "kind": "native",
    "url": "http://your-gpu-host:8188",
    "key_env": "COMFY_API_KEY"
  },
  "source": "source.mp4",
  "identity": "identity.png",
  "prompt": "填写根据本视频编写的素材角色和表演要求",
  "out": "case-output"
}
```

`duration_seconds`省略时读取完整原片；不会默认截成5秒。`seed`首次生成后持久化。两条直接路线可用`prompt`原文或`performance`文件编译，不插入默认案例文案。Character Swap只接受其八步组合；原生Ref2V和深度可选匹配四/八步，具体文件见任务`recipe.json`。检查/构图辅助使用的源首尾截图不等于要求生成首尾引导图。

深度任务可使用`wardrobe_reference`、`wardrobe_description`、已检查的`first/last/depth`；直接路线使用一张统一的已批准人物/服装照，不悄悄忽略额外服装图。任务保存素材摘要、种子、配方和实际图；变更条件新建目录，不把任务转到不同服务后冒充续跑。

已有封装服务只需改连接，不在客户端安装ComfyUI：

```json
{"connection":{"kind":"gateway","url":"http://your-api-host:8788","key_file":"secrets/comfy-key"}}
```

此适配器要求 [routes.md](routes.md) 所述接口协议及幂等提交语义。服务器选中的权重版本不同，应先明确是否映射`model_overrides`，不自动下载或修改服务。

## 分阶段或连续执行

```bash
python3 "$SKILL/scripts/dance.py" check --config "$CONFIG"
python3 "$SKILL/scripts/dance.py" prepare --config "$CONFIG"
python3 "$SKILL/scripts/dance.py" build --config "$CONFIG"
python3 "$SKILL/scripts/dance.py" validate --config "$CONFIG"
```

`build`导出所选路线的预览图和配方，不上传或采样；缺少准备素材时只做客户端FFmpeg预处理。`validate`在原生模式检查节点和模型选择器，在gateway模式检查对应接口是否存在；真正提交前还会核对`/v1/prepare`的实际模型配方。两者均不调用`/prompt`或`/v1/jobs`，不加载模型推理，也不能证明输出效果。

直接路线在已有生成授权时可执行：

```bash
python3 "$SKILL/scripts/dance.py" render --config "$CONFIG" --execute
python3 "$SKILL/scripts/dance.py" finish --config "$CONFIG"
python3 "$SKILL/scripts/dance.py" compare --config "$CONFIG"
```

也可用`run --execute`串联这些阶段。直接路线不执行`anchors/depth/approve`，不调用图片生成工具；需要更换服装时先准备已批准的人物照。深度路线仍由Agent查看接触表并按 [performance.md](performance.md) 创建本视频的`performance.json`；程序不自动理解视频。之后：

```bash
python3 "$SKILL/scripts/dance.py" compile --config "$CONFIG"
python3 "$SKILL/scripts/dance.py" anchors --config "$CONFIG"
```

`--execute`只标示会提交远端GPU任务的depth/render/run操作，不替代用户授权。`anchors`只检查图片或输出`anchors/request.json`，不调用图片生成API；Codex按 [images.md](images.md) 调用会话图像工具完成编辑，并将输出放到请求指定位置。检查原片姿势、手部、朝向、人物大小、服装发型及画外遮挡；不合格端点不进入视频。用户要求先确认时等待，其余情况Agent自行做视觉检查。没有图像工具时使用用户提供的端点或报告缺项，不安装Qwen Image 2.1。

```bash
python3 "$SKILL/scripts/dance.py" approve --config "$CONFIG" --item anchors
python3 "$SKILL/scripts/dance.py" depth --config "$CONFIG" --execute
```

`approve`记录Agent的视觉核对结果及文件摘要，不是每阶段额外向用户索取批准，也不会自动判断图片好坏。深度视频核对灰阶、帧率、尺寸、动作时间轴后：

```bash
python3 "$SKILL/scripts/dance.py" approve --config "$CONFIG" --item depth
python3 "$SKILL/scripts/dance.py" render --config "$CONFIG" --execute
python3 "$SKILL/scripts/dance.py" finish --config "$CONFIG"
python3 "$SKILL/scripts/dance.py" compare --config "$CONFIG"
```

`run --execute`最后制作同步对照和待填验收文件，不自动判人物/动作通过。深度路线遇到缺失表演记录或未核对图片/深度时暂停，Agent检查后继续，不增加用户批准环节。网络超时保留同一任务目录：原生已有prompt_id只续查；封装任务保留请求、recipe和幂等键，丢失响应时仅以相同键/正文恢复。两种接口日志不可互换，已有任务不迁移路线。进程被强行终止可能留下`active.lock`，确认原进程已停止后才移除；不自动重跑失败任务。

## 实际图与交付

- 深度：上传规范片 → DA3Inference → DA3Render → SaveVideo，下载后仅末尾补齐后端所需网格，无需SSH。具体模型和节点设置在 [presets.md](presets.md)。
- 原生Ref2V：人物照＋24fps/指定分辨率RGB动作参考 → Ref2VA/Ref2V Turbo → 视频；没有首尾引导、DA3或换人LoRA。
- Character Swap：人物照＋原视频 → SAM遮罩 → Ref2VA/Character Swap/FL2V Turbo8 → Relay窗口续接；没有自动首尾图或深度。
- 视频：H3首尾帧＋深度路线使用匹配LoRA和ControlNet。原RGB不作为视频模型参考送入；文字秒数不是硬关键帧。后端实现兼容条件与模型预设单独存放。
- `finish`按任务约定裁出交付帧、恢复原音乐并校验完整解码，输出`delivery/result.mp4`及`verification.json`。不兼容音频不得静默转码后仍声称逐包一致。
- 然后运行 [acceptance.md](acceptance.md) 中的通用`compare.py`，填写人物和动作验收；生成完成不能替代视觉验收。

高级用户仍可独立运行`media.py`、`prompt.py`、`h3_run.py`。`h3_run.py --base URL [--key-file PATH] run ...`与`resume --out ...`使用内置客户端；无需gateway-dir。旧任务的gateway Store格式不能自动迁移到新日志，新客户端不重发旧任务；旧任务继续使用原运行记录处理。

新本地预处理、参考片、拼接、音乐恢复与对照阶段支持检查点续跑，详见 [recovery.md](recovery.md)。已完成文件和输入均核对摘要；重试不自动覆盖旧版无检查点目录，也不覆盖已填写的视觉验收。

## 长视频自动分段

仅深度路线保留超过约10秒的 [共享边界分段](segmentation.md)，`plan`计算N段与N＋1张图。配置可设`"segmentation":{"target_seconds":10,"max_seconds":15}`。直接路线的`plan`报告完整2—15秒任务，不计算首尾引导；Character Swap的175帧内部窗口仍交由Relay处理。更长直接任务目前明确报错，不能静默截断、改速或改成另一条路线。
