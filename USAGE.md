# 五种负载：提前准备，之后测试

五项负载的访问生成种子统一为1048596。Baleen保留已选定的Region2键清单、抽样参数和原生频率表；新种子用于访问生成。只更换访问种子且容量、布局不变时，已准备的载体数据可以继续使用。

每种负载目录都有独立的三个入口：

| 负载目录 | 造数据 | 检查数据 | 正式测试 |
|---|---|---|---|
| bigdata_baleen | prepare_data.sh | check_data.sh | run_test.sh |
| graph_zipf | prepare_data.sh | check_data.sh | run_test.sh |
| hpc_wrf | prepare_data.sh | check_data.sh | run_test.sh |
| ai_training_ses | prepare_data.sh | check_data.sh | run_test.sh |
| ai_inference_ses | prepare_data.sh | check_data.sh | run_test.sh |

一次造数只准备一份共享数据集，不在每个压测客户端各造副本。准备完成后可以等待，不会自动启动测试；正式测试不会重新format或再次填满数据。图计算为纯读，其余负载按各自来源保留读写，写操作使用预先准备的容器空间。

## 1. 渲染配置

先按目标集群的数据路径渲染每个负载的render_config.sh。图计算可指定准确共享文件路径，例如：

```bash
./graph_zipf/render_config.sh \
  --data-file /mnt/nfs/graph/test.graph.edge.props \
  --vdbench /opt/vdbench50406/vdbench
```

其他四项使用--data-root设置数据集根目录，例如：

```bash
./bigdata_baleen/render_config.sh --data-root /mnt/idfs/workloads
```

仓库中rendered目录内的数据路径为示例，部署时先按实际挂载路径重新渲染。目录名称不决定数据容量，容量由模型和清单中的字节数确定。配置和结果必须放在数据目录之外。修改数据容量或布局后须重新渲染并按新布局准备数据；仅更换测试参数不意味着需要重造同一布局的数据。

## 2. 提前造数据

在对应目录独立运行，例如：

```bash
./graph_zipf/prepare_data.sh --execute \
  --vdbench /opt/vdbench50406/vdbench \
  --results /path/to/prepare-session
```

其余四项将graph_zipf替换为对应目录即可。每项有各自造数入口和结果子目录，可以分开安排时间，不必造完后立即测试。任何prepare_data.sh不带--execute都会拒绝写数据。

图计算独立写满指定的4TiB文件，写入块大小可调，默认16MiB；其余四项使用各自Vdbench准备配置。4MiB对象布局不是准备工具的写入块大小。准备脚本不挂载、不修改网关、服务或系统缓存；原生CephFS/IDFS须继承正确文件布局，NFS不暴露布局属性时由部署/造数据方确认该约定。

Baleen正式配置已采用Region2全时段整键抽样，须准备5,497,553,944,576字节（约5TiB）的容器，并留出5%空闲余量。旧Region7约394GiB的数据和ready标记不适用于此布局；准备入口拒绝自动覆盖已有内容，应为新数据选择独立目录后重新渲染，或由使用者另行处理旧数据。本脚本不自动迁移或删除旧容器。

WRF采用8成员方案，容器为3,417,015,582,720字节（约3.108 TiB）；AI训练容器为6,214,285,000,704字节（约5.652 TiB），其中有效数据约4.295 TiB；AI推理为4,947,802,324,992字节（4.5 TiB）。三项应分别准备共享容器并留出5%空闲余量。原约400 GiB版本的容器和ready标记不适用于扩容后的布局，使用新数据目录重新渲染后再准备；脚本不自动扩写、迁移或删除旧容器。各自造数、检查和测试入口保持独立。

重复准备不会自动覆盖数据。部分失败或中断保留状态和文件供检查，不会假装数据就绪；不要为了继续运行而手工伪造完成标记。

## 3. 检查、等待

```bash
./graph_zipf/check_data.sh
```

每个目录都可独立检查。检查是文件清单、大小、分配信息、准备状态和可用布局属性的元数据核验，不扫描payload，不要求再次腾出一个数据集容量。数据未被删除、移动或改变布局时，可留待后续测试。

## 4. 测试时再执行

```bash
./graph_zipf/run_test.sh --execute \
  --vdbench /opt/vdbench50406/vdbench \
  --results /path/to/test-session
```

同样将目录替换为其他负载即可。默认current方案，单个600秒RD、iorate=max、O_DIRECT；不带--execute拒绝启动。数据/配置/结果分开，结果子目录不得已存在。布局和所用原版安装必须与渲染配置一致。

只渲染、验证模型或执行原版-s解析不等于真实集群兼容性、吞吐或识别准确率验收。各负载来源及当前容量见[README](README.md)；图计算的多客户端和运行资产要求见[图计算使用说明](graph_zipf/USAGE.md)。

## 5. 仓库目录

五个负载目录各自保存入口脚本、来源说明和rendered配置。`idfs_workload/`保存共用工具、User源码与JAR、冻结来源参数及频率表；`tests/`保存离线校验代码；`docs/scientific-data/`保存五份汇交Word及目录对应说明。

正式运行需要的JAR、映射表和Baleen频率表已随仓库提供。重新构建User模块的方法见[复现文件说明](idfs_workload/README.md)。原版Vdbench运行软件按其许可另行取得。
