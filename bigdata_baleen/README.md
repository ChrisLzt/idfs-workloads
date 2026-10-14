# Baleen 大数据负载

## 来源

本负载采用论文
[Baleen: ML Admission & Prefetching for Flash Caches（FAST 2024）](https://www.usenix.org/conference/fast24/presentation/wong)
配套公开的访问 trace。

[官方数据集](https://ftp.pdl.cmu.edu/pub/datasets/Baleen24/)来自 Meta 的 Tectonic 大规模存储系统及其前端闪存缓存，
记录真实生产环境中的数据读取和写入。本测试选用
[Region7 的 full_0_0.1.trace 样本](https://ftp.pdl.cmu.edu/pub/datasets/Baleen24/storage/20230325/Region7/full_0_0.1.trace)。

## 样本与访问特征

所选样本包含277233条记录；计入重复操作后，共288539次访问，
其中 GET 为247264次，PUT 为41275次，约占85.70%和14.30%。

负载保留 GET 读取、PUT 写入的业务含义，并以样本的键集合、访问范围、
访问频率和请求大小作为设计依据。所覆盖的57311个键对应约361.34 GiB的数据容量。
该容量来自所选样本的访问范围；trace 记录的是访问元数据。

## 冷热设计

采用全样本统计构造一个600秒的访问阶段，以统计近似呈现原生冷热差异。
热度来自 trace 中各键的访问次数，不额外设置热点池或施加 Zipf。
此负载采用整体访问统计，原请求的时间顺序不逐条回放。
