# IDFS 存储负载

本仓库面向对象冷热识别与缓存实验，包含大数据、图计算、高性能计算、AI训练和AI推理五种存储负载。设计分别依据生产访问记录、QB认知域作战属性图访问模型、NCAR官方WRF资料和CCF-SES模板。

## 来源与规模

| 负载 | 来源依据 | 文件／容器容量 | 4MiB对象位置数 |
|---|---|---:|---:|
| [大数据 Baleen](bigdata_baleen/README.md) | [Baleen，FAST 2024](https://www.usenix.org/conference/fast24/presentation/wong)及其[Region2数据仓库公开记录](https://ftp.pdl.cmu.edu/pub/datasets/Baleen24/storage_all/storage_all_Region2.tar.gz)，在完整时间轴上整键抽样 | 约5.000 TiB | 1,310,719 |
| [图计算](graph_zipf/README.md) | QB认知域作战属性图的共享边属性文件与32字节随机读取模型 | 4.000 TiB | 1,048,576 |
| [高性能计算 WRF](hpc_wrf/README.md) | [NCAR WRF v4.4 CONUS benchmark](https://www2.mmm.ucar.edu/wrf/users/benchmark/v44/benchdata_v44.html)的科学字段和文件角色；采用1km、60层、8成员集合预报设计 | 约3.108 TiB | 814,680 |
| [AI训练](ai_training_ses/README.md) | [CCF-SES 1.2.0](https://ses.ccf.org.cn/) PERF_002的文件大小类别、读写及请求大小配比 | 容器约5.652 TiB；有效数据约4.295 TiB | 1,481,601 |
| [AI推理](ai_inference_ses/README.md) | [CCF-SES 1.2.0](https://ses.ccf.org.cn/) PERF_003的文件大小类别、读写及请求大小配比 | 4.500 TiB | 1,179,648 |

五项总容量约22.260 TiB。对象位置数表示按4MiB布局可容纳的对象库存，实际访问对象数取决于600秒内完成的请求。

## 访问结构与冷热设计

- **Baleen**：保留705,130个键的原生GET／PUT、偏移、请求大小和频率，原始时间轴映射至600秒，内部每秒更新对应区间的概率。各键独占4MiB对齐对象区间，不额外添加Zipf。采用频率采样保留热点变化。
- **图计算**：预先准备一份4TiB共享边属性文件，正式阶段持续600秒，每次随机读取32字节。在全文件对象集合上添加Zipf(0.99)，所有客户端共享固定热点排名。最热20%库存对象的理论请求份额约88.13%。
- **WRF**：42个逻辑文件分别表示8个预报成员的输入、边界、两次history、checkpoint及共享查找表。600秒内依次安排启动0–60秒、第0小时history 60–150秒、checkpoint及边界读取150–510秒、第6小时history 510–600秒。在当前活动文件内部添加对象级Zipf(0.99)，热点随业务角色切换。
- **AI训练**：七种逻辑文件大小类别，382,965个逻辑文件；小文件／大文件请求占比60%／40%，总读写比70%／30%。每个文件大小类别内部采用对象级Zipf(0.99)，逐I/O独立随机重选对象和合法偏移。
- **AI推理**：三种逻辑文件大小类别，4,007个逻辑文件；总读写比80%／20%，64KiB和128KiB请求各占一半。每个文件大小类别内部采用对象级Zipf(0.99)，逐I/O独立随机重选对象和合法偏移。

WRF和两项AI采用业务结构驱动的合成访问；图计算采用属性访问模型并添加可控冷热倾斜。两项AI保留SES的文件类别和请求配比，访问方式统一适配为纯随机。Baleen采用生产记录的原生频率，采样访问不保证逐事件顺序。

五项统一采用原版Vdbench 5.04.06与独立User模块、单个600秒RD、O_DIRECT和最大速率，不设置逐秒请求配额。访问生成种子为1048596；Baleen来源键选择参数另行保存在清单中。各负载均提供独立造数、就绪检查及测试入口，数据可提前准备后等待测试。

操作步骤见[使用说明](USAGE.md)，五份[科学数据汇交计划](docs/scientific-data/README.md)按各负载分别提供。
