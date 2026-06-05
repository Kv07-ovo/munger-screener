// Cat submit button — the pixel cat acts as the "analyze" affordance (matches kv_1).
// Asset copied to web_frontend/public/pixel_cat_cutout.png (original assets/ untouched).
interface Props {
  loading?: boolean
}

export default function CatButton({ loading }: Props) {
  return (
    <button type="submit" className="cat-btn" aria-label="分析" disabled={loading}>
      <img src="/pixel_cat_cutout.png" alt="" className="cat-img" />
    </button>
  )
}
