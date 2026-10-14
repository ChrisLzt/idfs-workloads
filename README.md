# IDFS 存储负载

本仓库面向冷热数据识别实验，包含大数据、图计算、高性能计算、人工智能四类业务，共五种存储访问负载。
设计依据分别来自真实生产 trace、GraphChi 论文、WRF 官方资料以及 CCF-SES 测试模板。

## 负载来源

| 类别 | 负载 | 来源 | 数据容量 |
|---|---|---|---:|
| 大数据 | [Baleen](bigdata_baleen/README.md) | [Baleen（FAST 2024）](https://www.usenix.org/conference/fast24/presentation/wong)及其公开的 Region7 trace | 约361.34 GiB |
| 图计算 | [GraphChi](graph_graphchi_psw/README.md) | [GraphChi（OSDI 2012）](https://www.usenix.org/system/files/conference/osdi12/osdi12-final-126.pdf)的 PSW 方法 | 400 GiB |
| 高性能计算 | [WRF 天气预报](hpc_wrf_continuous/README.md) | [WRF 官方运行指南](https://www2.mmm.ucar.edu/wrf/users/wrf_users_guide/build/html/running_wrf.html) | 400 GiB |
| 人工智能 | [AI训练](ai_training_ses/README.md) | [CCF-SES](https://ses.ccf.org.cn/) 1.2.0 的 PERF_002 模板 | 约399.82 GiB |
| 人工智能 | [AI推理](ai_inference_ses/README.md) | [CCF-SES](https://ses.ccf.org.cn/) 1.2.0 的 PERF_003 模板 | 399 GiB |

每种负载持续600秒，保留相应来源中的读取和写入角色。五种负载总容量约1960.16 GiB。

## 业务访问特征

- **Baleen**：依据 Meta 生产存储系统的公开访问记录，保留 GET 读取、PUT 写入及样本原生访问频率差异。
- **GraphChi**：按照 PSW 的区间处理过程，访问当前 shard 和其他 shard 的对应窗口，并描述活跃边的加载与写回。
- **WRF**：围绕预报运行中的初始输入、边界更新、history 输出和 restart 保存组织访问。
- **AI训练**：按照 PERF_002 的小文件、大文件顺序读写组合设计，读写请求比例为70%和30%。
- **AI推理**：按照 PERF_003 的顺序读写与随机读取组合设计，读写请求比例为80%和20%。

## 冷热分布

Baleen 使用所选 trace 的原生访问统计，不额外施加 Zipf。
GraphChi、WRF、AI训练和AI推理在各自业务文件集合或文件大小类别内添加文件级 Zipf(0.99)，
使业务访问结构与文件冷热差异同时体现。

容量、600秒时长、阶段划分以及附加的 Zipf 倾斜属于本测试的设计选择。
各出处提供业务结构或访问模板；这些参数不代表论文、官方指南或 SES 对所有业务的统一规定。
本实验描述存储访问特征，具体来源与模拟范围见各负载说明。
