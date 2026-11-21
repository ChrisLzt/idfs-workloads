# FAST 天文观测数据分析负载

FAST 容器版是五项套件的默认大数据负载。参考《课题5-测试大纲 V1.2》的 FAST 天文观测大数据分析场景和附录 A.4 `BigData_IOPS.vdb`，沿用 **400 KiB 文件、128 KiB 随机请求、90% 读 / 10% 写**，加入全局 Zipf(0.99) 以测试对象级冷热识别。

| 项目 | 设计 |
| --- | --- |
| 物理文件 | 1 个共享容器 `fast.bin`，逻辑长度 **4 TiB** |
| 有效数据 | **400 GiB**，共 1,048,576 个 400 KiB 的逻辑小文件 |
| 对象布局 | 每个逻辑小文件占一个 4 MiB 对象位置，条带单元 4 MiB，条带数 1 |
| 单次请求 | 128 KiB，随机选择逻辑文件内三个完整对齐块之一 |
| 热度 | 全局 Zipf(0.99)，映射种子 1048596。全库存最热 20% 对象的理论访问占比约 88.13% |
| 运行 | 原版 Vdbench 5.04.06 + 外置 User 模块，单个 600 秒 RD、`iorate=max`、`O_DIRECT`，支持多客户端发压 |

每个对象从对象内的 128 KiB 处放入 400 KiB 数据，其余位置留空。测试访问其中 384 KiB 的完整对齐块，末尾 16 KiB 保留为准备数据。所有客户端使用同一份热度排名，各自独立抽样。容器聚焦对象数据访问，文件级的目录遍历、打开和关闭开销由真实小文件版本反映。

4 TiB 是文件逻辑长度，400 GiB 是准备脚本写入的有效载荷。实际后端空间受副本、分配粒度和配额统计影响。目标文件系统的单文件大小上限须至少为 4 TiB。每个客户端的 SD 都通过同一路径访问整个文件。统计冷热时以 600 秒 RD 为窗口。

## 准备与测试

在仓库根目录运行。挂载点、运行路径和客户端名称按实际集群填写。渲染使用仓库内预编译的 Java 8 User JAR。修改 Java 源码时，用 JDK 11 或更高版本执行统一的 `./tools/workloads.sh build-user --case fast --vdbench /opt/vdbench50406/vdbench`。本地客户端可用 `localhost` 表示，远端客户端通过 SSH 启动。各客户端使用相同绝对路径的运行包和原版 Vdbench 安装。`--threads` 为每台客户端的 I/O 线程数，默认 64。

```bash
vdbench=/opt/vdbench50406/vdbench
data_root=/mnt/idfs/workloads
config_root=/opt/idfs-workloads/config
fast=./workloads/hot_cold/bigdata_fast

"$fast/render_config.sh" --vdbench "$vdbench" --data-root "$data_root" --config-root "$config_root" \
    --clients localhost,client2,client3,client4,client5,client6,client7,client8 --threads 64
"$fast/validate_model.sh" --config-root "$config_root"

./tools/workloads.sh parser-check --case fast --vdbench "$vdbench" \
    --config-root "$config_root" --results /path/to/fast-syntax
```

一台客户端通过 Vdbench User 模块填充共享容器的有效区。每完成并同步 65,536 个对象槽（25 GiB 有效数据），脚本保存一个续跑点。中断后指定新的结果目录即可续写。八容器版和单容器版使用不同的数据目录。检查通过后可单独启动测试。

```bash
"$fast/prepare_data.sh" --config-root "$config_root" --vdbench "$vdbench" --results /path/to/fast-preparation --execute
"$fast/check_data.sh" --config-root "$config_root"
"$fast/run_test.sh" --config-root "$config_root" --vdbench "$vdbench" --results /path/to/fast-results --execute
```

数据文件生成在 `$data_root/bigdata_fast/fast.bin`，运行包生成在 `$config_root/bigdata_fast/rendered/`。测试使用 `run_current.vdb`。原独立入口可继续使用 `--bundle` 指向已重新生成的运行包。
