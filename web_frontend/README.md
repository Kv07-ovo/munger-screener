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
