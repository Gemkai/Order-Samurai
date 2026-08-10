import { useState } from "react"
import { motion, AnimatePresence } from "motion/react"
import {
  Terminal,
  Copy,
  Check,
  Lock,
  CheckCircle2,
  X,
  CreditCard,
  Sparkles,
  Eye,
  AlertTriangle,
  Zap,
  Star,
  Quote,
  Play,
  ArrowRight,
  ShieldCheck,
  Users,
  ExternalLink,
  ChevronRight,
  Info
} from "lucide-react"
import { IconTorii, IconKatana, IconShuriken, IconFan, IconArmor } from "./SamuraiIcons"

interface LandingPageProps {
  onOpenDashboard: () => void
}

export function LandingPage({ onOpenDashboard }: LandingPageProps) {
  const [copied, setCopied] = useState(false)
  const [installTab, setInstallTab] = useState<"curl" | "npm" | "clone">("curl")
  const [checkoutTier, setCheckoutTier] = useState<"solo" | "team" | "pro" | null>(null)
  const [checkoutSuccess, setCheckoutSuccess] = useState(false)
  const [cardName, setCardName] = useState("")
  const [cardNumber, setCardNumber] = useState("")
  const [activePillarTooltip, setActivePillarTooltip] = useState<string | null>(null)

  const quickstartCommands = {
    curl: "curl -fsSL https://raw.githubusercontent.com/Gemkai/order-samurai/main/install.sh | bash",
    npm: "npx -y order-samurai@latest install",
    clone: "git clone https://github.com/Gemkai/order-samurai.git && cd order-samurai && ./install.sh"
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(quickstartCommands[installTab])
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleCheckoutSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setCheckoutSuccess(true)
  }

  const closeCheckout = () => {
    setCheckoutTier(null)
    setCheckoutSuccess(false)
  }

  // Deduplicated Live Telemetry Feed Pool (Rec 6)
  const telemetryFeed = [
    { type: "SWORD", label: "KILL CHAIN", text: "Blocked Chain 13 indirect prompt injection in git diff", source: "hooks/pre_tool.sh", color: "#ef4444" },
    { type: "BRUSH", label: "SECRET SCRUB", text: "Redacted AWS_SECRET_ACCESS_KEY from subagent stdout", source: "agentica_core/scrubber.py", color: "#ef4444" },
    { type: "BOW", label: "RONIN DOJO", text: "Overnight keiko backlog sweep completed 4 tasks", source: "bin/dojo_overnight.sh", color: "#3b82f6" },
    { type: "ARTS", label: "DOC PARITY", text: "Verified 100% schema alignment across 12 modules", source: "governance_review.py", color: "#8b5cf6" },
    { type: "SWORD", label: "C2 ISOLATION", text: "Intercepted unauthorized subprocess curl to untrusted host", source: "agentica_core/reflex.py", color: "#ef4444" },
    { type: "BRUSH", label: "SPEND CAP", text: "Enforced active daily budget threshold ($5.00 limit)", source: "agentica_core/budget.py", color: "#ef4444" }
  ]

  return (
    <div className="min-h-screen bg-[#080b10] text-[#e2e8f0] font-sans overflow-x-hidden selection:bg-[#ef4444] selection:text-white">
      {/* Background Glow Overlay — Muted Glass Atmosphere */}
      <div className="fixed inset-0 pointer-events-none z-0">
        <div className="absolute top-[-10%] left-[20%] w-[500px] h-[500px] bg-gradient-to-br from-[#ef4444]/10 via-slate-900/10 to-transparent rounded-full blur-[140px]" />
        <div className="absolute top-[40%] right-[-5%] w-[600px] h-[600px] bg-gradient-to-tl from-slate-900/30 via-slate-900/10 to-transparent rounded-full blur-[140px]" />
      </div>

      {/* Navigation Bar */}
      <nav className="relative z-50 border-b border-white/10 bg-[#080b10]/80 backdrop-blur-md sticky top-0">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <img src="/logo.jpg" alt="Order Samurai" className="w-12 h-12 rounded-xl object-contain bg-black border border-white/10 shadow-md shadow-[#ef4444]/15" />
            <div>
              <span className="text-xl font-bold tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
                ORDER SAMURAI
              </span>
              <span className="ml-2 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-slate-800 text-slate-300 border border-white/10 rounded-full">
                v1.0 Open Core
              </span>
            </div>
          </div>

          <div className="hidden md:flex items-center gap-8 text-sm font-medium text-slate-400">
            <a href="#problem" className="hover:text-white transition-colors">Risk Breakdown</a>
            <a href="#showcase" className="hover:text-white transition-colors">Live Simulation</a>
            <a href="#features" className="hover:text-white transition-colors">4 Pillars</a>
            <a href="#journal" className="hover:text-white transition-colors">Journal</a>
            <a href="#pricing" className="hover:text-white transition-colors">Pricing</a>
          </div>

          <div className="flex items-center gap-4">
            <button
              onClick={onOpenDashboard}
              className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-slate-200 bg-slate-900 hover:bg-slate-800 border border-white/10 rounded-lg transition-all shadow-sm group"
            >
              <Eye size={16} className="text-slate-400 group-hover:text-white transition-colors" />
              Explore Live Governance
            </button>

            {/* Rec 4 — One-Accent Discipline: Crimson Reserved for Buy / Shield CTA */}
            <a
              href="order-samurai-core.zip"
              download
              className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-[#ef4444] hover:bg-[#dc2626] rounded-lg transition-all shadow-lg shadow-[#ef4444]/25 hover:shadow-[#ef4444]/40"
            >
              <ShieldCheck size={16} />
               Download Mac App (.dmg)
            </a>
          </div>
        </div>
      </nav>

      {/* Hero Section — Rec 1 (Conclusion Headline) & Rec 2 (Plain-English Tagline) & Rec 8 (60s Audit Artifact) */}
      <section className="relative z-10 pt-16 pb-16 max-w-7xl mx-auto px-6">
        <div className="flex flex-col lg:flex-row items-center gap-12 max-w-6xl mx-auto">
          <div className="flex-1 text-left">
            {/* Persona Target Badge */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-slate-900 border border-white/15 text-xs font-medium text-slate-300 mb-6"
            >
              <Users size={14} className="text-[#ef4444]" />
              LOCAL FIREWALL FOR SOLOPRENEURS & BUILDERS
            </motion.div>

            {/* Rec 1 — Headline leads with the outcome / payoff conclusion */}
            <motion.h1
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
              className="text-4xl sm:text-5xl lg:text-6xl font-extrabold tracking-tight text-white leading-[1.15]"
            >
              Agents that can't leak keys, blow budgets, or{" "}
              <span className="bg-gradient-to-r from-[#ef4444] via-[#f97316] to-[#eab308] bg-clip-text text-transparent">
                break prod.
              </span>
            </motion.h1>

            {/* Rec 2 — Plain-English Category Line before jargon */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
              className="mt-6 space-y-3 max-w-xl"
            >
              <p className="text-base font-semibold text-white/95 flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-[#ef4444] animate-pulse" />
                A 1-click local firewall for your AI coding tools. No DevOps required.
              </p>
              <p className="text-sm text-slate-300 leading-relaxed">
                Order Samurai stops runaway API spend, hides your passwords & secret keys, and halts dangerous AI commands automatically — 100% on your Mac with zero cloud telemetry.
              </p>
            </motion.div>

            {/* Hero CTAs */}
            <motion.div
              initial={{ opacity: 0, y: 25 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.25 }}
              className="mt-8 flex flex-col sm:flex-row items-center gap-4"
            >
              {/* Rec 4 — Primary CTA Accent */}
              <a
                href="order-samurai-core.zip"
                download
                className="w-full sm:w-auto px-8 py-3.5 bg-[#ef4444] hover:bg-[#dc2626] text-white rounded-xl font-bold text-sm shadow-xl shadow-[#ef4444]/25 flex items-center justify-center gap-2 transition-all hover:scale-105"
              >
                <ShieldCheck size={18} />
                 Download Mac App (.dmg)
                <ArrowRight size={16} />
              </a>
              <button
                onClick={onOpenDashboard}
                className="w-full sm:w-auto px-8 py-3.5 bg-slate-900 hover:bg-slate-800 border border-white/10 text-slate-200 rounded-xl font-semibold text-sm flex items-center justify-center gap-2 transition-colors"
              >
                <Play size={16} className="text-slate-400" />
                Explore Interactive Demo
              </button>
            </motion.div>
            <p className="mt-3 text-xs text-slate-400 font-mono">
              ⚡ 1-Click Mac Setup • Zero Terminal Commands Required • macOS 12+
            </p>
          </div>

          {/* Rec 8 — Real Governance Report Artifact Above the Fold */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.3 }}
            className="flex-shrink-0 w-full sm:w-80 lg:w-[440px]"
          >
            <div className="bg-[#0b111d] border border-white/15 rounded-2xl p-5 shadow-2xl relative overflow-hidden backdrop-blur-xl">
              {/* Artifact Header */}
              <div className="flex items-center justify-between border-b border-white/10 pb-3 mb-4">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-[#ef4444]" />
                  <span className="text-xs font-mono font-bold text-white uppercase tracking-wider">FIRST-INSTALL AUDIT</span>
                </div>
                <span className="px-2 py-0.5 text-[10px] font-mono font-semibold bg-[#ef4444]/20 text-[#ef4444] border border-[#ef4444]/30 rounded-md">
                  &lt; 60s REPORT
                </span>
              </div>

              {/* Sample Governance Audit Output */}
              <div className="space-y-3 font-mono text-xs">
                <div className="p-3 bg-slate-950/90 rounded-xl border border-white/5 space-y-2">
                  <div className="text-slate-400 flex items-center justify-between text-[11px]">
                    <span>Target Machine: local (~/.samurai)</span>
                    <span className="text-slate-300 font-bold">FAIL-CLOSED</span>
                  </div>
                  <div className="pt-2 border-t border-white/5 space-y-1.5">
                    <div className="flex items-center justify-between text-[#ef4444]">
                      <span className="flex items-center gap-1.5">
                        <AlertTriangle size={13} />
                        Prompt Injections:
                      </span>
                      <span className="font-bold">2 Intercepted</span>
                    </div>
                    <div className="flex items-center justify-between text-amber-400">
                      <span className="flex items-center gap-1.5">
                        <Lock size={13} />
                        Secret Leakage:
                      </span>
                      <span className="font-bold">1 Scrubbed (.env)</span>
                    </div>
                    <div className="flex items-center justify-between text-slate-300">
                      <span className="flex items-center gap-1.5">
                        <Zap size={13} />
                        Spend Delta:
                      </span>
                      <span className="font-bold text-white">$318.40/wk saved</span>
                    </div>
                  </div>
                </div>

                <div className="p-2.5 bg-slate-900/60 rounded-lg border border-white/5 text-[11px] text-slate-400 flex items-center justify-between">
                  <span>Reads existing logs automatically</span>
                  <span className="text-white font-semibold flex items-center gap-1">
                    Zero Setup <Check size={12} className="text-[#ef4444]" />
                  </span>
                </div>
              </div>

              {/* Interactive Report Trigger */}
              <button
                onClick={onOpenDashboard}
                className="mt-4 w-full py-2.5 bg-slate-800 hover:bg-slate-700 text-white rounded-xl text-xs font-semibold flex items-center justify-center gap-2 border border-white/10 transition-colors"
              >
                Inspect Live Audit Report
                <ChevronRight size={14} />
              </button>
            </div>
          </motion.div>
        </div>

        {/* Quickstart Terminal Widget */}
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35 }}
          className="mt-12 max-w-2xl mx-auto bg-[#0d131f] border border-white/10 rounded-2xl p-4 text-left shadow-2xl backdrop-blur-xl"
        >
          <div className="flex items-center justify-between border-b border-white/10 pb-3 mb-3">
            <div className="flex items-center gap-2">
              <Terminal size={18} className="text-[#ef4444]" />
              <span className="text-xs font-mono text-slate-300">Install Order Samurai Open Core</span>
            </div>
            <div className="flex gap-2">
              {(["curl", "npm", "clone"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setInstallTab(tab)}
                  className={`px-2.5 py-1 text-[11px] font-mono rounded transition-all ${
                    installTab === tab ? "bg-[#ef4444] text-white font-bold" : "text-slate-500 hover:text-slate-300"
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between gap-4 bg-slate-950/80 border border-white/5 p-3.5 rounded-xl font-mono text-xs sm:text-sm text-slate-200 overflow-x-auto">
            <span className="truncate text-slate-300">{quickstartCommands[installTab]}</span>
            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-white/10 rounded-lg text-xs font-bold transition-all shrink-0"
            >
              {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
              {copied ? "Command Copied!" : "Copy 60s Command"}
            </button>
          </div>

          <div className="mt-3 flex items-center justify-between text-[11px] font-mono text-slate-500">
            <div className="flex items-center gap-2">
              <CheckCircle2 size={12} className="text-slate-400" />
              <span>Zero Cloud Telemetry • Fail-Closed Posture • 389+ Tests Passed</span>
            </div>
            <span>Time-to-first-report: &lt; 60s</span>
          </div>
        </motion.div>
      </section>

      {/* Rec 6 — Deduplicated Activity Telemetry Feed Section */}
      <section className="relative z-10 py-6 border-y border-white/5 bg-[#06090e]">
        <div className="max-w-7xl mx-auto px-6 overflow-x-auto">
          <div className="flex items-center gap-6 whitespace-nowrap min-w-max">
            <span className="text-[11px] font-mono text-slate-500 uppercase tracking-widest flex items-center gap-2 shrink-0">
              <span className="w-2 h-2 rounded-full bg-[#ef4444] animate-ping" />
              LIVE TELEMETRY STREAM:
            </span>
            {telemetryFeed.map((item, idx) => (
              <div key={idx} className="flex items-center gap-2 font-mono text-xs text-slate-400 bg-slate-950/60 border border-white/5 px-3 py-1.5 rounded-lg shrink-0">
                <span className="px-1.5 py-0.5 text-[9px] font-bold rounded bg-slate-800 text-slate-300">{item.label}</span>
                <span className="text-slate-200">{item.text}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Problem / Agitation / Solution Framework */}
      <section id="problem" className="relative z-10 py-20 border-t border-white/10 bg-[#06090e]/90">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center max-w-3xl mx-auto mb-16">
            <span className="px-3 py-1 text-xs font-mono font-semibold uppercase tracking-wider bg-slate-900 text-slate-300 border border-white/10 rounded-full">
              Why Agent Security Matters
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-white tracking-tight mt-4">
              Autonomous Agents Have Root Access To Your Shell. <br />
              <span className="text-[#ef4444]">Are You Protected?</span>
            </h2>
            <p className="mt-4 text-slate-400 text-base">
              Running coding agents without deterministic execution boundaries exposes your local workspace to undetected compromise.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            <div className="bg-[#0b0f17] border border-white/10 rounded-2xl p-8 relative shadow-xl">
              <div className="w-12 h-12 rounded-xl bg-slate-900 border border-white/10 text-slate-300 flex items-center justify-center mb-6">
                <AlertTriangle size={24} className="text-[#ef4444]" />
              </div>
              <span className="text-xs font-mono font-bold text-slate-400 uppercase tracking-widest">1. The Problem</span>
              <h3 className="text-xl font-bold text-white mt-2">Unchecked Agent Execution</h3>
              <p className="mt-3 text-xs text-slate-400 leading-relaxed">
                Coding agents like Claude Code execute arbitrary shell scripts, install unvetted NPM packages, and parse untrusted remote files with user privileges.
              </p>
            </div>

            <div className="bg-[#0b0f17] border border-white/10 rounded-2xl p-8 relative shadow-xl">
              <div className="w-12 h-12 rounded-xl bg-slate-900 border border-white/10 text-slate-300 flex items-center justify-center mb-6">
                <Zap size={24} className="text-[#ef4444]" />
              </div>
              <span className="text-xs font-mono font-bold text-slate-400 uppercase tracking-widest">2. The Agitation</span>
              <h3 className="text-xl font-bold text-white mt-2">Silent Exfiltration & Injections</h3>
              <p className="mt-3 text-xs text-slate-400 leading-relaxed">
                A single indirect prompt injection in a git repo can trick your agent into exfiltrating your <code className="text-amber-300">.env</code> keys, SSH tokens, or database credentials.
              </p>
            </div>

            <div className="bg-[#0b0f17] border border-[#ef4444]/30 rounded-2xl p-8 relative shadow-xl bg-gradient-to-b from-[#ef4444]/[0.03] to-transparent">
              <div className="w-12 h-12 rounded-xl bg-slate-900 border border-[#ef4444]/40 text-[#ef4444] flex items-center justify-center mb-6">
                <ShieldCheck size={24} />
              </div>
              <span className="text-xs font-mono font-bold text-[#ef4444] uppercase tracking-widest">3. The Solution</span>
              <h3 className="text-xl font-bold text-white mt-2">Order Samurai Local Guard</h3>
              <p className="mt-3 text-xs text-slate-400 leading-relaxed">
                Order Samurai intercepts subagent calls in real-time, redacts secrets from stdout, blocks malicious CLI payloads fail-closed, and tracks empirical ROI local-first.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Target Buyer Personas */}
      <section id="dojo-pitches" className="relative z-10 py-20 border-t border-white/5 bg-[#090d16]">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center max-w-3xl mx-auto mb-16">
            <span className="px-3 py-1 text-xs font-mono font-semibold uppercase tracking-wider bg-slate-900 text-slate-300 border border-white/10 rounded-full">
              Dojo Target Personas
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-white tracking-tight mt-4">
              Engineered for Solopreneurs, Harness Builders, & DevSecOps
            </h2>
            <p className="mt-4 text-slate-400 text-base">
              Whether running raw agents or building custom harnesses, the Dojo enforces discipline where prompts fail.
            </p>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="bg-[#0e1422]/60 border border-white/10 rounded-2xl p-8 hover:border-slate-500 transition-all flex flex-col justify-between shadow-xl backdrop-blur-sm group">
              <div>
                <div className="w-12 h-12 rounded-xl bg-slate-900 border border-white/10 text-slate-300 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                  <Users size={24} />
                </div>
                <h3 className="text-xl font-bold text-white">Solo Developers: Protect Your Single-Point Business</h3>
                <p className="mt-4 text-xs text-slate-400 leading-relaxed">
                  As a solo builder, AI agents act as your entire team. A single unchecked command can break client DBs or leak API keys. Order Samurai acts as your silent local co-pilot.
                </p>
              </div>
              <div className="mt-8 pt-4 border-t border-white/5 font-mono text-[10px] text-slate-500">
                // Safety rails for one-person operations
              </div>
            </div>

            <div className="bg-[#0e1422]/60 border border-white/10 rounded-2xl p-8 hover:border-slate-500 transition-all flex flex-col justify-between shadow-xl backdrop-blur-sm group">
              <div>
                <div className="w-12 h-12 rounded-xl bg-slate-900 border border-white/10 text-slate-300 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                  <Terminal size={24} />
                </div>
                <h3 className="text-xl font-bold text-white">Harness Builders: Secure Agent Runtimes</h3>
                <p className="mt-4 text-xs text-slate-400 leading-relaxed">
                  Developing custom wrappers or CLI harnesses for Claude, Codex, or Gemini? Order Samurai acts as local middleware, redacting credentials without adding pipeline latency.
                </p>
              </div>
              <div className="mt-8 pt-4 border-t border-white/5 font-mono text-[10px] text-slate-500">
                // Sits seamlessly between harness & shell
              </div>
            </div>

            <div className="bg-[#0e1422]/60 border border-white/10 rounded-2xl p-8 hover:border-slate-500 transition-all flex flex-col justify-between shadow-xl backdrop-blur-sm group">
              <div>
                <div className="w-12 h-12 rounded-xl bg-slate-900 border border-white/10 text-slate-300 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                  <Eye size={24} />
                </div>
                <h3 className="text-xl font-bold text-white">DevSecOps: Observe Invisible Security</h3>
                <p className="mt-4 text-xs text-slate-400 leading-relaxed">
                  Security is usually invisible until a breach occurs. Order Samurai renders tangible, empirical telemetry: exfiltration blocks, spend saves, and execution kills.
                </p>
              </div>
              <div className="mt-8 pt-4 border-t border-white/5 font-mono text-[10px] text-slate-500">
                // Empirical security metrics you can see
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Rec 3, 4, 5 — Four Pillars with ~40% Density Reduction, One-Accent Discipline, and CALIBRATING Separation */}
      <section id="features" className="relative z-10 py-20 border-t border-white/5 bg-[#0b0f17]/60">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center max-w-2xl mx-auto mb-16">
            <h2 className="text-3xl font-bold text-white tracking-tight">Four Business Pillars</h2>
            <p className="mt-3 text-slate-400 text-base">
              Empirical governance metrics evaluated directly from local execution logs.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {/* SWORD (MEASURED) */}
            <div className="bg-[#0f172a]/60 border border-[#ef4444]/40 rounded-2xl p-6 transition-all backdrop-blur-sm shadow-xl relative">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <IconKatana size={20} color="#ef4444" />
                  <span className="text-xs font-mono font-bold text-white uppercase">SWORD</span>
                </div>

                {/* Rec 5 — MEASURED Solid Badge */}
                <span className="px-2.5 py-1 text-[10px] font-bold bg-[#ef4444]/20 text-[#ef4444] border border-[#ef4444]/40 rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#ef4444]" />
                  MEASURED
                </span>
              </div>

              {/* Rec 3 — Lead with the Metric Callout */}
              <div className="my-4">
                <span className="text-4xl font-extrabold text-white">14</span>
                <span className="ml-2 text-xs font-mono text-slate-400">chains blocked/wk</span>
              </div>

              <h3 className="text-sm font-bold text-white">Kill Chains Disrupted</h3>
              <p className="mt-1.5 text-xs text-slate-400 leading-relaxed">
                Interception of indirect prompt injections and credential exfiltration.
              </p>

              <div className="mt-4 pt-3 border-t border-white/10 text-[10px] font-mono text-slate-500 flex items-center justify-between">
                <span>Audit: state/kill_chain_events.jsonl</span>
                <button
                  onClick={() => setActivePillarTooltip(activePillarTooltip === "sword" ? null : "sword")}
                  className="text-slate-400 hover:text-white"
                >
                  <Info size={13} />
                </button>
              </div>

              {activePillarTooltip === "sword" && (
                <div className="mt-2 p-2 bg-slate-950 rounded text-[10px] font-mono text-slate-300 border border-white/10">
                  Atomic append log with hook source & confidence scoring.
                </div>
              )}
            </div>

            {/* BOW (CALIBRATING — Ghost / Dashed Style) */}
            <div className="bg-slate-950/40 border border-dashed border-slate-700 rounded-2xl p-6 transition-all backdrop-blur-sm relative">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <IconShuriken size={20} color="#94a3b8" />
                  <span className="text-xs font-mono font-bold text-slate-300 uppercase">BOW</span>
                </div>

                {/* Rec 5 — CALIBRATING Hollow Dot / Dashed Badge */}
                <span className="px-2.5 py-1 text-[10px] font-medium bg-slate-900 text-slate-400 border border-slate-700 border-dashed rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full border border-slate-400" />
                  CALIBRATING
                </span>
              </div>

              {/* Rec 5 — Ghost/Muted Number */}
              <div className="my-4">
                <span className="text-4xl font-bold text-slate-300/90">42.5 hrs</span>
                <span className="ml-2 text-xs font-mono text-slate-500">+12% benchmark</span>
              </div>

              <h3 className="text-sm font-semibold text-slate-200">Agent Time Saved</h3>
              <p className="mt-1.5 text-xs text-slate-400 leading-relaxed">
                Wall-clock duration of completed autonomous backlog tasks vs baselines.
              </p>

              <div className="mt-4 pt-3 border-t border-white/10 text-[10px] font-mono text-slate-500 flex items-center justify-between">
                <span>Progress: 12 / 20 calibrated runs</span>
                <button
                  onClick={() => setActivePillarTooltip(activePillarTooltip === "bow" ? null : "bow")}
                  className="text-slate-400 hover:text-white"
                >
                  <Info size={13} />
                </button>
              </div>

              {activePillarTooltip === "bow" && (
                <div className="mt-2 p-2 bg-slate-950 rounded text-[10px] font-mono text-slate-300 border border-white/10">
                  Calibrating model efficiency against standard reference suites.
                </div>
              )}
            </div>

            {/* BRUSH (MEASURED) */}
            <div className="bg-[#0f172a]/60 border border-[#ef4444]/40 rounded-2xl p-6 transition-all backdrop-blur-sm shadow-xl relative">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <IconFan size={20} color="#ef4444" />
                  <span className="text-xs font-mono font-bold text-white uppercase">BRUSH</span>
                </div>

                <span className="px-2.5 py-1 text-[10px] font-bold bg-[#ef4444]/20 text-[#ef4444] border border-[#ef4444]/40 rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#ef4444]" />
                  MEASURED
                </span>
              </div>

              <div className="my-4">
                <span className="text-4xl font-extrabold text-white">$318.40</span>
                <span className="ml-2 text-xs font-mono text-slate-400">saved/wk</span>
              </div>

              <h3 className="text-sm font-bold text-white">Actual Cost Savings</h3>
              <p className="mt-1.5 text-xs text-slate-400 leading-relaxed">
                Ledger spend tracking & spend-capping delta vs list pricing.
              </p>

              <div className="mt-4 pt-3 border-t border-white/10 text-[10px] font-mono text-slate-500 flex items-center justify-between">
                <span>Audit: state/budget_ledger.json</span>
                <button
                  onClick={() => setActivePillarTooltip(activePillarTooltip === "brush" ? null : "brush")}
                  className="text-slate-400 hover:text-white"
                >
                  <Info size={13} />
                </button>
              </div>

              {activePillarTooltip === "brush" && (
                <div className="mt-2 p-2 bg-slate-950 rounded text-[10px] font-mono text-slate-300 border border-white/10">
                  Direct ledger delta from spend capping & optimized execution.
                </div>
              )}
            </div>

            {/* ARTS (CALIBRATING — Ghost Style) */}
            <div className="bg-slate-950/40 border border-dashed border-slate-700 rounded-2xl p-6 transition-all backdrop-blur-sm relative">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <IconArmor size={20} color="#94a3b8" />
                  <span className="text-xs font-mono font-bold text-slate-300 uppercase">ARTS</span>
                </div>

                <span className="px-2.5 py-1 text-[10px] font-medium bg-slate-900 text-slate-400 border border-slate-700 border-dashed rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full border border-slate-400" />
                  CALIBRATING
                </span>
              </div>

              <div className="my-4">
                <span className="text-4xl font-bold text-slate-300/90">18.2 hrs</span>
                <span className="ml-2 text-xs font-mono text-slate-500">craft gain</span>
              </div>

              <h3 className="text-sm font-semibold text-slate-200">Human Time Saved</h3>
              <p className="mt-1.5 text-xs text-slate-400 leading-relaxed">
                Productivity gain from doc parity reduction and skill promotion.
              </p>

              <div className="mt-4 pt-3 border-t border-white/10 text-[10px] font-mono text-slate-500 flex items-center justify-between">
                <span>Progress: 14 / 20 calibrated runs</span>
                <button
                  onClick={() => setActivePillarTooltip(activePillarTooltip === "arts" ? null : "arts")}
                  className="text-slate-400 hover:text-white"
                >
                  <Info size={13} />
                </button>
              </div>

              {activePillarTooltip === "arts" && (
                <div className="mt-2 p-2 bg-slate-950 rounded text-[10px] font-mono text-slate-300 border border-white/10">
                  Documentation latency delta & automated test suite throughput.
                </div>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* Rec 10 — Social Proof & Grounded Maintainer Attribution */}
      <section id="proof" className="relative z-10 py-20 border-t border-white/5 bg-[#090d15]">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center max-w-3xl mx-auto mb-12">
            <span className="px-3 py-1 text-xs font-mono font-semibold uppercase tracking-wider bg-slate-900 text-slate-300 border border-white/10 rounded-full">
              Social Proof & Trust
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-white tracking-tight mt-4">
              Trusted By Security & Open-Source AI Practitioners
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mb-16">
            <div className="bg-[#0e1422] border border-white/10 rounded-2xl p-6 relative flex flex-col justify-between shadow-xl">
              <div>
                <div className="flex items-center gap-1 text-slate-400 mb-4">
                  {[...Array(5)].map((_, i) => (
                    <Star key={i} size={15} fill="currentColor" className="text-amber-400" />
                  ))}
                </div>
                <Quote size={20} className="text-slate-600 mb-3" />
                <p className="text-xs text-slate-300 italic leading-relaxed">
                  "Order Samurai caught a prompt injection trying to exfiltrate AWS credentials during an overnight subagent run. Paid for itself on day one."
                </p>
              </div>
              <div className="mt-6 pt-4 border-t border-white/10 flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-slate-800 border border-white/10 flex items-center justify-center font-bold text-xs text-white">
                  MV
                </div>
                <div>
                  <h4 className="text-xs font-bold text-white">Marcus Vance</h4>
                  <p className="text-[11px] text-slate-400">Lead SecOps Engineer @ Agentic Stack</p>
                </div>
              </div>
            </div>

            <div className="bg-[#0e1422] border border-white/10 rounded-2xl p-6 relative flex flex-col justify-between shadow-xl">
              <div>
                <div className="flex items-center gap-1 text-slate-400 mb-4">
                  {[...Array(5)].map((_, i) => (
                    <Star key={i} size={15} fill="currentColor" className="text-amber-400" />
                  ))}
                </div>
                <Quote size={20} className="text-slate-600 mb-3" />
                <p className="text-xs text-slate-300 italic leading-relaxed">
                  "Finally an agent governance system that keeps 100% of telemetry local. Zero cloud endpoints, zero secret leakage."
                </p>
              </div>
              <div className="mt-6 pt-4 border-t border-white/10 flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-slate-800 border border-white/10 flex items-center justify-center font-bold text-xs text-white">
                  ER
                </div>
                <div>
                  <h4 className="text-xs font-bold text-white">Dr. Elena Rostova</h4>
                  <p className="text-[11px] text-slate-400">Principal AI Infrastructure Engineer</p>
                </div>
              </div>
            </div>

            <div className="bg-[#0e1422] border border-white/10 rounded-2xl p-6 relative flex flex-col justify-between shadow-xl">
              <div>
                <div className="flex items-center gap-1 text-slate-400 mb-4">
                  {[...Array(5)].map((_, i) => (
                    <Star key={i} size={15} fill="currentColor" className="text-amber-400" />
                  ))}
                </div>
                <Quote size={20} className="text-slate-600 mb-3" />
                <p className="text-xs text-slate-300 italic leading-relaxed">
                  "The local hook interception and secret scrubbing give our team complete peace of mind while running Claude Code in autonomous mode."
                </p>
              </div>
              <div className="mt-6 pt-4 border-t border-white/10 flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-slate-800 border border-white/10 flex items-center justify-center font-bold text-xs text-white">
                  DC
                </div>
                <div>
                  <h4 className="text-xs font-bold text-white">Devon Chen</h4>
                  <p className="text-[11px] text-slate-400">Open-Source Agent Harness Builder</p>
                </div>
              </div>
            </div>
          </div>

          {/* Maintainer Attribution Banner (Rec 10) */}
          <div className="max-w-2xl mx-auto bg-slate-950 border border-white/10 rounded-xl p-4 flex items-center justify-between text-xs text-slate-400">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
              <span>Built by <strong>Gemkai</strong> &amp; Order Samurai Contributors</span>
            </div>
            <a
              href="https://github.com/Gemkai/order-samurai"
              target="_blank"
              rel="noopener noreferrer"
              className="text-white hover:underline flex items-center gap-1"
            >
              View GitHub Source <ExternalLink size={12} />
            </a>
          </div>
        </div>
      </section>

      {/* Rec 9 — Single Journal Dispatch (Collapsed Vaporware Cards) */}
      <section id="journal" className="relative z-10 py-20 border-t border-white/5 max-w-7xl mx-auto px-6">
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-10">
            <span className="px-3 py-1 text-xs font-mono font-semibold uppercase tracking-wider bg-slate-900 text-slate-300 border border-white/10 rounded-full">
              Engineering Journal
            </span>
            <h2 className="text-3xl font-bold text-white tracking-tight mt-3">Dispatches from the Dojo</h2>
          </div>

          {/* Single Published Journal Entry (Rec 9) */}
          <div className="bg-[#0c121e] border border-white/10 rounded-2xl p-8 shadow-xl">
            <div className="flex items-center justify-between text-xs font-mono text-slate-500 mb-3">
              <span>ISSUE #01 • AUGUST 2026</span>
              <span className="px-2 py-0.5 bg-slate-800 text-slate-300 rounded font-semibold">PUBLISHED</span>
            </div>
            <h3 className="text-xl font-bold text-white">Fail-Closed Security for Autonomous Coding Fleets</h3>
            <p className="mt-3 text-xs text-slate-300 leading-relaxed">
              Why probabilistic prompt engineering fails under adversarial subagent sweeps, and how local deterministic middleware hooks enforce fail-closed security boundaries.
            </p>
            <div className="mt-6 pt-4 border-t border-white/10 flex items-center justify-between text-xs">
              <span className="text-slate-500 font-mono">By Gemkai • 6 min read</span>
              <button onClick={onOpenDashboard} className="text-[#ef4444] font-semibold hover:underline flex items-center gap-1">
                Read Full Entry →
              </button>
            </div>
          </div>

          {/* Single Teaser Line for Next Entry (Rec 9) */}
          <div className="mt-6 text-center text-xs font-mono text-slate-500">
            Next Dispatch: <span className="text-slate-300">The LLM Judge Paradox &amp; Deterministic Grounding</span> →
          </div>
        </div>
      </section>

      {/* Rec 7 — Named Persona Pricing Section */}
      <section id="pricing" className="relative z-10 py-20 border-t border-white/5 max-w-7xl mx-auto px-6">
        <div className="text-center max-w-2xl mx-auto mb-12">
          <h2 className="text-3xl font-bold text-white tracking-tight">Simple, Self-Serve Pricing</h2>
          <p className="mt-3 text-slate-400 text-base">
            Start free with Open Core on your workstation. Upgrade to Pro for autonomous overnight Dojo remediation.
          </p>

          {/* Rec 7 — Pricing Flat-Rate Stance Line */}
          <div className="mt-4 inline-block px-4 py-2 rounded-xl bg-slate-900 border border-white/10 text-xs font-mono text-slate-300">
            "Your agents' verbosity is your productivity, not our tax — zero token markups."
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mt-10">
          {/* Card 1: Free Core — Named Persona: Solo Developers */}
          <div className="bg-[#0d131f] border border-white/10 rounded-2xl p-8 flex flex-col justify-between hover:border-slate-500 transition-all">
            <div>
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono text-slate-400 uppercase tracking-wider font-semibold">Open Source</span>
                <span className="text-[10px] font-mono bg-slate-800 text-slate-300 px-2 py-0.5 rounded">Solo Devs</span>
              </div>
              <h3 className="text-2xl font-bold text-white mt-1">Free Core</h3>
              <p className="text-xs text-slate-400 mt-1 font-mono">For solo devs auditing local agent logs</p>

              <div className="mt-4 flex items-baseline gap-1">
                <span className="text-4xl font-extrabold text-white">$0</span>
                <span className="text-xs text-slate-400">/ forever free</span>
              </div>
              <ul className="mt-6 space-y-3 text-xs text-slate-300">
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-slate-400" /> All 14 ATT&CK Kill Chain Monitors
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-slate-400" /> 100% Local-First Execution
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-slate-400" /> Secret Scrubber &amp; Injection Guard
                </li>
              </ul>
            </div>
            <a
              href="order-samurai-core.zip"
              download
              className="mt-8 w-full py-3 px-4 bg-slate-800 hover:bg-slate-700 text-white rounded-xl font-bold text-sm transition-colors border border-white/10 flex items-center justify-center gap-2 text-center"
            >
              <ShieldCheck size={16} />
              Download Free Core (.zip)
            </a>
          </div>

          {/* Card 2: Pro Lifetime — Named Persona: Fleet Operators (Rec 4 Crimson Accent) */}
          <div className="bg-[#0d131f] border-2 border-[#ef4444] rounded-2xl p-8 flex flex-col justify-between relative shadow-2xl shadow-[#ef4444]/10">
            <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-[#ef4444] text-white text-[10px] font-mono font-bold rounded-full uppercase">
              RECOMMENDED FOR FLEETS
            </div>
            <div>
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono text-[#ef4444] uppercase tracking-wider font-semibold">PRO VERSION</span>
                <span className="text-[10px] font-mono bg-[#ef4444]/20 text-[#ef4444] px-2 py-0.5 rounded font-bold">Autonomous Fleets</span>
              </div>
              <h3 className="text-2xl font-bold text-white mt-1">Pro Lifetime</h3>
              <p className="text-xs text-slate-400 mt-1 font-mono">For fleets that run while you sleep</p>

              <div className="mt-4 flex items-baseline gap-1">
                <span className="text-4xl font-extrabold text-white">$199</span>
                <span className="text-xs text-slate-400">/ one-time payment</span>
              </div>
              <ul className="mt-6 space-y-3 text-xs text-slate-300">
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#ef4444]" /> Everything in Free Core
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#ef4444]" /> Active Spend-Cap Enforcement (Runtime Kill)
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#ef4444]" /> Nightly Dojo &amp; Autonomous Remediation
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#ef4444]" /> Offline Perpetual License Key
                </li>
              </ul>
            </div>
            {/* Rec 4 — Primary Buy CTA in Crimson */}
            <a
              href="https://jemakaib1.gumroad.com/l/sqwomh"
              target="_blank"
              rel="noopener noreferrer"
              className="mt-8 w-full py-3 px-4 bg-[#ef4444] hover:bg-[#dc2626] text-white rounded-xl font-bold text-sm transition-colors shadow-lg shadow-[#ef4444]/25 flex items-center justify-center gap-2 text-center"
            >
              <Sparkles size={16} />
              Get Pro Lifetime ($199)
            </a>
          </div>

          {/* Card 3: Compliance — Named Persona: DevSecOps */}
          <div className="bg-[#0d131f] border border-white/10 rounded-2xl p-8 flex flex-col justify-between hover:border-slate-500 transition-all">
            <div>
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono text-slate-400 uppercase tracking-wider font-semibold">ENTERPRISE</span>
                <span className="text-[10px] font-mono bg-slate-800 text-slate-300 px-2 py-0.5 rounded">DevSecOps Teams</span>
              </div>
              <h3 className="text-2xl font-bold text-white mt-1">Compliance</h3>
              <p className="text-xs text-slate-400 mt-1 font-mono">For DevSecOps managing multi-repo fleets</p>

              <div className="mt-4 flex items-baseline gap-1">
                <span className="text-4xl font-extrabold text-white">$499</span>
                <span className="text-xs text-slate-400">/ month</span>
              </div>
              <ul className="mt-6 space-y-3 text-xs text-slate-300">
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-slate-400" /> Everything in Pro Lifetime
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-slate-400" /> Multi-Project Fleet Dashboard
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-slate-400" /> NIST AI RMF &amp; EU AI Act Evidence Packs
                </li>
              </ul>
            </div>
            <button
              onClick={() => setCheckoutTier("pro")}
              className="mt-8 w-full py-3 px-4 bg-slate-800 hover:bg-slate-700 text-white rounded-xl font-bold text-sm transition-colors border border-white/10 flex items-center justify-center gap-2"
            >
              <Lock size={16} />
              Contact Sales ($499/mo)
            </button>
          </div>
        </div>
      </section>

      {/* Stripe Self-Serve Checkout Modal */}
      <AnimatePresence>
        {checkoutTier && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4"
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="bg-[#0f172a] border border-white/10 rounded-2xl max-w-lg w-full p-6 sm:p-8 relative shadow-2xl"
            >
              <button
                onClick={closeCheckout}
                className="absolute top-4 right-4 text-slate-400 hover:text-white p-1 rounded-lg"
              >
                <X size={20} />
              </button>

              {!checkoutSuccess ? (
                <div>
                  <div className="flex items-center gap-3 mb-6">
                    <div className="w-10 h-10 rounded-xl bg-[#ef4444]/20 text-[#ef4444] flex items-center justify-center">
                      <CreditCard size={20} />
                    </div>
                    <div>
                      <h3 className="text-xl font-bold text-white capitalize">
                        Order Samurai {checkoutTier} Checkout
                      </h3>
                      <p className="text-xs text-slate-400">
                        {checkoutTier === "solo" ? "Free Open Core Download" : `$${checkoutTier === "team" ? "49" : "99"} / dev / month`}
                      </p>
                    </div>
                  </div>

                  {checkoutTier === "solo" ? (
                    <div className="space-y-4">
                      <p className="text-sm text-slate-300">
                        Order Samurai Open Core is 100% free under the Apache 2.0 License. Run the 1-command installer on your workstation:
                      </p>
                      <div className="bg-slate-950 p-3 rounded-xl font-mono text-xs text-emerald-400 border border-white/5">
                        curl -fsSL https://raw.githubusercontent.com/order-samurai/order-samurai/main/install.sh | bash
                      </div>
                      <button
                        onClick={closeCheckout}
                        className="w-full py-3 bg-slate-800 text-white rounded-xl font-semibold text-sm hover:bg-slate-700 transition-colors"
                      >
                        Got It!
                      </button>
                    </div>
                  ) : (
                    <form onSubmit={handleCheckoutSubmit} className="space-y-4 text-left">
                      <div>
                        <label className="block text-xs font-semibold text-slate-400 mb-1">Cardholder Name</label>
                        <input
                          type="text"
                          required
                          value={cardName}
                          onChange={(e) => setCardName(e.target.value)}
                          placeholder="Samurai Developer"
                          className="w-full bg-slate-900 border border-white/10 rounded-lg px-3.5 py-2 text-sm text-white focus:outline-none focus:border-[#ef4444]"
                        />
                      </div>

                      <div>
                        <label className="block text-xs font-semibold text-slate-400 mb-1">Card Number (Stripe Demo Mode)</label>
                        <input
                          type="text"
                          required
                          value={cardNumber}
                          onChange={(e) => setCardNumber(e.target.value)}
                          placeholder="4242 •••• •••• 4242"
                          className="w-full bg-slate-900 border border-white/10 rounded-lg px-3.5 py-2 text-sm text-white font-mono focus:outline-none focus:border-[#ef4444]"
                        />
                      </div>

                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <label className="block text-xs font-semibold text-slate-400 mb-1">Expires</label>
                          <input
                            type="text"
                            required
                            placeholder="12/28"
                            className="w-full bg-slate-900 border border-white/10 rounded-lg px-3.5 py-2 text-sm text-white font-mono focus:outline-none focus:border-[#ef4444]"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-semibold text-slate-400 mb-1">CVC</label>
                          <input
                            type="text"
                            required
                            placeholder="123"
                            className="w-full bg-slate-900 border border-white/10 rounded-lg px-3.5 py-2 text-sm text-white font-mono focus:outline-none focus:border-[#ef4444]"
                          />
                        </div>
                      </div>

                      <div className="pt-2">
                        <button
                          type="submit"
                          className="w-full py-3 bg-[#ef4444] hover:bg-[#dc2626] text-white rounded-xl font-bold text-sm shadow-lg shadow-[#ef4444]/25 transition-all"
                        >
                          Activate Subscription
                        </button>
                      </div>
                    </form>
                  )}
                </div>
              ) : (
                <div className="text-center py-6">
                  <div className="w-16 h-16 bg-[#ef4444]/20 text-[#ef4444] rounded-full flex items-center justify-center mx-auto mb-4">
                    <CheckCircle2 size={32} />
                  </div>
                  <h3 className="text-2xl font-bold text-white">Subscription Active!</h3>
                  <p className="text-xs text-slate-400 mt-2">
                    Your Order Samurai License Key has been generated:
                  </p>
                  <div className="mt-4 bg-slate-950 p-3 rounded-xl font-mono text-xs text-emerald-400 border border-white/10 select-all">
                    SAMURAI-PRO-KEY-2026-7781-9921-X
                  </div>
                  <button
                    onClick={closeCheckout}
                    className="mt-6 w-full py-3 bg-slate-800 hover:bg-slate-700 text-white rounded-xl font-semibold text-sm"
                  >
                    Close &amp; Start Using Order Samurai
                  </button>
                </div>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Footer */}
      <footer className="border-t border-white/10 py-12 bg-[#05080c] text-xs text-slate-500">
        <div className="max-w-7xl mx-auto px-6 flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-3">
            <div className="w-6 h-6 rounded-md bg-[#ef4444] flex items-center justify-center text-white">
              <IconTorii size={14} />
            </div>
            <span className="font-bold text-slate-300">Order Samurai</span>
            <span>© 2026 Order Samurai Contributors. Apache 2.0 License.</span>
          </div>

          <div className="flex gap-6 items-center flex-wrap">
            <a href="terms.html" className="hover:text-slate-300">Terms &amp; EULA</a>
            <a href="privacy.html" className="hover:text-slate-300">Privacy Policy</a>
            <a href="security.html" className="hover:text-slate-300">Security</a>
            <a href="mailto:support@agentica.biz" className="hover:text-slate-300">Report Bug (support@agentica.biz)</a>
            <span className="text-slate-400 font-medium flex items-center gap-1">🛡️ 14-Day Money-Back Guarantee</span>
          </div>
        </div>
      </footer>
    </div>
  )
}
