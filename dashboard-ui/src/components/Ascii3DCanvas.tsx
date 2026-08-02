import { useRef, useEffect, useState } from "react"
import { parseAsciiGrid } from "@/lib/ascii3d"

interface Ascii3DCanvasProps {
  art: string
  color: string
  pillar: string
  opacity: number
}

export function Ascii3DCanvas({ art, color, pillar, opacity }: Ascii3DCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const animFrameRef = useRef<number | null>(null)
  const mouseRef = useRef({ x: 0, y: 0, targetX: 0, targetY: 0 })
  const [isSupported, setIsSupported] = useState(true)

  // FPS performance monitoring (operational proposal from html-in-canvas.md)
  const fpsRef = useRef({ frames: 0, lastTime: 0, lowFpsCount: 0 })

  // Mouse tracking for 3D parallax tilt
  useEffect(() => {
    function handleMouseMove(e: MouseEvent) {
      const cx = window.innerWidth / 2
      const cy = window.innerHeight / 2
      mouseRef.current.targetX = (e.clientX - cx) / cx
      mouseRef.current.targetY = (e.clientY - cy) / cy
    }
    window.addEventListener("mousemove", handleMouseMove)
    return () => window.removeEventListener("mousemove", handleMouseMove)
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) {
      setIsSupported(false)
      return
    }

    const { points, numRows, numCols } = parseAsciiGrid(art)
    const startTime = performance.now()
    fpsRef.current.lastTime = startTime

    function render() {
      if (!canvas || !ctx) return
      const now = performance.now()
      const dt = (now - startTime) / 1000

      // FPS tracking & fallback check
      fpsRef.current.frames++
      if (now - fpsRef.current.lastTime >= 1000) {
        const currentFps = fpsRef.current.frames
        fpsRef.current.frames = 0
        fpsRef.current.lastTime = now
        if (currentFps < 45) {
          fpsRef.current.lowFpsCount++
          if (fpsRef.current.lowFpsCount >= 3) {
            // Sustained low FPS -> degrade to static 2D DOM layout
            setIsSupported(false)
            return
          }
        } else {
          fpsRef.current.lowFpsCount = 0
        }
      }

      // Smooth mouse lerp for 3D rotation
      mouseRef.current.x += (mouseRef.current.targetX - mouseRef.current.x) * 0.05
      mouseRef.current.y += (mouseRef.current.targetY - mouseRef.current.y) * 0.05

      // Resize canvas to match display size
      const width = canvas.clientWidth
      const height = canvas.clientHeight
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width
        canvas.height = height
      }

      ctx.clearRect(0, 0, width, height)

      // 3D Projection parameters
      const fov = 450
      const rotY = mouseRef.current.x * 0.25 // Yaw tilt
      const rotX = -mouseRef.current.y * 0.2 // Pitch tilt
      const cosY = Math.cos(rotY), sinY = Math.sin(rotY)
      const cosX = Math.cos(rotX), sinX = Math.sin(rotX)

      const scale = Math.min(width / (numCols * 8), height / (numRows * 12)) * 0.95

      ctx.font = `${Math.max(6, Math.floor(10 * scale))}px "JetBrains Mono Variable", "JetBrains Mono", monospace`
      ctx.textAlign = "center"
      ctx.textBaseline = "middle"

      // Pillar-specific 3D Shader parameters
      const sweepX = (dt * 0.6) % 3.0 - 1.5 // Light sweep for Katana (sword)
      const pulse = Math.sin(dt * 2) * 0.15 + 1.0

      // Render 3D points in z-sorted order for correct depth layering
      const projected = points.map((p) => {
        // Dynamic animation per pillar
        let animZ = p.z
        let extraGlow = 0

        if (pillar === "sword") {
          // Katana blade sheen pass
          const distToSweep = Math.abs(p.x - sweepX)
          if (distToSweep < 0.25) {
            extraGlow = (1 - distToSweep / 0.25) * 0.8
            animZ += extraGlow * 0.08
          }
        } else if (pillar === "bow") {
          // Archery bow tension flex & ember pulse
          const tension = Math.sin(dt * 3 + p.y * 2) * 0.03
          animZ += tension
        } else if (pillar === "brush") {
          // Ink calligraphy Z-wave & sakura swirl
          animZ += Math.sin(dt * 1.5 + p.x * 3) * 0.04
        } else if (pillar === "arts") {
          // Crystalline snow matrix shimmer
          extraGlow = Math.sin(dt * 4 + p.x * 10 + p.y * 10) * 0.2 + 0.2
        } else {
          // Overview 3D Ronin depth pulse
          animZ += Math.sin(dt * 1.2 + p.ny * 2) * 0.02
        }

        // Apply 3D Rotations (Y then X)
        const x1 = p.x * cosY + animZ * sinY
        const z1 = -p.x * sinY + animZ * cosY

        const y2 = p.y * cosX - z1 * sinX
        const z2 = p.y * sinX + z1 * cosX + 2.5 // Camera distance

        // 3D Perspective Projection
        const projScale = fov / z2
        const screenX = width / 2 + x1 * projScale * scale * 12
        const screenY = height / 2 - y2 * projScale * scale * 12

        return {
          char: p.char,
          screenX,
          screenY,
          z: z2,
          weight: p.weight,
          projScale,
          extraGlow,
        }
      })

      // Sort points back to front
      projected.sort((a, b) => b.z - a.z)

      // Draw projected 3D ASCII characters
      for (let i = 0; i < projected.length; i++) {
        const p = projected[i]
        const alpha = Math.min(1, Math.max(0, (opacity * (0.6 + p.weight * 0.6) + p.extraGlow * 0.2) * pulse))

        if (alpha <= 0.01) continue

        ctx.fillStyle = color
        ctx.globalAlpha = alpha

        if (p.extraGlow > 0.3) {
          ctx.shadowColor = color
          ctx.shadowBlur = 8 * p.extraGlow
        } else {
          ctx.shadowBlur = 0
        }

        ctx.fillText(p.char, p.screenX, p.screenY)
      }

      ctx.globalAlpha = 1.0
      ctx.shadowBlur = 0

      // Render floating 3D ambient particle dust (sakura / embers / snow / rain)
      if (pillar === "brush" || pillar === "bow" || pillar === "arts" || pillar === "sword") {
        const particleCount = 20
        ctx.fillStyle = color
        for (let i = 0; i < particleCount; i++) {
          const px = ((Math.sin(i * 99 + dt * 0.3) + 1) / 2) * width
          const py = ((Math.cos(i * 33 + dt * 0.2) + 1) / 2) * height
          const pSize = 1.5 + Math.sin(i + dt) * 0.8
          ctx.globalAlpha = opacity * 0.4 * (0.5 + Math.sin(i * 7 + dt * 2) * 0.5)
          ctx.beginPath()
          ctx.arc(px, py, pSize, 0, Math.PI * 2)
          ctx.fill()
        }
        ctx.globalAlpha = 1.0
      }

      animFrameRef.current = requestAnimationFrame(render)
    }

    animFrameRef.current = requestAnimationFrame(render)

    return () => {
      if (animFrameRef.current !== null) {
        cancelAnimationFrame(animFrameRef.current)
      }
    }
  }, [art, color, pillar, opacity])

  if (!isSupported) {
    return null // Graceful fallback to static 2D DOM pre tag
  }

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
      }}
    />
  )
}
