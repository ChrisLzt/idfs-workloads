# IDFS 存储负载

本仓库面向对象冷热识别与缓存实验，默认提供大数据、图计算、高性能计算、AI训练和AI推理五种存储负载。设计分别依据中国天眼天文观测数据分析测试、QB认知域作战属性图访问模型、NCAR官方WRF资料和CCF-SES模板。

## 来源与规模

| 负载 | 来源依据 | 文件／容器容量 | 4MiB对象位置数 |
|---|---|---:|---:|
| [大数据 FAST](workloads/hot_cold/bigdata_fast/README.md) | 中国天眼天文观测数据分析场景，[课题五附录 A.4](workloads/original/A.4_BigData_IOPS.vdb) 的小文件随机读写模型 | 单容器 4.000 TiB，有效数据 400 GiB | 1,048,576 |
| [图计算](workloads/hot_cold/graph_zipf/README.md) | QB认知域作战属性图的共享边属性文件与32字节随机读取模型 | 4.000 TiB | 1,048,576 |
| [高性能计算 WRF](workloads/hot_cold/hpc_wrf/README.md) | [NCAR WRF v4.4 CONUS benchmark](https://www2.mmm.ucar.edu/wrf/users/benchmark/v44/benchdata_v44.html)的科学字段和文件角色；采用1km、60层、8成员集合预报设计 | 约3.108 TiB | 814,680 |
| [AI训练](workloads/hot_cold/ai_training_ses/README.md) | [CCF-SES 1.2.0](https://ses.ccf.org.cn/) PERF_002的文件大小类别、读写及请求大小配比 | 容器约5.652 TiB；有效数据约4.295 TiB | 1,481,601 |
| [AI推理](workloads/hot_cold/ai_inference_ses/README.md) | [CCF-SES 1.2.0](https://ses.ccf.org.cn/) PERF_003的文件大小类别、读写及请求大小配比 | 4.500 TiB | 1,179,648 |

五项逻辑总容量约 21.260 TiB。对象位置数表示按4MiB布局可容纳的对象库存，实际访问对象数取决于600秒内完成的请求。

FAST 容器版是默认大数据负载。[Baleen Region2](workloads/hot_cold/bigdata_baleen/README.md) 为备选，通过 `--case baleen` 单独选择。Baleen 来自 [Region2 数据仓库公开记录](https://ftp.pdl.cmu.edu/pub/datasets/Baleen24/storage_all/storage_all_Region2.tar.gz)，容器约 5 TiB，共 1,310,719 个对象位置。

冷热识别负载集中在 `workloads/hot_cold/`，涵盖 Baleen 的原生冷热以及其余用例的人工倾斜。测试脚本 A.1–A.6 另存为[原负载集（未添加冷热倾斜）](workloads/original/README.md)，用于对照。

## 访问结构与冷热设计

- **FAST**：保留 400 KiB 逻辑文件、128 KiB 随机请求和 90% 读 / 10% 写。每个逻辑文件独占一个 4 MiB 对象位置，全部对象按固定的全局 Zipf(0.99) 排名采样。
- **图计算**：预先准备一份4TiB共享边属性文件，正式阶段持续600秒，每次随机读取32字节。在全文件对象集合上添加Zipf(0.99)，所有客户端共享固定热点排名。最热20%库存对象的理论请求份额约88.13%。
- **WRF**：42个逻辑文件分别表示8个预报成员的输入、边界、两次history、checkpoint及共享查找表。600秒内依次安排启动0–60秒、第0小时history 60–150秒、checkpoint及边界读取150–510秒、第6小时history 510–600秒。在当前活动文件内部添加对象级Zipf(0.99)，热点随业务角色切换。
- **AI训练**：七种逻辑文件大小类别，382,965个逻辑文件；小文件／大文件请求占比60%／40%，总读写比70%／30%。每个文件大小类别内部采用对象级Zipf(0.99)，逐I/O独立随机重选对象和合法偏移。
- **AI推理**：三种逻辑文件大小类别，4,007个逻辑文件；总读写比80%／20%，64KiB和128KiB请求各占一半。每个文件大小类别内部采用对象级Zipf(0.99)，逐I/O独立随机重选对象和合法偏移。

WRF和两项AI采用业务结构驱动的合成访问。两项AI保留SES的文件类别和请求配比，采用随机访问。备选 Baleen 保留 705,130 个键的原生 GET/PUT、偏移、请求大小和频率，将原始时间轴映射至 600 秒，每秒更新对应区间的采样频率。

五项默认负载和备选 Baleen 的造数与测试统一采用原版 Vdbench 5.04.06 和 Java User 扩展。造数按布局写满预定范围并同步，之后可单独启动测试。正式测试使用单个 600 秒 RD、O_DIRECT 和 `iorate=max`，不设置逐秒请求配额。访问生成种子为 1048596，Baleen 来源键选择参数另行保存在清单中。

六套均支持多客户端发压。渲染时用 `--clients` 指定主机，用 `--threads` 指定每台主机的 I/O 线程数。一台客户端准备共享数据，各发压客户端访问同一份文件。

操作步骤见[使用说明](USAGE.md)。既有[科学数据汇交计划](handover/plans/README.md)对应大数据负载采用 Baleen 的汇交批次。
