// Helper functions for 3D ASCII point cloud parsing & Braille depth mapping

export interface Glyph3DPoint {
  x: number
  y: number
  z: number
  weight: number
  char: string
  nx: number
  ny: number
}

export function getGlyphDepth(char: string): { depth: number; weight: number } {
  const code = char.charCodeAt(0)
  if (code === 32 || char === "⠀") {
    return { depth: 0, weight: 0 }
  }
  // Braille Patterns U+2800 - U+28FF
  if (code >= 0x2800 && code <= 0x28FF) {
    const dots = code - 0x2800
    // Count active dots in 8-dot matrix
    let count = 0
    let temp = dots
    while (temp > 0) {
      if (temp & 1) count++
      temp >>= 1
    }
    return { depth: count / 8, weight: count / 8 }
  }
  // Standard block / high density ASCII characters
  if ("█▓▒░#@$W%8&M".includes(char)) return { depth: 0.9, weight: 0.95 }
  if ("*+=-:. ".includes(char)) return { depth: 0.3, weight: 0.3 }
  return { depth: 0.5, weight: 0.5 }
}

export function parseAsciiGrid(artText: string): {
  points: Glyph3DPoint[]
  numRows: number
  numCols: number
} {
  const lines = artText.split("\n")
  const numRows = lines.length
  const numCols = Math.max(...lines.map((l) => l.length), 1)
  const points: Glyph3DPoint[] = []

  for (let r = 0; r < numRows; r++) {
    const line = lines[r]
    for (let c = 0; c < line.length; c++) {
      const char = line[c]
      const { depth, weight } = getGlyphDepth(char)
      if (weight > 0) {
        // Centered 3D coordinates [-1, 1]
        const x = (c / numCols - 0.5) * 2.4
        const y = (0.5 - r / numRows) * 2.4
        // Z depth extrusion: 3D parallax displacement
        const z = (depth - 0.5) * 0.4
        points.push({
          x,
          y,
          z,
          weight,
          char,
          nx: c / numCols - 0.5,
          ny: 0.5 - r / numRows,
        })
      }
    }
  }
  return { points, numRows, numCols }
}
