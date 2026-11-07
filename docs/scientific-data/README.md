# 科学数据汇交计划

本目录分别提供大数据处理、图计算、高性能计算、人工智能训练、人工智能推理五份Word汇交计划。五份材料统一采用课题2023YFB4502701信息和访问生成种子1048596，容量、对象库存、业务结构与当前负载模型对应。

| 负载 | 汇交计划 | 负载目录 |
|---|---|---|
| 大数据处理 | [Baleen](01_数据汇交计划_大数据处理_Baleen.docx) | [bigdata_baleen](../../bigdata_baleen/) |
| 图计算 | [GraphIO](02_数据汇交计划_图计算_GraphIO.docx) | [graph_zipf](../../graph_zipf/) |
| 高性能计算 | [WRF](03_数据汇交计划_高性能计算_WRF.docx) | [hpc_wrf](../../hpc_wrf/) |
| 人工智能训练 | [AITraining](04_数据汇交计划_人工智能训练_AITraining.docx) | [ai_training_ses](../../ai_training_ses/) |
| 人工智能推理 | [AIInference](05_数据汇交计划_人工智能推理_AIInference.docx) | [ai_inference_ses](../../ai_inference_ses/) |

## 目录对应关系

Word第七章描述正式汇交资料包的目录；本仓库按可运行负载组织。对应关系如下。

| 汇交资料包 | 本仓库对应内容 |
|---|---|
| datasets/source/ | idfs_workload/sources/中的来源参数、Baleen频率表及来源清单 |
| datasets/model/ | 各负载rendered/中的模型、运行配置、映射及User配套文件 |
| tools/ | [五套独立复现工具](../../handover_tools/)，各自包含本负载入口、运行组件及冻结输入 |
| verification/、metadata/ | 汇交归档中保存的数据质量记录和资源清单，按汇交批次管理 |

本次GitHub发布包括五种负载及复现文件、五份汇交Word。Word中登记的现有资料文件数和体量按完整汇交归档统计。归档中的原始资料、质量记录与管理清单另外保存；大容量载体和未来集群实测资料按计划形成后追加登记。
