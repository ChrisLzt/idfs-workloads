# 科学数据汇交计划

本目录保存既有批次的大数据处理、图计算、高性能计算、人工智能训练、人工智能推理五份 Word 汇交计划，统一采用课题 2023YFB4502701 信息和访问生成种子 1048596。该批次的大数据方案为 Baleen Region2。仓库当前默认测试套件采用 FAST 容器版，Baleen 可作为备选独立运行。

| 负载 | 汇交计划 | 负载目录 |
|---|---|---|
| 大数据处理 | [Baleen](01_数据汇交计划_大数据处理_Baleen.docx) | [bigdata_baleen](../../workloads/hot_cold/bigdata_baleen/) |
| 图计算 | [GraphIO](02_数据汇交计划_图计算_GraphIO.docx) | [graph_zipf](../../workloads/hot_cold/graph_zipf/) |
| 高性能计算 | [WRF](03_数据汇交计划_高性能计算_WRF.docx) | [hpc_wrf](../../workloads/hot_cold/hpc_wrf/) |
| 人工智能训练 | [AITraining](04_数据汇交计划_人工智能训练_AITraining.docx) | [ai_training_ses](../../workloads/hot_cold/ai_training_ses/) |
| 人工智能推理 | [AIInference](05_数据汇交计划_人工智能推理_AIInference.docx) | [ai_inference_ses](../../workloads/hot_cold/ai_inference_ses/) |

## 目录对应关系

Word第七章描述正式汇交资料包的目录；本仓库按可运行负载组织。对应关系如下。

| 汇交资料包 | 本仓库对应内容 |
|---|---|
| datasets/source/ | tools/idfs_workload/sources/中的来源参数、Baleen频率表及来源清单 |
| datasets/model/ | `workloads/hot_cold/` 下各负载 `rendered/` 中的模型、运行配置、映射及User配套文件 |
| tools/ | [五套独立复现工具](../tools/)，各自包含本负载入口、运行组件及冻结输入 |
| verification/、metadata/ | 汇交归档中保存的数据质量记录和资源清单，按汇交批次管理 |

本次GitHub发布包括五种负载及复现文件、五份汇交Word。Word中登记的现有资料文件数和体量按完整汇交归档统计。归档中的原始资料、质量记录与管理清单另外保存；大容量载体和未来集群实测资料按计划形成后追加登记。
