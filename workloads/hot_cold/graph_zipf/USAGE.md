# 使用已有4TiB文件

本目录提供独立的 `prepare_data.sh` 和 `run_test.sh`。先准备共享文件，再用 `run_current.vdb` 执行对象级 Zipf 随机读。也可复用其他工具提前准备的合格文件。

## 环境与输入

- 原版Vdbench5.04.06，Java8或更高版本。使用随包提供的独立`graph-zipf-user.jar`，不改原版JAR。
- 一份已经完整准备的4398046511104字节普通文件。数据内容不要求真实图拓扑。
- 文件布局由造数据方预先确认：object_size和stripe_unit均为4194304，stripe_count=1；测试脚本不设置布局。
- 路径须为无空格和参数特殊字符的绝对路径，不经过符号链接。
- 多客户端挂载同一个共享文件，路径一致；每个客户端能运行相同绝对路径的生成目录和匹配的原版安装。存储节点数与压测客户端数独立。

## 只生成配置

在本目录执行，例如：

```bash
./render_config.sh \
  --data-file /mnt/nfs/graph/test.graph.edge.props \
  --vdbench /opt/vdbench50406/vdbench
./validate_model.sh
```

以上不创建、填充或读取测试文件的数据内容。指定的文件此时可以尚不存在；配置默认一个localhost客户端、64个I/O线程。

多客户端可在渲染时指定：

```bash
./render_config.sh \
  --data-file /mnt/nfs/graph/test.graph.edge.props \
  --clients reader-a,reader-b \
  --threads 64 \
  --vdbench /opt/vdbench50406/vdbench
```

`--threads` 指每台客户端的 I/O 线程数。各客户端的 SD 指向同一共享文件，在完整的 1,048,576 个对象上按相同的 Zipf 排名独立抽样。控制端通过 User 参数核对各客户端的扩展 JAR。

未指定`--data-file`时，默认采用`DATA_ROOT/graph/test.graph.edge.props`；可用`--data-root`或DATA_ROOT设置根目录。`--config-root`可将生成目录放到另一位置。数据文件、配置和结果目录必须分开。

## 提前造数据：另行明确授权后执行

渲染目标文件路径后，若数据尚未准备，可单独执行：

```bash
./prepare_data.sh --execute \
  --vdbench /opt/vdbench50406/vdbench \
  --results /path/to/new-prepare-results
```

这一步使用 Vdbench User 独立写入并同步一份 4 TiB 共享文件。单次写入为 4 MiB。文件内容为合成字节。正式测试独立执行 600 秒、32 字节随机读取。

只在显式 `--execute` 后创建数据。脚本检查挂载、布局和 5% 空闲余量，已有文件满足检查时直接复用。写入进度保存在结果目录的 Vdbench 日志。中断后保留文件和准备记录，测试入口会拒绝运行。完整写入并同步后，脚本登记就绪状态。

一台客户端负责造数。造数和测试都使用 O_DIRECT。

## 检查后等待测试

```bash
./check_data.sh
```

此步骤只检查挂载、普通文件类型、精确长度、分配块信息和能读取到的布局元数据，不读取数据内容，不重新造数，不要求重新预留4TiB可用空间。测试也接受其他工具生成的完整文件，不强制要求本脚本的ready记录；本脚本未完成的preparing记录则必须先处理。

原生 CephFS/IDFS 通过布局 xattr 核验对象大小和条带。NFS 未暴露布局属性时，`layout_verification` 为 `unavailable`。

## 正式运行：另行明确授权后执行

```bash
./run_test.sh --execute \
  --vdbench /opt/vdbench50406/vdbench \
  --results /path/to/new-results
```

不带`--execute`会拒绝执行。运行前重新检查已有文件和生成资产。结果目录必须是新的独立目录，不覆盖数据或配置；每个客户端使用的原版安装路径必须与渲染记录一致，VDBENCH_HOME不能覆盖成另一目录。改变配置目录、扩展或安装路径后需重新渲染并同步到所有客户端。

默认只运行一个600秒RD：32字节、随机纯读、O_DIRECT、iorate=max、warmup=0，无stopafter/maxdata或逐秒配额。启动和最终排空不算额外业务阶段。User在实际I/O回调时抽取地址，不重复固定前缀，也不每秒重置随机状态。

## 离线原版解析与扩展重建

```bash
../../../tools/workloads.sh parser-check --case graph \
  --vdbench /opt/vdbench50406/vdbench \
  --results /path/to/new-parser-results
```

解析检查使用审计过的原版`-s`模式，不执行数据I/O。解析通过和离线地址测试不等于目标NFS/文件系统已实测支持32字节Direct I/O。

Java源文件在`tools/idfs_workload/graph_zipf_java/idfsgraphzipf/`。若修改扩展源文件，需要Java11或更高版本的编译器输出Java8字节码，再重新渲染：

```bash
../../../tools/workloads.sh build-user --case graph --vdbench /opt/vdbench50406/vdbench
```

设置TMPDIR可指定编译临时目录。发布包包含Java8扩展，运行端不需要编译器或任何trace、SES框架。
