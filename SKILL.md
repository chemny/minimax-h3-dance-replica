---
name: minimax-h3-dance-replica
description: 当用户需要根据人物照片和参考视频复刻舞蹈时，使用本地 MiniMax H3 与 ComfyUI，选择 Character Swap、原生 Ref2V 或首尾帧加深度路线，保留原音乐并同步对照。不承诺任意视频逐帧一致。
metadata:
  version: "0.1.0"
---

# MiniMax H3 Dance Replica

输入舞蹈参考视频与人物照片，通过统一入口选择生成路线，输出保留原音乐的成片、实际配方记录和同步对照。只有深度路线需要首尾帧与深度视频；不能把这些阶段强加给 Character Swap 或原生 Ref2V。流程完成与人物、服装、动作是否保持分别验收。

## 执行边界

- 先判断用户要分析、提示词、预览还是生成；只执行已授权阶段。已有生成授权内不重复确认。
- 先查现有模型、节点与队列。缺模型时列出缺项；**新模型、插件、依赖的下载或安装须有明确授权**，不能从“试一下”推导为允许换模型或下载。
- 本地推理可在用户的远程 Windows GPU 上执行，不调用付费云视频服务。不要为此重启服务、开放端口或改 API 默认。
- 提交前检查队列，避免与其他 GPU 任务冲突。提交不确定时恢复同一任务，不盲目再提交。
- 默认完成一次生成与对照；若未通过，报告具体偏差和下一项单变量建议。没有额外优化授权时不连续重跑整片或另换路线。
- 不自动恢复已删除的模型，不复制大文件作备份。清理遵循用户规则；移入废纸篓不代表释放空间。

## 首次使用

先读 [references/onboarding.md](references/onboarding.md)，区分已有封装 API、本机 ComfyUI 和远程原生 ComfyUI。API 用户提供地址、接口说明和安全密钥位置；本机用户运行根目录 `Check-Environment.bat`（Windows）或 `scripts/check_environment.py`。检查节点、模型、ComfyUI Python/CUDA/依赖、FFmpeg 和当前客户端依赖，按报告引导补齐；不自动下载或安装，不把盘点通过当成生成验证通过。生成使用内置原生API客户端，无需作者gateway工程或SSH。封装API须先验证等价能力，不能自动替换路线。

安装来源、路线所需模型与节点见 [installation.md](references/installation.md)；精确文件、固定版本链接、大小和SHA-256在`presets/model-manifest.json`，可运行`model_inventory.py`仅列清单或检查已有文件。客户端需Python≥3.10、Pillow≥10和通过功能检查的FFmpeg；GPU依赖只在ComfyUI主机准备。

## 统一入口

使用 `scripts/dance.py`，以任务配置的`route`选择`character_swap`、`ref2v`或`depth`。用`routes`查看三条配方，`build`导出图，`validate`做不提交生成的预检；只有`render/run/depth --execute`可提交GPU阶段。见 [runtime.md](references/runtime.md) 与 [routes.md](references/routes.md)。新任务明确选择路线；旧配置未写`route`时继续原深度路线，不修改公共API默认。素材、路线或参数改变须新任务目录，已有任务按日志恢复。

| route | 生成条件 | 首尾图／DA3 |
|---|---|---|
| `character_swap` | Ref2VA＋Character Swap V1.5＋FL2V Turbo8；SAM遮罩与Relay续接 | 不自动添加 |
| `ref2v` | Ref2VA＋匹配步数的Ref2V Turbo；人物照片＋RGB动作参考 | 不自动添加 |
| `depth` | FL2VA＋匹配步数的FL2V Turbo＋Fun ControlNet | Codex首尾图＋DA3/DA3Render |

路线名称不代表已证明哪条更优。按用户选择与同素材验证结果决定，不因文件已安装或短片成功就宣称整片达标。

## 通用规则与参数边界

- 身份照决定人物，独立服装图只决定衣服，视频对应帧决定姿态与场景；按 [images.md](references/images.md) 标注角色，不把服装图模特的脸带入输出。
- 图像工具拒绝时说明实际状态；改变已选人物/衣服须有对应授权，不能为重试擅自改款或换模型。沿用已有明确授权，不重复确认。
- 统一验收与对照见 [acceptance.md](references/acceptance.md)：分别评价人物与动作，检查图片引导前后连续帧；跳变先核对时间戳再查生成画面。用户接受后记录已知限制并停止追加测试，接受交付不等于技术问题已解决。脚本不自动判定视觉合格。
- 当前后端参数单独放在 [presets.md](references/presets.md) 和JSON预设；用户指定优先，预设不代表所有视频最优。案例仅见 [evidence.md](references/evidence.md) 的独立记录，不加载为新任务模板。

## 工作顺序

1. **核实输入与路线**：路径、完整时长、帧率、画幅、切镜、遮挡、人物数量；以原片为时序真值。深度路线保留超过约10秒的共享边界分段；直接路线目前单次支持2—15秒，Character Swap在其中使用Relay内部窗口。不要把内部续接当成独立首尾帧分段；更长直接任务先明确分段能力缺口，不静默裁短或换路线。
2. **准备素材**：使用 `scripts/media.py prepare` 生成保持原速度、匹配当前后端帧率的视频、原始首尾帧、时间标注接触表与元数据。原音乐留在源视频中，最后直接取回。
3. **分析和提示词**：覆盖整段视频，定位动作变化点，快速转身、手势和表情处加密查看，见 [performance.md](references/performance.md)。深度路线使用结构化表演记录；直接路线可使用本任务的`prompt`原文，或编译`performance`记录。不自动加重复前缀，不套用历史动作时间点；照片、视频的角色需说明清楚。
4. **首尾图，仅depth**：以原片对应画面为底，用人物照替换身份、发型、服装，保持姿态、构图及遮挡。用Codex自身图像工具，见 [images.md](references/images.md)，不接入Qwen Image 2.1。Agent检查图片；用户要求确认时才交还用户。直接路线使用已批准的一张人物／服装参考，不自动重绘端点。
5. **深度，仅depth**：使用DA3＋DA3Render，先检查连续灰阶、身体层次与时间轴，见 [depth.md](references/depth.md)。不把RGB亮度、服装图案当深度，不默认叠加增强。深度通过原生API运行；其他路线不执行此阶段。
6. **生成与配方预检**：读 [routes.md](references/routes.md)，核实所选路线的主模型、换人LoRA、加速LoRA、步数、采样器、shift和注意力实现。Character Swap的FL2V八步是其组合配方的一部分，不要擅自换成原生Ref2V八步。原生Ref2V的RGB参考会带入源人物外观，参考分辨率是显式任务参数，不是已解决换人的通用保证。封装API的`/v1/prepare`必须与选定配方一致后才可提交；发现差异就说明缺项，不静默改模型或公共默认。
7. **恢复与验收**：`scripts/media.py finish` 裁至交付帧数、复制原音轨、验证完整解码及音频包一致性。使用 `scripts/compare.py` 将原片和成片按原时间轴对照，按本视频动作变化点核对人物、姿态、时序、表情及场景，并填写验收清单。不能只挑好看的帧，也不能用改速/整体平移隐藏错位。

中断后按 [recovery.md](references/recovery.md) 续跑原任务。新本地阶段复用摘要一致的已完成文件，不重提生成；旧版无检查点的中断目录先检查，不自动覆盖或清理。

## 提示词原则

全局部分定义人物、服装发型与镜头场景的来源。需要文字分时段描述时保留时间边界，只写实际变化的动作、姿态、表情和视线。依据可见事实写程度、起止与恢复，避免重复微笑或添加夸张表演。看不清不猜，背身不强行修脸。直接路线提供的`prompt`按原文使用；文字秒数不是硬关键帧。

## 可复用资源与验证状态

- [references/performance.md](references/performance.md)：陌生视频观察方法、记录格式及自然表情规则。
- [references/depth.md](references/depth.md)：连续灰阶、前景层次、控制区间与时序排查。
- [references/segmentation.md](references/segmentation.md)：自动分段、共享首尾边界、局部提示词时间轴与拼接去重。
- [references/runtime.md](references/runtime.md)：本机连接、已有接口区别、脚本调用及生成参数。
- [references/routes.md](references/routes.md)：三条路线的准确配方、输入要求与API适配边界。
- [references/evidence.md](references/evidence.md)：已验证结论、失败组合与仍待验证事项。执行优化或解释“为什么失败”时读取，不将猜测写成已证实原因。

交付时给成片/同步对照、实际模型与步数、耗时、改善和残留问题。记录任务 ID、种子、实际提交图、素材摘要和核验结果；密钥只从现有安全位置读取，不写入技能、提示词或报告。
