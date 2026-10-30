# SWMM 管网计算与二维地表流三维演示

本仓库收录 EPA SWMM 5.2.4 管网计算引擎、Python 二维地表积水求解器，以及两套三维流动展示 demo。用于阅读源码、验证基础物理过程和演示排水场景。

仓库仅包含相关源码、测试和必要的合成示例输入。GIS 原始资料、产品设计文档、演示文稿、编译二进制以及可重新生成的仿真结果不纳入版本管理。

## 模块与数据来源

| 模块 | 入口 | 功能与数据来源 |
| --- | --- | --- |
| SWMM 管网引擎 | `swmm524_engine/` | C 语言引擎；支持动态波、运动波、恒定流，输出共享库和 `runswmm` 命令行程序 |
| 二维地表流 | `swmm-2d-flood/src/` | NumPy 格网求解器；按水面坡度和 Manning 关系计算通量，更新水深，支持边界标记与节点入流源项 |
| 三维水面 demo | `swmm-2d-flood/build_prototype.py` | 直接运行二维求解器，再用 Three.js 播放坡面流、洼地汇流、节点溢流和封闭系统质量守恒四种场景 |
| 管网与径流展示 | `demo_2dflood/` | 通过 SWMM API 获取合成山谷模型的结果，以 Leaflet + Plotly 展示汇水链、节点水深、管道流量及三维动画 |
| 示例与基准 | `testSWMMsample/` | 小型城市模型、外部入流示例、约 1 万节点的 DW/KW/SF 基准生成与运行脚本 |

**当前能力边界：**

- 三维水面 demo 的溢流场景使用合成三角形流量过程线；二维求解器的水深由数值计算产生。该脚本没有直接连接 SWMM 引擎。
- 山谷 demo 的管网时间序列来自真实运行的 SWMM 合成模型；地形、流动粒子和地表水面表现用于演示，未调用上述二维求解器。
- `SwmmOverflowSource` 将节点流量单向注入格网；当前节点位置参数实际使用 `(列, 行)` 索引，需要调用方预先完成坐标映射。尚无地表水回流管网的完整双向耦合流程。
- `main.py` 目前可用的是 `--synthetic` 模式；真实 DEM + 溢流文件入口尚未实现。`--max-output` 当前写出最终时刻水深，并非全过程最大水深包络；`--manning` 参数目前未接入格网赋值。
- 类名虽为 `BatesSolver`，当前实现采用简化扩散波通量关系，并非完整二维浅水方程求解器。报告解析辅助函数也不能代替完整节点溢流时间序列导出。
- `BC_OPEN` 当前只取消边界墙，域外通量仍为零；完整的开放出流边界尚未实现。

## 目录

```text
.
├── swmm524_engine/          # SWMM C 源码、CMake、原始说明
├── swmm-2d-flood/
│   ├── src/                 # grid / solver / coupling / io
│   ├── tests/               # 格网、流动、质量守恒、稳定性等测试
│   ├── main.py              # 二维合成场景命令行入口
│   ├── build_prototype.py   # Three.js 三维水面页面生成器
│   └── demo_physics.py      # 物理过程静态图
├── demo_2dflood/
│   ├── demo_valley.inp      # 合成山谷 SWMM 输入
│   ├── run_demo.py          # 调用共享库并导出时间序列
│   ├── generate_data_json.py
│   ├── build_standalone.py  # 地图、图表与三维动画页面生成器
│   └── visualize.py         # 静态汇总图
├── testSWMMsample/          # 精选输入及可再生的大模型基准脚本
└── requirements.txt        # 二维计算及可选绘图依赖
```

## 环境准备

以下命令均从仓库根目录执行。建议使用 Python 3.10 或更高版本；编译 SWMM 还需要 CMake 和 C 编译器。Windows 示例使用 Visual Studio 2022 的“使用 C++ 的桌面开发”工具链和 x64 Python。

```powershell
git clone https://github.com/shallowyjf/swmm_engine_improve_demo.git
cd swmm_engine_improve_demo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Linux/macOS 的虚拟环境激活命令为 `source .venv/bin/activate`。若 PowerShell 不允许激活脚本，可直接使用 `.\.venv\Scripts\python.exe` 替代后续的 `python`，无需调整系统执行策略。

只运行二维求解器和 Three.js demo 时，安装 `swmm-2d-flood/requirements.txt` 即可；`matplotlib` 用于静态报告和基准图。Three.js、Leaflet、Plotly 通过固定版本 CDN 加载，浏览器需联网并支持 WebGL。

## 快速运行三维水面 demo

此流程不需要编译 SWMM。

```powershell
python swmm-2d-flood/build_prototype.py
python -m http.server 8000 --bind 127.0.0.1
```

打开 [三维水面 demo](http://127.0.0.1:8000/swmm-2d-flood/prototype_viewer.html)。页面支持旋转、缩放、场景切换、播放和时间轴拖动。生成的 HTML 内嵌格网与水深帧，前端库仍需从 CDN 加载。服务使用完毕后按 `Ctrl+C` 退出。

单独运行二维合成场景并导出 ASCII 水深格网：

```powershell
python swmm-2d-flood/main.py --synthetic --rows 60 --cols 60 --dx 10 --duration 600 --output swmm-2d-flood/final_depth.asc
```

## 编译与运行 SWMM

Windows / Visual Studio 2022：

```powershell
cmake -S swmm524_engine -B swmm524_engine/build_vs -G "Visual Studio 17 2022" -A x64
cmake --build swmm524_engine/build_vs --config Release
.\swmm524_engine\build_vs\Release\runswmm.exe testSWMMsample/basic_city_dem_chicago.inp testSWMMsample/basic_city_dem_chicago.rpt testSWMMsample/basic_city_dem_chicago.out
```

输出位于 `swmm524_engine/build_vs/Release/`，包括 `runswmm.exe` 和 `swmm5.dll`；运行时保留二者在同一目录。

Linux/macOS 可使用：

```bash
cmake -S swmm524_engine -B swmm524_engine/build -DCMAKE_BUILD_TYPE=Release
cmake --build swmm524_engine/build --parallel
./swmm524_engine/build/runswmm testSWMMsample/basic_city_dem_chicago.inp testSWMMsample/basic_city_dem_chicago.rpt testSWMMsample/basic_city_dem_chicago.out
```

输入为 `.inp`，文本报告为 `.rpt`，二进制结果为 `.out`。请检查报告是否完成、是否有错误，以及连续性误差和未收敛节点；这些指标比进程正常退出更能说明模型运行质量。

## 生成 SWMM 管网与径流三维展示

先完成上面的共享库编译，再执行：

```powershell
python demo_2dflood/run_demo.py
python demo_2dflood/generate_data_json.py
python demo_2dflood/build_standalone.py
python -m http.server 8000 --bind 127.0.0.1
```

打开 [管网与径流展示](http://127.0.0.1:8000/demo_2dflood/demo_viewer.html)。点击地图汇水区域可查看三维地形与流动动画。

`run_demo.py` 自动查找仓库下常见 CMake 输出目录；自定义构建路径可通过 `--dll` 指定，例如：

```powershell
python demo_2dflood/run_demo.py --dll ./swmm524_engine/build_vs/Release/swmm5.dll
```

Linux/macOS 同一参数可传入 `libswmm5.so` / `libswmm5.dylib`。Python 与共享库须使用匹配的系统及位数。

生成顺序为：`demo_valley.inp` → SWMM → `demo_results.csv` / `demo_summary.json` → `data.json` / `data_compact.json` → `demo_viewer.html`。全部输出可重建，默认忽略。

可选静态图：

```powershell
python demo_2dflood/visualize.py
cd swmm-2d-flood
python demo_physics.py
cd ..
```

## 测试和基准

运行现有二维测试（使用 Python 自带 `unittest`，不需要安装 pytest）：

```powershell
python -m unittest discover -s swmm-2d-flood/tests -v
```

运行 SWMM 外部入流示例：

```powershell
.\swmm524_engine\build_vs\Release\runswmm.exe testSWMMsample/ext_inflow_smoke.inp testSWMMsample/ext_inflow_smoke.rpt testSWMMsample/ext_inflow_smoke.out
```

大型城市基准会生成约 1 万节点的输入和较大的结果文件，按需运行：

```powershell
python testSWMMsample/generate_large_city_benchmark.py --generate-only
python testSWMMsample/generate_large_city_benchmark.py
```

可用 `--swmm-exe <path>` 指定命令行引擎，`--output-dir <path>` 指定输出目录，或用 `--summarize-only` 汇总已有结果；Windows 另提供 `run_large_city_benchmark.ps1`。DW/KW/SF 的耗时和误差应以本机实际输出为准。

发布前已在 Windows / Visual Studio 2022 x64 / CMake 4.3.1 环境完成全新编译，运行两个小型 SWMM 输入和山谷示例，并验证二维测试及两个页面的生成流程。Linux/macOS 构建命令未在本次发布中实测；完整万节点基准未重跑。

## 版本管理与源码来源

`.gitignore` 使用根目录和子项目白名单，仅跟踪上述模块。构建目录、虚拟环境、Python 缓存、模拟输出、生成页面和本地配置均被排除；新增需要发布的入口或示例时，应同步调整白名单。

SWMM 部分基于 EPA SWMM 5.2.4，保留源文件中的作者、版本注释及原始 `Readme.txt`、`Roadmap.txt`。本仓库不是 EPA 官方发布。各模块未被统一改写为新的开源许可证。
