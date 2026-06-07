# Kv的选股小猫 — Web 前端（React + Vite + TS）

前后端分离迁移的最小闭环骨架。后端见 `../api`（FastAPI，缺失时自动回退 Starlette）。

## 运行

后端（项目根目录）:

```bash
uvicorn api.main:app --reload --port 8000
# 健康检查: http://localhost:8000/health
# 研究接口: http://localhost:8000/api/research?ticker=AAPL
```

前端（本目录）:

```bash
npm install
npm run dev        # http://localhost:5173
```

API 地址通过 `VITE_API_BASE_URL` 配置（见 `.env.example`），默认 `http://localhost:8000`。

## 现状

- 仅最小闭环：首页输入 ticker → 调 `/api/research` → 显示 basic result / error / loading。
- 视觉对齐 `移动端7/kv_1`（移动）与 `桌面端6/kv_3`（桌面）的设计 token（见 `src/styles/tokens.css`）。
- 未做：完整结果页、深色模式、i18n、登录、收藏、数据库（均属后续阶段）。
- Streamlit 旧版（`../web_app.py`）保留不动。

`public/pixel_cat_cutout.png` 由 `../assets/pixel_cat_cutout.png` 复制而来（原文件未改）。

## 前端验收（build / preview / 手动冒烟）

### 1. 构建验证

```bash
npm run build
```

`build` = `tsc`（TypeScript 类型检查）+ `vite build`（生产构建到 `dist/`）。类型错误或构建失败都会非零退出。

### 2. 生产产物预览

```bash
npm run build
npm run preview      # 本地预览 dist/ 生产产物（非 dev server）
```

`preview` 用打包后的产物起一个本地静态服务，验证「生产构建」而非「开发热更新」下的真实表现。需后端在 `VITE_API_BASE_URL` 指向的地址可用。

### 3. 手动冒烟清单

逐项过一遍（需后端在 8000 运行；移动端用浏览器开发者工具切到 ≤680px 视口）：

- [ ] 首页 idle 态：Hero + 搜索栏 + 示例 chips 正常
- [ ] 正常结果态：搜索 `AAPL` 或 `MSFT`，出现评分主叙事 + 右侧参考数据
- [ ] pending / 部分数据态：搜索数据部分缺失的 ticker，徽章显示「部分字段缺失」
- [ ] invalid_ticker：输入明显非法代码（如 `ZZZ!!!`），显示「没找到股票代码」+ 示例 chips
- [ ] insufficient_data：搜索数据不足的 ticker，显示「暂时没有足够数据」
- [ ] 连接出错：关闭后端，或把 `VITE_API_BASE_URL` 指向不可达地址，显示「连接出错」卡
- [ ] 服务器出错（`state='error'`）：属后端故障注入场景，**仅在本地 / 测试环境**模拟（如让后端 `run_research` 抛错），显示「服务器处理出错」卡；**不要在生产环境制造错误**
- [ ] loading 态：搜索后出现「小猫正在分析…」骨架
- [ ] loading 锁定：分析进行中输入框禁用、回车不会重复提交、猫咪按钮禁用
- [ ] 竞态：快速连续搜索不同 ticker，最终结果以**最后一次**搜索为准（旧响应不覆盖新结果）
- [ ] 移动端：视口 ≤680px 时切换到移动结果视图，布局正常
- [ ] 深链：访问 `?q=AAPL`（如 `http://localhost:5173/?q=AAPL`）进入即自动查询一次

> 本清单只描述验收方法，不修改任何前端代码。
