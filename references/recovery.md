# 中断与恢复

恢复使用同一个任务配置、目录、输入和种子。改变条件应新建任务，不把重试冒充成相同条件续跑。

## 远程生成

原生接口保存 `prompt_id`，封装 API 保存任务 ID 和幂等键。连接中断或本机进程退出后，重新运行原来的 `render --execute` 或 `run --execute`：已有 ID 时只续查、取图，不创建新视频任务。提交响应不确定时依据原日志恢复，不使用新键盲目重发。明确失败或取消的任务需要检查后再决定是否重新生成。

## 本地处理

新的预处理、音乐恢复和对照阶段会保存 `.stage.json`，记录输入摘要、参数和已完成文件的摘要。重新运行同一阶段会复用已完成文件，只继续未完成的步骤：

```text
python scripts/dance.py finish --config task.json
python scripts/dance.py compare --config task.json
```

这些操作不提交 H3 生成。临时文件先写到该阶段自己的 `.partial` 路径，完成后才替换正式文件；重试可以重写同一阶段尚未完成的临时文件。已完成文件若被修改、输入或参数改变，会停止并要求新目录，不覆盖用户文件。已填写的视觉验收结果在重做对照时保留。

统一入口复用交付结果时也核对视频摘要，不仅检查 `verification.json` 是否存在。对照复用还会核对原片、结果片及对照视频，防止沿用其他文件的旧验收。缺失或变动的文件会明确报错，不触发重新生成；旧交付记录没有摘要时，至少重新检查完整解码、帧数、帧率和尺寸。

旧版已完成任务仍可读取和交付。旧版中断目录若没有 `.stage.json`，不自动认领或清理；先检查已有产物，在新的本地后处理目录中使用原生成视频，不必重新生成 H3 视频。

当前自动检查点覆盖单段素材准备、Ref2V 参考编码、音轨恢复、对照制作和分段拼接。分段前的素材拆分、深度阶段的本地裁剪和补帧尚未全部采用这一机制；若这些 FFmpeg 步骤被强制中止，先检查本地文件及已保存的远程任务记录，勿把不完整 MP4 当成成功结果。确认文件确属未完成的临时产物后，按用户的清理规则处理，再继续对应本地步骤，不因此重新提交已经完成的 DA3／H3 生成。

## 锁文件

正常退出和可捕获异常会释放阶段锁。进程被强制结束可能留下 `active.lock` 或 `.active.lock`。确认对应任务进程已经停止后，才移除该任务的锁文件并续跑；不要删除远程任务或其他任务目录。

## English summary

Rerun the same task/stage with unchanged inputs. Saved native prompt IDs or gateway idempotency keys prevent a new video submission. Checkpoints cover single-clip preparation, Ref2V reference encoding, audio finishing, comparison and segment joining; `finish` and `compare` submit no GPU job. Segment-source splitting and local depth trimming/padding still require inspection after a forced interruption. Modified inputs or completed checkpoint outputs are rejected. User-written visual acceptance is preserved. Legacy partial directories without checkpoints are not overwritten: inspect them and finish the existing raw video into a new local directory. Remove a stale stage lock only after confirming its process has stopped.
