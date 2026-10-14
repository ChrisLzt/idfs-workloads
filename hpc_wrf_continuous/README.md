# WRF 高性能计算负载

## 来源

本负载依据 WRF 天气预报模型的文件角色与连续预报运行过程设计。
主要参考
[WRF 官方运行指南](https://www2.mmm.ucar.edu/wrf/users/wrf_users_guide/build/html/running_wrf.html)
和 [namelist 参数说明](https://www2.mmm.ucar.edu/wrf/users/wrf_users_guide/build/html/namelist_variables.html)。

官方资料区分初始输入、边界输入、history 预报输出与 restart 状态保存，
并通过相应参数控制输出和保存间隔。

## 业务访问结构

- **初始输入与边界数据**：读取预报初始状态及运行中所需的边界条件。
- **history 输出**：周期性写入预报结果，体现连续运行中的输出活动。
- **restart 保存**：按间隔写入可供后续恢复使用的状态数据。

本测试采用连续预报情形，围绕启动读取、边界更新、history 输出和 restart 保存组织业务阶段。
restart 阶段表示状态保存，不额外加入中断恢复过程。

数据容量400 GiB，总时长600秒。容量、输出事件的组合和阶段时长是本测试的选择，
不表示 WRF 官方规定的容量比例或真实预报耗时。

## 冷热设计

不同阶段访问对应的输入、边界或输出文件集合，体现文件随业务进程活跃和冷却的变化。
在每种文件角色内部添加文件级 Zipf(0.99)，呈现同类文件中的访问频率差异。

本负载模拟天气预报的存储访问角色与阶段，Zipf 倾斜属于冷热识别实验的附加设计。
