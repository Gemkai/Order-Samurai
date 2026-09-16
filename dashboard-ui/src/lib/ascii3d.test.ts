import { describe, it, expect } from "vitest"
import { getGlyphDepth, parseAsciiGrid } from "./ascii3d"

describe("ascii3d 3D glyph depth & matrix parsing", () => {
  it("returns depth 0 for space and braille empty char", () => {
    expect(getGlyphDepth(" ")).toEqual({ depth: 0, weight: 0 })
    expect(getGlyphDepth("⠀")).toEqual({ depth: 0, weight: 0 })
  })

  it("calculates braille dot density correctly for 3D extrusion", () => {
    // Full braille dot character ⣿ (U+28FF -> 8 active dots)
    const fullDots = getGlyphDepth("⣿")
    expect(fullDots.weight).toBe(1.0)
    expect(fullDots.depth).toBe(1.0)

    // Partial braille dot character ⡿ (7 dots active)
    const partialDots = getGlyphDepth("⡿")
    expect(partialDots.weight).toBe(7 / 8)
  })

  it("parses multi-line ASCII art grid into 3D point cloud", () => {
    const art = "⠀⢸⣦⡀⠀\n⠀⣿⡇⠀"
    const grid = parseAsciiGrid(art)

    expect(grid.numRows).toBe(2)
    expect(grid.numCols).toBe(5)
    expect(grid.points.length).toBeGreaterThan(0)

    const firstPoint = grid.points[0]
    expect(firstPoint).toHaveProperty("x")
    expect(firstPoint).toHaveProperty("y")
    expect(firstPoint).toHaveProperty("z")
    expect(firstPoint).toHaveProperty("weight")
    expect(firstPoint).toHaveProperty("char")
  })
})
