# Web Beta 渲染回归（unittest，纯内存、不联网、不碰真实 data/）
#
# 机制：在 import web_app 前向 sys.modules 注入一个 Fake Streamlit（记录所有 st.* 调用），
#       并把 web_app.compute_all_metrics 打桩为返回 {}，从而：
#         - 无需真装 streamlit；
#         - 不读真实年度财务数据、不联网；
#         - 可断言 render_result 的章节/指标渲染顺序与三态兜底。
# 仅当本机装有 pandas 时运行（web_app -> research_service 需 pandas）；否则整文件 skip。
import os
import sys
import re
import importlib
import importlib.util
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAS_PANDAS = importlib.util.find_spec("pandas") is not None
HAS_PIL = importlib.util.find_spec("PIL") is not None
_TRANSPARENT_CAT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "pixel_cat_cutout.png")


class _Ctx:
    """st.expander / st.form / st.spinner 的上下文管理器替身。"""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Surface:
    """streamlit 顶层 st 或单个 column 的替身：把所有调用记录进共享的 calls 列表。"""
    def __init__(self, rec):
        object.__setattr__(self, "_rec", rec)

    # 上下文管理器类
    def expander(self, *a, **k):
        self._rec.append(("expander", a, k)); return _Ctx()

    def form(self, *a, **k):
        self._rec.append(("form", a, k)); return _Ctx()

    def spinner(self, *a, **k):
        self._rec.append(("spinner", a, k)); return _Ctx()

    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        self._rec.append(("columns", (spec,), k))
        # 返回 _CtxSurface 以支持 `with col:`（桌面两栏）与列上方法调用
        return [_CtxSurface(self._rec) for _ in range(n)]

    # 有返回值的控件（必须给出合理默认，否则 import 期页面主体会出错）
    def text_input(self, *a, **k):
        self._rec.append(("text_input", a, k)); return ""

    def form_submit_button(self, *a, **k):
        self._rec.append(("form_submit_button", a, k)); return False

    def button(self, *a, **k):
        self._rec.append(("button", a, k)); return False

    def container(self, *a, **k):
        # Gamma 卡片化用 with st.container(border=True[, key=...]):；返回可 with 且可记录子调用的 surface
        self._rec.append(("container", a, k)); return _CtxSurface(self._rec)

    def popover(self, *a, **k):
        # Header 的 说明/设置 用 with st.popover(...): —— 返回可 with 且可记录的 surface
        self._rec.append(("popover", a, k)); return _CtxSurface(self._rec)

    # 其它 st.* / col.* 一律泛化为记录器，返回 None
    def __getattr__(self, name):
        rec = object.__getattribute__(self, "_rec")

        def _call(*a, **k):
            rec.append((name, a, k))
            return None
        return _call


class _CtxSurface(_Surface):
    """既能 with（上下文管理器）又能记录子调用的 surface，供 st.container 卡片化使用。"""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_fake_st():
    calls = []
    fake = _Surface(calls)
    # session_state 必须是真 dict（import 期页面主体会用 setdefault/pop/get）
    object.__setattr__(fake, "session_state", {})
    return fake, calls


_CACHE = {}


def _load_web_app():
    """注入 Fake Streamlit 后导入 web_app；返回 (web_app, calls)。缓存以复用。"""
    if "mod" not in _CACHE:
        fake, calls = _make_fake_st()
        sys.modules["streamlit"] = fake
        sys.modules.pop("web_app", None)
        web_app = importlib.import_module("web_app")
        web_app.compute_all_metrics = lambda *a, **k: {}   # 不读真实年度数据
        _CACHE["mod"] = web_app
        _CACHE["calls"] = calls
    return _CACHE["mod"], _CACHE["calls"]


def _stub_res(ai="normal", pending=False):
    result = {
        "ticker": "DEMO", "long_name": "示例公司", "name": "示例", "market": "US", "currency": "USD",
        "data_source": "年报", "data_date": "2026-06-01",
        "final_decision": "数据不足（待补录）" if pending else "加入观察池",
        "data_status": "待补录" if pending else "完整",
        "total_score": 80.0, "quality_score": 27, "growth_score": 13,
        "balance_sheet_score": 14, "valuation_score": 4,
        "pe": "22", "pb": "8", "market_cap": "2000",
        "missing_fields": "市盈率PE、ROIC(5年均值)" if pending else "（无）",
        "final_score_preview": 80.0,
        # 旧持久化 ai_*（初判，与本次 ai_dynamic 不同）
        "ai_model": "heuristic-v1", "ai_moat_score": "7", "ai_management_score": "6",
        "ai_confidence": "0.4", "ai_reason": "毛利率/ROIC 等特征…", "ai_evidence_needed": "年报…",
        "needs_human_review": "true",
    }
    if ai == "normal":
        result["ai_dynamic"] = {
            "ai_score": 91.0, "ai_rating": "质优", "ai_reasoning": "基于可用指标的确定性映射…",
            "key_strengths": [{"point": "ROE 数据上偏强", "evidence_metric": "roe_5y_avg"}],
            "key_risks": [{"point": "市盈率 数据上偏弱", "evidence_metric": "pe"}],
            "missing_data_warnings": [], "confidence": 0.5, "needs_human_review": True,
        }
    elif ai == "insufficient":
        result["ai_dynamic"] = {
            "ai_score": None, "ai_rating": "数据不足", "ai_reasoning": "数据不足，需补录后复评。",
            "key_strengths": [], "key_risks": [], "missing_data_warnings": ["市盈率PE"],
            "confidence": 0.0, "needs_human_review": True,
        }
    elif ai == "none":
        result["ai_dynamic"] = None
    return {
        "result": result, "canonical": "DEMO",
        "research_priority": ("高研究优先级（待人工复核）", "机器分高，AI 暂定无重大风险；非买卖建议。"),
        "card_text": "=== 研究卡片 DEMO ===", "warnings": [],
    }


def _render(ai="normal", pending=False):
    web_app, calls = _load_web_app()
    calls.clear()
    web_app.render_result(_stub_res(ai=ai, pending=pending))
    return calls


def _headers(calls):
    return [a[0] for (name, a, k) in calls
            if name == "markdown" and a and isinstance(a[0], str) and a[0].startswith("### ")]


def _metric_labels(calls):
    return [a[0] for (name, a, k) in calls if name == "metric" and a]


def _metric_values(calls):
    return [str(a[1]) for (name, a, k) in calls if name == "metric" and len(a) >= 2]


def _all_text(calls):
    out = []
    for (name, a, k) in calls:
        for x in a:
            out.append(str(x))
    return "\n".join(out)


# Aura Logic 两栏：左主栏先于右辅栏发出 ### 标题。
# 左主栏：评分 → 关键优势 → 主要风险 → 研究优先级；右辅栏：核心财务数据 → 缺失字段 → 完整研究卡片。
_EXPECTED_HEADERS = ["### 评分", "### 关键优势", "### 主要风险", "### 研究优先级",
                     "### 核心财务数据", "### 缺失字段", "### 完整研究卡片"]
_SCORE_PAT = re.compile(r"\d\s*/\s*(100|75)")


@unittest.skipUnless(HAS_PANDAS, "需要 pandas（web_app -> research_service）")
class TestWebRender(unittest.TestCase):

    def test_import_no_crash(self):
        web_app, _ = _load_web_app()
        self.assertTrue(hasattr(web_app, "render_result"))

    def test_section_order(self):
        calls = _render("normal")
        self.assertEqual(_headers(calls), _EXPECTED_HEADERS)
        # 评分区前 5 个 metric 必须是评分主线，顺序固定
        self.assertEqual(_metric_labels(calls)[:5],
                         ["规则总分", "机器财务分", "AI 动态分", "AI 置信度", "最终预览分（实验）"])

    def test_ai_dynamic_normal(self):
        text = _all_text(_render("normal"))
        self.assertIn("ROE 数据上偏强", text)       # key_strengths 已渲染
        self.assertIn("市盈率 数据上偏弱", text)     # key_risks 已渲染
        self.assertIn("质优", text)

    def test_ai_dynamic_none_shows_not_generated(self):
        text = _all_text(_render("none"))
        self.assertIn("AI 动态评分未生成", text)
        # 不应崩，且关键优势/风险走「未生成」兜底
        self.assertNotIn("ROE 数据上偏强", text)

    def test_ai_dynamic_insufficient_distinct_from_failure(self):
        calls = _render("insufficient")
        text = _all_text(calls)
        self.assertIn("数据不足", text)
        self.assertNotIn("AI 动态评分未生成", text)   # 数据不足 != 未生成
        self.assertIn("数据不足", _metric_values(calls)[2] if len(_metric_values(calls)) > 2 else "")

    def test_pending_degrades_scores(self):
        calls = _render("insufficient", pending=True)
        # pending 时不渲染任何 /100、/75 分数 metric
        self.assertFalse(any(_SCORE_PAT.search(v) for v in _metric_values(calls)),
                         _metric_values(calls))
        self.assertIn("数据不足（待补录）", _all_text(calls))

    def test_machine_total_isolation(self):
        web_app, _ = _load_web_app()
        res = _stub_res("normal")["result"]
        mt = web_app._machine_total(res)
        self.assertEqual(mt, 27 + 13 + 14 + 4)
        self.assertLessEqual(mt, 75)
        res["ai_dynamic"] = {"ai_score": 5}      # 改 AI 不应影响机器分
        self.assertEqual(web_app._machine_total(res), mt)

    def test_ai_not_in_preview_sentinel(self):
        import ai_scorer
        self.assertEqual(ai_scorer.AI_WEIGHT, 0.0)
        self.assertEqual(ai_scorer.compute_final_preview(80, 91.0), 80.0)
        self.assertEqual(ai_scorer.compute_final_preview(80, None), 80.0)

    def test_safety_copy_present(self):
        text = _all_text(_render("normal"))
        self.assertIn("不参与正式评分与排序", text)
        self.assertIn("AI 权重 = 0.0", text)
        self.assertIn("≠ 可买入", text)
        self.assertIn("待补录", text)

    def test_cat_asset_is_offline(self):
        # 非谈判约束：小猫资源必须离线——本地 PNG（优先透明版）或内联 SVG（fallback）的 data URI，
        # 绝不用远程 URL。注意 SVG 含命名空间 xmlns="http://www.w3.org/2000/svg"（非网络抓取），
        # 故只锁「远程抓取」特征：https:// / src= / googleusercontent。
        web_app, _ = _load_web_app()
        self.assertTrue(web_app._CAT_DATA_URI.startswith(
            ("data:image/png;base64,", "data:image/svg+xml;base64,")))
        self.assertNotIn("googleusercontent", web_app._CAT_DATA_URI)
        # 读取优先级：用户透明抠图优先、内联 SVG 兜底
        self.assertEqual(web_app._CAT_PNG_CANDIDATES[0], "pixel_cat_cutout.png")
        self.assertTrue(web_app._CAT_PNG_CANDIDATES[-1] != ""
                        and len(web_app._CAT_PNG_CANDIDATES) >= 2)  # 仍有 fallback 候选
        # SVG fallback 本身亦无远程抓取（命名空间 xmlns 除外）
        for needle in ("https://", "googleusercontent", "src="):
            self.assertNotIn(needle, web_app._CAT_SVG)

    @unittest.skipUnless(HAS_PIL and os.path.exists(_TRANSPARENT_CAT),
                         "需要 PIL 且 assets/pixel_cat_cutout.png 存在")
    def test_transparent_cat_has_alpha(self):
        # 防回退：当前小猫必须是带 alpha 的 PNG/WebP，且同时含全透明与全不透明像素（确为抠图、非方底）
        from PIL import Image
        img = Image.open(_TRANSPARENT_CAT)
        self.assertIn(img.format, ("PNG", "WEBP"))
        rgba = img.convert("RGBA")
        alpha = rgba.getchannel("A")
        lo, hi = alpha.getextrema()
        self.assertEqual(lo, 0)     # 有全透明像素（背景已抠掉）
        self.assertEqual(hi, 255)   # 有全不透明像素（猫主体保留）
        # 透明/不透明像素数量均 > 0
        hist = alpha.histogram()
        self.assertGreater(hist[0], 0)     # transparent_pixels > 0
        self.assertGreater(hist[255], 0)   # opaque_pixels > 0
        # web_app 应选中该本地透明文件
        web_app, _ = _load_web_app()
        self.assertEqual(web_app._CAT_ASSET_NAME, "pixel_cat_cutout.png")
        self.assertTrue(web_app._CAT_IS_LOCAL_PNG)

    def test_dark_mode_css_present(self):
        # 自适应深色模式：CSS 必须含 prefers-color-scheme: dark（纯 CSS、无 JS）
        web_app, _ = _load_web_app()
        self.assertIn("prefers-color-scheme: dark", web_app._CSS)

    def test_heading_anchor_icon_hidden(self):
        # 干净感：隐藏 Streamlit 给 heading 自动生成的锚点链条图标（只隐藏 anchor 容器，不动标题文字）
        web_app, _ = _load_web_app()
        self.assertIn('[data-testid="stHeaderActionElements"]', web_app._CSS)
        self.assertIn("display: none", web_app._CSS)

    def test_error_input_dark_text_override(self):
        # P0：深色模式 + 错误态命令栏，浅暖底上输入文字必须可见
        # —— 强制深色文字 + Safari -webkit-text-fill-color + 可见 placeholder + 光标颜色
        web_app, _ = _load_web_app()
        css = web_app._CMDBAR_ERROR_CSS
        self.assertIn(".st-key-mg-cmdbar input", css)
        self.assertIn("-webkit-text-fill-color", css)
        self.assertIn("#111111", css)          # 深色输入文字
        self.assertIn("caret-color", css)       # 光标不消失
        self.assertIn("::placeholder", css)     # placeholder 可见
        self.assertIn("#7A6A5A", css)           # 暖灰 placeholder

    def test_i18n_basic(self):
        # 中英双语：zh 默认、en 可切；未知 key 安全 fallback 不崩
        web_app, _ = _load_web_app()
        self.assertEqual(web_app._t("app_title", "zh"), "Kv的选股小猫")
        self.assertEqual(web_app._t("app_title", "en"), "Kv's Stock Cat")
        self.assertNotEqual(web_app._t("sec_score", "en"), web_app._t("sec_score", "zh"))
        # 未知 key → 返回 key 本身（不抛异常）
        self.assertEqual(web_app._t("___missing_key___"), "___missing_key___")
        # zh 默认下，section 标题/metric label 仍是 test 锁定的中文（防 i18n 改默认）
        self.assertEqual(web_app._t("sec_score"), "评分")
        self.assertEqual(web_app._t("m_rule"), "规则总分")


if __name__ == "__main__":
    unittest.main(verbosity=2)
