# 三条路线与明确配方

这些是当前后端的工作配方，不能当成所有素材的最优解。`presets/h3-local.json`保留深度路线；`presets/routes.json`记录两条直接路线。公共服务的默认设置与这些独立配方分开维护，本技能不修改公共默认。

| route | 主模型 | 换人LoRA | 加速LoRA | 采样 |
|---|---|---|---|---|
| depth | FL2VA INT8 | 无 | FL2V Turbo4/8 v1.0 768P，匹配步数 | 默认8步，CFG1，Euler/simple，shift6/3，Sage |
| ref2v | Ref2VA INT8 | 无 | Ref2V Turbo4/8 v1.0 768P，匹配步数 | 默认8步，CFG1，Euler/simple，shift12/3，Sage |
| character_swap | Ref2VA INT8 | Character Swap V1.5，强度1 | **FL2V** Turbo8 v1.0 768P，强度1 | 固定8步，CFG1，er_sde/normal，shift12/3 |

共用Qwen3VL H3文本编码器和H3视频VAE；它们不等于Qwen Image图片编辑模型。Character Swap另需音频VAE、SAM3.1及H3 Relay/SAM节点。当前Character Swap工作图使用`ModelAttentionBackend: comfy kitchen attention`，不是Sage；原生Ref2V和深度路线默认用KJ Sage。注意力实现、17GiB采样内存预估、175帧窗口与18帧历史均属于当前后端预设，不是任意GPU的保证。

Character Swap换人LoRA来源：[akatz-ai/MiniMax-H3-Character-Swap-LoRA](https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA)。原生图使用已部署的H3 Relay节点组合，随技能保存在`assets/character-swap-api.json`，只含节点配置，不含模型、素材、密钥或作者运行目录。原生图不依赖私有gateway缓存节点；通过封装API调用时，缓存和GPU调度仍由服务负责。

## 输入和阶段

- `depth`：原片＋人物照，或已检查首尾图；需表演记录、Codex首尾图、DA3/DA3Render、深度ControlNet。原RGB不再作为生成模型视频参考。保留现有共享边界自动分段。
- `ref2v`：已批准的人物/服装照片＋原RGB动作视频。原生执行时在客户端生成24fps、指定`motion_reference_long_edge`的参考片，默认长边512；**输出尺寸单独设置**。无需首尾图、遮罩或DA3。
- `character_swap`：已批准的人物/服装照片＋原RGB动作视频；SAM追踪人物，Relay生成与续接。输入源音频参与现有工作图的音频条件；最终声音仍取原音乐。无需首尾图或DA3。

直接路线不接受`first/last/depth/wardrobe_reference`，防止看似提供、实际未使用。独立服装图需要先做成已批准的统一人物照，再建立直接任务。两条直接路线当前接受2—15秒；超过范围明确报错，不裁短或套用深度路线的N＋1张边界图算法。Character Swap内部窗口续接不等于已证明接缝和服装稳定。

`prompt`提供时按原文使用，不自动追加通用换人描述；也可改用`performance`记录编译。照片负责哪些外观、视频负责哪些表演/场景，应在本任务提示词中明确。模型条件是软约束，不承诺写了“保持一致”就能锁定。

## 原生接口与封装API

`connection.kind=native`：直接对配置的ComfyUI地址执行相应图。Character Swap地址必须实际加载Relay/SAM节点；远程客户端不能把服务器的`127.0.0.1`当作自己的地址。没有可访问的该后端时，可使用已存在的封装API，不自动开端口、重启或建立SSH隧道。

`connection.kind=gateway`：支持已有的`/v1/workflows`、`/v1/assets`、`/v1/prepare`、`/v1/jobs`和稳定`Idempotency-Key`协议。`ref2v`映射`video.reference`，`character_swap`映射`video.character_swap`。深度路线暂不映射到封装接口；`video.replace_person`不是这里的FL2VA深度图，不能代替。

封装请求总是显式传入步数和支持的任务参数。提交前必须检查`/v1/prepare`返回的主模型、两种LoRA、步数、采样器、scheduler、shift、编码器/VAE和注意力设置；不一致时拒绝`/v1/jobs`。例如服务器使用Ref2V八步v1.1，而任务明确选v1.0，不能静默接受。若用户明确选择服务器已有v1.1，可在新任务写：

```json
{"model_overrides":{"lora_8":"minimax_h3_ref2v_turbo_8step_v1.1_768p_comfyui_bf16.safetensors"}}
```

`model_overrides`只做显式模型文件映射并记录快照，不下载、升级或改公共默认。不要跨路线混用LoRA家族。其他采样配方字段在后端预设中维护，不接受被默默忽略的任务字段。任意厂商API不能仅因有“视频生成”接口就视为兼容。

## 记录与验证状态

每个任务的`recipe.json`/`task.json`保存独立配方、种子和输入摘要；实际图和提交ID另存。变更路线、步数或权重后新建任务，恢复已提交任务不改图。`build/validate/check`不提交生成；`validate`只是节点/模型选择器或API能力预检，不能代表加载、推理或视觉质量验证通过。视频生成与音乐恢复后，仍分别检查人物、服装、动作、表情和过渡。
