import CatButton from './CatButton'

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  loading?: boolean
}

// Single pill: search icon + input + cat button on one row (kv_1). In React this is a
// plain <form> flex container — none of the Streamlit baseui pill-in-pill problems.
export default function SearchBar({ value, onChange, onSubmit, loading }: Props) {
  return (
    <form
      className="cmdbar"
      onSubmit={(e) => {
        e.preventDefault()
        if (loading) return
        onSubmit()
      }}
    >
      <svg className="search-icon" width="20" height="20" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <circle cx="11" cy="11" r="7" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
      <input
        className="cmd-input"
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="输入股票代码，例如 AAPL / 600519.SH"
        autoComplete="off"
        spellCheck={false}
        disabled={loading}
      />
      <CatButton loading={loading} />
    </form>
  )
}
