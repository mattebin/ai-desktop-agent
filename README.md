# AI Desktop Operator

A local AI that controls your Windows desktop. Tell it what to do in plain language and it does it — opens files, runs commands, types text, clicks things, manages email, and more.

AI Desktop Operator is built to feel like a capable assistant sitting at your computer. You talk to it in chat, it picks the right action, and it does it. No complicated setup, no multi-step wizards. Just say what you want and it figures out the rest. Common things like opening folders, pressing keys, or running shell commands happen instantly without even needing to think about it.

## What it can do

- Open files, folders, URLs, and apps by name — no full paths needed
- Run PowerShell and CMD commands with a safety filter that blocks dangerous stuff
- Type text, press keys, click the screen, take screenshots
- Work through Gmail — read threads, draft replies, send with your approval
- Set up automations that run on a schedule
- Remember what just happened so it doesn't repeat mistakes
- Stay local — everything runs on your machine

## How it works

You type a message. The operator picks a tool and uses it. If the result needs another step, it picks the next tool. Simple actions like "open Downloads" skip the AI entirely and execute instantly through a fast path.

Behind the scenes it's a straightforward loop: your message goes to an LLM, the LLM picks a tool, the tool runs, the result goes back. Same pattern that powers tools like Claude Code and Codex — proven and fast.

## What makes it different

- **Fast**: common actions execute instantly, no waiting for the AI to "think" about opening a folder
- **Direct**: it acts like you'd expect — say "open Downloads" and it opens Downloads
- **Shell access**: full PowerShell and CMD with a safety filter that blocks credential theft, persistence attacks, and destructive operations
- **Local-first**: runs on your machine, your data stays on your machine
- **Chat-first**: the conversation is the interface, not a dashboard full of buttons

## UI overview

![AI Desktop Operator UI](images/ui-main.png)

The interface is simple — chat in the center, workspaces on the left for things like Gmail, Automations, and Runs. You talk to it like a person and it gets things done.

## Example use cases

- "Open my Downloads folder" — opens it instantly, no questions asked
- "Run `git status` in my project folder" — executes the command and shows you the output
- "Take a screenshot" — captures the screen and saves it
- "Press Ctrl+S" — sends the key combo to whatever window is focused
- "What windows are open?" — lists everything on screen
- "Draft a reply to that email" — works through Gmail with your approval before sending

## Getting started

1. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Install the desktop UI:

   ```bash
   cd desktop-ui
   npm install
   ```

3. Run it:

   ```bash
   npm run tauri dev
   ```

Or run the backend directly:

```bash
python main.py
```

## Project structure

- `core/` — the operator brain: execution loop, state, fast path, LLM client
- `desktop-ui/` — Tauri + React frontend
- `tools/` — thin wrappers around Windows APIs (keyboard, mouse, screenshots, shell, processes)
- `docs/` — deeper technical notes if you want to dig in

## Current status

This is an active project with a real working product. The core desktop operator is solid — it can open things, run commands, control the keyboard and mouse, and work through multi-step tasks. Gmail, automations, and browser control are integrated. It's still evolving, but it's genuinely usable today.
