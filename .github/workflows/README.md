# 本地股票信息收集与通知推送

这是一个本地运行的 Python 小工具，用于收集自选股的消息面和技术面信息，并推送到 Bark、微信或企业微信群。

当前支持：

- A 股日线行情采集：基于 AKShare
- 技术面：MA5/MA10/MA20/MA60、RSI、MACD、BOLL、KDJ、成交量放大
- 消息面：个股新闻标题摘要，优先使用 AKShare 东方财富新闻接口
- 市场扫描：结合上证指数、深证成指、科创50的大盘环境，从东方财富行业/概念板块排名中筛强势板块，再从板块成分股里按涨幅、成交额、换手率和营收增长筛个股候选
- 账户风险：对真实持仓做成本亏损、止损价、均线破位、放量下跌、重大事件预警
- 同花顺数据：主营介绍、财务摘要、盈利预测、股东变化、分红配股、全球财经资讯匹配
- 指南针数据：支持正式 API 配置接入；未配置 API 时读取本地导出的 CSV/Excel 文件并写入日报
- 外围市场：美股指数、美股重点标的、韩国指数、韩国重点标的的行情技术面和新闻
- 通知推送：Bark、Server 酱、企业微信群机器人 Webhook
- 自动运行：本地 Windows 任务计划程序，或 GitHub Actions 云端定时运行

## 1. 安装

建议使用 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

AKShare 官方文档说明当前库要求 Python 64 bit 3.11 或更高版本；如果安装慢，可以使用国内镜像：

```powershell
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

## 2. 配置

复制环境变量样例：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

- Bark 推送到 iPhone：填写 `BARK_ENDPOINT` 或 `BARK_KEY`
- 个人微信推送推荐用 Server 酱：填写 `SERVERCHAN_SENDKEY`
- 企业微信群机器人：填写 `WECOM_BOT_WEBHOOK`

Bark 获取方式：

1. iPhone 安装并打开 Bark。
2. App 首页会显示一条测试 URL，类似 `https://api.day.app/xxxx/这里改成你自己的推送内容`。
3. 复制完整测试 URL，运行下面的配置脚本时粘贴进去即可。

也可以直接运行交互式配置脚本，密钥只会写入本机 `.env`：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_push.ps1
```

如果 Windows PowerShell 出现中文乱码，不影响功能。配置脚本已使用英文提示；也可以先执行：

```powershell
chcp 65001
$env:PYTHONIOENCODING="utf-8"
```

测试推送：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test_push.ps1
```

编辑 `config.yaml` 里的自选股：

```yaml
watchlist:
  - code: "000001"
    name: "平安银行"
    sectors: ["银行"]
    themes: ["跨境支付(CIPS)", "金融科技"]
  - code: "600519"
    name: "贵州茅台"
    sectors: ["食品饮料", "白酒"]
    themes: ["高股息", "消费"]
```

月度趋势参数也在 `config.yaml`：

```yaml
monthly:
  enabled: true
  lookback_days: 22
  top_n: 10
  min_score: 60
  board_types: ["industry", "concept"]
  top_board_n: 8
  stocks_per_board: 5
  max_boards_to_scan: 16
  min_amount: 100000000
  require_revenue_growth: true
  allow_unknown_revenue: false
  min_revenue_growth_pct: 0
  indexes: ["000001", "399001", "000688"]
```

账户持仓风险配置在 `positions` 和 `risk`：

```yaml
risk:
  enabled: true
  lookback_days: 90
  loss_warn_pct: -5
  loss_danger_pct: -10
  single_day_drop_pct: -4
  volume_ratio_threshold: 1.8
  push_when_empty: false

positions:
  - code: "000001"
    name: "平安银行"
    cost_price: 10.50
    quantity: 1000
    stop_loss_price: 9.80
    note: "账户持仓"
```

盘中量能监控配置在 `volume_monitor`：

```yaml
volume_monitor:
  enabled: true
  source: "ths"
  eastmoney_fallback: false
  lookback_days: 40
  avg_volume_days: 20
  high_volume_ratio: 1.8
  low_volume_ratio: 0.55
  price_move_pct: 2.0
  min_progress: 0.15
```

它会按当前交易进度估算全天成交量，识别放量上涨、放量下跌、明显放量、缩量上涨和缩量下跌。
默认使用同花顺榜单接口，只在自选股命中持续放量、持续缩量、量价齐升、量价齐跌等榜单时推送；正常无异动时不推送。`eastmoney_fallback` 设为 `true` 后，会在同花顺无命中时尝试东方财富实时行情估算。

数据源开关也在 `config.yaml`：

```yaml
data_sources:
  eastmoney_news: true
  ths: true
  compass:
    enabled: true
    mode: "auto"
    api_base_url: ""
    api_endpoint_template: ""
    api_timeout: 15
    export_dir: "data/compass"
    file_pattern: "{code}.*"
    max_rows: 8
```

说明：

- `eastmoney_news`：东方财富个股新闻
- `ths`：同花顺公开数据接口
- `compass`：指南针 API 或本地导出数据

外围市场配置在 `overseas`：

```yaml
overseas:
  enabled: true
  history_days: 90
  news_limit: 5
  indexes:
    - symbol: "^IXIC"
      name: "纳斯达克"
      market: "US"
    - symbol: "^KS11"
      name: "韩国KOSPI"
      market: "KR"
  watchlist:
    - symbol: "NVDA"
      name: "英伟达"
      market: "US"
      themes: ["AI算力", "半导体"]
    - symbol: "005930.KS"
      name: "三星电子"
      market: "KR"
      themes: ["存储芯片", "消费电子"]
```

美股一般直接填股票代码，例如 `AAPL`、`NVDA`、`TSLA`；韩国股票使用 Yahoo Finance 代码格式，例如 `005930.KS`、`000660.KS`。

## 3. 指南针数据接入

目前没有确认到指南针个人版公开稳定 API。项目已经做成两层接入：

- `mode: "auto"`：优先调用你配置的指南针 API，失败或未配置时读取本地导出文件
- `mode: "api"`：只调用指南针 API
- `mode: "export"`：只读取本地导出文件
- `mode: "off"`：关闭指南针数据

如果你拿到了指南针官方或机构版 API 文档，可以把接口模板写入 `.env`：

```text
COMPASS_API_BASE_URL=https://example.com
COMPASS_API_ENDPOINT_TEMPLATE=/stock/{code}/summary
COMPASS_API_TOKEN=你的token
COMPASS_API_TIMEOUT=15
```

`COMPASS_API_ENDPOINT_TEMPLATE` 也可以直接写完整 URL，例如：

```text
COMPASS_API_ENDPOINT_TEMPLATE=https://example.com/api/stock/{code}/summary
```

也可以运行配置脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_compass_api.ps1
```

没有 API 时，继续使用本地导入方式。你可以从指南针软件里导出 CSV 或 Excel，然后放到：

```text
data/compass/
```

默认按股票代码匹配文件名，例如：

```text
data/compass/000001.csv
data/compass/600519.xlsx
```

日报会读取匹配股票代码的最新文件，并把前几行摘要写入“指南针数据”部分。

## 4. 运行

只生成报告，不推送：

```powershell
python run.py --dry-run
```

生成全市场板块扫描与个股候选报告：

```powershell
python run.py --monthly --dry-run
```

生成报告并推送：

```powershell
python run.py
```

只监控新增重大事件，不重复推旧消息：

```powershell
python run.py --monitor
```

查看监控结果但不推送、不更新增量状态：

```powershell
python run.py --monitor --dry-run --no-state
```

监控账户持仓风险：

```powershell
python run.py --risk-monitor
```

查看账户风险但不推送、不更新增量状态：

```powershell
python run.py --risk-monitor --dry-run --no-state
```

监控盘中放量/缩量：

```powershell
python run.py --volume-monitor
```

查看盘中量能但不推送、不更新增量状态：

```powershell
python run.py --volume-monitor --dry-run --no-state
```

重大事件监控覆盖：

- 重大消息面：按关键词筛选东方财富个股新闻
- 外围消息：按关键词筛选美股、韩国股票和外围指数新闻
- 公告：重大事项、财务报告、风险提示、信息变更、持股变动
- 财报/业绩：年报、中报、半年报、季报、业绩预告、业绩快报
- 增量去重：已推送过的事件指纹保存在 `state/event_state.json`

手机通知默认发送“文字简报”，包含结论、重点事件、趋势联动和数据源状态；完整原始事件、链接和异常详情仍保存在 `reports/`。
简报还会结合 A 股消息主题和市场扫描结果，给出板块机会跟踪与候选股观察清单。候选逻辑基于消息触发、板块强度、成交额、换手率、营收增长和大盘环境，不构成买卖建议。

报告会保存在 `reports/` 目录。

## 5. 定时运行

可以用 Windows 任务计划程序每天收盘后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_daily.ps1
```

重大事件监控建议盘中和晚间高频运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_monitor.ps1
```

账户持仓风险监控建议盘中和收盘后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_risk_monitor.ps1
```

盘中量能监控：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_volume_monitor.ps1
```

建议时间：

- 盘中重大事件监控：09:35 到 11:30、13:00 到 15:00，每 15 到 30 分钟
- 盘中量能监控：09:45 到 14:45，每 30 分钟
- A 股收盘复盘：15:15 到 16:30
- 晚间消息面补充：20:00 到 22:00

## 6. 关机后仍自动运行

电脑真正关机后，本地任务计划程序无法继续运行。要做到关机也能更新和推送，需要把任务放到云端。本项目已内置 GitHub Actions 配置：

```text
.github/workflows/stock-auto-update.yml
```

用法：

1. 把项目上传到 GitHub 仓库。
2. 在仓库 `Settings -> Secrets and variables -> Actions` 添加密钥：
   - `BARK_ENDPOINT`：Bark App 中复制的推送地址，推荐
   - `BARK_KEY`：Bark key，如果不用完整地址才填
   - `SERVERCHAN_SENDKEY`：Server 酱 SendKey
   - `WECOM_BOT_WEBHOOK`：企业微信群机器人 Webhook
3. 如果只用其中一个推送方式，可以在 `Variables` 里添加 `PUSH_CHANNELS`，例如 `bark`、`serverchan` 或 `wecom`。
4. Bark 可选变量：
   - `BARK_SERVER`：自建 Bark 服务端地址，默认 `https://api.day.app`
   - `BARK_GROUP`：通知分组，默认 `股票提醒`
   - `BARK_LEVEL`：通知级别，例如 `timeSensitive`
   - `BARK_SOUND`：通知声音
   - `BARK_URL`：点击通知后打开的链接
5. 进入仓库 `Actions -> Stock Auto Update`，可以手动运行，也会按定时计划自动运行。

云端定时安排：

- 盘中重大事件监控：北京时间工作日 09:05 到 15:35，每 30 分钟一次
- 盘中量能监控：北京时间工作日 10:15 到 14:45，每 30 分钟一次
- 收盘完整日报：北京时间工作日 15:25
- 晚间外围和消息面刷新：北京时间工作日 20:30

GitHub Actions 会缓存 `state/event_state.json`，用于保存已推送事件，减少重复提醒；每次运行生成的报告会作为 artifact 保存在 Actions 运行记录里。

## 7. 重要提醒

本项目只做信息收集和指标提示，不构成投资建议。AKShare 数据来自公开数据源，官方也提示其主要用于学术研究，使用时需要注意数据源稳定性和商业使用风险。

参考：

- [AKShare GitHub](https://github.com/akfamily/akshare)
- [AKShare 股票数据文档](https://akshare.akfamily.xyz/data/stock/stock.html)
- [Server 酱 SDK 示例](https://github.com/easychen/serverchan-sdk)
- [企业微信发送应用消息文档](https://s.apifox.cn/apidoc/docs-site/406014/api-10061348)
