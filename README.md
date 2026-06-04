# Munger Screener — 芒格式选股评分器

> **⚠ 免责声明**
> 本工具是个人学习和研究辅助工具，所有输出结果仅供参考，**不构成任何买入、卖出或持有建议**。
> 投资有风险，最终决策请结合自身判断和专业建议。

---

## 简介

基于查理·芒格的多维度选股框架，对美股进行量化打分。评分维度包括：

| 维度 | 满分 | 来源 |
|------|------|------|
| 生意质量（ROE / 毛利率 / 净利率 / ROIC / FCF）| 30 | 自动（annual_financials）|
| 护城河（品牌 / 转换成本 / 网络效应 / 规模 / 定价权 / 持续性）| 20 | 人工判断 |
| 成长稳定性 | 15 | 自动 |
| 资产负债安全 | 15 | 自动 |
| 估值合理性（PE / FCF Yield）| 15 | 自动 + 人工 |
| 管理层质量 | 5 | 人工判断 |
| 风险扣分 | -15 | 人工判断 |

**总分 100 分**，85 分以上建议深入研究，70–84 分进入观察池。

---

## 按需查询任意股票（v2.2.0-alpha1）

除了批量评分 watchlist，现在可以**直接查询任意股票代码**——本地没有就自动建档：

```bash
python main.py AAPL          # 美股：本地有则直接分析
python main.py ORCL          # 美股：本地无 → 建骨架 + 自动抓取财务（yfinance）
python main.py 600519        # A股：自动规范化为 600519.SH
python main.py 600519.SH     # 上交所
python main.py 000001.SZ     # 深交所
```

行为：
- **识别市场并规范化**：`AAPL`/`BRK-B`（美股）、`600519`/`600519.SH`/`000001.SZ`（A股）；
  `600519`、`600519.SH`、`600519.SS` 都归一为 `600519.SH`，不会产生重复行。
- **美股**：本地无 → 建骨架 → 自动抓年度财务（写 `annual_financials.csv`）+ 估值 `pe`/`fcf_yield`。
- **A股（第一阶段）**：本地无 → 仅建骨架，`market=CN`、`currency=CNY`、`review_status=pending_manual_review`，
  **暂不自动抓取财务**（后续阶段支持），数据不足时显示「数据不足（待补录）」而非误判为差公司。
- **无参数 `python main.py` 仍是原批量评分模式，行为完全不变。**

### 数据写入与人工字段保护
- 所有对 `stocks.csv` 的自动写入都经过 `store.py` 的**白名单**：只写机器字段（财务/估值/市场基础信息），
  `moat_score`、`management_score`、`circle_of_competence`、各 `reason`、`risk_note` 等**人工判断字段永不被自动覆盖**，
  建骨架时一律留空、标记待人工补录。每次写入前自动备份 `data/stocks_backup_*.csv`。
- 新增列 `market` / `currency` / `canonical_ticker` / `review_status` 追加在末尾，**向后兼容**；
  旧 `stocks.csv` 无需手工迁移，运行时自动补全。

> 数据访问层 `store.py` 为将来迁移到 SQLite 预留了统一接口；v2.2.0 仍使用 CSV。

### A股数据支持（v2.4.0，AKShare）

A股（沪/深/北）数据由 `providers/ashare_provider.py` 经 **AKShare** 自动抓取——**可选依赖**：

```bash
# 安装 A股数据支持（可选）
pip install -r requirements-optional.txt

python main.py 600519        # 自动规范化为 600519.SH，抓取 + 算分 + 研究卡片
python main.py 000001.SZ     # 平安银行（银行业，自动套用金融类豁免）
python main.py 300750        # 创业板；688981 科创板
```

- **未安装 akshare 时**：A股自动回退为「数据不足，待补录」，**不会崩、不会误判为差公司**；安装后重跑即可。
- 自动抓取：名称/行业、PE、PB、市值、年度营收/净利润、ROE、毛利率、净利率（写入 `annual_financials.csv`，单位与美股一致）。
- **v2.4.1 关键字段补算（仅用真实 AKShare 字段，不伪造）**：
  - `debt_to_equity` ← 资产负债率换算 `r/(1-r)`（仅非金融；银行/保险/券商留空，避免 D/E>3 误判为高风险）；
  - `free_cash_flow` ← 每股企业自由现金流量 × 推算股本（≈OCF−资本支出，数据源已算）；据此再得 `fcf_positive_years`、`fcf_yield`；
  - **ROIC 无可靠口径 → 一律留空，绝不乱算**。
- **拿不到的字段不造假、直接留空**进 `missing_fields`。普通 A股若仍缺 ROIC：**显示部分机器财务分，但研究优先级最高只到「中研究优先级（数据不完整）」**，绝不误判为差公司。
- **银行/保险/券商**：自动识别为特殊金融类，不硬套普通企业 FCF Yield / 毛利率 / ROIC，缺数据进入「待补录」而非判差。
- AKShare 全部调用封装在 `providers/ashare_provider.py`，`main.py` 只按市场分发，不出现 akshare 代码。
- **数据口径自动刷新（v2.4.2）**：`stocks.csv` 的机器列 `data_rev` 记录 A股数据抓取口径版本。升级后若该列为空/旧（`< ASHARE_DATA_REV`），下次查询会**自动用 AKShare 重新抓取**该股（补上 D/E、FCF 等新口径字段），而非沿用旧的不完整数据；抓取成功后 `data_rev` 置为当前版本，之后不再重复抓。未装 akshare 或抓取失败时**只提示「待刷新」、保留现有数据，不清空、不崩**。
- 所有 A股写入仍经 `store.py` 白名单：**绝不覆盖** `moat_score`/`management_score`/`circle_of_competence`/`notes`/各 `reason`/`risk_note` 等人工字段。`notes` 为**纯人工备注列**（机器与 AI 都不可写）。

---

## 自动研究卡片（v2.3.0-alpha1）

`python main.py <ticker>` 现在输出一张**研究卡片**，把三类信息明确分区，避免误导：

```
【机器财务评分（客观，满分75）】   ← 纯财务计算：生意质量/成长/负债/估值
【质化评分（人工权威 / AI暂定）】   ← 护城河/管理层/风险
【研究优先级】高/中/低             ← 仅研究排序，非买卖建议
```

要点：
- **机器财务分与质化分分区展示**：机器分只来自客观财务数据；质化分（护城河/管理层）来自人工，
  人工为空时显示「未评估」，**绝不把缺失当成 0 分判差**。
- 美股查询时自动补全基础信息（`long_name` / `sector` / `industry` / `country`）。
- **AI 初步质化判断（v2.3.0-alpha2，HeuristicProvider 纯规则，零依赖）**：查询单只股票时，
  `ai_analysis.py` 用财务指标生成**护城河 / 管理层（0–10）暂定分 + 风险标记 + 置信度 + 待补证据**，
  写入 `ai_*` 字段。要点：
  - 置信度**封顶 0.5**，措辞一律「可能 / 待证实 / 需人工复核」，**不写确定结论**；
  - **数据不足时不编分**（`ai_confidence=0`，moat/management 留空）；
  - AI **只能写** `ai_*` 字段（经 `store.update_ai_fields` 白名单），
    **永远不会覆盖** `moat_score` / `management_score` / `circle_of_competence` / `notes` / 各 `reason` / `risk_note` 等人工字段；
  - **人工字段有值时人工优先**，AI 仅在旁边标注「AI 暂定，非人工确认」作参考；
  - 真正的 LLM 后端是 alpha4 的可选功能，本阶段不接 LLM、不引入新依赖。
- 数据不足的股票卡片显示「数据不足（待补录）」，不输出任何公司质量结论。
- 全程不输出买入/卖出/持有建议。

### 研究优先级 research_priority（v2.3.0-alpha3）

卡片底部用「机器财务分 + AI 暂定 + 人工状态 + 数据完整度」合成**研究优先级**（仅研究排序，**非买卖建议**）：

| 取值 | 条件 |
|------|------|
| **高研究优先级（人工确认）** | 机器财务分高 **且** 人工护城河/管理层已补录并支持 |
| **高研究优先级（待人工复核）** | 机器财务分高、数据完整、AI 暂定无重大风险，但人工未确认——**醒目标注「AI 暂定，非人工确认，不能作为投资建议」** |
| **中研究优先级** | 机器分中等；或机器分高但 AI 有风险提示/质化未确认/人工质化偏弱 |
| **低研究优先级** | 机器财务分偏弱 |
| **数据不足，待补录** | A股骨架或关键财务字段缺失（不参与高/中/低排序） |
| **超出能力圈** | `circle_of_competence=outside`，即使机器分高也提示谨慎 |

要点：**人工字段优先**（人工已评则人工决定档位，AI 仅参考）；AI 只能把股票推到"高（待人工复核）"，**不能写成人工确认**；数据不足股票**永不**进入高优先级。

### 研究卡片落盘
查询单只股票时，卡片自动保存到 `research_notes/<canonical>_card.md`（如 `MSFT_card.md`、`600519.SH_card.md`）。
这些自动生成文件已在 `.gitignore` 忽略（`research_notes/*_card.md`），仅保留 `research_notes/.gitkeep` 与手写的 `*_report.txt`。批量模式不落盘卡片。

---

## 网页版（Web MVP，Streamlit）

除命令行外，提供一个网页入口（输入代码 → 点按钮 → 生成研究卡片）。CLI 与网页**共用同一核心** `research_service.run_research()`，不复制逻辑。

```bash
# 安装网页依赖（akshare 仍为可选，不在此）
pip install -r requirements-web.txt

# 可写模式（本地：本地无该代码时会自动抓取并建档，经 store 白名单，不覆盖人工字段）
streamlit run web_app.py

# 只读模式（不抓取、不建骨架、不写任何本地文件）
WEB_READONLY=1 streamlit run web_app.py
```

默认地址 http://localhost:8501 。页面展示：标的/市场/数据来源、机器财务分(/75)、研究优先级（标注「研究优先级，不是买卖建议」）、AI 初判（标注「AI 暂定，非人工确认」）、缺失字段（显示「待补录」）、核心财务数据、研究卡片全文与 Markdown 下载。

- **只读模式 `WEB_READONLY=1`**：本地没有该代码时显示「本地暂无数据，当前为只读模式」，**绝不抓取/建骨架/写盘**。
- 第一版**只读展示**，无人工字段编辑入口；全程不输出买入/卖出/持有建议。

> 云端部署注意：CSV 在临时文件系统会随重启丢失，需 persistent disk 或迁移到 SQLite/Postgres；多用户写入需加锁。详见项目设计说明。

---

## 文件结构

```
munger_screener/
├── data/
│   ├── stocks.csv                # 人工判断字段（护城河/管理层/能力圈等）
│   └── annual_financials.csv     # 自动抓取的年度财务数据
├── output/
│   ├── munger_score_result.csv   # 完整评分结果
│   ├── research_candidates.csv   # 研究候选股
│   ├── manual_review_needed.csv  # 人工字段缺失报告
│   ├── manual_fill_template.csv  # 人工补录模板（CSV 格式）
│   └── manual_fill_template.xlsx # 人工补录模板（Excel 格式，推荐）
├── ticker_resolver.py    # v2.2.0：识别市场(US/CN)+规范化代码
├── store.py              # v2.2.0：CSV 数据访问层 + 人工/机器/AI 三类白名单
├── research_service.py   # v2.5.0：核心研究流水线（CLI 与 Web 共用，run_research）
├── web_app.py            # v2.5.0：Streamlit 网页入口（调 run_research，只读展示）
├── requirements-web.txt  # 网页依赖（streamlit）
├── research_card.py      # v2.3.0：自动研究卡片（机器财务分/质化分分区展示）
├── ai_analysis.py        # v2.3.0：AI 质化初判（HeuristicProvider 纯规则，只写 ai_*）
├── providers/
│   └── ashare_provider.py # v2.4.0：A股数据源（AKShare，可选依赖，唯一调用处）
├── requirements-optional.txt # 可选依赖：akshare（A股数据支持）
├── add_stocks.py         # 向 stocks.csv 添加新股票框架（委托 store）
├── fetcher.py            # 自动抓取年度财务数据（yfinance）
├── manual_review_helper.py # 人工字段缺失检查 + Excel 模板生成/导入
├── preflight.py          # 运行前自检（依赖/目录/文件/列）
├── main.py               # 主评分程序 + 按需查询
├── scorer.py             # 评分引擎（各维度打分逻辑）
├── validator.py          # 数据校验 + 最终决策逻辑
└── financial_analyzer.py # 年度财务数据计算（5年均值/趋势）
```

---

## 完整使用流程

> 下列命令统一写作 `python`，Mac/Linux 用户请改用 `python3`，Windows 用户可用 `py`（见文末「环境与依赖安装」）。
> 首次运行前建议先执行 `python preflight.py` 做一次环境自检。

### 第一步：添加股票

```bash
python add_stocks.py GOOGL MA COST ADBE CRM JPM BRK-B
```

将新 ticker 写入 `data/stocks.csv`，财务字段留空，人工判断字段填入保守默认值。  
**此时评分不完整**，需要后续步骤补充数据。

---

### 第二步：自动抓取年度财务数据

```bash
# 抓取指定股票
python fetcher.py GOOGL MA COST

# 抓取 stocks.csv 里全部股票
python fetcher.py --watchlist
```

从 Yahoo Finance（yfinance）下载最近 4–5 年的年度财报数据，写入 `data/annual_financials.csv`。  
**绝对不修改 stocks.csv**。

---

### 第三步：补充 PE / FCF Yield 估值数据

```bash
# 补充指定股票
python fetcher.py --valuation GOOGL MA COST ADBE CRM JPM BRK-B

# 补充全部股票
python fetcher.py --valuation --watchlist

# 强制覆盖已有值
python fetcher.py --valuation --force GOOGL
```

PE 优先使用 `trailingPE`，回退 `forwardPE`；FCF Yield = `freeCashflow / marketCap`。  
**只更新 `pe` 和 `fcf_yield` 两个字段**，其余字段不动，写入前自动备份 `stocks.csv`。

> 银行（如 JPM）的 `freeCashflow` 在 yfinance 中通常缺失，这是正常现象，见下文"行业特殊规则"。

---

### 第四步：检查人工字段缺失

```bash
python manual_review_helper.py
```

扫描 `stocks.csv` 中以下字段是否为空、`0`、`Unknown` 或 `manual_pending`：

```
industry / brand_score / switching_cost_score / network_effect_score
scale_advantage_score / pricing_power_score / moat_durability_score
management_score / circle_of_competence / confidence_score
risk_note / moat_reason / management_reason / risk_reason / pe / fcf_yield
```

输出结果写入 `output/manual_review_needed.csv`，并在终端打印摘要。

> **v2.1.0：缺失 ≠ 公司差。** `manual_review_needed.csv` 现包含清晰的可操作列：
> `ticker, company, missing_fields, missing_count, suggested_action, reason`。
> `suggested_action` 会区分「打开 Excel 模板补录人工字段」与「运行 `fetcher.py --valuation` 获取估值」；
> `reason` 明确说明这些字段只是**待补录数据，不代表公司质量差**。
> 同时生成的 Excel 模板会把**待填单元格高亮为浅红**，已有值保留原色、不会被清空覆盖。

---

### 第五步：生成 Excel 补录模板

```bash
python manual_review_helper.py --template
```

同时生成：
- `output/manual_fill_template.csv`（程序兼容格式，英文字段名）
- `output/manual_fill_template.xlsx`（推荐使用，中文表头 + 颜色分组 + 下拉菜单）

Excel 模板特性：
- **第 2 行**：中文表头（股票代码 / 行业 / 品牌强度…）
- **第 3 行**：隐藏的英文字段名（导入时使用，用户无需关心）
- 评分字段（0–10）、能力圈（inside/edge/outside）、风险标签均有下拉菜单
- 冻结前 3 行和前 3 列，方便横向滚动时始终看到股票名和行业
- `字段说明` Sheet 包含每个字段的填写说明

---

### 第六步：填写模板并导入

打开 `output/manual_fill_template.xlsx`，填写空白字段，保存后运行：

```bash
python manual_review_helper.py --apply-xlsx-template
```

导入规则：
- 只更新模板中**非空**的字段，空单元格不会覆盖 `stocks.csv` 已有值
- 导入前自动备份 `stocks.csv`（`data/stocks_backup_YYYYMMDD_HHMMSS.csv`）
- 按 ticker 匹配，不在 `stocks.csv` 中的 ticker 会提示跳过
- 导入完成后自动重新运行缺失检查，刷新 `manual_review_needed.csv`

---

### 第七步：运行评分

```bash
# 完整评分（所有股票）
python main.py

# 深度分析单只股票
python main.py AAPL
```

程序流程：
1. 读取 `stocks.csv`（人工判断字段）
2. 读取 `annual_financials.csv` 并计算 5 年均值、趋势
3. 将年度财务指标注入每只股票行（只覆盖量化财务字段，不动人工判断字段）
4. 评分 + 数据校验 + 最终决策
5. 输出 `output/munger_score_result.csv` 和 `output/research_candidates.csv`
6. 打印前 10 名排行榜

---

## 核心数据文件说明

### `data/stocks.csv`

存储**需要人工判断**的字段。这些字段反映分析师的主观研判，程序不会自动覆盖。

| 字段 | 说明 | 来源 |
|------|------|------|
| `ticker / name / industry` | 基本信息 | 人工填写 |
| `moat_score` | 护城河总分（0–10） | 人工打分 |
| `brand_score` … `moat_durability_score` | 护城河六项拆分（0–10 各） | 人工打分 |
| `moat_reason` | 护城河理由（文字） | 人工填写 |
| `management_score` | 管理层评分（0–10） | 人工打分 |
| `management_reason` | 管理层理由（文字） | 人工填写 |
| `circle_of_competence` | 能力圈：inside / edge / outside | 人工填写 |
| `confidence_score` | 数据可信度（0–10） | 人工填写 |
| `risk_note` | 风险短标签（high valuation / regulatory risk…）| 人工填写 |
| `risk_reason` | 风险详细说明 | 人工填写 |
| `pe / fcf_yield` | 估值数据 | 人工填写或 `fetcher.py --valuation` |
| `roe_5y_avg` 等财务字段 | 初始手填，`main.py` 运行时由 annual_financials 自动覆盖 | 优先自动 |

### `data/annual_financials.csv`

由 `fetcher.py` **全自动生成**，存储每只股票每年的财务数据。  
`main.py` 运行时读取此文件，计算 5 年均值和趋势后注入评分流程。

| 字段 | 说明 |
|------|------|
| `ticker / year` | 股票代码 + 财年 |
| `revenue / net_income / free_cash_flow` | 营收 / 净利润 / 自由现金流（十亿美元）|
| `roe / roic / gross_margin / net_margin` | 各项比率（%）|
| `debt_to_equity` | 负债权益比 |
| `source / updated_at` | yfinance / 抓取日期 |
| `data_warning` | 自动检测到的数据异常（如近似ROIC、缺字段等）|

**两个文件的核心区别：**

```
stocks.csv          = 人的判断（护城河/管理层/能力圈/风险）+ 少量估值数据
annual_financials.csv = 机器从财报抓取的量化财务历史数据

main.py 在运行时把两者合并：
  年度财务数据 → 覆盖量化财务字段（ROE均值/趋势等）
  stocks.csv   → 保留所有人工判断字段（永不覆盖）
```

---

## 行业特殊规则

银行、保险、综合控股公司的财务口径与普通工业/消费企业不同，用普通指标评价会产生误导性警告。

### 银行（如 JPM）

| 指标 | 为什么不适用 |
|------|-------------|
| **毛利率（gross_margin）** | 银行主营利差和手续费，没有商品成本概念，毛利率无意义 |
| **自由现金流 / FCF Yield** | 银行的现金流由贷款发放和回收驱动，不能用制造业口径的 FCF 评价 |
| **FCF 正数年数** | 同上，yfinance 的 `freeCashflow` 对银行通常缺失或无参考价值 |

银行应重点关注：**ROE、净息差（NIM）、资本充足率（CET1）、坏账率（NPL）、存贷比**。

### 保险公司（如 AIG、MET）

| 指标 | 为什么不适用 |
|------|-------------|
| **毛利率** | 保险的收入是保费，成本是赔付，会计口径与普通企业根本不同 |
| **FCF Yield** | 保险公司的现金流受准备金释放节奏影响，不宜直接与市值对比 |

保险应重点关注：**综合赔付率（Combined Ratio）、内含价值（EV）、偿付能力充足率**。

### 综合控股公司（如 BRK-B）

| 指标 | 为什么不适用 |
|------|-------------|
| **毛利率** | 伯克希尔等控股公司同时持有制造、保险、铁路、能源资产，合并报表毛利率没有行业可比性 |

综合控股应重点关注：**账面价值增长率、各子业务分部盈利、浮存金成本**。

### 程序内处理方式

当 `industry` 字段包含"银行"/"保险"/"综合控股"（或 ticker 命中内置映射，如 `JPM`→银行、`BRK-B`→综合控股）时：

- `manual_review_helper.py`：不再将上述字段标记为"人工缺失待补录"
- `validator.py`：不生成"关键字段缺失"警告，改为输出行业专项提示
- `main.py`：过滤 `annual_financials` 中不适用的 `缺少gross_margin` 警告

> **评分主逻辑不变**。`scorer.py` 仍按同一公式打分，行业规则只影响警告和缺失提示，不改变分数计算。

---

## 环境与依赖安装

### 1. Python 版本

需要 **Python 3.7+**（开发环境为 3.14）。

> **关于 `python` 还是 `python3`？**
> 本文档命令统一写作 `python`。请按你的系统替换：
>
> | 系统 | 运行命令 | 安装依赖 |
> |------|----------|----------|
> | **macOS / Linux** | `python3 main.py` | `pip3 install ...` 或 `python3 -m pip install ...` |
> | **Windows** | `python main.py` 或 `py main.py` | `py -m pip install ...` |
>
> 不确定时，先运行 `python3 --version`（Mac/Linux）或 `py --version`（Windows）确认。

### 2. 安装依赖

```bash
# 推荐：先建虚拟环境（隔离依赖，避免污染系统 Python）
# macOS / Linux:
python3 -m venv .venv && source .venv/bin/activate
# Windows (PowerShell):
py -m venv .venv ; .\.venv\Scripts\Activate.ps1

# 安装全部依赖（pandas / yfinance / openpyxl）
pip install -r requirements.txt
```

各依赖用途：

| 依赖 | 必需性 | 用途 |
|------|--------|------|
| `pandas` | **必需** | `main.py` 评分与所有 CSV 读写 |
| `yfinance` | 可选 | 仅 `fetcher.py` 自动抓取财务数据时需要 |
| `openpyxl` | 可选 | 仅 `manual_review_helper.py` 生成/导入 Excel 模板时需要 |

### 3. 运行前自检（推荐）

正式评分前，可先跑一次自检，确认依赖、目录、数据文件、必要列都就绪：

```bash
python preflight.py
```

- 列出**致命问题**（缺 `pandas` / 缺 `data/` / 缺 `stocks.csv` / 缺 `ticker` 列）并给出解决命令；
- 提示**非致命问题**（如未装 `yfinance`、未生成 `annual_financials.csv`），不影响评分本身；
- `main.py` 启动时也会自动执行同样的自检，遇到致命问题会清晰退出。

> 自检是**纯只读**的，不会修改任何数据文件。

---

## 常见问题

**Q：fetcher.py 抓取失败怎么办？**  
yfinance 偶尔超时，重试一次通常可以解决。已有 `annual_financials.csv` 的股票不受影响，`main.py` 仍可正常评分。

**Q：新股票的评分很低/为 0 怎么办？**  
新股票尚未填入护城河和管理层分数（默认为 0），导致评分偏低。按照流程第四到第六步补录人工判断字段后，评分会恢复正常。

**Q：最终决策出现"数据不足（待补录）"是什么意思？**  
这是 v2.1.0 新增状态，用于**区分"数据缺失"和"公司质量差"**。当 PE、FCF Yield、ROIC、ROE、营收增速、D/E 等关键量化字段**缺失 ≥2 项**时，程序不会把它当成差公司死扣分，而是标记为待补录，引导你去 `manual_review_needed.csv` 按 `suggested_action` 补齐数据（如运行 `fetcher.py --watchlist`），补齐后重新 `python main.py` 即可得到真实评分。  
`munger_score_result.csv` 和 `research_candidates.csv` 也新增了 `data_status` / `missing_fields` / `reason` 列，方便你看出"为什么值得研究、哪里还缺数据"。

**Q：数据不足的股票，详情页会显示"质量很差"吗？**  
**不会。** v2.2.0-alpha2 起，决策为"数据不足（待补录）"的股票进入**待补录展示模式**：只显示股票代码、市场、币种、数据状态、缺失字段、下一步建议和免责声明，**绝不输出"生意质量差 / 护城河薄弱 / 估值偏高 / 管理层差"等任何质量结论**（这些只是字段缺失被当成 0 分，并非真实判断）。这类股票也**不参与行业排名**，详情页显示"数据不足，暂不参与有效排名"。空字段统一显示为"未填写"。补齐数据后重新运行即可得到正常的完整分析。

**Q：为什么同一只股票有时是"自动"模式有时是"手动"模式？**  
`annual_financials.csv` 中有该 ticker 的年度数据时使用自动计算；没有时回退到 `stocks.csv` 中手填的财务数据。运行 `fetcher.py --watchlist` 可以为所有股票生成年度数据。

**Q：`stocks.csv` 里的财务字段（如 roe_5y_avg）还需要手填吗？**  
不需要。`main.py` 运行时会用 `annual_financials.csv` 的计算值覆盖这些字段。只有在 `fetcher.py` 从未运行过、没有年度数据时，才会回退读取手填值。

**Q：BRK-B 的毛利率警告能关掉吗？**  
已经自动处理。检测到 `综合控股` 行业后，`缺少gross_margin` 警告会被过滤，同时输出"综合控股财务口径特殊"的说明提示。

**Q：运行报 `ModuleNotFoundError: No module named 'pandas'` 怎么办？**  
说明当前 Python 环境没装依赖。运行 `pip install -r requirements.txt`（Mac/Linux 用 `pip3`，Windows 用 `py -m pip`）。若用了虚拟环境，记得先激活再运行。可先用 `python preflight.py` 确认到底缺哪个依赖。

**Q：Windows 下中文输出乱码或报 `UnicodeEncodeError` 怎么办？**  
程序已在启动时把标准输出重配为 UTF-8。若仍异常，可在运行前执行 `chcp 65001` 切换控制台代码页，或使用 Windows Terminal / VS Code 终端。

**Q：提示找不到 `stocks.csv` / 缺少 `ticker` 列怎么办？**  
请确认在**项目根目录**运行命令，且 `data/stocks.csv` 存在。新建数据用 `python add_stocks.py AAPL MSFT`。`python preflight.py` 会明确指出缺哪个文件或哪一列。

**Q：`data/stocks.csv` 用 Excel 另存后第一列读不出来？**  
这是 UTF-8 BOM 导致的。本工具读取时已用 `utf-8-sig` 兼容 BOM；若仍异常，请用「UTF-8（不含 BOM）」重新另存。

---

## 数据流示意

```
yfinance (Yahoo Finance)
        │
        ▼
  fetcher.py --watchlist          →  data/annual_financials.csv
  fetcher.py --valuation          →  data/stocks.csv (pe / fcf_yield only)
        │
        ▼
  manual_review_helper.py         →  output/manual_review_needed.csv
  manual_review_helper.py --template  →  output/manual_fill_template.xlsx
        │
        │  ← 人工填写 Excel 模板
        ▼
  manual_review_helper.py --apply-xlsx-template  →  data/stocks.csv (人工字段)
        │
        ▼
  main.py
    读取 stocks.csv + annual_financials.csv
    ↓ 合并 → 评分 → 校验 → 行业对比
    ↓
  output/munger_score_result.csv
  output/research_candidates.csv
```
