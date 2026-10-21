---
name: recast-video-dgx
description: 制作或修复视频人物替换。默认支持 Gemini App 无 API Key 人工交接模式（Codex 准备分镜、参考、提示词；用户在 App 生成并下载；Codex 映射、拼接和动态验收），同时保留 MiniMax H3/ComfyUI/DGX Spark 原生生成模式。先询问/遵照用户所选后端；没有授权不生成，不恢复历史已停止的工程。
---

# 视频人物替换 Skill：Gemini App 人工交接 / H3 原生模式

以用户指定的原片、角色、原声、节奏、方向和验收范围为准。先读现有工程交接与停止记录，再决定本次属于规划、参考准备、生产、修复、交付或仅文档模式。用户叫停时停止本工程消费者、监控和自动补位，保留素材；之后的封装、文档或合成请求不等于恢复生成授权。

## 入口与按需阅读

- 新版 Gemini App 默认使用文档：[README.md](README.md)、[references/gemini-app.md](references/gemini-app.md)。
- 旧版 H3 / DGX 完整技术文档：[README_H3.md](README_H3.md)。
- 新片、分段、参考和目录规范：[references/production.md](references/production.md)。
- H3 图、模型、首中尾及阶段关系：[references/comfyui.md](references/comfyui.md)。
- 实际启动、两种双机模式、三机补位、锁、回收与停止：[references/dgx.md](references/dgx.md)。
- 时钟、音频、动态验收与修复：[references/quality.md](references/quality.md)。
- 图文归档及飞书发布：[references/documentation.md](references/documentation.md)。
- 《流星雨》实例与24组真实配图：[examples/f4-case-study.md](examples/f4-case-study.md)、[examples/gallery.md](examples/gallery.md)。实例是历史教学资料，绝不能作为待运行任务。

## 后端选择：先分流，再执行

- 用户只有 Gemini App、没有 API Key 时，**优先选择 `gemini-app-manual` 模式**：先确认 App 视频功能在账号与地区可用，逐镜审核，然后按 `references/gemini-app.md` 运行 `scripts/gemini_app.py prepare`，停在人工交接点。等待用户手动从 App 下载视频后再运行 `collect`，接入通用合成和质检。不能使用 `build-h3`、`native_run.py`、SSH/GPU 来伪装成 Gemini 路线。
- 这条路线的本地工具不访问 Gemini。**不要承诺有 API 或批量自动生成能力**；不要自动代登录、代点击，不能使用隐藏接口绕过订阅或权限。源片和最多 5 张图片供 App 人工上传；片段建议不超过 10 秒。Gemini 输出并不保证与原片严格同长、动作一致或嘴形同步。
- 用户明确选择 H3、已有授权的模型与兼容 GPU 集群时，才走 `README_H3.md` / `references/comfyui.md` / `references/dgx.md` 的原始流程。
- H3 历史 F4 示例不是 Gemini 实测；不要把旧版 GPU 基准、API 图或素材许可套到 Gemini 模式。

## 必须保持的决策约束

1. 从真实画面建立角色与服装轨迹，按核实的镜头和完整动作分段；自动切点只是候选。所有区间使用从0开始的 `[start,end)` 源帧，整片覆盖每帧恰好一次。
2. 分开记录身份卡、场景服装、人物位置、姿势、遮挡和动作相位。参考图必须实际查看；不能因文件存在或脚本运行就自动写审核通过。必要生成图使用当前可用且已授权的图像工具；拒绝后不换未授权 API/CLI 绕过。
3. RGB、图像 Guide 和提示词是软参考。只有实际连通并验证的控制才能记录为控制能力；不要宣称动作、嘴形或机位硬锁。连续段使用检查过的实际前段尾帧，不能拿预计尾帧代替。
4. 两机合算同一段 `world_size=2` 占两台；两机分别做两段是两个 `world_size=1`。输出非重叠的镜头→节点分配，再提交。每台至多一个生成任务；GPU 后处理共享该台同一个物理 GPU 锁。第三台不能同时充当双机 worker 和独立生成机。
5. 先核对输入、唯一 run ID、回执、远端真实进程和锁。回执或提交意图存在时只监控/回收；丢失 SSH 回答不代表任务未启动。失败或未知状态先留证诊断，改变输入需新版本，不覆盖旧版。
6. 技术检查、静态图检查、实际连续播放及最终验收分别记账。错误身份、头部几何、发色、嘴形/眼神相位、动作或缺帧都不能靠原演员片段填补成“完成”。缺段预览必须清楚标注；默认合成工具拒绝缺段。
7. 交付必须以当前选片清单与实际产物为准；不要用更漂亮的失败修复图代表成片。全片实际画面与原声检查通过后才能标最终通过；仅抽样或技术通过的版本标内部待审。

## 执行顺序（以下为原生 H3 路线；Gemini 路线按 `references/gemini-app.md`）

1. 建立/读取工程；`scripts/recast.py probe` 留原片指纹与时间线。读取角色映射、分段和停止记录，只做当前授权范围。
2. 查看完整动作与复杂镜头，准备参考；用 `examples/task.example.json` 格式记录实际审核与资产来源。按 `production.md` 完成每段素材后才入 ready 队列。
3. 构建并冻结任务：`recast.py rgb`、`build-h3`、`verify-task`。图片不合格先修参考。新参考/参数/模型必须新版本及新 run ID。
4. 依据 `dgx.md` 选独立或合算模式，先 `allocate` 和 `native_run.py submit` 预演。实际提交仅在用户授权、工程未停止、环境已验证、相关 GPU 空闲时加 `--execute`。工具不会安装模型、改网络或重新启动旧监督。
5. `status` 单次读真实回执；完成后 `collect` 对账两阶段输出、各 rank 日志与 SHA。生产期间继续为下一批准备和审核参考；有独立 ready 任务且机器确实空闲才补位，不重复跑同一段凑并发。
6. 按源帧偏移映射，保留映射索引；合成原声内部预览。按 `quality.md` 实际播放源/目标，失败留截图和局部视频，返修只针对具体缺陷。
7. 交付与文档：保存选片表、检查记录、完整输入版本、示例图来源，核实外部文档中图片确实显示。仅在用户要求时发布到外部服务或新建监督。

## 工具与能力边界

Gemini 人工路线仅需本地 Python 3.10+、FFmpeg/ffprobe，以及用户可用的 Gemini App。运行 `gemini_app.py prepare` 只生成上传素材和提示词；用户必须自行上传、确认、下载，再由 `collect` 恢复源帧时间线，后续使用 `recast.py assemble` / `qa`。每段下载文件必须实际检查且标注视觉审查状态。

本包的本地脚本只需 Python 3.10+、FFmpeg/ffprobe。`native_run.py` 通过 SSH 对**已有合格 H3 原生环境**运行作者图；需 Linux、NVIDIA 驱动、已有模型/节点和同一个共享 GPU 锁。双机需另外核实两端版本与 RoCE/NCCL。不能把复制 Skill 视为集群部署完成。

`vendor/dual-spark` 保留之前双机实现的已提交源码、测试和归属，不带权重。先读其 AUDIT/SETUP/S02_AUTHOR；它的旧基准和新片参数不是同一性能结论。全流程示例与本包测试范围见 [VALIDATION.md](VALIDATION.md)。
