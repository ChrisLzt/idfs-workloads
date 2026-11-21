# 负载复现文件

本目录服务含 FAST 的五种默认负载及 Baleen 备选，提供Python生成与校验工具、Java User源码、构建工具、User JAR及冻结来源参数。仅生成或验证配置时，无需准备多TiB数据。

## 文件用途

| 内容 | 用途 |
|---|---|
| sources/baleen_region2.json、sources/baleen_region2_user/ | 冻结Region2键选择、4MiB对齐地址映射和600份频率表 |
| sources/wrf_conus1km60.json | WRF科学字段、网格和文件角色的容量依据 |
| sources/ai_training.json、sources/ai_inference.json | SES文件类别及请求配比的冻结输入 |
| source_pin.json | 来源版本、原始出处及冻结输入摘要 |
| *_user_java/、graph_zipf_java/ | 各业务 User 源码、预编译 JAR 和构建元数据 |
| common_user_java/ | 造数共用的 User 写入模块，随各业务 JAR 一起构建 |
| user_build.py、user_vdbench.sh | 共用的扩展构建和运行入口模板 |

Baleen的冻结来源目录与`workloads/hot_cold/bigdata_baleen/rendered/`中的表和映射分别承担生成输入、运行输出的用途。两处内容由SHA-256核对，渲染配置时可直接复用冻结输入，无需下载原始trace或实际生成容器数据。

## 离线生成与核验

从仓库的 `tools/` 目录执行。

```bash
./workloads.sh render --config-root /path/to/new-config --data-root /mnt/idfs/workloads
./workloads.sh validate --config-root /path/to/new-config
python3 -m unittest discover -s tests -v
```

这些步骤生成小体量配置并运行离线校验。造数和测试按根目录USAGE.md分别执行。

## 重新构建User模块

使用 JDK 11 或更高版本和原版 Vdbench 5.04.06，在仓库的 `tools/` 目录执行。

```bash
./workloads.sh build-user --vdbench /opt/vdbench50406/vdbench
```

默认构建全部扩展（包括备选 Baleen），也可用 `--case` 构建指定负载。两种 AI 共用一个 User JAR。`runtime.json` 记录 Java 8 版本及原版 JAR、源码和扩展 JAR 的摘要。构建后重新渲染配置。日常渲染直接使用预编译包。
