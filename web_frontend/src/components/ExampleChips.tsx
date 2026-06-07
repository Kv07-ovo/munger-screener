interface Props {
  tickers: string[]
  onPick: (t: string) => void
  disabled?: boolean
}

// Horizontal small pills (kv_3 chips). Wraps on narrow screens; never full-width buttons.
export default function ExampleChips({ tickers, onPick, disabled }: Props) {
  return (
    <div className="chips">
      {tickers.map((t) => (
        <button key={t} type="button" className="chip" onClick={() => onPick(t)} disabled={disabled}>
          {t}
        </button>
      ))}
    </div>
  )
}
