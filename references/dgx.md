# DGX运行、协作和恢复

## 三个层次分清

1. 制作调度：哪个独立镜头分给哪个物理节点；`allocate`仅生成计划，不是持久队列服务。
2. 原生作者图执行：`native_run.py`一节点world1、两节点world2；使用已合格的author_runner与legacy_backend。
3. 原先双机实验仓库：`vendor/dual-spark`固定提交源码，含旧CLI基准与S02插件。它不等于任意参考图工作流的可一键部署环境。

本包不安装驱动/模型、不配置RoCE、不开放服务、不创建常驻消费者或自动监督。长任务自动补位需在用户明确要求持续生产/监督时由当前Codex调用，或另建用户授权的自动化；没有ready任务就继续备料，不制造重复渲染。

## 配置

`cluster-two.example.json`与`cluster-three.example.json`只有占位信息。每个节点填：

- id、SSH alias、准确expected_hostname；不同alias指向同一物理机器也必须去重。
- python、comfy_root、已合格author_runner和legacy_backend。
- runs_root（本Skill新任务独立目录）和gpu_lock（该台所有消费者共享一个物理GPU锁）。
- comfy_ports列出所有正在使用的ComfyUI实例；排队中也视为忙。
- env保留该环境所需的CPATH、缓存目录、内存分配及NCCL/GLOO配置。
- world2还要两台版本匹配、master_addr为已验证fabric地址，master_port唯一；paired_qualified默认为false。

运行前人工核对模型、节点和后端内容，保留SHA和环境清单。远端包装器另检查真实hostname、共享锁、nvidia-smi compute processes和列出的ComfyUI队列。已有常驻GPU模型进程会被保守拒绝；不要为了通过而清空别人的队列或杀服务，应另选空闲节点或协调专用窗口。

## 提交流程与防重

1. 本地先verify-task；默认submit只打印计划，不联系远端。
2. 实际提交要求`--execute --project`、工程未停止且generation_authorized为true。此字段只能反映人类明确授权，不能由教程请求自动改成true。
3. 独占创建submission.json，先记录提交意图，再联系远端。每台唯一run目录使用mkdir exclusive，不覆盖旧目录。
4. 上传冻结输入、spec与包装器。每rank先拿共享锁并检查空闲，再报告ready，等待GO最多120秒。
5. 控制端看到全部rank ready才发GO；各rank持锁直到独立子进程结束。world2占满两台，不能分配其他镜头给其中任一台。
6. stage/ready失败会发送这些已启动包装器自己的STOP，保留submission intent。未知状态不自动重试。

提交命令返回只代表已发GO，不代表完成。使用status检查原生events/exit与包装器状态；包装器pid还核对/proc启动标识，避免PID复用被当作仍在跑。GPU进度与视觉通过分开。

## 锁的实际范围

该机制需要所有GPU消费者遵守同一个gpu_lock。它不是操作系统对所有GPU应用的强制隔离：不合作的外部提交仍可能在预检后竞争GPU，所以还需专用窗口/服务调度约束。第三台GPU后处理也用同一锁，例如Linux上：

```bash
flock -n /absolute/shared/physical-gpu.lock python3 owned_postprocess.py
```

原生包装器用fcntl.flock，与Linux flock命令互斥。CPU抽帧/文档整理无需占GPU锁。不要用一个工程一个锁、也不要仅在上传期锁住而生成期释放。

## 两机旧方案怎样复用

vendor固定提交包含原先的cluster.py、runtime/s02与测试/结果。查看其PROVENANCE对应版本，保留LICENSE/CREDITS。旧`cluster.py run`本身没有本包的共享GPU锁；不要与新调度并行使用。需要重跑旧基准时，单独隔离这两台，先validate、锁定两台资源、按其文档运行和回收，结束后核实空闲再交还。

日常人物替换优先通过本包native_run执行当前冻结的作者图，原生world2会调用既有已合格双机实现。迁移环境后先新同输入world1/world2短片对照，记录两阶段的数值/视频/音轨检查和耗时口径；旧2.24×热短片基准不能套给长片或带LoRA/参考输入的新任务。

## 回收

collect仅在每rank包装器finished且child_exit0时进行。以rank0真实history列出的输出取MP4，检查远端文件SHA与下载SHA相同，同时归档各rank日志/events/history/exit。回收目录必须新建。技术检查、映射和视觉验收尚未开始，不因collect成功而改为accepted。

断线后先status。若本地submission是intent，查看每台配置runs_root/run_id和wrapper/worker/exit，确认到底上传、ready、running、finished还是未知。不能删除意图后直接重提交。若确认从未启动，留存故障记录并创建新run/version；原回执保留。

## 停止与异常

`stop`给回执记录的每台唯一run目录写STOP。包装器只结束它拥有的子进程组，先TERM，15秒未退出再KILL；不pkill所有Python，不调用全局ComfyUI interrupt。再次status直到所有rank终止。包装器崩溃而child可能存活时，STOP文件可能无人处理：检查该唯一run的日志、命令行和真实进程归属，再只处理确认拥有的进程，不能声称stop命令成功就已停。

world2任一rank失败时立即查另一rank并发送同一任务STOP，保留两端证据。若控制端在发GO中断可能只有一端开始，尽快对账并停止，不能依赖通信最终自行恢复。timeout_s是整个原生子任务最长时间；0为无限，仅在用户工作方式和监控能承担时选择，不能靠加超时绕过故障。

用户叫停时先把工程状态持久化stopped_by_user并禁新任务，再停止当前rank、消费者和相关监督，核实没有本工程生成/GPU后处理。文档或包装阶段不恢复它。原项目未迁移到本包的旧进程必须按旧回执单独停止；本包不追杀它不拥有的进程。
