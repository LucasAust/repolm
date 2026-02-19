# RepoLM Launch Content

---

## 🐦 Twitter/X Launch Thread

**Tweet 1 (hook):**
> I built a tool that turns any GitHub repo into a learning experience.
> 
> Paste a URL → get an architecture overview, a podcast, slide deck, and interactive chat.
> 
> It adapts to your level — beginner to expert.
> 
> Free to try: https://repolm.com
> 
> Here's how it works 🧵

**Tweet 2 (overview feature):**
> 📖 Overview Mode
> 
> Paste any repo and get a structured breakdown:
> - What it does (TL;DR)
> - Architecture & how it works
> - Key patterns & concepts
> - Tech stack analysis
> 
> Not a generic summary — it references actual files and functions from the code.

**Tweet 3 (podcast feature):**
> 🎙️ Podcast Mode
> 
> Two AI hosts (Alex & Sam) break down the codebase like friends geeking out.
> 
> Sam explains with analogies. Alex asks the questions you'd ask. They reference real function names from the repo.
> 
> You can listen to the audio or read the transcript.

**Tweet 4 (slides feature):**
> 📊 Slide Deck
> 
> Generates a presentation you can actually use:
> - Visual slide viewer with keyboard nav
> - Download as .pptx
> - Code snippets, key takeaways per slide
> 
> Great for study groups, team onboarding, or teaching.

**Tweet 5 (chat + immersive):**
> 💬 Chat with any codebase
> 
> Ask questions and get answers that reference specific files and functions.
> 
> Highlight any code in the viewer and ask "why is this done this way?" — it explains with full context.
> 
> Conversation history means follow-ups work naturally.

**Tweet 6 (levels):**
> 🎚️ Three expertise levels:
> 
> 🌱 Beginner — analogies, step-by-step, minimal code
> 🔧 Amateur — balanced code & explanation
> ⚡ Expert — code-heavy, skip the basics, discuss tradeoffs
> 
> Same repo, completely different experience depending on your level.

**Tweet 7 (CTA):**
> First repo is free, no signup needed.
> 
> Try it: https://repolm.com
> 
> Built with FastAPI, Gemini, and edge-tts.
> 
> If you learn from codebases, I think you'll like this.

---

## 📱 Reddit Posts

### r/learnprogramming

**Title:** I built a free tool that explains any GitHub repo at your skill level — overviews, podcasts, slides, and chat

**Body:**
Hey everyone — I built RepoLM because I was tired of staring at unfamiliar codebases trying to figure out how they work.

You paste a GitHub URL and it generates:
- A structured overview (architecture, key concepts, how it works)
- A podcast-style conversation between two hosts breaking it down
- A slide deck you can download as .pptx
- Interactive chat where you can ask questions about the code

The key thing: you pick your level. Beginner mode uses analogies and avoids jargon. Expert mode is code-heavy and discusses tradeoffs.

It also has a "Learning Paths" section with curated repos organized by concept — event-driven architecture, REST API design, state machines, testing patterns, etc.

First repo is free without even signing up. Would love feedback from actual learners.

https://repolm.com

---

### r/webdev

**Title:** Built a tool that generates architecture overviews, podcasts, and slides from any GitHub repo

**Body:**
I built RepoLM to solve the "how does this codebase actually work" problem.

Paste any GitHub URL and get:
- Architecture overview with TL;DR, collapsible sections, code references
- Podcast script (two hosts discussing the repo) with audio generation
- Slide deck with visual viewer + PPTX download
- Chat that understands the entire codebase

You choose depth (high-level vs low-level) and expertise (beginner/amateur/expert). Same repo, different output.

Tech stack: FastAPI, Gemini 2.5 Pro, edge-tts, SQLite. Handles concurrent users with thread pools and job queuing.

Free tier lets you try it without signing up. Feedback welcome.

https://repolm.com

---

### r/sideproject

**Title:** RepoLM — Paste a GitHub repo, get overviews, podcasts, and slides. Launched today.

**Body:**
Been building this for a few weeks. The idea: make any codebase learnable in minutes.

**What it does:**
- Paste a GitHub URL → get a structured overview, podcast conversation, slide deck
- Chat with the codebase — ask questions, highlight code, get explanations
- Three expertise levels (beginner → expert)
- Concept Lab: describe what you want to learn, AI generates a teaching repo and explains it
- 10 curated learning paths with real repos

**Revenue model:**
- Free tier with ads
- Token packs ($5-$79)
- Pro subscription ($19/mo) and Team ($49/mo)
- Public API for developers
- Concept Lab (premium feature)

**Tech:** FastAPI, Gemini, edge-tts, SQLite, deployed on Railway.

Would love feedback on the product and the pricing. First repo is free.

https://repolm.com

---

### r/programming

**Title:** RepoLM: AI-powered tool that generates architecture overviews, podcasts, and slide decks from GitHub repos

**Body:**
Built a tool that analyzes GitHub repos and generates structured learning content — overviews, podcast scripts with audio, slide decks with PPTX export, and interactive chat.

You pick your expertise level and it adapts: beginners get analogies, experts get code-heavy analysis with tradeoff discussions.

Free to try, no signup for first repo: https://repolm.com

Happy to answer questions about the approach or architecture.

---

## 🟠 Hacker News

**Title:** 
Show HN: RepoLM – Turn any GitHub repo into overviews, podcasts, and slides

**Body (first comment):**
Hi HN — I built RepoLM to make unfamiliar codebases learnable fast.

Paste a GitHub URL, pick your expertise level (beginner/amateur/expert), and get:

- Structured overview with architecture breakdown, key concepts, code references
- Podcast: two AI hosts discuss the repo conversationally (with audio generation)  
- Slide deck: visual viewer + .pptx download
- Chat: ask questions about the codebase with conversation context

The content adapts significantly based on level — beginner mode uses analogies and minimal code, expert mode is code-heavy with tradeoff analysis.

Also has curated "Learning Paths" (repos organized by concept) and a "Concept Lab" where you describe a concept and it generates a small teaching repo.

Stack: FastAPI, Gemini 2.5 Pro, edge-tts, SQLite, Railway.

Free to try: https://repolm.com

Feedback welcome — especially on output quality and what else would be useful.

---

## 🏹 Product Hunt (schedule for a Tuesday or Wednesday morning)

**Tagline:** Learn any GitHub codebase in minutes — AI overviews, podcasts, and slides

**Description:**
RepoLM turns any GitHub repository into learning content adapted to your skill level.

🎯 Paste a URL, pick beginner/amateur/expert
📖 Get architecture overviews with code references  
🎙️ Listen to two AI hosts break it down podcast-style
📊 Download presentation-ready slide decks
💬 Chat with the codebase — ask anything
🧪 Concept Lab: describe what you want to learn, get a custom teaching repo

Built for students, self-taught developers, and anyone who learns by reading code.

**First maker comment:**
I built RepoLM because reading unfamiliar codebases is one of the hardest parts of learning to code. Documentation is often outdated, and jumping between files hoping to understand the architecture wastes hours.

RepoLM analyzes the actual source code and generates content at your level. A beginner learning Express.js gets analogies and step-by-step explanations. A senior engineer gets code-heavy analysis with tradeoff discussions.

The podcast feature surprised me the most — hearing two hosts discuss a codebase conversationally is way more engaging than reading docs.

Would love your feedback on what to build next.

---

## 📋 Posting Schedule

**Day 1 (Launch Day):**
- 9 AM ET: Twitter thread
- 10 AM ET: r/sideproject
- 11 AM ET: r/learnprogramming
- 12 PM ET: Hacker News (Show HN)

**Day 2:**
- 9 AM ET: r/webdev
- 10 AM ET: r/programming

**Day 3:**
- Product Hunt launch (if approved)

**Tips:**
- Reply to EVERY comment in the first 24 hours
- On HN, engage thoughtfully with technical questions
- On Reddit, don't be defensive about criticism — thank people and note the feedback
- Retweet/quote tweet anyone who shares it
- Post a follow-up tweet with early stats: "24 hours: X repos analyzed, Y signups"

---

## 🎬 Video Scripts

### Script 1: Quick Demo (60 seconds — Twitter/TikTok/Shorts)

**[Screen: repolm.com landing page]**

"What if you could understand any GitHub repo in under a minute?"

**[Type "expressjs/express" into the URL bar, hit Go]**

"Paste any GitHub URL..."

**[Show the repo loading, files appearing in sidebar]**

"RepoLM clones it, analyzes every file..."

**[Click Generate → Overview. Show content streaming in]**

"...and gives you a full architecture breakdown. TL;DR at the top, collapsible sections, actual code references."

**[Switch to the expertise toggle, click Beginner → Expert]**

"Set your level. Beginner gets analogies. Expert gets raw code."

**[Click Generate → Podcast. Show the chat bubble UI]**

"Or generate a podcast — two hosts discuss the codebase like they're on a show."

**[Click the Listen button, show audio playing briefly]**

"With actual audio you can listen to."

**[Click Generate → Slides. Show the slide viewer, click through 2-3 slides]**

"Need a presentation? Download it as a PowerPoint."

**[Click PPTX download button]**

**[Switch to Chat tab, type "How does the middleware pipeline work?"]**

"Or just ask questions. It knows the entire codebase."

**[Show response streaming in]**

**[Cut to landing page]**

"repolm.com — first repo is free."

---

### Script 2: Full Walkthrough (3-5 minutes — YouTube/Twitter long-form)

**[Screen: repolm.com]**

"I built something I wish existed when I was learning to code. Let me show you."

**[Click into the app]**

"This is RepoLM. You give it any GitHub repo, and it turns it into actual learning content."

**SECTION 1: Ingestion**

**[Paste https://github.com/pallets/flask, hit Go]**

"Let's try Flask — the Python web framework. I paste the URL, and it clones the repo, analyzes the file structure, builds an import graph to figure out which files matter most..."

**[Show files loading in sidebar]**

"Now I've got the full codebase. I can browse any file right here."

**[Click on a file, show code viewer]**

"But reading files isn't the point. Watch this."

**SECTION 2: Overview**

**[Click Generate → Overview]**

"I'll generate an overview. You can see it streaming in real-time — this isn't a canned response, it's analyzing Flask's actual source code right now."

**[Show content appearing, pause to read the TL;DR]**

"It starts with a TL;DR, then breaks down architecture, key concepts, how the request lifecycle works... and every section is collapsible so you can focus on what matters to you."

**[Click open/closed a few sections]**

"Notice it's referencing real files — app.py, ctx.py, the Blueprint class. This isn't generic."

**SECTION 3: Levels**

**[Switch to Beginner mode in the top bar]**

"Here's the magic. I switch to Beginner mode..."

**[Generate overview again]**

"Same repo, completely different explanation. Now it's using analogies — 'think of Flask like a restaurant where routes are the menu items...' No jargon, no assumptions."

**[Switch to Expert]**

"Expert mode? Code-heavy. It shows you the WSGI internals, discusses the tradeoffs of the application factory pattern, references specific decorators."

**SECTION 4: Podcast**

**[Click Generate → Podcast]**

"Now this is my favorite feature. It generates a podcast script — two hosts, Alex and Sam, discussing the codebase."

**[Show the chat bubble UI with the conversation]**

"Alex asks the questions you'd ask. Sam explains with analogies. They reference actual function names from Flask's source."

**[Scroll to show stage directions like [LAUGHS] and code references]**

"And you can generate actual audio..."

**[Click Listen, let it play for 5-10 seconds]**

"...and listen to it while you commute or work out."

**SECTION 5: Slides**

**[Click Generate → Slides]**

"If you need a presentation — maybe for a study group or a tech talk — it builds a slide deck."

**[Show the slide viewer, click through a few slides]**

"Visual viewer, keyboard navigation... and you can download it as an actual PowerPoint file."

**[Click PPTX download]**

**SECTION 6: Chat**

**[Switch to Chat tab]**

"And then there's chat. This is where it gets really useful."

**[Type: "How does Flask handle request context?"]**

"I can ask anything. It knows every file in the repo."

**[Show response streaming]**

"It references specific files, shows code snippets... and it remembers the conversation."

**[Type a follow-up: "How is that different from the application context?"]**

"Follow-ups work naturally. No re-explaining needed."

**SECTION 7: Immersive Mode**

**[Open a file in the viewer, highlight a block of code]**

"One more thing — Immersive mode. I highlight any code and ask about it."

**[Type "Why is it done this way?" in the immersive sidebar]**

**[Show the contextual response]**

"It explains that specific code in context of the entire codebase."

**SECTION 8: Closing**

**[Go back to landing page]**

"repolm.com. First repo is free, no signup needed. If you learn by reading code, this will save you hours."

**[Show the Learning Paths page briefly]**

"There's also curated learning paths — repos organized by concept. Event-driven architecture, REST APIs, state machines, testing..."

**[Back to landing]**

"Link in the description. Let me know what you think."

---

### Script 3: Problem-Hook Short (30 seconds — Reels/TikTok/Shorts)

**[Face camera or text overlay on screen]**

"You ever open a GitHub repo with 200 files and think... where do I even start?"

**[Cut to repolm.com, paste a repo URL]**

"Paste it here."

**[Show overview streaming in]**

"Full architecture breakdown in 30 seconds."

**[Show podcast bubbles]**

"Or listen to two people explain it like a podcast."

**[Show slide viewer]**

"Or get a slide deck you can download."

**[Show chat]**

"Or just ask it questions."

"repolm.com. Free."

---

### Script 4: "I Learned React in 10 Minutes" (2 min — YouTube/Twitter)

**[Screen: repolm.com]**

"I'm going to learn how React works in 10 minutes using AI. Let's see if this actually works."

**[Paste facebook/react, hit Go]**

"Loading the actual React source code... 1,800 files. Good luck reading that manually."

**[Generate overview at Beginner level]**

"Beginner overview... okay, TL;DR: React is a library for building UIs using a virtual DOM. Cool."

**[Scroll through architecture section]**

"There's the reconciler, the fiber architecture, the scheduler... it's breaking down each piece with analogies."

**[Generate podcast]**

"Let's hear the podcast version."

**[Play audio for 15 seconds — listen to hosts discussing React]**

"Okay that's actually fun to listen to."

**[Switch to Expert mode, generate overview]**

"Now let me switch to Expert..."

**[Show the code-heavy output]**

"Completely different. Now it's showing me the fiber node structure, the work loop, how concurrent mode works under the hood."

**[Chat: "How does React's diffing algorithm work?"]**

**[Show response]**

"Ten minutes, and I genuinely understand React better than after reading the docs for an hour."

"repolm.com. First repo free."
