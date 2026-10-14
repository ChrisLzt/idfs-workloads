# hpc_wrf_continuous：约400 GiB版本

依据WRF的输入、边界、history和restart文件角色设计连续预报存储访问。
输入与边界执行读取；history与restart执行写入，覆盖预创建的对应角色文件。
6400个64MiB文件组成独立角色集合，合计400GiB。
角色依据见[WRF运行指南](https://www2.mmm.ucar.edu/wrf/users/wrf_users_guide/build/html/running_wrf.html)
及[namelist说明](https://www2.mmm.ucar.edu/wrf/users/wrf_users_guide/build/html/namelist_variables.html)。

## 阶段与访问意图

| 时间区间 | 访问集合与请求配额 |
|---|---|
| 0–80秒 | input 80%，boundary_0 20%，模拟初始化输入与初始边界 |
| 80–180秒 | history_01_04 100%，表示连续四次history输出事件 |
| 180–280秒 | history_05_08占8/9，boundary_1占1/9 |
| 280–340秒 | restart_08h 100%，表示restart输出事件 |
| 340–440秒 | history_09_12占8/9，boundary_2占1/9 |
| 440–540秒 | history_13_16占8/9，boundary_3占1/9 |
| 540–600秒 | restart_16h 100%，表示后续restart输出事件 |

history每四次输出合并为一个角色组，以减少阶段切换。
时长和请求配额是面向存储实验的工程参数，不是WRF官方性能统计；
restart阶段执行保存事件的写入，不包含故障、回滚或恢复读取。

## 阶段内冷热设计

各角色内部独立计算文件级Zipf(0.99)，跨角色的请求份额按上表固定。
随机选择文件，在文件内按4MiB顺序读写。未进入当前阶段的角色文件不参与请求。
600秒是定时访问预算，不要求扫完每个文件，也不执行天气计算或NetCDF回放。

容量：400.000000000 GiB；物理文件：6400；最大活跃FWD：42。
默认数据路径：`/mnt/idfs/workloads_400g/hpc_wrf_continuous`。此路径尚未准备，不能把配置存在视为数据已READY。

| 阶段 | 秒 | FWD |
|---|---:|---:|
| cold_start | 80 | 42 |
| history_01_04 | 100 | 30 |
| history_05_08 | 100 | 41 |
| restart_08h_output | 60 | 30 |
| history_09_12 | 100 | 41 |
| history_13_16 | 100 | 41 |
| restart_16h_output | 60 | 30 |

文件选择Zipf分桶的最大阶段加权TV误差为1.929213%；它是概率近似审计，不是识别错误率。

## 配置和入口

- rendered/run_current.vdb：正式配置。
- rendered/run_baseline.vdb：相同阶段和数据布局，组内均匀选择文件的对照。
- rendered/prepare_data.vdb：独立造数据配置。
- rendered/model.json：完整角色、排名范围、配比与来源证据。
- rendered/manifest.json：容量、布局和配置哈希。

本目录render_config.sh仅生成配置，validate_model.sh仅离线验证。
prepare_data.sh和run_test.sh均要求用户授权、--execute和独立的新结果目录。
使用普通Python3与原版Vdbench5.04.07，测量保留角色对应读写、max、无stopafter。
数据文件需要真实写入，不能用稀疏文件代替。详细命令见[套件README](../README.md)。
