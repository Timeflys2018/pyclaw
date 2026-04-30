# Skill Hub (ClawHub) 兼容性

## 概述

PyClaw 完全兼容 OpenClaw 的 [ClawHub](https://clawhub.ai) 技能生态。任一工具安装的技能对双方都可用。

## 什么是 Skill？

技能是一个包含 `SKILL.md` 的目录 — 带 YAML frontmatter 的 Markdown 文件：

```yaml
---
name: my-skill
description: "这个技能做什么，什么时候用"
metadata:
  openclaw:
    emoji: "🔧"
    requires:
      bins: ["some-binary"]     # 必须全部在 PATH 中
      anyBins: ["alt1", "alt2"] # 至少一个在 PATH 中
      env: ["SOME_API_KEY"]     # 必需的环境变量
    install:
      - kind: brew
        formula: "package-name"
      - kind: node
        package: "@scope/pkg"
      - kind: uv
        package: "httpie"
---

# 技能指令 (Markdown)

这些指令会注入 agent 的系统 prompt...
```

技能不是代码 — 是给 AI agent 的 prompt（指令）。语言无关。

## ClawHub API

Base URL: `https://clawhub.ai`

| Endpoint | 用途 |
|----------|------|
| GET /api/v1/search?q=... | 搜索技能 |
| GET /api/v1/skills/{slug} | 技能详情 |
| GET /api/v1/download?slug=...&version=... | 下载 ZIP |
| GET /api/v1/skills | 列出全部技能 |

认证: `Authorization: Bearer {token}`（来自 `CLAWHUB_TOKEN` 环境变量或 `~/.config/clawhub/config.json`）

## 安装流程

1. GET `/api/v1/skills/{slug}` → 获取元数据 + 最新版本
2. GET `/api/v1/download?slug={slug}&version={version}` → 下载 ZIP
3. 解压 ZIP，验证 SKILL.md 存在
4. 安装到 `{workspace}/skills/{slug}/`
5. 写入 `.clawhub/origin.json`（registry, slug, version, installedAt）
6. 更新 `.clawhub/lock.json`（workspace 级版本跟踪）

## 共享目录

PyClaw 读取 `~/.openclaw/skills/` — 与 OpenClaw 管理技能的目录相同。通过 OpenClaw 的 `clawhub install` 命令安装的技能对 PyClaw 立即可用。

## 发现顺序（高 → 低优先级）

1. `{workspace}/skills/`（workspace 本地）
2. `~/.openclaw/skills/`（用户级，与 OpenClaw 共享）
3. Bundled 技能（随 PyClaw 发布）

## Prompt 注入格式

技能以 XML 形式注入系统 prompt：
```xml
<available_skills>
  <skill>
    <name>github</name>
    <description>GitHub CLI 操作...</description>
    <location>~/.openclaw/skills/github/SKILL.md</location>
  </skill>
</available_skills>
```

Agent 决定使用某个技能时通过 `read` 工具加载完整内容。

## 兼容范围

| 兼容 | 不兼容 |
|------|-------|
| family="skill"（Markdown） | family="code-plugin"（TypeScript） |
| SKILL.md 格式 | bundle-plugin（TypeScript） |
| ClawHub REST API | OpenClaw extension 系统 |
| 安装 spec（brew, node, uv, go, download） | TypeScript 特定工具 |
| .clawhub/lock.json + origin.json | |
| Auth token 共享 | |

## Lockfile 格式

`.clawhub/lock.json`:
```json
{
  "version": 1,
  "skills": {
    "weather": { "version": "1.2.3", "installedAt": 1714500000000 }
  }
}
```

每个技能的 `.clawhub/origin.json`:
```json
{
  "version": 1,
  "registry": "https://clawhub.ai",
  "slug": "weather",
  "installedVersion": "1.2.3",
  "installedAt": 1714500000000
}
```
