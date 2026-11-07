# 使用已有4TiB文件

本目录提供独立的prepare_data.sh和run_test.sh。先提前造好数据，等待测试时再运行正式入口；测试不会自动调用造数。默认正式配置为rendered/run_current.vdb，只读已有文件；run_baseline.vdb是可选均匀对象对照，不会与默认测试自动连跑。也可使用其他工具提前准备的合格文件。

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

客户端共享1048576个对象的同一Zipf排名和固定映射，按客户端/JVM/线程派生独立请求流。不会给每个客户端固定划分文件区间，也不会复制4TiB数据。最多512个客户端SD，RD/WD均用通配符选择。控制端通过User参数向各slave传递扩展JAR指纹，拒绝不同采样版本；远端入口还会在启动JVM前检查扩展、元信息、安装路径及配置目录。

未指定`--data-file`时，默认采用`DATA_ROOT/graph/test.graph.edge.props`；可用`--data-root`或DATA_ROOT设置根目录。`--config-root`可将生成目录放到另一位置。数据文件、配置和结果目录必须分开。

## 提前造数据：另行明确授权后执行

渲染目标文件路径后，若数据尚未准备，可单独执行：

```bash
./prepare_data.sh --execute \
  --vdbench /opt/vdbench50406/vdbench \
  --results /path/to/new-prepare-results
```

这一步只造一份4TiB共享文件，不启动600秒测试。采用完整写入和fsync，默认16MiB写入块；可通过--prepare-chunk-mib调整块大小。内容为合成随机字节，不要求真实图格式，也不绑定原脚本的4MiB顺序写。正式读的O_DIRECT、32字节与Zipf参数不受影响。

只在显式--execute后创建数据，检查挂载、布局和5%空闲余量，使用独占创建避免覆盖已有文件；已有文件满足检查时直接复用。每5秒左右在结果目录写入进度。中断后保留文件和preparing记录，测试入口会拒绝运行；不自动清理、覆盖或续写。成功写满并同步后记录ready并移除preparing，之后可以等待任意时间再单独测试。

只需一台准备客户端执行一次，不能在每个客户端各造4TiB副本。准备时采用普通文件写入并fsync，正式测试才采用O_DIRECT。

## 检查后等待测试

```bash
./check_data.sh
```

此步骤只检查挂载、普通文件类型、精确长度、分配块信息和能读取到的布局元数据，不读取数据内容，不重新造数，不要求重新预留4TiB可用空间。测试也接受其他工具生成的完整文件，不强制要求本脚本的ready记录；本脚本未完成的preparing记录则必须先处理。

原生CephFS/IDFS能提供布局xattr时，布局不匹配会拒绝运行。NFS可能不导出这些私有xattr，此时明确报告`not exposed by NFS`，以造数据时确认的4MiB布局为输入约定；不会推断“已实测布局”，也不修改NFS网关配置。stat分配块信息不等于完整对象库存或内容校验，需要造数据方保证数据完整。

## 正式运行：另行明确授权后执行

```bash
./run_test.sh --execute \
  --vdbench /opt/vdbench50406/vdbench \
  --results /path/to/new-results
```

不带`--execute`会拒绝执行。运行前重新检查已有文件和生成资产。结果目录必须是新的独立目录，不覆盖数据或配置；每个客户端使用的原版安装路径必须与渲染记录一致，VDBENCH_HOME不能覆盖成另一目录。改变配置目录、扩展或安装路径后需重新渲染并同步到所有客户端。

默认只运行一个600秒RD：32字节、随机纯读、O_DIRECT、iorate=max、warmup=0，无stopafter/maxdata或逐秒配额。启动和最终排空不算额外业务阶段。User在实际I/O回调时抽取地址，不重复固定前缀，也不每秒重置随机状态。

若另行运行均匀对照，在上面的命令中增加`--profile baseline`。

## 离线原版解析与扩展重建

```bash
../workloads.sh parser-check --case graph \
  --vdbench /opt/vdbench50406/vdbench \
  --results /path/to/new-parser-results
```

解析检查使用审计过的原版`-s`模式，不执行数据I/O。解析通过和离线地址测试不等于目标NFS/文件系统已实测支持32字节Direct I/O。

Java源文件在`idfs_workload/graph_zipf_java/idfsgraphzipf/`。若修改扩展源文件，需要Java11或更高版本的编译器输出Java8字节码，再重新渲染：

```bash
cd ..
python3 -m idfs_workload.graph_zipf_build --jar /opt/vdbench50406/vdbench.jar
```

设置TMPDIR可指定编译临时目录。发布包包含Java8扩展，运行端不需要编译器或任何trace、SES框架。
