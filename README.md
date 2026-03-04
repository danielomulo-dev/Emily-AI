# Emily — AI Discord Bot

A Kenyan AI assistant with a **hive mind** powered by **Gemini** (real-time search) and **Claude** (deep analysis). Emily is a financial analyst, foodie, and cinephile.

## Features

- **Hive Mind Routing** — Automatically picks Gemini or Claude based on the task
- **Financial Advisor** — NSE & global stock tracking, investment advice, portfolio analysis
- **Foodie** — Kenyan & global cuisine expertise, cooking tips, restaurant recommendations
- **Cinephile** — Film recommendations, reviews, and hot takes
- **Voice Messages** — Transcribes voice → responds with voice (ElevenLabs)
- **Image Analysis** — Send photos for analysis via Gemini
- **File Handling** — PDFs, code files, text documents
- **Memory** — Remembers personal facts across conversations
- **Source Links** — Auto-appends article sources from Google Search

## Setup

### 1. Create GitHub Repo

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/emily-bot.git
git branch -M main
git push -u origin main
```

### 2. Environment Variables (set in Koyeb dashboard)

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `ANTHROPIC_API_KEY` | Yes | Anthropic Claude API key |
| `MONGO_URI` | Yes | MongoDB connection string |
| `ELEVENLABS_API_KEY` | No | ElevenLabs TTS key |
| `ELEVENLABS_VOICE_ID` | No | ElevenLabs voice ID |
| `ALPHA_VANTAGE_KEY` | No | Stock data API key |
| `PORT` | No | Health check port (default: 8000) |

### 3. Deploy on Koyeb

1. Go to [Koyeb](https://www.koyeb.com) → Create Service
2. Select **GitHub** as source
3. Connect your repo
4. Set **Builder**: Dockerfile
5. Set **Port**: 8000
6. Add all environment variables
7. Deploy

### 4. Uptime Monitoring

Set up [cron-job.org](https://cron-job.org) to ping your Koyeb URL every 2 minutes to keep the instance warm.

## File Structure

```
├── main.py            # Bot + hive mind router + Discord handler
├── memory.py          # MongoDB: user profiles, chat history, reminders
├── finance_tools.py   # Alpha Vantage + NSE scrapers
├── image_tools.py     # DuckDuckGo image/GIF search
├── web_tools.py       # News, web scraping, YouTube transcripts
├── voice_tools.py     # ElevenLabs text-to-speech
├── requirements.txt   # Python dependencies
├── Dockerfile         # Container config
├── .env.example       # Environment variable template
└── .gitignore         # Git ignore rules
```

## Hive Mind Routing

| Task | Model | Reason |
|---|---|---|
| News, weather, current events | Gemini | Has Google Search |
| "Should I buy SCOM?" | Claude | Financial reasoning |
| Code review | Claude | Better analysis |
| "How do I make pilau?" | Claude | Cooking expertise |
| "Recommend a movie" | Claude | Film opinions |
| Quick greetings | Gemini | Faster response |
| Image sent (no analysis request) | Gemini | Native multimodal |
| If primary model fails | Other model | Automatic fallback |
