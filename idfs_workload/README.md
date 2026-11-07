# 负载复现文件

本目录服务当前五种负载，提供Python生成与校验工具、Java User源码、构建工具、User JAR及冻结来源参数。仅生成或验证配置时，无需准备多TiB数据。

## 文件用途

| 内容 | 用途 |
|---|---|
| sources/baleen_region2.json、sources/baleen_region2_user/ | 冻结Region2键选择、4MiB对齐地址映射和600份频率表 |
| sources/wrf_conus1km60.json | WRF科学字段、网格和文件角色的容量依据 |
| sources/ai_training.json、sources/ai_inference.json | SES文件类别及请求配比的冻结输入 |
| source_pin.json | 来源版本、原始出处及冻结输入摘要 |
| *_user_java/、graph_zipf_java/ | 当前User源码、接口版本和构建产物摘要 |
| *_user_build.py、graph_zipf_build.py | 针对原版Vdbench 5.04.06构建独立User扩展 |

Baleen的冻结来源目录与bigdata_baleen/rendered中的表和映射分别承担生成输入、运行输出的用途。两处内容由SHA-256核对，渲染配置时可直接复用冻结输入，无需下载原始trace或实际生成容器数据。

## 离线生成与核验

从仓库根目录执行：

```bash
./workloads.sh render --config-root /path/to/new-config --data-root /mnt/idfs/workloads
./workloads.sh validate --config-root /path/to/new-config
python3 -m unittest discover -s tests -v
```

这些步骤生成小体量配置并运行离线校验。造数和测试按根目录USAGE.md分别执行。

## 重新构建User模块

使用JDK及按许可取得的原版Vdbench 5.04.06，在仓库根目录执行对应命令：

```bash
python3 -m idfs_workload.baleen_user_build --jar /opt/vdbench50406/vdbench.jar
python3 -m idfs_workload.graph_zipf_build --jar /opt/vdbench50406/vdbench.jar
python3 -m idfs_workload.wrf_user_build --jar /opt/vdbench50406/vdbench.jar
python3 -m idfs_workload.ai_user_build --jar /opt/vdbench50406/vdbench.jar
```

构建入口的参数以各模块`--help`为准。JAR与runtime.json记录Java8接口版本、原版JAR摘要和源码摘要。构建后重新渲染配置，使运行目录包含对应构建产物。
