import { useState, useEffect } from "react"
import AsciiObject from "./canvasui/AsciiObject"
import { Box, Sliders, Sparkles } from "lucide-react"
import { OVERVIEW_ART, BOW_ART, SWORD_ART, BRUSH_ART, ARTS_ART } from "./AsciiBackground"
import { build3DSceneFromAscii, exportSceneToGLBDataUrl } from "./Ascii3DFromText"

interface ModelOption {
  id: string
  name: string
  icon: string
  staticUrl?: string
  art?: string
  color: string
  depth: number
  scale: number
}

const MODEL_OPTIONS: ModelOption[] = [
  { id: "helmet_solid", name: "3D Kabuto Helmet", icon: "🥷", staticUrl: "/models/helmet.glb", color: "#38bdf8", depth: 1.0, scale: 2.8 },
  { id: "sword_solid", name: "3D Katana Sword", icon: "⚔️", staticUrl: "/models/sword.glb", color: "#ef4444", depth: 1.2, scale: 3.2 },
  { id: "bow_solid", name: "3D Yumi Archery Bow", icon: "🏹", staticUrl: "/models/bow.glb", color: "#f59e0b", depth: 0.8, scale: 3.0 },
  { id: "brush_solid", name: "3D Calligraphy Fude Brush", icon: "🖌️", staticUrl: "/models/brush.glb", color: "#ec4899", depth: 0.9, scale: 3.0 },
  { id: "arts_solid", name: "3D Pagoda Shiro Castle", icon: "🏯", staticUrl: "/models/arts.glb", color: "#4ade80", depth: 1.0, scale: 2.5 },
  { id: "duck_solid", name: "3D Duck (Canvas UI Demo)", icon: "🦆", staticUrl: "/models/duck.glb", color: "#facc15", depth: 1.0, scale: 3.5 },
  { id: "overview_ascii", name: "Volumetric Helmet (From ASCII)", icon: "🌀", art: OVERVIEW_ART, color: "#38bdf8", depth: 1.0, scale: 3.0 },
  { id: "sword_ascii", name: "Volumetric Sword (From ASCII)", icon: "🌀", art: SWORD_ART, color: "#ef4444", depth: 1.2, scale: 3.2 },
  { id: "bow_ascii", name: "Volumetric Bow (From ASCII)", icon: "🌀", art: BOW_ART, color: "#f59e0b", depth: 0.8, scale: 3.0 },
  { id: "brush_ascii", name: "Volumetric Brush (From ASCII)", icon: "🌀", art: BRUSH_ART, color: "#ec4899", depth: 0.9, scale: 3.0 },
  { id: "arts_ascii", name: "Volumetric Pagoda (From ASCII)", icon: "🌀", art: ARTS_ART, color: "#4ade80", depth: 1.0, scale: 2.5 },
]

const CHARSET_PRESETS: Array<{ label: string; value: string }> = [
  { label: "Standard ASCII", value: " .:-=+*#%@" },
  { label: "Dense Blocks", value: " ░▒▓█" },
  { label: "Braille Grid", value: " ⠀⡀⡄⡆⡇⣇⣧⣷⣿" },
  { label: "Matrix Code", value: " 0123456789ABCDEF" },
  { label: "Full Printable ASCII", value: Array.from({ length: 95 }, (_, i) => String.fromCharCode(32 + i)).join("") },
]

export function Ascii3DPlayground({ onClose }: { onClose?: () => void }) {
  const [selectedModel, setSelectedModel] = useState<ModelOption>(MODEL_OPTIONS[0])
  const [asciiArtUrl, setAsciiArtUrl] = useState<string>("")
  const [asciiEnabled, setAsciiEnabled] = useState(true)
  const [colored, setColored] = useState(true)
  const [color, setColor] = useState("#38bdf8")
  const [autoRotate, setAutoRotate] = useState(true)
  const [autoRotateSpeed, setAutoRotateSpeed] = useState(1.5)
  const [cellSize, setCellSize] = useState(9)
  const [cellAspect, setCellAspect] = useState(0.55)
  const [contrast, setContrast] = useState(1.5)
  const [edgeContrast, setEdgeContrast] = useState(3.2)
  const [exposure, setExposure] = useState(1.2)
  const [invert, setInvert] = useState(false)
  const [floatIntensity, setFloatIntensity] = useState(1.8)
  const [charsetIndex, setCharsetIndex] = useState(4)

  const activeModelUrl = selectedModel.staticUrl || asciiArtUrl

  useEffect(() => {
    let isMounted = true
    if (selectedModel.art) {
      const colorHex = parseInt(selectedModel.color.replace("#", "0x"), 16)
      const scene = build3DSceneFromAscii(selectedModel.art, colorHex, selectedModel.depth)
      exportSceneToGLBDataUrl(scene).then((url) => {
        if (isMounted) setAsciiArtUrl(url)
      })
    }
    return () => {
      isMounted = false
    }
  }, [selectedModel])

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 100,
        background: "rgba(5, 7, 15, 0.95)",
        backdropFilter: "blur(16px)",
        display: "flex",
        flexDirection: "column",
        color: "#f8fafc",
        fontFamily: '"Geist Variable", system-ui, sans-serif',
      }}
    >
      {/* Top Header Bar */}
      <header
        style={{
          height: 64,
          padding: "0 24px",
          borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "rgba(15, 23, 42, 0.6)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 36, height: 36, borderRadius: 10, background: "rgba(56, 189, 248, 0.15)", border: "1px solid rgba(56, 189, 248, 0.3)", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Box size={20} color="#38bdf8" />
          </div>
          <div>
            <h1 style={{ fontSize: 16, fontWeight: 700, margin: 0, letterSpacing: "0.02em" }}>
              3D Solid & Volumetric ASCII Studio
            </h1>
            <span style={{ fontSize: 11, color: "rgba(255, 255, 255, 0.5)", fontFamily: '"JetBrains Mono", monospace' }}>
              Verified Matching 3D Geometries & WebGL ASCII Shaders · Orbit 360°
            </span>
          </div>
        </div>

        {/* Model Selector Tabs */}
        <div style={{ display: "flex", gap: 6, background: "rgba(0,0,0,0.4)", padding: 4, borderRadius: 12, border: "1px solid rgba(255,255,255,0.08)", overflowX: "auto" }}>
          {MODEL_OPTIONS.map((m) => {
            const isActive = m.id === selectedModel.id
            return (
              <button
                key={m.id}
                onClick={() => {
                  setSelectedModel(m)
                  setColor(m.color)
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 12px",
                  borderRadius: 8,
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: "pointer",
                  border: "none",
                  whiteSpace: "nowrap",
                  transition: "all 0.2s ease",
                  background: isActive ? `${m.color}33` : "transparent",
                  color: isActive ? m.color : "rgba(255, 255, 255, 0.6)",
                  boxShadow: isActive ? `0 0 12px ${m.color}40` : "none",
                }}
              >
                <span>{m.icon}</span>
                <span>{m.name}</span>
              </button>
            )
          })}
        </div>

        {onClose && (
          <button
            onClick={onClose}
            style={{
              padding: "8px 16px",
              borderRadius: 8,
              background: "rgba(255,255,255,0.08)",
              border: "1px solid rgba(255,255,255,0.15)",
              color: "#ffffff",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Close Studio
          </button>
        )}
      </header>

      {/* Main Viewport & Controls Area */}
      <div style={{ flex: 1, display: "flex", position: "relative", overflow: "hidden" }}>
        {/* 3D WebGL Viewport */}
        <div style={{ flex: 1, position: "relative", background: "#05070f" }}>
          {activeModelUrl && (
            <AsciiObject
              key={selectedModel.id + (asciiEnabled ? "-ascii" : "-raw") + activeModelUrl}
              src={activeModelUrl}
              ascii={asciiEnabled}
              colored={colored}
              color={color}
              orbit={true}
              zoom={true}
              autoRotate={autoRotate}
              autoRotateSpeed={autoRotateSpeed}
              cellSize={cellSize}
              cellAspect={cellAspect}
              contrast={contrast}
              edgeContrast={edgeContrast}
              exposure={exposure}
              invert={invert}
              scale={selectedModel.scale}
              floatIntensity={floatIntensity}
              charset={CHARSET_PRESETS[charsetIndex].value}
            />
          )}

          {/* Interactive Guidance Overlay */}
          <div
            style={{
              position: "absolute",
              bottom: 24,
              left: 24,
              padding: "10px 16px",
              borderRadius: 12,
              background: "rgba(15, 23, 42, 0.75)",
              backdropFilter: "blur(12px)",
              border: "1px solid rgba(255, 255, 255, 0.1)",
              display: "flex",
              alignItems: "center",
              gap: 12,
              fontSize: 12,
              color: "rgba(255, 255, 255, 0.7)",
              pointerEvents: "none",
            }}
          >
            <Sparkles size={16} color={selectedModel.color} />
            <span>
              <strong>Click & Drag</strong> to orbit 360° 3D model with ASCII edge contour shaders
            </span>
          </div>
        </div>

        {/* Floating Controls Sidebar */}
        <aside
          style={{
            width: 320,
            background: "rgba(15, 23, 42, 0.85)",
            backdropFilter: "blur(16px)",
            borderLeft: "1px solid rgba(255, 255, 255, 0.1)",
            padding: 24,
            display: "flex",
            flexDirection: "column",
            gap: 20,
            overflowY: "auto",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14, fontWeight: 700, borderBottom: "1px solid rgba(255,255,255,0.08)", paddingBottom: 12 }}>
            <Sliders size={18} color={selectedModel.color} />
            <span>Shader & Studio Controls</span>
          </div>

          {/* Render Toggles */}
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: "rgba(255,255,255,0.8)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span>ASCII Mode</span>
              <input type="checkbox" checked={asciiEnabled} onChange={(e) => setAsciiEnabled(e.target.checked)} style={{ cursor: "pointer" }} />
            </label>

            <label style={{ fontSize: 12, fontWeight: 600, color: "rgba(255,255,255,0.8)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span>Colored Tint</span>
              <input type="checkbox" checked={colored} onChange={(e) => setColored(e.target.checked)} style={{ cursor: "pointer" }} />
            </label>

            <label style={{ fontSize: 12, fontWeight: 600, color: "rgba(255,255,255,0.8)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span>Auto-Rotate</span>
              <input type="checkbox" checked={autoRotate} onChange={(e) => setAutoRotate(e.target.checked)} style={{ cursor: "pointer" }} />
            </label>

            <label style={{ fontSize: 12, fontWeight: 600, color: "rgba(255,255,255,0.8)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span>Invert Tones</span>
              <input type="checkbox" checked={invert} onChange={(e) => setInvert(e.target.checked)} style={{ cursor: "pointer" }} />
            </label>
          </div>

          {/* Color Accent Picker */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: "rgba(255,255,255,0.8)" }}>Tint Color</label>
            <div style={{ display: "flex", gap: 8 }}>
              {["#38bdf8", "#ef4444", "#f59e0b", "#ec4899", "#4ade80", "#a855f7"].map((c) => (
                <button
                  key={c}
                  onClick={() => setColor(c)}
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: 6,
                    background: c,
                    border: color === c ? "2px solid #ffffff" : "1px solid rgba(255,255,255,0.2)",
                    cursor: "pointer",
                  }}
                />
              ))}
            </div>
          </div>

          {/* Character Preset */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: "rgba(255,255,255,0.8)" }}>Character Set</label>
            <select
              value={charsetIndex}
              onChange={(e) => setCharsetIndex(Number(e.target.value))}
              style={{
                width: "100%",
                padding: "8px 12px",
                borderRadius: 8,
                background: "rgba(0,0,0,0.5)",
                border: "1px solid rgba(255,255,255,0.15)",
                color: "#ffffff",
                fontSize: 12,
              }}
            >
              {CHARSET_PRESETS.map((p, idx) => (
                <option key={idx} value={idx}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          {/* Sliders */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "rgba(255,255,255,0.8)", marginBottom: 4 }}>
                <span>Cell Size</span>
                <span>{cellSize}px</span>
              </div>
              <input type="range" min="4" max="20" step="1" value={cellSize} onChange={(e) => setCellSize(Number(e.target.value))} style={{ width: "100%" }} />
            </div>

            <div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "rgba(255,255,255,0.8)", marginBottom: 4 }}>
                <span>Cell Aspect Ratio</span>
                <span>{cellAspect.toFixed(2)}</span>
              </div>
              <input type="range" min="0.3" max="1.0" step="0.05" value={cellAspect} onChange={(e) => setCellAspect(Number(e.target.value))} style={{ width: "100%" }} />
            </div>

            <div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "rgba(255,255,255,0.8)", marginBottom: 4 }}>
                <span>Edge Contour Contrast</span>
                <span>{edgeContrast.toFixed(1)}</span>
              </div>
              <input type="range" min="1.0" max="5.0" step="0.2" value={edgeContrast} onChange={(e) => setEdgeContrast(Number(e.target.value))} style={{ width: "100%" }} />
            </div>

            <div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "rgba(255,255,255,0.8)", marginBottom: 4 }}>
                <span>Tone Contrast</span>
                <span>{contrast.toFixed(1)}</span>
              </div>
              <input type="range" min="0.5" max="3.0" step="0.1" value={contrast} onChange={(e) => setContrast(Number(e.target.value))} style={{ width: "100%" }} />
            </div>

            <div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "rgba(255,255,255,0.8)", marginBottom: 4 }}>
                <span>Exposure</span>
                <span>{exposure.toFixed(1)}</span>
              </div>
              <input type="range" min="0.5" max="2.5" step="0.1" value={exposure} onChange={(e) => setExposure(Number(e.target.value))} style={{ width: "100%" }} />
            </div>

            <div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "rgba(255,255,255,0.8)", marginBottom: 4 }}>
                <span>Float Bobbing</span>
                <span>{floatIntensity.toFixed(1)}</span>
              </div>
              <input type="range" min="0.0" max="4.0" step="0.2" value={floatIntensity} onChange={(e) => setFloatIntensity(Number(e.target.value))} style={{ width: "100%" }} />
            </div>

            <div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "rgba(255,255,255,0.8)", marginBottom: 4 }}>
                <span>Auto-Rotate Speed</span>
                <span>{autoRotateSpeed.toFixed(1)}</span>
              </div>
              <input type="range" min="0.2" max="5.0" step="0.2" value={autoRotateSpeed} onChange={(e) => setAutoRotateSpeed(Number(e.target.value))} style={{ width: "100%" }} />
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
