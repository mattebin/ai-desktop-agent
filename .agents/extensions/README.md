# Local extensions

Drop local extension manifests in this folder to add slash commands without editing the app.

Supported manifest formats:

- `.json`
- `.yaml`
- `.yml`

Each manifest can add `prompt` commands or map to existing UI actions with `local` commands.

Example:

```yaml
title: Review helpers
description: Extra prompts for repo inspection.
commands:
  - type: prompt
    name: repo-health
    description: Summarize the current repo health and risks.
    prompt: Inspect this project and summarize the current repo health, risks, and missing tests.

  - type: local
    name: open-runtime
    description: Show the runtime summary in the chat activity log.
    action: show-runtime
```

Supported local actions:

- `new-chat`
- `refresh`
- `toggle-details`
- `toggle-theme`
- `approve`
- `reject`
- `show-skills`
- `show-runtime`
- `show-tools`
- `show-extensions`
- `help`
