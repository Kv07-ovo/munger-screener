// Top bar: product name left, 说明/设置 as quiet text links right (matches 桌面端6/kv_3).
// Static for this skeleton round (no popover/i18n yet, per Phase 7).
export default function Header() {
  return (
    <header className="app-header">
      <span className="brand">Kv的选股小猫</span>
      <nav className="app-nav">
        <button type="button" className="nav-link">说明</button>
        <button type="button" className="nav-link">设置</button>
      </nav>
    </header>
  )
}
