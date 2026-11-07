# 五种负载独立复现工具

各负载的工具包随附配置生成与校验入口、User 运行组件和冻结输入。按各包《使用说明》的配置生成步骤操作，运行依赖原版 Vdbench 5.04.06。

| 负载 | 工具包 |
|---|---|
| 大数据处理 | [Baleen](01_Baleen/tools/) |
| 图计算 | [GraphIO](02_GraphIO/tools/) |
| 高性能计算 | [WRF](03_WRF/tools/) |
| 人工智能训练 | [AITraining](04_AITraining/tools/) |
| 人工智能推理 | [AIInference](05_AIInference/tools/) |

各工具包的 `check_delivery.py` 以对应的完整汇交资料目录为输入，用于核验数据、工具和清单。[科学数据汇交计划](../docs/scientific-data/README.md)
