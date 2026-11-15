# Installation and model setup

The client needs Python 3.10 or newer, Pillow 10 or newer, and FFmpeg/FFprobe with `libx264` and the filters checked by `check_environment.py`. GPU packages are installed in the ComfyUI interpreter, not the client environment. For Chinese guidance, see [首次接入与检测](onboarding.md).

## 1. Client

Install the skill folder through Codex. In the client interpreter, after authorizing dependency installation:

```text
python -m pip install -r requirements.txt
python scripts/dance.py routes
```

On macOS use `python3` when `python` is not the selected interpreter. On Windows `py -3` or the full Python executable path also works. Install FFmpeg through the operating system's package manager or the [FFmpeg download page](https://ffmpeg.org/download.html), then open a new terminal so PATH changes take effect. The checker runs the executables and checks encoding/filter capabilities; finding a filename is not sufficient.

## 2. Choose the connection

| Connection | Client needs | GPU host needs |
|---|---|---|
| Native ComfyUI on the same computer | Client requirements | NVIDIA/CUDA ComfyUI and selected route models/nodes |
| Remote native ComfyUI | Client requirements and reachable HTTP(S) address | Same route models/nodes on the remote computer |
| Compatible workflow API | Client requirements, API address and credentials | Managed by that API operator |

API users do not install ComfyUI, PyTorch, CUDA or model weights on the client. The gateway adapter requires the `/v1/workflows`, `/v1/assets`, `/v1/prepare` and `/v1/jobs` contract described in [routes.md](routes.md); it does not adapt arbitrary video APIs. Depth uses native ComfyUI only. For an authenticated service, store the key in a file or environment variable. Do not put its value in a task configuration or prompt.

Copy one configuration from [examples](../examples), put the portrait and source video beside it, and set the service address. Paths are relative to the configuration. These configurations contain no bundled sample media. Choose a new output directory whenever inputs or generation settings change. Add action descriptions from the actual video; the example prompt is only the material-role description.

## 3. GPU host: nodes

The current graphs use the ComfyUI 0.42.5 node interfaces. Check the running service with `validate` before generation; do not automatically replace a working installation. Windows with an NVIDIA GPU is an inference-host option; macOS can be a client for a remote NVIDIA host. This package does not supply an Apple GPU inference recipe.

| Route | Required nodes and source |
|---|---|
| All native routes | [ComfyUI](https://github.com/Comfy-Org/ComfyUI): H3, model loaders, video I/O and sampling |
| Ref2V/depth with Sage enabled | [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) for `PathchSageAttentionKJ`, plus [SageAttention](https://github.com/thu-ml/SageAttention) compatible with the host's PyTorch/CUDA |
| Depth | Native DA3/DA3Render and H3 Fun ControlNet nodes |
| Character Swap | [H3 Relay at the recipe's source revision](https://github.com/akatz-ai/h3-relay/tree/e2a7665b6eb8ea37a88c0de2c9c6b69a8f7c9220), native SAM3.1, KJ helper nodes and [comfy-kitchen](https://github.com/Comfy-Org/comfy-kitchen) attention support |

Install only missing components, using each upstream project's instructions and the ComfyUI Python executable. Do not install LTX, FastH3 VSA, video upscalers or other optional Relay workflows for this skill's Character Swap recipe. An installed node package may still fail to load; inspect its startup errors and rerun the check before downloading models again.

## 4. Models

[model-manifest.json](../presets/model-manifest.json) pins each file's repository revision, download URL, size, SHA-256 and ComfyUI folder. It contains metadata only. Select one route and matching step count:

```text
python scripts/model_inventory.py --route ref2v --steps 8
python scripts/model_inventory.py --route depth --steps 8
python scripts/model_inventory.py --route character_swap
```

Download only files selected for the route, after authorizing their storage and download. Shared text encoder/VAEs need one copy. Put each file in its listed folder; additional model roots can be declared in ComfyUI's `extra_model_paths.yaml`. To inspect existing files without downloading:

```text
python scripts/model_inventory.py --route ref2v --models-dir /path/to/ComfyUI/models
python scripts/model_inventory.py --route ref2v --models-dir /path/to/ComfyUI/models --verify-sha256
```

Use the actual Windows models-directory path in PowerShell. `--models-dir` may be repeated for external roots. Hashing large weights reads the full files and is explicitly opt-in. A same-size file is reported as present, not checksum-verified. Explicitly mapped custom filenames require their own verified source/checksum; an alias is not automatically assumed equivalent.

Ref2V requires the Ref2VA base and Ref2V Turbo LoRA. Depth requires the FL2VA base, FL2V Turbo, Fun ControlNet and DA3 Mono Large. Character Swap requires the Ref2VA base, Character Swap V1.5 and **FL2V** Turbo8 together, plus SAM3.1 and the audio VAE. The Qwen3VL encoder is H3's text encoder; Qwen Image 2.1 is not required. Depth endpoint editing uses the Codex image tool or reviewed user-supplied endpoint images.

## 5. Check before generation

```text
python scripts/dance.py check --config task.json
python scripts/dance.py build --config task.json
python scripts/dance.py validate --config task.json
```

These actions do not submit video jobs. Review missing/unknown checks; remote Python cannot be verified from client imports. Run the checker on the GPU host when host-runtime information is needed. A gateway-only configuration does not probe a local ComfyUI installation. On Windows, `Check-Environment.bat -Config task.json` uses the same task configuration; `-ClientPython` selects client Python and `-Python` explicitly selects ComfyUI Python for inspection.

When the environment and the selected route are ready, ask Codex to analyze the actual source performance and perform the authorized generation. See [runtime.md](runtime.md) for stage commands and [recovery.md](recovery.md) for resuming a task.
