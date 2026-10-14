# graph_graphchi_psw：约400 GiB版本

按照GraphChi的Parallel Sliding Windows（PSW）结构设计图访问负载。
拓扑依据web-Google的边连接统计：按累计入边数量划分四个顶点区间，
将边归入4×4的源区间/目标区间cell，并用6400个64MiB合成文件表示这些片段。
PSW机制参考[GraphChi OSDI论文](https://www.usenix.org/system/files/conference/osdi12/osdi12-final-126.pdf)。
区间数量、合成文件容量和阶段时长是工程参数。

## 阶段与访问集合

四阶段各150秒，依次处理区间0至3。处理区间i时，访问：

- 目标区间为i的cell：对应当前memory shard的边数据。
- 源区间为i的其他cell：对应其他shard的sliding window。

两者取并集，对角cell只计一次。所有片段只有一份库存，各阶段共用数据。
采用全更新模板：加载为读，写回为写；每阶段对相同活跃文件集合各分配50%请求。
这是假设活跃边全部写回的统计模型，不是论文规定的所有图算法固定读写比例。
读写通道在阶段内并发；模型不复刻图计算、加载与写回的调用先后或完整PSW扫描。

## 阶段内冷热设计

先按拓扑统计设定各活跃cell的请求份额，再在每个cell内部计算文件级Zipf(0.99)。
文件选择为random，文件内部读写为sequential，单次I/O为4MiB。
每桶至少两个文件，为独立读写通道提供文件并发资源。
每阶段仅实例化活跃cell的FWD，分桶控制每RD不超过256个FWD。
图的顶点度分布不直接等同于文件访问Zipf；这里的冷热倾斜是显式添加的文件选择模型。

容量：400.000000000 GiB；物理文件：6400；最大活跃FWD：238。
默认数据路径：`/mnt/idfs/workloads_400g/graph_graphchi_psw`。此路径尚未准备，不能把配置存在视为数据已READY。

| 阶段 | 秒 | FWD |
|---|---:|---:|
| psw_interval_0 | 150 | 228 |
| psw_interval_1 | 150 | 214 |
| psw_interval_2 | 150 | 238 |
| psw_interval_3 | 150 | 220 |

文件选择Zipf分桶的最大阶段加权TV误差为7.378393%，最大cell条件TV为7.953532%。
独立读写通道和最小文件数约束减少了分桶自由度；条件TV上限为8%。
这些是概率近似审计，不是识别错误率。

## 配置和入口

- rendered/run_current.vdb：正式配置。
- rendered/run_baseline.vdb：相同阶段和数据布局，组内均匀选择文件的对照。
- rendered/prepare_data.vdb：独立造数据配置。
- rendered/model.json：完整角色、排名范围、配比与来源证据。
- rendered/manifest.json：容量、布局和配置哈希。

本目录render_config.sh仅生成配置，validate_model.sh仅离线验证。
prepare_data.sh和run_test.sh均要求用户授权、--execute和独立的新结果目录。
使用普通Python3与原版Vdbench5.04.07，测量保留读写、max、无stopafter。
数据文件需要真实写入，不能用稀疏文件代替。详细命令见[套件README](../README.md)。
