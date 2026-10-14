# IDFS 五类存储负载

本套件包含Baleen大数据、GraphChi图计算、WRF高性能计算、AI训练和AI推理五类负载。
设计采用业务角色与阶段约束，再在符合该角色的文件集合内设置访问热度：
Baleen使用trace原生访问频率，其余四项使用组内Zipf(0.99)。
Baleen完整保留Region7样本的键库存，容量约361.34GiB；其余四项约400GiB。
文件大小类别与业务访问配额分别定义，Zipf用例通过文件数量控制容量。
本目录包含生成器、模型和配置，可独立拷贝部署。
面向后续集群存储测试，保留来源中的读写语义；各阶段按实际文件角色执行读或写。

| 用例目录 | GiB | 物理文件 | 阶段数 | 最大活跃FWD |
|---|---:|---:|---:|---:|
| [bigdata_baleen](bigdata_baleen/README.md) | 361.337891 | 57311 | 1 | 256 |
| [graph_graphchi_psw](graph_graphchi_psw/README.md) | 400.000000 | 6400 | 4 | 238 |
| [hpc_wrf_continuous](hpc_wrf_continuous/README.md) | 400.000000 | 6400 | 7 | 42 |
| [ai_training_ses](ai_training_ses/README.md) | 399.821899 | 34815 | 1 | 256 |
| [ai_inference_ses](ai_inference_ses/README.md) | 399.000000 | 1388 | 1 | 255 |

合计1960.159790 GiB，106314个物理文件。
各项600秒、fwdrate=max、无stopafter，每RD最多256 FWD。
使用原版Vdbench5.04.07、Java和普通Python3.10及以上，仅Python标准库。
AI只参考SES1.2.0 PERF_002/PERF_003，不安装SES、不接入ResearchCase或SES报告。

## 业务阶段

| 用例 | 阶段与访问意图 | 阶段内冷热分布 |
|---|---|---|
| Baleen | 600秒静态原生频率访问，GET读、PUT写；约85.6952%读、14.3048%写 | 根据键访问次数划分频率桶，桶内均匀选择合成文件 |
| GraphChi | 四个PSW区间，各150秒；加载当前shard及窗口后写回的统计模型，各阶段50%读、50%写 | 每个源/目标cell内Zipf(0.99) |
| WRF | 80秒输入/边界读取、四组100秒history输出、两组60秒restart输出；边界更新仍为读 | 当前输入、边界、history或restart角色内Zipf(0.99) |
| AI训练 | 单个600秒PERF_002阶段，小/大文件承担60%/40%请求；70%读、30%写 | 文件大小类别内Zipf(0.99) |
| AI推理 | 单个600秒PERF_003阶段；顺序读40%、顺序写20%、随机读40% | 逻辑文件大小类别内Zipf(0.99) |

测量中的写操作覆盖预创建的合成文件，不创建原业务的编码数据。GraphChi/WRF只模拟文件访问结构，
AI只模拟存储请求构成，阶段时长不代表实际计算或完整数据扫描。
各阶段的文件集合、请求配额、文件大小与分桶误差详见用例README和rendered/model.json。

## 扩容和冷热设计

GraphChi按PSW区间、WRF按输入/边界/history/restart角色、AI按文件大小类别确定访问集合和请求份额，
再在各自集合内计算Zipf(0.99)。贪心分桶最大255桶，优先减小文件选择概率误差，并为低请求份额
类别保留分桶预算。GraphChi每桶至少两个文件，供独立顺序读写通道使用；
训练小文件使用四个固定大小/操作通道，每桶至少四个文件，大文件每桶至少两个。
条件TV上限分别为GraphChi 8%、训练12%，WRF和推理为6%，实际误差在model.json。
这些分桶预算同时计入读写通道，不通过增加FWD突破256限制。
这是文件级概率近似，不保证对象热度分布、完整扫描覆盖或分类器验收指标。
Baleen使用[Region7单份真实trace](https://ftp.pdl.cmu.edu/pub/datasets/Baleen24/storage/20230325/Region7/full_0_0.1.trace)：
以(block_id, rs_shard_id)为键，从每键访问范围确定文件大小，逐键统计GET/PUT次数与I/O大小。
保留57311个键、361.337890625GiB库存，按文件大小及操作集合重建194个桶、256个FWD，
不复制键、不合并其他样本。读写分别保留源I/O大小直方图。
测量是600秒统计访问，并非原时间轴逐请求回放。文件频率分桶TV约3.5535%；
原版xfersize百分比整数化后的全局请求大小分布TV约7.6116%，
PUT条件大小分布TV约17.3663%；逐操作误差和缺失类别详见Baleen README。


AI推理347个逻辑文件对应1388个等容量分片，物理大小类别为128MiB/256MiB/512MiB。
85个桶各有顺序读、顺序写和随机读三个FWD，共255FWD；每个逻辑文件的四块保持在同一个桶中。
四等分保证最小桶也有足够文件供三个通道并发访问，逻辑文件大小及总容量保持不变。
一个桶可以包含多个相近排名逻辑文件，桶内均匀选择物理分片，因此逻辑文件Zipf存在分桶近似。
rank_start/rank_end表示组内逻辑排名；本桶逻辑局部索引j对应物理文件索引4*j至4*j+3。

混合xfersize百分比四舍五入后修正整数总和为100，删除0%类别，兼容原版Vdbench。
这会近似I/O大小直方图；FWD skew仍保留小数。baseline是同布局对照，不占用额外数据容量。
模型与生成代码的校验记录见idfs_workload/source_pin.json，参数依据存放在idfs_workload/sources/。

## 目录结构

```text
idfs-workloads/
├── README.md
├── workloads.sh                 # 统一preview/render/validate/prepare/run入口
├── render_all.sh
├── validate_all.sh
├── bigdata_baleen/
├── graph_graphchi_psw/
├── hpc_wrf_continuous/
├── ai_training_ses/
├── ai_inference_ses/
├── idfs_workload/                # 生成器、配置校验与执行保护
│   ├── models.py
│   ├── baleen.py                 # Region7统计提取与原生频率分桶
│   ├── core.py
│   ├── lifecycle.py
│   ├── parser_check.py
│   ├── validate_vdbench.py
│   ├── source_pin.json
│   └── sources/                 # Region7统计与其他四项参数模型
└── tests/                       # 离线回归测试
```

五个负载目录都包含README.md、render_config.sh、validate_model.sh、prepare_data.sh、
run_test.sh和rendered/。rendered中保存prepare_data.vdb、run_current.vdb、
run_baseline.vdb、model.json与manifest.json。

## 离线使用

```bash
cd /home/chris/idfs-workloads
./workloads.sh                    # 默认preview，不访问测试数据
./render_all.sh                   # 仅重新生成配置
./validate_all.sh                 # 离线模型、配置、测试、Shell语法
./workloads.sh preview --case ai_inference
```

默认配置就在五个用例各自rendered目录。CONFIG_ROOT可指定其他配置根。
默认数据父目录暂定`/mnt/idfs/workloads_400g`，目前没有对应挂载。
实际路径确定后再重新渲染，例如：

```bash
DATA_ROOT=/实际挂载目录/workloads_400g ./render_all.sh
# 同样可以显式使用：./workloads.sh render --data-root /实际挂载目录/workloads_400g
```

render不会创建数据目录。READY必须与当前数据路径、容量和布局hash匹配，不能跨布局复用。
本次读写恢复重新分桶并调整了推理分片，旧布局的READY不能用于新配置。
只读准备检查用`./workloads.sh preflight --case baleen`；校验已有库存用verify。
目标必须是已挂载的IDFS或CephFS，且数据目录是挂载下的独立子目录；不会自动挂载或清理。

获得真实执行授权并确认空间后：

```bash
./workloads.sh prepare --case baleen --execute --results /独立目录/全新造数批次
./workloads.sh run --case baleen --execute --results /独立目录/全新测量批次
```

VDBENCH_HOME覆盖原版Vdbench安装目录，PYTHON_BIN覆盖Python路径。
两种真实操作均不自动重渲染，使用已核对的配置；要求结果目录全新，拒绝与数据/配置路径重叠。
准备会检查空间、空目录和真实文件分配，再写READY；运行要求布局hash及库存验证通过。
本套件只执行Vdbench，不负责HP重置、采样或冷热指标计算，也未修改现有HP实验工具。
集群挂载路径、压测客户端数量和并发需按部署环境配置。

## 当前服务器空间（2026-10-14只读快照）

Ceph数据池单副本，OSD总容量约900GiB；CephFS可写剩余约154.3GiB，系统盘约276.4GiB。
Baleen含5%余量约需379.41GiB，其余单项约需420GiB，当前未清理情况下连一项也装不下。
五项共约1960.160GiB，含5%余量约2058.168GiB，不能同时放入当前OSD。
需更大环境，或另外授权清理后逐项准备/测试。若新环境使用多副本或纠删码，还需计入存储开销。
本轮仅生成配置、离线验证，未造数据、未压测、未删除已有数据、未commit或push。
