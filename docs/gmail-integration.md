## Gmail Integration

This project now includes a Gmail-first email layer with a native provider, local draft store, API endpoints, slash commands, and agent tools.

### What it supports

- Connect Gmail with a Desktop OAuth client.
- List inbox threads and read thread context.
- Prepare frozen reply drafts and forward drafts without sending.
- Send a prepared draft only after explicit approval.

### Config

Add your Gmail Desktop OAuth client JSON at:

- `config/gmail-client-secret.json`

Enable Gmail in `config/settings.yaml` or `config/settings.local.yaml`:

```yaml
gmail:
  enabled: true
  client_secrets_path: "config/gmail-client-secret.json"
  token_path: "data/gmail_token.json"
  draft_state_path: "data/email_drafts.json"
  scopes:
    - "https://www.googleapis.com/auth/gmail.modify"
  watch_enabled: false
  watch_query: "label:inbox newer_than:7d"
  poll_seconds: 60
```

### API

- `GET /email/status`
- `POST /email/connect`
- `GET /email/threads`
- `GET /email/threads/{thread_id}`
- `GET /email/drafts`
- `POST /email/drafts/reply`
- `POST /email/drafts/forward`
- `POST /email/drafts/send`
- `POST /email/drafts/reject`

### Slash commands

- `/gmail-connect`
- `/email-status`
- `/inbox`
- `/drafts`

### Agent tools

- `email_connect_gmail`
- `email_list_threads`
- `email_read_thread`
- `email_prepare_reply_draft`
- `email_prepare_forward_draft`
- `email_send_draft`

### Approval model

`email_send_draft` is approval-gated. The tool pauses with the exact draft preview until the operator approves or rejects it.
