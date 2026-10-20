# ComfyUI / MiniMax H3 实际图

## 当前适配器范围

`assets/h3-api-base.json` 取自F003A v005真实执行图，保留节点、模型分支及两阶段结构；构建器重新绑定当前任务的scene、identity、RGB与动作提示词。模型、节点包、Python/CUDA与双机通信环境由使用者提供；包内没有权重。模板不声称适用全部H3版本或任意ComfyUI安装。

`H3_API.json` 为API prompt格式（class_type / inputs），通常无前端LiteGraph坐标、widgets_values和完整UI状态。可通过环境支持的API格式加载或经过转换导入；不能保证任意前端直接拖入。加载后红色缺失节点先核对依赖，不得删除后称复现成功。`native-spec.json` 是作者原生执行器读取的图+output_ids规格。

## 接线阅读

- 1→2→3：UNET、attention backend、chunk feedforward。
- 4→5：Qwen3VL文本编码器与Turbo LoRA；5提供模型与CLIP。
- 6→7：LMS LoRA与AdaLN修正，供细化分支Guider13。
- 8/9：视频和音频VAE。
- 11：首阶段ReferenceToVideo，连接scene/identity图片和原片RGB（35），有音频时也连接原声参考。
- 60起：MiniMaxH3AddGuide串联；frame_idx为24fps原生相对帧，来自有理数源时钟换算。
- 12/19：首阶段Guider与采样器。20/21解码，22保存首阶段。
- 23/24分离AV latent，25做3D latent upscale；26分出的空head sigma进入27，再经28接回音频latent。
- 45把首阶段视频缩到640×368，供细化条件46；70起再次串Guide；13/30细化，31/32解码，33保存第二阶段。

当前保留两阶段输出，便于判断缺陷从首阶段就存在，还是细化引入。最终原声通过源片恢复，不由模型生成音频代替。

## 采样参数不能只看节点名字

14 BasicScheduler生成sigma，15 ExtendIntermediateSigmas插入中间sigma，16 SplitSigmas分配前后阶段。15的steps不是整段实际总步数；权重名turbo_4step也不是本次固定采样4步的证明。F4曾比较默认split2与split6；某段构图改善不意味着所有嘴形相位也修好。

记录完整API图、seed、sigma分支、实际progress事件、模型/LoRA/VAE名与哈希。新图的真实步数以执行时事件和sigma长度为准。不要把参数修改描述为效果保证。

## 环境准入

核对已有安装的ComfyUI提交、模型文件、custom_nodes及后端源码。典型依赖：H3 ReferenceToVideo/AddGuide、ChunkFeedForward、AdaLNFix、3D upscaler、ExtendIntermediateSigmas、LTXV AV latent、rgthree LoRA Stack、VideoHelperSuite。旧双机完整清单见vendor的S02_AUTHOR与SETUP。

一些作者后端依赖环境中的AdaLN参考表等额外工件，它们没有随此Skill打包；只复制Python文件不构成可用环境。先做用户授权范围内的短片真实预检，核对history、两阶段MP4、模型参数和输出，再跑长片。

## 图示

四张图在README/画廊：总览、参考与Guide、模型与采样、latent放大与输出。它们依据真实API重绘，标题已标记“非历史界面截图”。今后可在当前实际环境加载该任务后截图，但要写为当前复现截图，附版本和时间，不能倒称历史构建截图。
