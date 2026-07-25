# Security Policy

## Supported versions

Clips Studio has not had a stable release yet. Security fixes land on `main`, and the
latest commit is the only supported version. Once releases begin, this table will list
the supported ones.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub's
[private vulnerability reporting](../../security/advisories/new). That opens a
discussion visible only to you and the maintainers.

Please include:

- what the vulnerability lets an attacker do
- the steps to reproduce it
- the affected file or component, if you know it
- your OS, Python version, and app commit

You can expect an acknowledgement within a week. If a fix is needed, we'll agree a
disclosure timeline with you and credit you in the advisory unless you'd rather stay
anonymous.

## What is in scope

Clips Studio is a **local desktop application**. The interesting attack surface is
mostly about untrusted input and local exposure:

- **The local API** (`server/`) binds to `127.0.0.1:8765`. Anything that lets a remote
  or cross-origin page reach it, or that widens that binding, is in scope.
- **Path handling** — export folders, media serving, and file imports. Path traversal
  out of the data directory is in scope.
- **Untrusted video and transcript content** reaching a shell, an FFmpeg argument, or a
  file path. Command injection through a video title or filename is in scope.
- **Prompt content reaching disk or the shell.** The LLM's output is treated as
  untrusted and validated before it is applied; a way around that validation is in
  scope.
- **Credential handling** — YouTube OAuth tokens and `config/client_secret.json`.
- **The feedback relay** (`feedback-relay/`) and the diagnostics attached to in-app
  reports. Diagnostics are redacted before leaving the reporter's machine; a leak of
  secrets or personal data through that path is in scope and important.
- **Dependency vulnerabilities** that are actually reachable from Clips Studio code.

## What is out of scope

- Attacks needing an attacker who already has local code execution or admin rights on
  the machine. A local desktop app can't defend against that.
- Vulnerabilities in Ollama, FFmpeg, yt-dlp, or the AI models themselves — report those
  upstream. If Clips Studio *uses* one of them unsafely, that part is in scope.
- The quality, bias, or content of AI-generated clips, titles, or translations. Those
  are bugs or feature requests, not security issues.
- Denial of service by feeding the app a deliberately enormous video.
- Anything requiring a user to change a default setting to something clearly unsafe.

## A note on what this app does

Clips Studio downloads videos with yt-dlp and processes them locally. It never uploads
your footage anywhere. The only outbound network traffic in a normal run is fetching
the source video, an optional Twitch chat-replay request, model downloads you ask for,
and — only if you submit one — an in-app feedback report.
