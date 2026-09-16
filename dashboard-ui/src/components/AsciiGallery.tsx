import { Ascii3DPlayground } from "./Ascii3DPlayground"

export function AsciiGallery({ onBack }: { onBack?: () => void }) {
  return <Ascii3DPlayground onClose={onBack} />
}


