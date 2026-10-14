# bigdata_baleen：Region7 原生 trace 版本

依据Baleen公开的Region7单份trace样本，构造保留原生文件访问频率的静态大数据负载。
以每个真实键生成一个合成文件，不增加文件副本，不额外施加Zipf。

## trace统计与数据布局

来源：[Region7 full_0_0.1.trace](https://ftp.pdl.cmu.edu/pub/datasets/Baleen24/storage/20230325/Region7/full_0_0.1.trace)。
按(block_id, rs_shard_id)识别键；host_name是来源机器字段，不参与键身份。
样本有277233条请求记录，展开op_count后共288539次访问，其中GET 247264次、PUT 41275次。
GET执行读、PUT执行写；请求次数份额约为85.695175%读、14.304825%写。

每个键的文件大小为ceil(max(io_offset+io_size)/2MiB)×2MiB。
57311个键对应57311个文件，总容量361.337890625GiB。
容量来自这份样本的访问范围，不再扩到400GiB；按该口径完整保留所有键。
trace提供访问元数据，Vdbench准备的是合成文件内容。

原trace日志SHA256：
`019ba3162716d6d3623df7bfd69be206ae00aff054d521933d102eee23a486a2`。
运行和重新渲染使用本地冻结统计idfs_workload/sources/baleen_region7.json，
不用联网下载日志。统计包含每键身份、访问范围、GET/PUT计数，
以及总计、GET、PUT三份对齐I/O大小直方图。

## 阶段与原生冷热设计

只有native_all一个600秒阶段：随机选择文件，在文件内随机偏移读写，
fwdrate=max、threads=1、无stopafter。原时间顺序和偏移不逐条回放。

先按文件大小和操作集合（只有GET、只有PUT、同时GET/PUT）分组，
在各组内按访问次数降序分桶；194个桶共使用256个FWD。
每桶GET/PUT权重分别等于其中源键对应操作次数之和，桶内均匀选择文件。
单文件混合操作桶的两个随机I/O通道使用原版fileio=(random,shared)，
避免两个线程争抢唯一文件；其他桶维持普通随机I/O。
各桶source_key_indices与冻结统计的键索引对应，每键只出现一次。
分桶产生的总体文件访问概率TV误差为3.553460%，上限为4%。
分别条件于读、写的文件概率TV为4.640520%、4.385481%。
新增独立操作通道占用FWD预算，分桶误差与此前合并操作时不同。
原样本最热键承担约0.489362%的访问；不固定额外热池占比。

## I/O大小适配

将原I/O覆盖范围向4KiB边界对齐，以支持o_direct；
逐键读写直方图分别按op_count累计，再在桶内精确汇总到对应操作通道。
原版Vdbench使用整数传输大小百分比：四舍五入后校正总和为100，舍去0%类别。
因此配置中的I/O大小分布是近似，未对齐的原请求不逐字节复现。

整数化造成的全局I/O大小分布TV为7.611592%，
操作与I/O大小的联合分布TV为8.576393%，
平均请求大小相对对齐后的源统计变化-2.612893%。
按读写分别归一化后，GET大小分布TV约7.109126%，PUT约17.366275%。
整数化后全配置中缺失的GET大小类别有988种，对应6023次源请求（GET的2.435858%）；
PUT缺失1311种，对应4962次源请求（PUT的12.021805%）。
源统计和模型保留这些类别，正式配置受原版整数采样器限制，不能声称精确复现写侧大小分布。
上述分布误差与文件访问概率误差是不同指标，均不代表识别准确率或实测对象热度。

容量：361.337890625 GiB；物理文件：57311；最大活跃FWD：256。
默认数据路径：`/mnt/idfs/workloads_400g/bigdata_baleen`。没有因为配置生成而准备数据。

| 阶段 | 秒 | FWD |
|---|---:|---:|
| native_all | 600 | 256 |

## 配置和入口

- rendered/run_current.vdb：正式配置。
- rendered/run_baseline.vdb：与current使用相同原生频率和数据布局。
- rendered/prepare_data.vdb：独立造数据配置。
- rendered/model.json：文件布局、trace键映射、请求配比与来源证据。
- rendered/manifest.json：容量、布局和配置哈希。

本目录render_config.sh仅生成配置，validate_model.sh仅离线验证。
prepare_data.sh和run_test.sh均要求用户授权、--execute和独立的新结果目录。
使用普通Python3与原版Vdbench5.04.07，测量GET读、PUT写、max、无stopafter。
数据文件需要真实写入，不能用稀疏文件代替。详细命令见[套件README](../README.md)。
