# chan.py 项目启动说明

## 1. 项目功能简介

当前仓库是一个缠论分析核心库，主要能力包括：

- 读取股票、CSV 或数字货币 K 线数据。
- 计算缠论基础元素：K 线合并、分型、笔、线段、中枢、形态学买卖点。
- 绘制分析图并保存图片。
- 通过 `Debug/strategy_demo*.py` 演示基于历史 K 线逐根回放的简单回测/模拟逻辑。
- 通过 `App/ashare_bsp_scanner_gui.py` 提供 A 股近期买点扫描 GUI，但该 GUI 的依赖未写入 `Script/requirements.txt`。

README 中描述了 Futu 交易引擎、数据库、模型训练、完整回测框架等能力，但当前工作区没有 `Trade/`、`ModelStrategy/`、`Config/` 等对应目录，属于完整版本文档内容，不是当前仓库已包含的可执行代码。

## 2. 目录结构说明

- `main.py`：默认演示入口，使用 Baostock 下载 `sz.000001` 日线数据，运行缠论分析并保存 `test.png`。
- `Chan.py`：核心编排类 `CChan`，负责加载数据源、维护多级别 K 线、触发缠论计算。
- `ChanConfig.py`：缠论计算配置，包括笔、线段、中枢、买卖点、MACD/BOLL/RSI/KDJ 等参数。
- `DataAPI/`：数据源适配层。
  - `BaoStockAPI.py`：Baostock A 股数据。
  - `AkshareAPI.py`：Akshare A 股/指数数据。
  - `csvAPI.py`：本地 CSV 数据。
  - `ccxt.py`：Binance/ccxt 数字货币 OHLCV 数据。
- `KLine/`、`Combiner/`：K 线单元、K 线列表与包含关系处理。
- `Bi/`、`Seg/`、`ZS/`：笔、线段、中枢计算。
- `BuySellPoint/`：形态学买卖点计算。
- `Math/`：MACD、BOLL、KDJ、RSI、Demark、趋势模型等指标。
- `Plot/`：Matplotlib 绘图与动画绘图。
- `Debug/`：回测/模拟演示脚本，只打印买卖价格，不接入真实交易接口。
- `App/`：A 股买点扫描 GUI。
- `Script/requirements.txt`：基础依赖清单。

## 3. Conda 环境创建

README 明确说明项目最低依赖 Python 3.11，因此推荐：

```bash
conda create -n chantrade python=3.11 -y
conda activate chantrade
```

在本机已验证可用的安装命令：

```bash
pip install -r Script/requirements.txt
pip install "matplotlib>=3.5.3"
```

说明：本机用户目录中已有 `matplotlib`，`pip install -r` 一开始复用了用户站点包。为了让环境更独立，已在 `chantrade` 内单独安装 `matplotlib`。如需严格隔离用户站点包，运行时可使用：

```bash
python -s main.py
```

或在当前 shell 设置：

```bash
set PYTHONNOUSERSITE=1
```

## 4. 可选依赖

基础 `main.py` 不需要 Akshare、PyQt6 或 ccxt。

如果要运行 A 股买点扫描 GUI：

```bash
pip install akshare PyQt6
python App/ashare_bsp_scanner_gui.py
```

如果要使用 `DATA_SRC.CCXT` 读取数字货币 K 线：

```bash
pip install ccxt
```

这些依赖当前没有写入 `Script/requirements.txt`，属于代码实际引用但基础依赖文件遗漏的可选依赖。

## 5. 配置文件说明

当前仓库没有独立 YAML/INI 配置文件。核心配置通过 Python 代码中的 `CChanConfig({...})` 传入，常见参数包括：

- `trigger_step`：是否逐根 K 线回放。
- `bi_strict`、`bi_algo`、`bi_fx_check`：笔相关配置。
- `seg_algo`、`left_seg_method`：线段相关配置。
- `zs_algo`、`zs_combine`：中枢相关配置。
- `bs_type`、`min_zs_cnt`、`divergence_rate`、`macd_algo`：买卖点相关配置。
- `cal_rsi`、`cal_kdj`、`cal_demark`：额外指标开关。

## 6. 数据准备

默认入口不需要提前准备本地数据，会通过 Baostock 在线获取历史行情。

可选数据源：

- Baostock：`DATA_SRC.BAO_STOCK`，需要网络访问，代码会自动 `login()` 和 `logout()`。
- Akshare：`DATA_SRC.AKSHARE`，需要安装 `akshare`，无需登录。
- CSV：`DATA_SRC.CSV`，默认读取项目根目录下形如 `{code}_{k_type}.csv` 的文件，例如 `sz.000001_day.csv`。
- ccxt：`DATA_SRC.CCXT`，默认用 `ccxt.binance()` 获取 OHLCV。

## 7. 启动命令

从项目根目录运行：

```bash
conda activate chantrade
python -s main.py
```

如果不想弹出图窗或在无图形环境运行：

```bash
set MPLBACKEND=Agg
python -s main.py
```

成功后会生成：

```text
test.png
```

交互式 Web 单股缠论图：

```bash
conda activate chantrade
pip install -r Web/requirements.txt
python -s Web/app.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

页面支持输入 `sz.000001`、`sh.600519` 这类股票代码，切换日线、周线、月线，并在交互式 K 线图上显示 MA5/10/20/30、笔、线段、中枢和买卖点。
Web 图表层使用 `pyecharts` 生成 ECharts 配置，ECharts JS 静态文件由本项目本地提供，不依赖 pyecharts 默认远程资源。

## 8. 如何运行选股

当前仓库的选股入口是 GUI：

```bash
conda activate chantrade
pip install akshare PyQt6
python App/ashare_bsp_scanner_gui.py
```

GUI 会通过 Akshare 获取 A 股实时列表，过滤 ST、科创板、北交所、B 股、停牌等股票，然后对每只股票用日线缠论分析，查找最近 3 天内出现的买点。

注意：全市场扫描耗时较长，且依赖 Akshare 当前接口可用性。

## 9. 如何运行回测/模拟

建议从项目根目录用模块方式运行，否则直接执行 `Debug/strategy_demo.py` 会找不到根目录模块 `Chan`：

```bash
conda activate chantrade
python -s -m Debug.strategy_demo
```

已验证该命令会用 Baostock 历史数据逐根回放，并打印模拟买入/卖出价格，不会真实下单。

其他示例：

```bash
python -s -m Debug.strategy_demo2
python -s -m Debug.strategy_demo4
```

`Debug/strategy_demo3.py` 文件注释明确写着“代码不能直接跑，仅用于展示”，不建议作为首次启动命令。

## 10. 如何避免误触发实盘交易

当前工作区没有发现真实交易执行目录或下单入口。README 中提到的 `Trade/`、Futu 交易引擎、开仓/平仓脚本等目录并不存在。

仍建议遵守以下边界：

- 不执行 README 中提到但当前仓库不存在的 `Trade/Script/*` 相关命令。
- 不自行添加 Futu、券商、交易所 API key 或账户配置。
- 不运行任何包含 `place_order`、`add_trade`、`FutuTradeEngine`、`OpenTrade`、`CoverTrade` 字样的外部脚本。
- 当前可安全运行的范围：数据下载、缠论分析、绘图、A 股买点扫描、示例回测/模拟。

## 11. 常见报错和解决方法

- `ModuleNotFoundError: No module named 'Chan'`
  - 原因：从 `Debug/` 子目录脚本路径直接启动，根目录不在导入路径。
  - 解决：在项目根目录运行 `python -s -m Debug.strategy_demo`。

- `ModuleNotFoundError: No module named 'akshare'`
  - 原因：GUI 或 Akshare 数据源依赖未安装。
  - 解决：`pip install akshare`。

- `ModuleNotFoundError: No module named 'PyQt6'`
  - 原因：GUI 依赖未安装。
  - 解决：`pip install PyQt6`。

- `ModuleNotFoundError: No module named 'ccxt'`
  - 原因：使用 `DATA_SRC.CCXT` 但未安装 ccxt。
  - 解决：`pip install ccxt`。

- Baostock 登录或数据为空
  - 原因：网络、Baostock 服务、股票代码格式或日期范围问题。
  - 解决：确认代码格式如 `sz.000001`，并缩小日期范围重试。

- 图窗不显示或运行后自动关闭
  - 可使用 `MPLBACKEND=Agg` 保存图片，或在 Jupyter/IPython 中交互查看。

## 12. 已验证结果

本机验证：

```bash
conda create -n chantrade python=3.11 -y
pip install -r Script/requirements.txt
pip install "matplotlib>=3.5.3"
set MPLBACKEND=Agg
python -s main.py
python -s -m Debug.strategy_demo
```

结果：

- `main.py` 成功登录/登出 Baostock，并生成 `test.png`。
- `Debug.strategy_demo` 成功完成历史回放并打印模拟买卖记录。

## 13. 下一步建议

- 将 `akshare`、`PyQt6`、`ccxt` 是否作为可选依赖写入单独文档或额外 requirements 文件。
- 为 `Debug/` 示例统一增加根目录导入处理，或在 README 明确要求用 `python -m` 启动。
- 如果要做稳定选股流程，建议从 GUI 扫描逻辑中抽出命令行批处理脚本，输出 CSV，避免必须打开桌面窗口。
