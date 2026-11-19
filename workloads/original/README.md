# 原负载集（未添加冷热倾斜）

本目录保存测试脚本文本中的 A.1–A.6，按用例拆成独立文件。保留原文的文件布局、请求大小、读写比例和运行阶段。访问方式沿用原配置，用于和冷热识别负载对照。默认五项及 FAST 容器备选的入口见[仓库说明](../../README.md)。

| 编号 | 用例 | 脚本 |
|---|---|---|
| A.1 | AI 训练 OPS | [Vdbench 配置](A.1_AI_CCF_SES_Training_OPS.vdb) |
| A.2 | AI 训练带宽 | [Vdbench 配置](A.2_AI_CCF_SES_Training_Bandwidth.vdb) |
| A.3 | AI 推理带宽 | [Vdbench 配置](A.3_AI_CCF_SES_Inference_Bandwidth.vdb) |
| A.4 | 400 KiB 小文件随机读写 | [Vdbench 配置](A.4_BigData_IOPS.vdb) |
| A.5 | WRF 输出和 checkpoint 带宽（单轮） | [MPI 和 IOR 脚本](A.5_HPC_Wrf_Bandwidth.sh) |
| A.6 | 图计算边属性文件读写 | [Vdbench 配置](A.6_Graph_IOPS.vdb) |

五份 Vdbench 配置保留了原文的 `…` 或 `...` 占位。部署时按目标集群补齐客户端定义，并填写 Vdbench 安装目录与存储挂载路径。WRF 脚本的运行环境由 `hosts`、`ior` 和 `out_path` 指定。
