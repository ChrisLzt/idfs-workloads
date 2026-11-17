# FAST 天文观测数据分析负载

参考《课题5-测试大纲 V1.2》的 FAST 天文观测大数据分析场景和附录 A.4 `BigData_IOPS.vdb`。沿用 **400 KiB 文件、128 KiB 随机请求、90% 读 / 10% 写**，增加全局 Zipf(0.99) 以测试对象级冷热识别。此目录可独立部署，当前五项默认套件中的大数据用例为 Baleen。

| 项目 | 设计 |
| --- | --- |
| 物理文件 | 8 个共享容器，每个逻辑长度 512 GiB，总逻辑长度 **4 TiB** |
| 有效数据 | **400 GiB**，共 1,048,576 个 400 KiB 的逻辑小文件 |
| 对象布局 | 每个逻辑小文件占一个 4 MiB 对象位置，条带单元 4 MiB，条带数 1 |
| 单次请求 | 128 KiB，随机选择逻辑文件内三个完整对齐块之一 |
| 热度 | 全局 Zipf(0.99)，映射种子 1048596。全库存最热 20% 对象的理论访问占比约 88.13% |
| 运行 | 原版 Vdbench 5.04.06 + 外置 User 模块，单个 600 秒 RD、`iorate=max`、`O_DIRECT`，最多 8 个压测客户端 |

每个对象从对象内的 128 KiB 处放入 400 KiB 数据，其余位置留空。测试访问其中 384 KiB 的完整对齐块，末尾 16 KiB 保留为准备数据。所有客户端使用同一份热度排名，各自独立抽样。容器聚焦对象数据访问，文件级的目录遍历、打开和关闭开销由真实小文件版本反映。

4 TiB 是文件逻辑长度，400 GiB 是准备脚本写入的有效载荷。实际后端空间受副本、分配粒度和配额统计影响。每个测试客户端须以同一路径访问同一组容器。原版 Vdbench 的拼接初始化会在计时前写入各容器末尾的 4 KiB 标记，该位置位于有效数据区之外。统计冷热时以 600 秒 RD 为窗口。

## 准备与测试

在仓库根目录运行。以下挂载点、运行路径和客户端名称按实际集群填写。使用 JDK 11 或以上编译 Java 8 兼容的 User 模块，运行使用原版 Vdbench 5.04.06 及兼容 Java。Master 上运行的本地客户端用 `localhost` 表示，以便 Vdbench 从 Master 写入拼接标记。其余客户端通过 SSH 启动。将生成的运行包部署到各客户端的同一绝对路径，且各主机的原版 Vdbench 安装位置一致。`--threads` 为拼接 WD 的总线程数，默认 64。

```bash
stock=/opt/vdbench50406
data=/mnt/idfs/fast
bundle=/opt/idfs-workloads/fast-runtime

./bigdata_fast/render_config.sh --stock-home "$stock" --data-root "$data" --bundle "$bundle" \
    --client localhost --client client2 --client client3 --client client4 \
    --client client5 --client client6 --client client7 --client client8 --threads 64

./bigdata_fast/run_test.sh --bundle "$bundle" --output "$PWD/fast-syntax" --simulate
```

造数只需在共享文件系统上执行一次。脚本按容器保存准备进度，停止后使用相同命令继续。检查通过后，可以在验收时单独启动负载。

```bash
./bigdata_fast/prepare_data.sh --bundle "$bundle" --execute
./bigdata_fast/check_data.sh --bundle "$bundle"
./bigdata_fast/run_test.sh --bundle "$bundle" --output "$PWD/fast-results" --execute
```

通过 NFS 访问且后端布局属性未透出时，在后端核对对象大小、条带单元和条带数，再将 `--confirm-nfs-layout` 加到造数、检查和测试命令中。Vdbench 语法模拟只验证配置，实际部署需要目标集群上的目录布局、直读写和并发运行验证。
