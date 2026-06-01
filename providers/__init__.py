# providers/  —  数据源 provider 层（v2.4.0）
#
# 每个 provider 封装一个市场/数据源的抓取逻辑，对上层暴露统一结果结构。
# main.py 只按市场分发，不直接调用任何数据源 SDK。
#
#   ashare_provider —— A股，使用 AKShare（可选依赖）
#   （美股仍由根目录 fetcher.py / yfinance 负责）
