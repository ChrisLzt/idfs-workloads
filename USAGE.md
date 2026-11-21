# 五种负载：提前准备，之后测试

默认五项的访问生成种子统一为 1048596。备选 Baleen 使用已选定的 Region2 键清单和原生频率表。只更换访问种子且容量、布局相同时，已准备的载体数据可以继续使用。

默认五项在 `workloads/hot_cold/` 下各有独立的造数、检查和测试入口。

| 负载目录 | 造数据 | 检查数据 | 正式测试 |
|---|---|---|---|
| `workloads/hot_cold/bigdata_fast` | prepare_data.sh | check_data.sh | run_test.sh |
| `workloads/hot_cold/graph_zipf` | prepare_data.sh | check_data.sh | run_test.sh |
| `workloads/hot_cold/hpc_wrf` | prepare_data.sh | check_data.sh | run_test.sh |
| `workloads/hot_cold/ai_training_ses` | prepare_data.sh | check_data.sh | run_test.sh |
| `workloads/hot_cold/ai_inference_ses` | prepare_data.sh | check_data.sh | run_test.sh |
| `workloads/hot_cold/bigdata_baleen`（备选） | prepare_data.sh | check_data.sh | run_test.sh |

默认大数据为 [FAST 容器版](workloads/hot_cold/bigdata_fast/README.md)。要使用 [Baleen Region2 备选](workloads/hot_cold/bigdata_baleen/README.md)，可单独运行其目录脚本，或为统一入口指定 `--case baleen`。

一台客户端为每套负载准备一份共享数据。测试时，发压客户端通过同一路径访问这份数据，各自从该负载的访问分布抽样。图计算为纯读，其余负载按各自的读写配比访问已准备的容器。

## 1. 渲染配置

先按目标集群的数据路径渲染每个负载的render_config.sh。图计算可指定准确共享文件路径，例如：

```bash
./workloads/hot_cold/graph_zipf/render_config.sh \
  --data-file /mnt/nfs/graph/test.graph.edge.props \
  --vdbench /opt/vdbench50406/vdbench
```

各用例都可以在渲染时用 `--clients` 填写发压主机列表，用 `--threads` 设置**每台**客户端的 I/O 线程数。默认是 `localhost` 和 64 线程。以下示例为两台客户端，每台 16 线程。

```bash
./workloads/hot_cold/bigdata_fast/render_config.sh \
  --data-root /mnt/idfs/workloads --vdbench /opt/vdbench50406/vdbench \
  --clients client-a,client-b --threads 16
```

各发压客户端使用相同的共享数据挂载路径、运行包绝对路径和 Vdbench 安装路径。控制端通过 SSH 启动远端 JVM，默认使用渲染配置时的登录账号，也可在 `--clients` 中用 `账号@主机` 指定远端账号。从仓库根目录运行 `./tools/workloads.sh render` 可给五项默认负载同时配置客户端，Baleen 用 `--case baleen` 单独指定。

仓库中rendered目录内的数据路径为示例，部署时先按实际挂载路径重新渲染。目录名称不决定数据容量，容量由模型和清单中的字节数确定。配置和结果必须放在数据目录之外。修改数据容量或布局后须重新渲染并按新布局准备数据；仅更换测试参数不意味着需要重造同一布局的数据。

每套运行包包含 `model.json`、`manifest.json`、`runtime.json`、`prepare_data.vdb`、测试配置和独立 User JAR。渲染使用已构建的 JAR。修改 Java 源码后，从仓库根目录统一执行 `./tools/workloads.sh build-user --vdbench /opt/vdbench50406/vdbench`，再重新渲染。

## 2. 提前造数据

在对应目录独立运行，例如：

```bash
./workloads/hot_cold/graph_zipf/prepare_data.sh --execute \
  --vdbench /opt/vdbench50406/vdbench \
  --results /path/to/prepare-session
```

在负责准备数据的客户端上调用对应负载的脚本。造数配置只在这台机器运行，使用独立的写入并发配置。每项有独立结果目录，准备完后可等待测试。

六套造数都由原版 Vdbench 5.04.06 的 User 模块按偏移写入并核验完成量。图计算、WRF、两项 AI 和 Baleen 以 4 MiB 为单次写入大小，填充整个容器。FAST 在每个 4 MiB 对象槽内写入 400 KiB，共写入 400 GiB，容器逻辑长度为 4 TiB。预检按待写字节预留 5% 空闲余量。

Baleen正式配置已采用Region2全时段整键抽样，须准备5,497,553,944,576字节（约5TiB）的容器，并留出5%空闲余量。旧Region7约394GiB的数据和ready标记不适用于此布局；准备入口拒绝自动覆盖已有内容，应为新数据选择独立目录后重新渲染，或由使用者另行处理旧数据。本脚本不自动迁移或删除旧容器。

WRF采用8成员方案，容器为3,417,015,582,720字节（约3.108 TiB）；AI训练容器为6,214,285,000,704字节（约5.652 TiB），其中有效数据约4.295 TiB；AI推理为4,947,802,324,992字节（4.5 TiB）。三项应分别准备共享容器并留出5%空闲余量。原约400 GiB版本的容器和ready标记不适用于扩容后的布局，使用新数据目录重新渲染后再准备；脚本不自动扩写、迁移或删除旧容器。各自造数、检查和测试入口保持独立。

FAST 每确认写完并同步 65,536 个对象槽保存一个检查点，继续时使用新的结果目录，从最近的检查点开始。图计算及其他用例若中断，保留文件和准备状态供检查。测试入口要求数据就绪。

## 3. 检查、等待

```bash
./workloads/hot_cold/graph_zipf/check_data.sh
```

六套采用相同的就绪检查，核验目标文件、逻辑大小、已分配字节、准备状态和对象布局。已准备的数据可继续使用原有完成记录，图计算可复用满足条件的共享文件。NFS 未暴露布局属性时，`layout_verification` 为 `unavailable`。

## 4. 测试时再执行

```bash
./workloads/hot_cold/graph_zipf/run_test.sh --execute \
  --vdbench /opt/vdbench50406/vdbench \
  --results /path/to/test-session
```

在控制端对所选负载执行一次测试脚本。同样将目录替换为其他负载即可。测试使用 `run_current.vdb`，单个 600 秒 RD、`iorate=max`、`O_DIRECT`；不带--execute拒绝启动。数据/配置/结果分开，结果子目录不得已存在。布局和所用原版安装必须与渲染配置一致。

只渲染、验证模型或执行原版-s解析不等于真实集群兼容性、吞吐或识别准确率验收。各负载来源及当前容量见[README](README.md)；图计算的多客户端和运行资产要求见[图计算使用说明](workloads/hot_cold/graph_zipf/USAGE.md)。

## 5. 仓库目录

`workloads/hot_cold/` 下的五种默认负载和 Baleen 备选各自保存入口脚本、负载说明和 `rendered/` 配置。到目标集群时按部署路径重新生成。[原负载集（未添加冷热倾斜）](workloads/original/README.md) 单独保存测试脚本 A.1–A.6。

`tools/` 保存统一命令、Python 实现、Java User 扩展、冻结来源输入及离线测试。从仓库根目录可执行 `./tools/workloads.sh` 查看默认五项，或执行 `./tools/validate_all.sh` 做离线校验。

`handover/` 集中存放五份 Word 汇交计划和五套独立复现工具包，分别位于 `plans/` 和 `tools/`。

正式运行需要的JAR、映射表和Baleen频率表已随仓库提供。重新构建User模块的方法见[复现文件说明](tools/idfs_workload/README.md)。原版Vdbench运行软件按其许可另行取得。
