# Skill Hub (ClawHub) Compatibility

## Overview

PyClaw is fully compatible with OpenClaw's [ClawHub](https://clawhub.ai) skill ecosystem. Skills installed by either tool are available to both.

## What Is a Skill?

A skill is a directory containing `SKILL.md` — a Markdown file with YAML frontmatter:

```yaml
---
name: my-skill
description: "What this skill does and when to use it"
metadata:
  openclaw:
    emoji: "🔧"
    requires:
      bins: ["some-binary"]     # ALL must exist on PATH
      anyBins: ["alt1", "alt2"] # ANY must exist
      env: ["SOME_API_KEY"]     # Required env vars
    install:
      - kind: brew
        formula: "package-name"
      - kind: node
        package: "@scope/pkg"
      - kind: uv
        package: "httpie"
---

# Skill Instructions (Markdown)

These instructions are injected into the agent's system prompt...
```

Skills are NOT code — they are prompts (instructions for the AI agent). Language-agnostic.

## ClawHub API

Base URL: `https://clawhub.ai`

| Endpoint | Purpose |
|----------|---------|
| GET /api/v1/search?q=... | Search skills |
| GET /api/v1/skills/{slug} | Skill detail |
| GET /api/v1/download?slug=...&version=... | Download ZIP |
| GET /api/v1/skills | List all skills |

Auth: `Authorization: Bearer {token}` (from `CLAWHUB_TOKEN` env or `~/.config/clawhub/config.json`)

## Install Flow

1. GET `/api/v1/skills/{slug}` → get metadata + latest version
2. GET `/api/v1/download?slug={slug}&version={version}` → download ZIP
3. Extract ZIP, verify SKILL.md exists
4. Install to `{workspace}/skills/{slug}/`
5. Write `.clawhub/origin.json` (registry, slug, version, installedAt)
6. Update `.clawhub/lock.json` (workspace-level version tracking)

## Shared Directory

PyClaw reads from `~/.openclaw/skills/` — the same directory OpenClaw uses for managed skills. Skills installed by OpenClaw's `clawhub install` command are immediately available to PyClaw.

## Discovery Order (high → low priority)

1. `{workspace}/skills/` (workspace-local)
2. `~/.openclaw/skills/` (user-level, shared with OpenClaw)
3. Bundled skills (shipped with PyClaw)

## Prompt Injection Format

Skills are injected into the system prompt as XML:
```xml
<available_skills>
  <skill>
    <name>github</name>
    <description>GitHub CLI operations...</description>
    <location>~/.openclaw/skills/github/SKILL.md</location>
  </skill>
</available_skills>
```

The agent uses its `read` tool to load a skill's full content when it decides to use it.

## Compatibility Scope

| Compatible | Not Compatible |
|------------|---------------|
| family="skill" (Markdown) | family="code-plugin" (TypeScript) |
| SKILL.md format | bundle-plugin (TypeScript) |
| ClawHub REST API | OpenClaw extension system |
| Install specs (brew, node, uv, go, download) | TypeScript-specific tools |
| .clawhub/lock.json + origin.json | |
| Auth token sharing | |

## Lockfile Format

`.clawhub/lock.json`:
```json
{
  "version": 1,
  "skills": {
    "weather": { "version": "1.2.3", "installedAt": 1714500000000 }
  }
}
```

Per-skill `.clawhub/origin.json`:
```json
{
  "version": 1,
  "registry": "https://clawhub.ai",
  "slug": "weather",
  "installedVersion": "1.2.3",
  "installedAt": 1714500000000
}
```
