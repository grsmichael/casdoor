# Code style

- Do not add Go tests (`_test.go`) unless explicitly asked.
- Keep comments sparse: only for genuinely non-obvious mechanics, not one per block.

# Frontend migration (`web-old` → `web`)

`web` is the shadcn/Tailwind rewrite of the antd frontend in `web-old`. Apart from
the UI layer, `web` should behave exactly like `web-old` — same validations, same
field formats, same conditions. When the two differ, `web-old` is the reference.

`web-old` is deprecated and read-only: never modify it (code or locales). New
features and fixes go into `web` only.

Not migrated on purpose:

- **AI assistant.** The `aiAssistantUrl` config, its header button and the iframe
  drawer (`Conf.AiAssistantUrl` / `renderAiAssistant()` in `web-old/src/App.js`,
  the `ai-assistant` entry of `WidgetItemTree`) are dropped. Do not port them.
- **Web3 providers.** The `Web3` category (MetaMask, Web3-Onboard) was removed
  from the backend and `web`. Do not port `web-old`'s wallet sign-in code.

# Agent skills

This project adopts [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)
(MIT) as the engineering workflow for AI agents. Installed at project scope:

| Path | Contents |
|---|---|
| `.workbuddy-ai/skills/` | 25 lifecycle skills (24 + the `using-agent-skills` meta-skill) |
| `.workbuddy-ai/agents/` | 4 review personas — code-reviewer, test-engineer, security-auditor, web-performance-auditor |
| `.workbuddy-ai/references/` | 7 shared checklists — definition-of-done, testing, security, performance, accessibility, observability, orchestration |

Lifecycle: **DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP**
(`/spec` → `/plan` → `/build` → `/test` → `/review` → `/ship`).

Standing rules from the skill set that apply to every change:

- **Spec before code.** Non-trivial work starts with a written spec.
- **Tests are proof.** "Seems right" is never sufficient — ship evidence: test output,
  build output, or runtime data.
- **Small, atomic changes.** Keep diffs near ~100 lines so review stays meaningful.
- **Verification is non-negotiable.** Every skill ends with evidence requirements;
  skipping them is not a valid shortcut.

**Precedence: instructions in this file win over the generic skills.** The skills encode
general senior-engineer defaults; this file encodes what is true for *this* repo. Where
they conflict, follow this file. The sharpest example: `test-driven-development` and
`spec-driven-development` push toward writing tests, while "Code style" above says not to
add Go tests unless explicitly asked — the latter governs here.

These files are tooling for agents working on this repo; they are not part of the Casdoor
build and are not shipped.
