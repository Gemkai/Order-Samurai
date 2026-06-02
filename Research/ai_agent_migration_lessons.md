# I Gave My AI Agent Its Own Computer. Here’s Every Lesson From 72 Hours of Migration.

> I spent a weekend moving my entire AI agent architecture from my MacBook to a dedicated Mac Mini. It broke in ways I didn’t expect.
> 
> **Author:** Pawel Jozefiak  
> **Date:** Mar 16, 2026

Some things were easier than I thought. And a few surprises changed how I think about running AI agents entirely.

I bought a Mac Mini. Not because of hype. Not because someone on Twitter said it was cool. I bought it because after months of running an AI agent on my personal laptop, I hit a wall that kept getting higher.

The wall was simple: every time I took my MacBook somewhere, my agent stopped working.

I had built workarounds. Scripts that prevented sleep. Auto-wake systems that restarted everything when I opened the lid again. It worked. Until it didn’t. Because the fundamental problem was that my personal computer and my AI agent’s computer were the same device. And that creates friction in ways you don’t expect until you live with it for months.

So I started thinking about a dedicated machine. Not “wouldn’t it be nice” thinking. Practical “what are my options” thinking.

## Why Mac Mini (And What I Considered Instead)
I looked at everything. Seriously.

*   **Raspberry Pi.** Cheap. Could probably run a Claude Code-based agent. But administrating it would be rough. ARM Linux, limited RAM, no macOS ecosystem. Every tool I’d built relied on AppleScript, macOS permissions, iMessage, Calendar. Starting over on Linux felt like rebuilding from scratch for no good reason.
*   **Mac Studio.** The dream machine. Enough power to run serious local LLMs. Real independence from API keys and subscriptions. But expensive. And here’s the thing I kept coming back to: local models are getting better fast. The hardware you need to run them is getting smaller every few months. I ran Qwen 3.5 on my M1 Pro MacBook and it worked. Not great for daily driving, but it worked. In two years, the Mac Studio I’d buy today would be overkill for what local models will need. The bet I’m making is that models get more efficient faster than hardware gets cheaper.
*   **Custom PC / mini server.** Same Linux problem as Raspberry Pi, times ten. I’m deep in the Apple ecosystem. My agent uses iMessage, Apple Mail, Calendar, Reminders, the Passwords app. Rebuilding all that on Linux would take weeks and produce something worse.

**Mac Mini was the answer.** Base model. Cheapest option. M4 chip, which is genuinely more than enough for an AI agent that mostly coordinates cloud APIs and runs local scripts. Cost-effective, same ecosystem, small enough to sit on a shelf and forget about.

I walked into a store on a Friday and started migrating the same evening.

---

## The Differences

Before the migration story, you need to understand what actually changes when your agent moves from your daily-driver laptop to a dedicated headless machine. These aren’t obvious until you’re in the middle of it.

### 1. Headless means no display. That breaks more than you think.
When your AI agent runs on your laptop, it has a screen. Tools like Peekaboo (which controls macOS UI elements) just work. Screenshots just work. Anything that needs to “see” what’s on screen just works.

Mac Mini with no monitor attached? None of that works.

I didn’t fully appreciate this until things started failing silently. `screencapture` returned empty files. UI automation scripts ran but couldn’t find any windows. My agent was trying to interact with a desktop that technically didn’t exist.

**The fix: BetterDisplay.** This app creates a persistent virtual display that macOS treats as real. I set up a 5K virtual screen called “WizDisplay” that auto-starts when the Mac Mini boots. It has an HTTP API so my health monitor can check if it’s still alive and reconnect it automatically if BetterDisplay crashes. After this, `screencapture`, Peekaboo, and all UI automation worked perfectly.

*Why not Apple’s built-in CGVirtualDisplay API?* I tried. It requires special entitlements that only signed apps can use. A standalone Python or Swift script can’t create a virtual display. BetterDisplay handles those entitlements as a signed app. This took me an entire day to figure out.

### 2. Full machine authority changes the game.
On my MacBook, my agent had limited permissions. I controlled which files it could access, which system settings it could change. Made sense. My personal data was there. Work documents. Passwords. Everything.

On a dedicated Mac Mini with a fresh macOS install and nothing personal on it? Different story.

I gave Wiz full root access. Passwordless sudo. Full Disk Access. Screen Recording. Accessibility permissions. Every Automation grant (Messages, Mail, Calendar, Contacts, Finder, Safari, Reminders, Notes, Photos). All of it.

This sounds reckless. It’s not. There is nothing on this machine that I’m afraid of losing. It’s a clean environment built specifically for the agent. If something goes wrong, I wipe it and start over. The real data lives in Git and iCloud.

The practical difference is huge. My agent can now install software, modify system preferences, manage LaunchAgents, fix permission issues, and restart services without ever asking me. 

### 3. Clean slate means you rebuild everything.
I installed a fresh macOS. No migration from my MacBook. New user account just for the agent. Completely clean.

This means: no apps, no calendars, no messages, no mail, no documents, no browser sessions, no saved passwords. Everything my agent could access on my MacBook through proximity and shared filesystem? Gone.

This is actually healthy. It forces you to be intentional about what your agent has access to, rather than the lazy “it can see everything because we share a filesystem” approach.

### 4. Your laptop stops being a server.
My M1 Pro MacBook was fine for most things. But running AI agents alongside my actual work? It got sluggish. Browser tabs competing with background automation. Local model experiments eating RAM that I needed for Xcode or Figma.

Now my MacBook is just my MacBook again. Fast, clean, mine. All 25 LaunchAgents that used to run in the background? Moved to Mac Mini. 

### 5. Development needs Git discipline.
When everything lived on one machine, there was no friction. Edit a file, run it, done. Now I have code on two machines. My MacBook (where I sometimes write code) and the Mac Mini (where it runs).

GitHub became essential, not optional. I created a separate branch (`wiz-mini`) for the Mac Mini paths. Push to main from MacBook, Mac Mini fetches and rebases.

---

## The Migration Itself

### Step 1: Map everything first.
Before I touched the Mac Mini, I asked Wiz to create a complete inventory of itself. Every automation. Every cron job. Every LaunchAgent. Every skill. Every script that runs on a schedule. Every external service it connects to. Every file path it depends on.

### Step 2: Fresh install, not migration.
I updated macOS to the latest version on the Mac Mini. Created a new user account for the agent. Did not use Apple’s migration assistant. Starting clean meant I knew exactly what was on the machine because I put it there.

### Step 3: SSH first, everything else second.
The first thing I set up was SSH access from my MacBook to the Mac Mini. Then **Tailscale**, so I could reach it from anywhere, not just my home network.

### Step 4: The path problem.
My MacBook username was `joozio`. My Mac Mini username is `wiz`. Every hardcoded path in every script had to change from `/Users/joozio/` to `/Users/wiz/`.

Don’t forget: If you use Claude Code skills, custom tools, or any configuration outside your main repo, those paths need updating too.

### Step 5: Turn off the old, turn on the new.
I unloaded all LaunchAgents on the MacBook, verified they were stopped, then activated them on the Mac Mini. The WizBoard hooks, the Discord bot, the iMessage watcher, the health monitor, the daily planner. One by one.

---

## The Virtual Display Problem (This Deserves Its Own Section)
This was the single most frustrating issue of the entire migration.

Day one, everything seemed fine. Agent was running, responding, executing tasks. Then I noticed: screenshots were blank. Peekaboo captures returned nothing. UI automation silently failed.

The problem: macOS on a headless Mac Mini doesn’t initialize a graphics context. There’s no display, so the system decides there’s nothing to render. 

**The Solution:** BetterDisplay. An app that handles virtual display creation as a signed, entitled macOS application. 

If you’re setting up a headless Mac for any kind of UI automation or screen interaction, BetterDisplay is not optional. It’s essential. 

---

## Communication Channels: iMessage Changes Everything
Before the Mac Mini, I had three ways to talk to my agent: CLI (SSH), Discord DMs, and email. The Mac Mini added something new: **iMessage.**

Because the Mac Mini has its own Apple ID, it has its own iMessage. I added it as a contact on my iPhone. Now I can text my AI agent like I’d text a person.

**File sharing solution:** Since the Mac Mini has its own iCloud account, I created a shared folder called “Wiz Shared” between my Apple ID and the agent’s. Everything the agent generates (PDFs, images, exports) goes there.

---

## The Password Problem
I use Apple’s built-in Passwords app. For agent credentials, I created a shared password group between my Apple ID and the agent’s Apple ID. I share specific passwords, not everything.

Please don’t do this: Notes files with passwords in plain text. Use encrypted password sharing.

---

## The OpenClaw Comparison (Honest Take After 3 Days)

I installed OpenClaw side-by-side with Wiz. Here’s the assessment:

### Where OpenClaw is clearly better:
*   **Onboarding and setup.** The Mac companion app (Codex.app) and installation wizard make it dramatically easier for non-technical users.
*   **Communication channels.** Supports 20+ platforms out of the box (WhatsApp, Telegram, Slack, etc.).
*   **Community and ecosystem.** 52 available skills and regular updates.

### Where Wiz holds its own (or wins):
*   **Stability.** Custom self-healing monitors and error registries battle-tested over months.
*   **Personalization depth.** Months of accumulated context about my ADHD patterns, goals, and communication style.
*   **Custom workflows.** Systems built exactly for how I work with no compromises.

---

## What I’d Do Differently
1. Set up **BetterDisplay** first.
2. Audit every skill and tool dependency before migrating.
3. Migrate one LaunchAgent at a time.
4. Set up **Tailscale** immediately.
5. Keep the old machine running in parallel for a week.

## Is It Worth It?
**Absolutely.**

My laptop is my laptop again. My agent runs 24/7 without depending on whether my lid is open or I'm using too much RAM. It has more permissions, more autonomy, more stability. 

Your agent just runs. Always on. Always ready. Even when you’re asleep.
