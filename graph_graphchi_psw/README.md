# GraphChi 图计算负载

## 来源

访问结构依据
[GraphChi: Large-Scale Graph Computation on Just a PC（OSDI 2012）](https://www.usenix.org/system/files/conference/osdi12/osdi12-final-126.pdf)
提出的 Parallel Sliding Windows（PSW）方法。

图拓扑采用 [Stanford SNAP 的 web-Google 数据集](https://snap.stanford.edu/data/web-Google.html)，
由网页及其有向超链接构成。图数据用于确定不同顶点区间之间的边连接关系。

## 业务访问结构

PSW 按目标顶点区间组织 shard。处理一个区间时，读取当前区间的完整 memory shard，
同时访问其他 shard 中与当前源顶点区间对应的 sliding window。

本测试以四个区间表示处理过程，每区间150秒，总时长600秒，数据容量400 GiB。
选用活跃边全部写回的情形，加载读取和结果写回各占50%请求。
该读写配比是本测试选择的更新情形，并非论文对所有图算法的固定规定。

## 冷热设计

各阶段的访问集合随当前处理区间变化；同一边片段可在相关区间中被再次访问。
在每个源、目标区间对应的边片段集合内添加文件级 Zipf(0.99)，
保留图访问结构并形成集合内部的冷热差异。

Zipf 是为冷热识别添加的访问倾斜，不把图的顶点度分布直接等同于文件访问热度。
本负载描述 PSW 的存储访问特征，阶段时长不代表一次完整图计算的执行耗时。
