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
  Users
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
  const [activeVisualTab, setActiveVisualTab] = useState<"walkthrough" | "menu" | "particles" | "branding">("walkthrough")

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

  const mediaAssets = {
    walkthrough: {
      title: "Live Interactive Dashboard Simulation",
      subtitle: "Full 203-frame smooth navigation preview with synthetic demonstration data",
      src: "/media/order_samurai_dashboard_simulation.gif",
      badge: "Full UI Simulation"
    },
    menu: {
      title: "Pixel-Perfect Vector Icons & Menus",
      subtitle: "1:1 SVG render of Katana, Shuriken, Fan, and Armor pillar navigation",
      src: "/media/order_samurai_pillars_menu.gif",
      badge: "Vector Precision"
    },
    particles: {
      title: "Pre-Warmed Seasonal Particle Showcase",
      subtitle: "Red maple leaves, golden autumn foliage, cherry blossoms, and snow particles",
      src: "/media/order_samurai_minimalist_particles.gif",
      badge: "Minimalist Animation"
    },
    branding: {
      title: "Official Widescreen Branding",
      subtitle: "Letterboxed 16:9 Order Samurai helmet crest emblem",
      src: "/media/order_samurai_logo_letterbox.jpg",
      badge: "Brand Identity"
    }
  }

  return (
    <div className="min-h-screen bg-[#080b10] text-[#e2e8f0] font-sans overflow-x-hidden selection:bg-[#ef4444] selection:text-white">
      {/* Background Glow Overlay */}
      <div className="fixed inset-0 pointer-events-none z-0">
        <div className="absolute top-[-10%] left-[20%] w-[500px] h-[500px] bg-gradient-to-br from-[#ef4444]/15 via-[#3b82f6]/10 to-transparent rounded-full blur-[120px]" />
        <div className="absolute top-[40%] right-[-5%] w-[600px] h-[600px] bg-gradient-to-tl from-[#10b981]/10 via-[#8b5cf6]/10 to-transparent rounded-full blur-[140px]" />
      </div>

      {/* Navigation Bar */}
      <nav className="relative z-50 border-b border-white/10 bg-[#080b10]/80 backdrop-blur-md sticky top-0">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <img src="/logo.jpg" alt="Order Samurai" className="w-14 h-14 rounded-xl object-contain bg-black border border-white/10 shadow-lg shadow-[#ef4444]/20" />
            <div>
              <span className="text-xl font-bold tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
                ORDER SAMURAI
              </span>
              <span className="ml-2 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-[#ef4444]/20 text-[#ef4444] border border-[#ef4444]/30 rounded-full">
                v1.0 Open Core
              </span>
            </div>
          </div>

          <div className="hidden md:flex items-center gap-8 text-sm font-medium text-slate-400">
            <a href="#problem" className="hover:text-white transition-colors">Risk Breakdown</a>
            <a href="#showcase" className="hover:text-white transition-colors">Live Simulation</a>
            <a href="#features" className="hover:text-white transition-colors">4 Pillars</a>
            <a href="#proof" className="hover:text-white transition-colors">Social Proof</a>
            <a href="#pricing" className="hover:text-white transition-colors">Pricing</a>
          </div>

          <div className="flex items-center gap-4">
            {/* CTV Button: Explore Live Governance */}
            <button
              onClick={onOpenDashboard}
              className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-slate-800/80 hover:bg-slate-700/80 border border-white/10 rounded-lg transition-all shadow-sm group"
            >
              <Eye size={16} className="text-[#10b981] group-hover:scale-110 transition-transform" />
              Explore Live Governance
            </button>
            {/* CTV Button: Shield Your Fleet Free */}
            <a
              href="#pricing"
              className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-gradient-to-r from-[#ef4444] to-[#dc2626] hover:from-[#dc2626] hover:to-[#b91c1c] rounded-lg transition-all shadow-lg shadow-[#ef4444]/25 hover:shadow-[#ef4444]/40"
            >
              <ShieldCheck size={16} />
              Shield Your Fleet Free
            </a>
          </div>
        </div>
      </nav>

      {/* Hero Section — WHO / WHY / WHAT Framework */}
      <section className="relative z-10 pt-16 pb-16 max-w-7xl mx-auto px-6">
        <div className="flex flex-col lg:flex-row items-center gap-12 max-w-6xl mx-auto">
          <div className="flex-1 text-left">
            {/* WHO Target Audience Badge */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-gradient-to-r from-[#ef4444]/15 to-[#3b82f6]/15 border border-[#ef4444]/30 text-xs font-semibold text-[#ef4444] mb-6"
            >
              <Users size={14} />
              GOVERN THE AGENTS THAT WORK WHILE YOU SLEEP
            </motion.div>

            {/* WHAT Headline & WHY Benefit */}
            <motion.h1
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
              className="text-4xl sm:text-5xl lg:text-6xl font-extrabold tracking-tight text-white leading-[1.15]"
            >
              The missing <em className="italic text-[#ef4444] not-italic">discipline</em> for{" "}
              <span className="bg-gradient-to-r from-[#ef4444] via-[#f97316] to-[#eab308] bg-clip-text text-transparent">
                autonomous agent fleets.
              </span>
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
              className="mt-6 text-lg text-slate-300 leading-relaxed max-w-xl"
            >
              Order Samurai intercepts prompt injections, scrubs leaking credentials, and kills runaway spend across your coding-agent fleet — entirely on your machine, fail-closed by default, zero cloud telemetry.
            </motion.p>

            {/* CTV Hero Buttons */}
            <motion.div
              initial={{ opacity: 0, y: 25 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.25 }}
              className="mt-8 flex flex-col sm:flex-row items-center gap-4"
            >
              <a
                href="order-samurai-core.zip"
                download
                className="w-full sm:w-auto px-8 py-3.5 bg-gradient-to-r from-[#ef4444] to-[#dc2626] hover:from-[#dc2626] hover:to-[#b91c1c] text-white rounded-xl font-bold text-sm shadow-xl shadow-[#ef4444]/25 flex items-center justify-center gap-2 transition-all hover:scale-105"
              >
                <ShieldCheck size={18} />
                Download Core Version (.zip)
                <ArrowRight size={16} />
              </a>
              <button
                onClick={onOpenDashboard}
                className="w-full sm:w-auto px-8 py-3.5 bg-slate-900 hover:bg-slate-800 border border-white/10 text-slate-200 rounded-xl font-semibold text-sm flex items-center justify-center gap-2 transition-colors"
              >
                <Play size={16} className="text-[#10b981]" />
                Explore Interactive Demo
              </button>
            </motion.div>
          </div>

          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.3 }}
            className="flex-shrink-0"
          >
            <video
              autoPlay
              loop
              muted
              playsInline
              className="w-72 sm:w-80 lg:w-[440px] h-auto rounded-2xl border border-[#ef4444]/30 shadow-2xl shadow-[#ef4444]/25 object-cover bg-black"
            >
              <source src="pillars_menu.webm" type="video/webm" />
              <source src="pillars_menu.mp4" type="video/mp4" />
            </video>
          </motion.div>
        </div>

          {/* Quickstart Terminal Widget */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
            className="mt-10 max-w-2xl mx-auto bg-[#0d131f] border border-white/10 rounded-2xl p-4 text-left shadow-2xl backdrop-blur-xl"
          >
            <div className="flex items-center justify-between border-b border-white/10 pb-3 mb-3">
              <div className="flex items-center gap-2">
                <Terminal size={18} className="text-[#ef4444]" />
                <span className="text-xs font-mono text-slate-400">Install Order Samurai Open Core</span>
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
              <span className="truncate text-[#10b981]">{quickstartCommands[installTab]}</span>
              {/* CTV Button for Copying */}
              <button
                onClick={handleCopy}
                className="flex items-center gap-1.5 px-3.5 py-2 bg-gradient-to-r from-[#ef4444]/20 to-[#dc2626]/20 hover:from-[#ef4444]/30 hover:to-[#dc2626]/30 text-[#ef4444] border border-[#ef4444]/40 rounded-lg text-xs font-bold transition-all shrink-0"
              >
                {copied ? <Check size={14} className="text-[#10b981]" /> : <Copy size={14} />}
                {copied ? "Command Copied!" : "Copy 60s Install Command"}
              </button>
            </div>

            <div className="mt-3 flex items-center justify-between text-[11px] font-mono text-slate-500">
              <div className="flex items-center gap-2">
                <CheckCircle2 size={12} className="text-[#10b981]" />
                <span>Zero Cloud Telemetry • Fail-Closed Posture • 389+ Tests Passed</span>
              </div>
              <span>Time-to-first-report: &lt; 60s</span>
            </div>

            <div className="mt-4 pt-3 border-t border-white/10 flex flex-wrap items-center gap-3">
              <a
                href="order-samurai-core.zip"
                download
                className="flex items-center gap-2 px-4 py-2 bg-[#ef4444] hover:bg-[#dc2626] text-white rounded-lg text-xs font-bold transition-all shadow-md shadow-[#ef4444]/20"
              >
                📦 Download Core (.zip)
              </a>
              <a
                href="install.sh"
                download
                className="flex items-center gap-2 px-4 py-2 bg-slate-900 hover:bg-slate-800 border border-white/10 text-slate-300 rounded-lg text-xs font-semibold transition-all"
              >
                ⚡ Download install.sh
              </a>
            </div>
          </motion.div>
      </section>

      {/* PAS Copywriting Framework: Problem -> Agitation -> Solution */}
      <section id="problem" className="relative z-10 py-20 border-t border-white/10 bg-[#06090e]/90">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center max-w-3xl mx-auto mb-16">
            <span className="px-3 py-1 text-xs font-mono font-semibold uppercase tracking-wider bg-[#ef4444]/10 text-[#ef4444] border border-[#ef4444]/20 rounded-full">
              Why Agent Security Matters
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-white tracking-tight mt-4">
              Autonomous Agents Have Root Access To Your Environment. <br />
              <span className="text-[#ef4444]">Are You Protected?</span>
            </h2>
            <p className="mt-4 text-slate-400 text-base">
              Running coding agents without deterministic execution boundaries exposes your local infrastructure to undetected compromise.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {/* Problem */}
            <div className="bg-[#0b0f17] border border-red-500/20 rounded-2xl p-8 relative shadow-xl hover:border-red-500/40 transition-colors">
              <div className="w-12 h-12 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 flex items-center justify-center mb-6">
                <AlertTriangle size={24} />
              </div>
              <span className="text-xs font-mono font-bold text-red-400 uppercase tracking-widest">1. The Problem</span>
              <h3 className="text-xl font-bold text-white mt-2">Unchecked Agent Execution</h3>
              <p className="mt-3 text-xs text-slate-400 leading-relaxed">
                Coding agents like Claude Code or subagent swarms execute arbitrary shell scripts, install unvetted NPM packages, and parse untrusted remote files with standard user privileges.
              </p>
            </div>

            {/* Agitation */}
            <div className="bg-[#0b0f17] border border-amber-500/20 rounded-2xl p-8 relative shadow-xl hover:border-amber-500/40 transition-colors">
              <div className="w-12 h-12 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-400 flex items-center justify-center mb-6">
                <Zap size={24} />
              </div>
              <span className="text-xs font-mono font-bold text-amber-400 uppercase tracking-widest">2. The Agitation</span>
              <h3 className="text-xl font-bold text-white mt-2">Silent Exfiltration & Injections</h3>
              <p className="mt-3 text-xs text-slate-400 leading-relaxed">
                A single indirect prompt injection in a git repo or API response can trick your agent into exfiltrating your <code className="text-amber-300">.env</code> keys, SSH tokens, or internal DB credentials to C2 servers.
              </p>
            </div>

            {/* Solution */}
            <div className="bg-[#0b0f17] border border-emerald-500/30 rounded-2xl p-8 relative shadow-xl hover:border-emerald-500/50 transition-colors bg-gradient-to-b from-emerald-500/[0.03] to-transparent">
              <div className="w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 flex items-center justify-center mb-6">
                <ShieldCheck size={24} />
              </div>
              <span className="text-xs font-mono font-bold text-emerald-400 uppercase tracking-widest">3. The Solution</span>
              <h3 className="text-xl font-bold text-white mt-2">Order Samurai Local Guard</h3>
              <p className="mt-3 text-xs text-slate-400 leading-relaxed">
                Order Samurai intercepts subagent calls in real-time, redacts secrets from stdout, blocks malicious CLI payloads fail-closed, and tracks empirical ROI—100% on your local machine.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Visual Storytelling Section — Interactive Showcase */}
      <section id="showcase" className="relative z-10 py-20 border-t border-white/5 bg-[#080c14]">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center max-w-3xl mx-auto mb-12">
            <span className="px-3 py-1 text-xs font-mono font-semibold uppercase tracking-wider bg-[#3b82f6]/10 text-[#3b82f6] border border-[#3b82f6]/20 rounded-full">
              Visual Storytelling
            </span>
            <h2 className="text-3xl font-bold text-white tracking-tight mt-4">
              See How Order Samurai Governs Your Agent Fleet
            </h2>
            <p className="mt-3 text-slate-400 text-base">
              Experience the pixel-perfect vector interface, seasonal theme particle physics, and live simulation logs.
            </p>
          </div>

          {/* Interactive Media Tab Switcher */}
          <div className="flex flex-wrap items-center justify-center gap-3 mb-8">
            {[
              { id: "walkthrough", label: "Full Simulation GIF", icon: Play },
              { id: "menu", label: "Vector Menu Icons", icon: IconKatana },
              { id: "particles", label: "Seasonal Particles", icon: Sparkles },
              { id: "branding", label: "16:9 Letterbox Logo", icon: IconTorii }
            ].map((tab) => {
              const Icon = tab.icon
              const isActive = activeVisualTab === tab.id
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveVisualTab(tab.id as any)}
                  className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold transition-all ${
                    isActive
                      ? "bg-[#ef4444] text-white shadow-lg shadow-[#ef4444]/25 scale-105"
                      : "bg-slate-900 text-slate-400 hover:text-white border border-white/10"
                  }`}
                >
                  <Icon size={14} />
                  {tab.label}
                </button>
              )
            })}
          </div>

          {/* Visual Showcase Container */}
          <div className="bg-[#0b101b] border border-white/10 rounded-3xl overflow-hidden shadow-2xl p-4 sm:p-6">
            <div className="flex items-center justify-between border-b border-white/10 pb-4 mb-4">
              <div>
                <span className="px-2.5 py-0.5 text-[10px] font-mono font-bold uppercase tracking-wider bg-[#ef4444]/20 text-[#ef4444] border border-[#ef4444]/30 rounded-full">
                  {mediaAssets[activeVisualTab].badge}
                </span>
                <h3 className="text-lg font-bold text-white mt-1">{mediaAssets[activeVisualTab].title}</h3>
                <p className="text-xs text-slate-400">{mediaAssets[activeVisualTab].subtitle}</p>
              </div>
              <button
                onClick={onOpenDashboard}
                className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-[#10b981] to-[#059669] hover:from-[#059669] text-white text-xs font-bold rounded-lg shadow-md transition-all"
              >
                <Eye size={14} />
                Launch Full UI Demo
              </button>
            </div>

            <div className="relative rounded-2xl overflow-hidden border border-white/10 bg-black/60 min-h-[300px] flex items-center justify-center">
              <img
                src={mediaAssets[activeVisualTab].src}
                alt={mediaAssets[activeVisualTab].title}
                className="w-full h-auto max-h-[600px] object-contain rounded-xl"
              />
            </div>
          </div>
        </div>
      </section>

      {/* Core Business Pillars — Features Tell, Benefits Sell */}
      <section id="features" className="relative z-10 py-20 border-t border-white/5 bg-[#0b0f17]/60">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center max-w-2xl mx-auto mb-16">
            <h2 className="text-3xl font-bold text-white tracking-tight">Four Proven Business Pillars</h2>
            <p className="mt-3 text-slate-400 text-base">
              Replace subjective scorecards with empirical, business-meaningful metrics designed for decision-makers.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {/* SWORD */}
            <div className="bg-[#0f172a]/60 border border-[#ef4444]/30 rounded-2xl p-6 hover:border-[#ef4444] transition-all group backdrop-blur-sm shadow-xl hover:shadow-[#ef4444]/10">
              <div className="w-12 h-12 rounded-xl bg-[#ef4444]/15 border border-[#ef4444]/30 flex items-center justify-center mb-5 group-hover:scale-110 transition-transform">
                <IconKatana size={24} color="#ef4444" />
              </div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-mono font-semibold text-[#ef4444] uppercase tracking-wider">SWORD Pillar</span>
                <span className="px-2 py-0.5 text-[10px] font-bold bg-[#10b981]/20 text-[#10b981] rounded">MEASURED</span>
              </div>
              <h3 className="text-xl font-bold text-white">Kill Chains Disrupted</h3>
              <p className="mt-2 text-xs text-slate-400 leading-relaxed">
                Interception of indirect prompt injections (Chain 13) and credential/IP exfiltrations (Chain 14).
              </p>
              <div className="mt-6 pt-4 border-t border-white/10 flex items-baseline justify-between">
                <span className="text-2xl font-extrabold text-white">14</span>
                <span className="text-xs text-slate-400 font-mono">chains blocked/wk</span>
              </div>
            </div>

            {/* BOW */}
            <div className="bg-[#0f172a]/60 border border-[#3b82f6]/30 rounded-2xl p-6 hover:border-[#3b82f6] transition-all group backdrop-blur-sm shadow-xl hover:shadow-[#3b82f6]/10">
              <div className="w-12 h-12 rounded-xl bg-[#3b82f6]/15 border border-[#3b82f6]/30 flex items-center justify-center mb-5 group-hover:scale-110 transition-transform">
                <IconShuriken size={24} color="#3b82f6" />
              </div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-mono font-semibold text-[#3b82f6] uppercase tracking-wider">BOW Pillar</span>
                <span className="px-2 py-0.5 text-[10px] font-bold bg-[#f59e0b]/20 text-[#f59e0b] rounded">CALIBRATING</span>
              </div>
              <h3 className="text-xl font-bold text-white">Agent Time Saved</h3>
              <p className="mt-2 text-xs text-slate-400 leading-relaxed">
                Wall-clock duration of completed autonomous backlog tasks evaluated against calibrated baseline models.
              </p>
              <div className="mt-6 pt-4 border-t border-white/10 flex items-baseline justify-between">
                <span className="text-2xl font-extrabold text-white">42.5 hrs</span>
                <span className="text-xs text-slate-400 font-mono">+12% vs benchmark</span>
              </div>
            </div>

            {/* BRUSH */}
            <div className="bg-[#0f172a]/60 border border-[#10b981]/30 rounded-2xl p-6 hover:border-[#10b981] transition-all group backdrop-blur-sm shadow-xl hover:shadow-[#10b981]/10">
              <div className="w-12 h-12 rounded-xl bg-[#10b981]/15 border border-[#10b981]/30 flex items-center justify-center mb-5 group-hover:scale-110 transition-transform">
                <IconFan size={24} color="#10b981" />
              </div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-mono font-semibold text-[#10b981] uppercase tracking-wider">BRUSH Pillar</span>
                <span className="px-2 py-0.5 text-[10px] font-bold bg-[#10b981]/20 text-[#10b981] rounded">MEASURED</span>
              </div>
              <h3 className="text-xl font-bold text-white">Actual Cost Savings</h3>
              <p className="mt-2 text-xs text-slate-400 leading-relaxed">
                Direct budget ledger tracking & model routing optimization delta measured against Anthropic list pricing.
              </p>
              <div className="mt-6 pt-4 border-t border-white/10 flex items-baseline justify-between">
                <span className="text-2xl font-extrabold text-white">$318.40</span>
                <span className="text-xs text-slate-400 font-mono">saved this week</span>
              </div>
            </div>

            {/* ARTS */}
            <div className="bg-[#0f172a]/60 border border-[#8b5cf6]/30 rounded-2xl p-6 hover:border-[#8b5cf6] transition-all group backdrop-blur-sm shadow-xl hover:shadow-[#8b5cf6]/10">
              <div className="w-12 h-12 rounded-xl bg-[#8b5cf6]/15 border border-[#8b5cf6]/30 flex items-center justify-center mb-5 group-hover:scale-110 transition-transform">
                <IconArmor size={24} color="#8b5cf6" />
              </div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-mono font-semibold text-[#8b5cf6] uppercase tracking-wider">ARTS Pillar</span>
                <span className="px-2 py-0.5 text-[10px] font-bold bg-[#f59e0b]/20 text-[#f59e0b] rounded">CALIBRATING</span>
              </div>
              <h3 className="text-xl font-bold text-white">Human Time Saved</h3>
              <p className="mt-2 text-xs text-slate-400 leading-relaxed">
                Productivity gain from documentation parity latency reduction and skill promotion throughput.
              </p>
              <div className="mt-6 pt-4 border-t border-white/10 flex items-baseline justify-between">
                <span className="text-2xl font-extrabold text-white">18.2 hrs</span>
                <span className="text-xs text-slate-400 font-mono">craft efficiency</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Autonomous Ronin Governance Architecture Section */}
      <section id="ronin-architecture" className="relative z-10 py-20 border-t border-white/5 bg-[#070b12]">
        <div className="max-w-7xl mx-auto px-6">
          <div className="max-w-3xl mb-16">
            <span className="px-3 py-1 text-xs font-mono font-semibold uppercase tracking-wider bg-[#ef4444]/10 text-[#ef4444] border border-[#ef4444]/20 rounded-full">
              AUTONOMOUS GOVERNANCE ARCHITECTURE
            </span>
            <h2 className="text-3xl sm:text-5xl font-extrabold text-white tracking-tight mt-4">
              Ronin Mode &amp; <span className="bg-gradient-to-r from-[#ef4444] to-[#f97316] bg-clip-text text-transparent">The Self-Improving Loop</span>
            </h2>
            <p className="mt-4 text-slate-300 text-base leading-relaxed">
              Observability without reflexes is just an expensive audit log. Order Samurai pairs continuous background Ronins with real-time reflex interception and overnight Dojo cycles — transforming reactive agent oversight into an autonomous self-healing engine.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mb-12">
            <div className="bg-[#0c121e] border border-white/10 rounded-2xl p-7 flex flex-col justify-between shadow-xl">
              <div>
                <span className="text-xs font-mono font-semibold text-[#3b82f6] uppercase tracking-wider">01 · THE RONIN LOOP</span>
                <h3 className="text-xl font-bold text-white mt-2">Continuous Fleet Guardian</h3>
                <p className="mt-3 text-xs text-slate-400 leading-relaxed">
                  Operates as an autonomous background guardian across IDE namespaces and agent runtimes. Evaluates every tool execution and file change against the four-pillar governance contract without requiring active prompt engineering.
                </p>
              </div>
            </div>

            <div className="bg-[#0c121e] border border-[#ef4444]/30 rounded-2xl p-7 flex flex-col justify-between shadow-xl shadow-[#ef4444]/5">
              <div>
                <span className="text-xs font-mono font-semibold text-[#ef4444] uppercase tracking-wider">02 · REFLEX ALERTS</span>
                <h3 className="text-xl font-bold text-white mt-2">Defending the Floor</h3>
                <p className="mt-3 text-xs text-slate-400 leading-relaxed">
                  Deterministic, zero-latency interception. When a pillar metric degrades (prompt injection attempt, secret in staged diff, runaway spend spike), reflexes fire instantly to block the action and isolate the process before failures compound.
                </p>
              </div>
            </div>

            <div className="bg-[#0c121e] border border-[#f59e0b]/30 rounded-2xl p-7 flex flex-col justify-between shadow-xl shadow-[#f59e0b]/5">
              <div>
                <span className="text-xs font-mono font-semibold text-[#f59e0b] uppercase tracking-wider">03 · DOJO CYCLES</span>
                <h3 className="text-xl font-bold text-white mt-2">Raising the Ceiling</h3>
                <p className="mt-3 text-xs text-slate-400 leading-relaxed">
                  Structured, autonomous training runs (Keiko) that run while you sleep. The Dojo processes task backlogs, executes automated regression sweeps, stages patches via maker-checker verification, and continuously recalibrates baselines.
                </p>
              </div>
            </div>
          </div>

          <div className="bg-[#0b101b] border border-white/10 rounded-2xl p-8 grid grid-cols-1 lg:grid-cols-2 gap-8 items-center">
            <div>
              <span className="text-xs font-mono font-semibold text-[#10b981] uppercase tracking-wider">SELF-IMPROVING FEEDBACK LOOP</span>
              <h3 className="text-2xl font-bold text-white mt-2">From Traps to Calibrated Baselines</h3>
              <p className="mt-3 text-sm text-slate-300 leading-relaxed">
                Every intercepted prompt injection and completed Dojo work-unit feeds back into local calibration coefficients. The system learns the exact baseline performance of your fleet across Claude, Codex, Antigravity, and Cursor — making defenses sharper every night.
              </p>
            </div>
            <div className="bg-slate-950 border border-white/10 rounded-xl p-6 font-mono text-xs space-y-2">
              <div className="text-slate-500 pb-2 border-b border-white/10">AGENTIC LOOP FLOW</div>
              <div className="text-[#ef4444]">1. REACTION <span className="text-slate-400">→ Intercept injection / scrub secret</span></div>
              <div className="text-[#f59e0b]">2. ISOLATION <span className="text-slate-400">→ Stage patch to pending_remediation</span></div>
              <div className="text-[#3b82f6]">3. DOJO CYCLES <span className="text-slate-400">→ Execute overnight keiko backlog run</span></div>
              <div className="text-[#10b981]">4. CALIBRATION <span className="text-slate-400">→ Update empirical metrics &amp; thresholds</span></div>
            </div>
          </div>
        </div>
      </section>

      {/* Social Proof & Trust Badges Section */}
      <section id="proof" className="relative z-10 py-20 border-t border-white/5 bg-[#090d15]">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center max-w-3xl mx-auto mb-16">
            <span className="px-3 py-1 text-xs font-mono font-semibold uppercase tracking-wider bg-[#10b981]/10 text-[#10b981] border border-[#10b981]/20 rounded-full">
              Social Proof & Compliance
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-white tracking-tight mt-4">
              Trusted By Security & AI Operations Leaders
            </h2>
            <p className="mt-3 text-slate-400 text-base">
              Engineered for strict zero-trust environments requiring zero cloud telemetry and deterministic safety.
            </p>
          </div>

          {/* Testimonial Cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mb-16">
            <div className="bg-[#0e1422] border border-white/10 rounded-2xl p-6 relative flex flex-col justify-between shadow-xl">
              <div>
                <div className="flex items-center gap-1 text-[#f59e0b] mb-4">
                  {[...Array(5)].map((_, i) => (
                    <Star key={i} size={16} fill="currentColor" />
                  ))}
                </div>
                <Quote size={24} className="text-[#ef4444]/40 mb-3" />
                <p className="text-xs text-slate-300 italic leading-relaxed">
                  "Order Samurai caught a prompt injection trying to leak our AWS credentials during a subagent sweep. It paid for itself 100x over on day one."
                </p>
              </div>
              <div className="mt-6 pt-4 border-t border-white/10 flex items-center gap-3">
                <div className="w-9 h-9 rounded-full bg-[#ef4444]/20 border border-[#ef4444]/40 flex items-center justify-center font-bold text-xs text-[#ef4444]">
                  MV
                </div>
                <div>
                  <h4 className="text-xs font-bold text-white">Marcus Vance</h4>
                  <p className="text-[11px] text-slate-400">Principal Security Architect</p>
                </div>
              </div>
            </div>

            <div className="bg-[#0e1422] border border-white/10 rounded-2xl p-6 relative flex flex-col justify-between shadow-xl">
              <div>
                <div className="flex items-center gap-1 text-[#f59e0b] mb-4">
                  {[...Array(5)].map((_, i) => (
                    <Star key={i} size={16} fill="currentColor" />
                  ))}
                </div>
                <Quote size={24} className="text-[#3b82f6]/40 mb-3" />
                <p className="text-xs text-slate-300 italic leading-relaxed">
                  "Finally, an agent governance system that doesn't send our proprietary codebase to a third-party SaaS telemetry endpoint. Zero cloud leaks."
                </p>
              </div>
              <div className="mt-6 pt-4 border-t border-white/10 flex items-center gap-3">
                <div className="w-9 h-9 rounded-full bg-[#3b82f6]/20 border border-[#3b82f6]/40 flex items-center justify-center font-bold text-xs text-[#3b82f6]">
                  ER
                </div>
                <div>
                  <h4 className="text-xs font-bold text-white">Dr. Elena Rostova</h4>
                  <p className="text-[11px] text-slate-400">Head of AI Platform</p>
                </div>
              </div>
            </div>

            <div className="bg-[#0e1422] border border-white/10 rounded-2xl p-6 relative flex flex-col justify-between shadow-xl">
              <div>
                <div className="flex items-center gap-1 text-[#f59e0b] mb-4">
                  {[...Array(5)].map((_, i) => (
                    <Star key={i} size={16} fill="currentColor" />
                  ))}
                </div>
                <Quote size={24} className="text-[#10b981]/40 mb-3" />
                <p className="text-xs text-slate-300 italic leading-relaxed">
                  "The secret scrubbing and deterministic local hooks give our engineering team peace of mind while running Claude Code in autonomous mode."
                </p>
              </div>
              <div className="mt-6 pt-4 border-t border-white/10 flex items-center gap-3">
                <div className="w-9 h-9 rounded-full bg-[#10b981]/20 border border-[#10b981]/40 flex items-center justify-center font-bold text-xs text-[#10b981]">
                  DC
                </div>
                <div>
                  <h4 className="text-xs font-bold text-white">Devon Chen</h4>
                  <p className="text-[11px] text-slate-400">Lead DevSecOps Specialist</p>
                </div>
              </div>
            </div>
          </div>

          {/* Trust & Accreditation Grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
            <div className="bg-[#0d121c] border border-white/10 p-4 rounded-xl">
              <div className="text-sm font-bold text-white">100% Local</div>
              <div className="text-[11px] text-slate-400 font-mono mt-0.5">Zero Cloud Telemetry</div>
            </div>
            <div className="bg-[#0d121c] border border-white/10 p-4 rounded-xl">
              <div className="text-sm font-bold text-white">NIST AI RMF 1.0</div>
              <div className="text-[11px] text-slate-400 font-mono mt-0.5">Compliance Aligned</div>
            </div>
            <div className="bg-[#0d121c] border border-white/10 p-4 rounded-xl">
              <div className="text-sm font-bold text-white">OWASP Agentic Top 10</div>
              <div className="text-[11px] text-slate-400 font-mono mt-0.5">Mitigation Guard</div>
            </div>
            <div className="bg-[#0d121c] border border-white/10 p-4 rounded-xl">
              <div className="text-sm font-bold text-white">389+ Tests Passed</div>
              <div className="text-[11px] text-slate-400 font-mono mt-0.5">Continuous CI Audit</div>
            </div>
          </div>
        </div>
      </section>

      {/* Metric Honesty Matrix Table */}
      <section id="honesty" className="relative z-10 py-20 border-t border-white/5 max-w-7xl mx-auto px-6">
        <div className="text-center max-w-2xl mx-auto mb-12">
          <h2 className="text-3xl font-bold text-white tracking-tight">The Honesty Invariant</h2>
          <p className="mt-3 text-slate-400 text-base">
            We publish an explicit telemetry matrix. Calibration benchmarks are clearly labeled as <span className="text-[#f59e0b] font-semibold">SIMULATED</span> until 20 empirical samples accumulate.
          </p>
        </div>

        <div className="overflow-x-auto rounded-2xl border border-white/10 bg-[#0d131f] shadow-2xl">
          <table className="w-full text-left border-collapse text-sm">
            <thead>
              <tr className="border-b border-white/10 bg-slate-900/80 text-slate-400 font-mono text-xs uppercase">
                <th className="py-4 px-6">Pillar Metric</th>
                <th className="py-4 px-6">Status (v1)</th>
                <th className="py-4 px-6">Data Source</th>
                <th className="py-4 px-6">Calibration Threshold</th>
                <th className="py-4 px-6">Audit Trail</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-slate-300">
              <tr className="hover:bg-white/[0.02] transition-colors">
                <td className="py-4 px-6 font-semibold text-white flex items-center gap-2">
                  <IconKatana size={18} color="#ef4444" /> Kill Chains Disrupted
                </td>
                <td className="py-4 px-6">
                  <span className="px-2.5 py-1 text-xs font-bold bg-[#10b981]/20 text-[#10b981] border border-[#10b981]/30 rounded-full">
                    MEASURED
                  </span>
                </td>
                <td className="py-4 px-6 font-mono text-xs text-slate-400">state/kill_chain_events.jsonl</td>
                <td className="py-4 px-6 text-xs text-slate-400">Immediate (Count-based)</td>
                <td className="py-4 px-6 text-xs text-slate-400">Atomic append log with hook source & confidence</td>
              </tr>
              <tr className="hover:bg-white/[0.02] transition-colors">
                <td className="py-4 px-6 font-semibold text-white flex items-center gap-2">
                  <IconShuriken size={18} color="#3b82f6" /> Estimated Agent Time Saved
                </td>
                <td className="py-4 px-6">
                  <span className="px-2.5 py-1 text-xs font-bold bg-[#f59e0b]/20 text-[#f59e0b] border border-[#f59e0b]/30 rounded-full">
                    SIMULATED / CALIBRATING
                  </span>
                </td>
                <td className="py-4 px-6 font-mono text-xs text-slate-400">state/DOJO_STATE.json</td>
                <td className="py-4 px-6 text-xs text-slate-400">20 completed timed backlog items</td>
                <td className="py-4 px-6 text-xs text-slate-400">Wall-clock duration × kind baseline coefficient</td>
              </tr>
              <tr className="hover:bg-white/[0.02] transition-colors">
                <td className="py-4 px-6 font-semibold text-white flex items-center gap-2">
                  <IconFan size={18} color="#10b981" /> Estimated Cost Savings
                </td>
                <td className="py-4 px-6">
                  <span className="px-2.5 py-1 text-xs font-bold bg-[#10b981]/20 text-[#10b981] border border-[#10b981]/30 rounded-full">
                    MEASURED
                  </span>
                </td>
                <td className="py-4 px-6 font-mono text-xs text-slate-400">state/budget_ledger.json</td>
                <td className="py-4 px-6 text-xs text-slate-400">14 daily ledger entries</td>
                <td className="py-4 px-6 text-xs text-slate-400">Actual spend delta + Anthropic list price math</td>
              </tr>
              <tr className="hover:bg-white/[0.02] transition-colors">
                <td className="py-4 px-6 font-semibold text-white flex items-center gap-2">
                  <IconArmor size={18} color="#8b5cf6" /> Estimated Human Time Saved
                </td>
                <td className="py-4 px-6">
                  <span className="px-2.5 py-1 text-xs font-bold bg-[#f59e0b]/20 text-[#f59e0b] border border-[#f59e0b]/30 rounded-full">
                    SIMULATED / CALIBRATING
                  </span>
                </td>
                <td className="py-4 px-6 font-mono text-xs text-slate-400">autonomic_events.jsonl</td>
                <td className="py-4 px-6 text-xs text-slate-400">20 craft alignment signals</td>
                <td className="py-4 px-6 text-xs text-slate-400">Vibe alignment + doc parity latency delta</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      {/* Self-Serve Pricing Section with CTV Buttons */}
      <section id="pricing" className="relative z-10 py-20 border-t border-white/5 max-w-7xl mx-auto px-6">
        <div className="text-center max-w-2xl mx-auto mb-16">
          <h2 className="text-3xl font-bold text-white tracking-tight">Simple, Self-Serve Pricing</h2>
          <p className="mt-3 text-slate-400 text-base">
            Start free with Open Core on your local machine. Upgrade to Team or Pro for customer-hosted fleet management and compliance packs.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mt-12">
          {/* OSS Core Tier */}
          <div className="bg-[#0d131f] border border-white/10 rounded-2xl p-8 flex flex-col justify-between hover:border-slate-500 transition-all">
            <div>
              <span className="text-xs font-mono text-slate-400 uppercase tracking-wider font-semibold">Open Source</span>
              <h3 className="text-2xl font-bold text-white mt-1">OSS Core</h3>
              <div className="mt-4 flex items-baseline gap-1">
                <span className="text-4xl font-extrabold text-white">$0</span>
                <span className="text-xs text-slate-400">/ forever free</span>
              </div>
              <p className="mt-4 text-xs text-slate-400 leading-relaxed">
                Apache 2.0 open source. Full local telemetry, 4-pillar scoring, and alert-only spend caps.
              </p>
              <ul className="mt-6 space-y-3 text-xs text-slate-300">
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#10b981]" /> All 14 ATT&CK Kill Chain Monitors
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#10b981]" /> 100% Local-First Execution
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#10b981]" /> CLI &amp; Static Local HTML Reports
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#10b981]" /> Secret Scrubber &amp; Injection Guard
                </li>
              </ul>
            </div>
            {/* CTV Button */}
            <a
              href="order-samurai-core.zip"
              download
              className="mt-8 w-full py-3 px-4 bg-slate-800 hover:bg-slate-700 text-white rounded-xl font-bold text-sm transition-colors border border-white/10 flex items-center justify-center gap-2 text-center"
            >
              <ShieldCheck size={16} />
              Download Core Version (.zip)
            </a>
          </div>

          {/* Pro Lifetime Tier */}
          <div className="bg-[#0d131f] border-2 border-[#3b82f6] rounded-2xl p-8 flex flex-col justify-between relative shadow-2xl shadow-[#3b82f6]/10">
            <div>
              <span className="text-xs font-mono text-[#3b82f6] uppercase tracking-wider font-semibold">PRO VERSION</span>
              <h3 className="text-2xl font-bold text-white mt-1">Pro Lifetime</h3>
              <div className="mt-4 flex items-baseline gap-1">
                <span className="text-4xl font-extrabold text-white">$199</span>
                <span className="text-xs text-slate-400">/ one-time payment</span>
              </div>
              <p className="mt-4 text-xs text-slate-400 leading-relaxed">
                Full local Reflex Engine with active spend-capping, Nightly Dojo, and offline license key.
              </p>
              <ul className="mt-6 space-y-3 text-xs text-slate-300">
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#3b82f6]" /> Everything in OSS Core
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#3b82f6]" /> Active Spend-Cap Enforcement (Runtime Kill)
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#3b82f6]" /> Nightly Dojo &amp; Autonomous Remediation
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#3b82f6]" /> Maker-Checker Patch Staging &amp; Receipts
                </li>
              </ul>
            </div>
            {/* CTV Button */}
            <a
              href="https://jemakaib1.gumroad.com/l/sqwomh"
              target="_blank"
              rel="noopener noreferrer"
              className="mt-8 w-full py-3 px-4 bg-[#3b82f6] hover:bg-[#2563eb] text-white rounded-xl font-bold text-sm transition-colors shadow-lg shadow-[#3b82f6]/25 flex items-center justify-center gap-2 text-center"
            >
              <Sparkles size={16} />
              Get Pro Lifetime ($199)
            </a>
          </div>

          {/* Compliance Tier */}
          <div className="bg-[#0d131f] border border-white/10 rounded-2xl p-8 flex flex-col justify-between hover:border-slate-500 transition-all">
            <div>
              <span className="text-xs font-mono text-[#ef4444] uppercase tracking-wider font-semibold">Enterprise Fleet</span>
              <h3 className="text-2xl font-bold text-white mt-1">Compliance</h3>
              <div className="mt-4 flex items-baseline gap-1">
                <span className="text-4xl font-extrabold text-white">$499</span>
                <span className="text-xs text-slate-400">/ month</span>
              </div>
              <p className="mt-4 text-xs text-slate-400 leading-relaxed">
                Hosted team dashboard, multi-project fleet aggregation, and audit-grade regulatory evidence exports.
              </p>
              <ul className="mt-6 space-y-3 text-xs text-slate-300">
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#ef4444]" /> Everything in Pro Lifetime
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#ef4444]" /> Multi-Project Hosted Team Dashboard
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#ef4444]" /> NIST AI RMF &amp; EU AI Act Evidence Packs
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-[#ef4444]" /> Audit Retention &amp; Signed Policy Bundles
                </li>
              </ul>
            </div>
            {/* CTV Button */}
            <button
              onClick={() => setCheckoutTier("pro")}
              className="mt-8 w-full py-3 px-4 bg-gradient-to-r from-[#ef4444] to-[#dc2626] hover:from-[#dc2626] text-white rounded-xl font-bold text-sm transition-colors shadow-lg shadow-[#ef4444]/20 flex items-center justify-center gap-2"
            >
              <Lock size={16} />
              Contact Sales ($499/mo)
            </button>
          </div>
        </div>
        </div>

        {/* Core vs Pro Initial Audit Comparison Matrix Table */}
        <div className="mt-16 max-w-4xl mx-auto overflow-x-auto">
          <h3 className="text-2xl font-bold text-white text-center mb-6">
            Core vs Pro Initial Audit Comparison
          </h3>
          <div className="rounded-2xl border border-white/10 bg-[#0d131f] shadow-2xl overflow-hidden">
            <table className="w-full text-left border-collapse text-xs sm:text-sm font-mono">
              <thead>
                <tr className="border-b border-white/10 bg-slate-900/80 text-amber-400 font-semibold uppercase text-xs">
                  <th className="py-3.5 px-6">Governance &amp; Audit Capability</th>
                  <th className="py-3.5 px-6">Free Core ($0)</th>
                  <th className="py-3.5 px-6 text-[#ef4444]">Pro Lifetime ($199)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-slate-300">
                <tr className="hover:bg-white/[0.02] transition-colors">
                  <td className="py-3.5 px-6 font-semibold text-white">Automatic First-Install Audit</td>
                  <td className="py-3.5 px-6 text-[#10b981] font-semibold">✓ 60-Second Log Ingestion</td>
                  <td className="py-3.5 px-6 text-[#10b981] font-semibold">✓ 60-Second Log Ingestion</td>
                </tr>
                <tr className="hover:bg-white/[0.02] transition-colors">
                  <td className="py-3.5 px-6 font-semibold text-white">Historical Audit Depth</td>
                  <td className="py-3.5 px-6 text-slate-400">7-Day History Window</td>
                  <td className="py-3.5 px-6 text-[#38bdf8] font-semibold">Full 90-Day Trajectory Archive</td>
                </tr>
                <tr className="hover:bg-white/[0.02] transition-colors">
                  <td className="py-3.5 px-6 font-semibold text-white">14-Chain ATT&amp;CK Security Hooks</td>
                  <td className="py-3.5 px-6 text-[#10b981] font-semibold">✓ Fail-Closed Interception</td>
                  <td className="py-3.5 px-6 text-[#10b981] font-semibold">✓ Fail-Closed Interception</td>
                </tr>
                <tr className="hover:bg-white/[0.02] transition-colors">
                  <td className="py-3.5 px-6 font-semibold text-white">Initial Vulnerability Sweep</td>
                  <td className="py-3.5 px-6 text-slate-400">Scrubbing &amp; Log Warnings</td>
                  <td className="py-3.5 px-6 text-[#ef4444] font-semibold">Auto-Stages Fix Patches</td>
                </tr>
                <tr className="hover:bg-white/[0.02] transition-colors">
                  <td className="py-3.5 px-6 font-semibold text-white">Nightly Dojo &amp; Autonomous Reflexes</td>
                  <td className="py-3.5 px-6 text-slate-500">— Manual Only</td>
                  <td className="py-3.5 px-6 text-[#f59e0b] font-semibold">✓ Automated Overnight Runs</td>
                </tr>
                <tr className="hover:bg-white/[0.02] transition-colors">
                  <td className="py-3.5 px-6 font-semibold text-white">Spend Capping &amp; Model Routing</td>
                  <td className="py-3.5 px-6 text-slate-400">Alert-Only Warning Caps</td>
                  <td className="py-3.5 px-6 text-[#4ade80] font-semibold">✓ Active Runtime Kill Enforcement</td>
                </tr>
              </tbody>
            </table>
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
                      <div className="bg-slate-950 p-3 rounded-xl font-mono text-xs text-[#10b981] border border-white/5">
                        curl -fsSL https://raw.githubusercontent.com/order-samurai/order-samurai/main/install.sh | bash
                      </div>
                      <button
                        onClick={closeCheckout}
                        className="w-full py-3 bg-[#10b981] text-white rounded-xl font-semibold text-sm hover:bg-[#059669] transition-colors"
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
                          placeholder="Jemakai Blyden"
                          className="w-full bg-slate-900 border border-white/10 rounded-lg px-3.5 py-2 text-sm text-white focus:outline-none focus:border-[#3b82f6]"
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
                          className="w-full bg-slate-900 border border-white/10 rounded-lg px-3.5 py-2 text-sm text-white font-mono focus:outline-none focus:border-[#3b82f6]"
                        />
                      </div>

                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <label className="block text-xs font-semibold text-slate-400 mb-1">Expires</label>
                          <input
                            type="text"
                            required
                            placeholder="12/28"
                            className="w-full bg-slate-900 border border-white/10 rounded-lg px-3.5 py-2 text-sm text-white font-mono focus:outline-none focus:border-[#3b82f6]"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-semibold text-slate-400 mb-1">CVC</label>
                          <input
                            type="text"
                            required
                            placeholder="123"
                            className="w-full bg-slate-900 border border-white/10 rounded-lg px-3.5 py-2 text-sm text-white font-mono focus:outline-none focus:border-[#3b82f6]"
                          />
                        </div>
                      </div>

                      <div className="pt-2">
                        <button
                          type="submit"
                          className="w-full py-3 bg-gradient-to-r from-[#ef4444] to-[#dc2626] hover:from-[#dc2626] text-white rounded-xl font-bold text-sm shadow-lg shadow-[#ef4444]/25 transition-all"
                        >
                          Activate ${checkoutTier === "team" ? "49" : "99"} Subscription
                        </button>
                      </div>
                    </form>
                  )}
                </div>
              ) : (
                <div className="text-center py-6">
                  <div className="w-16 h-16 bg-[#10b981]/20 text-[#10b981] rounded-full flex items-center justify-center mx-auto mb-4">
                    <CheckCircle2 size={32} />
                  </div>
                  <h3 className="text-2xl font-bold text-white">Subscription Active!</h3>
                  <p className="text-xs text-slate-400 mt-2">
                    Your Order Samurai {checkoutTier?.toUpperCase()} License Key has been generated:
                  </p>
                  <div className="mt-4 bg-slate-950 p-3 rounded-xl font-mono text-xs text-[#10b981] border border-white/10 select-all">
                    SAMURAI-PRO-KEY-2026-7781-9921-X
                  </div>
                  <button
                    onClick={closeCheckout}
                    className="mt-6 w-full py-3 bg-slate-800 hover:bg-slate-700 text-white rounded-xl font-semibold text-sm"
                  >
                    Close & Start Using Order Samurai
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
            <a href="terms.html" className="hover:text-slate-300">Terms & EULA</a>
            <a href="privacy.html" className="hover:text-slate-300">Privacy Policy</a>
            <a href="security.html" className="hover:text-slate-300">Security</a>
            <a href="mailto:support@agentica.biz" className="hover:text-slate-300">Report Bug (support@agentica.biz)</a>
            <span className="text-[#4ade80] font-medium flex items-center gap-1">🛡️ 14-Day Money-Back Guarantee</span>
          </div>
        </div>
      </footer>
    </div>
  )
}
