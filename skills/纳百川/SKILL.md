---
name: 纳百川
description: "Install skills from agentskill.sh, GitHub, or other skill registries. Use when the user asks to install a skill from a URL, add a skill, or fetch a skill from agentskill.sh/@someone/skill-name or github.com/owner/repo."
---

# 纳百川 — Install Skills from Any Source

Install agent skills from URLs with minimal token waste. Follow the rules below strictly.

## Core Principles

1. **curl -o to disk, never stdout**: File content goes to disk, not context. Only exception: SKILL.md may be read ONCE for a brief summary to the user.
2. **API over HTML**: Never scrape web pages. Use API endpoints.
3. **One grep for file list, no more**: Extract the file inventory in one shot, then batch-download.
4. **skip = don't waste tokens**: If a step is purely cosmetic (listing all files again after download, printing file sizes), skip it.

## Installation Procedure

### Phase 1: Discover source type

Identify the source from the URL:

| URL pattern | Source type | Strategy |
|-------------|-------------|----------|
| `agentskill.sh/@owner/name` | agentskill.sh | Use API below |
| `github.com/owner/repo` | GitHub skill repo | Use GitHub API |
| `raw.githubusercontent.com/.../SKILL.md` | Direct raw | curl -o directly |

### Phase 2: Fetch skill metadata (agentskill.sh)

```bash
# Get skill API URL from page slug
# For agentskill.sh/@anthropics/docx → API: agentskill.sh/api/skills/anthropics%2Fdocx

curl -sL "https://agentskill.sh/api/skills/{owner}%2F{name}" > /tmp/skill_meta.json
```

Extract from the JSON with grep (do NOT read full JSON into context):
```bash
# Get file list
grep -o '"path":"[^"]*"' /tmp/skill_meta.json

# Get GitHub info
grep -o '"githubOwner":"[^"]*"\|"githubRepo":"[^"]*"\|"githubPath":"[^"]*"\|"githubBranch":"[^"]*"' /tmp/skill_meta.json
```

### Phase 3: Fetch skill metadata (GitHub)

```bash
curl -sL "https://api.github.com/repos/{owner}/{repo}/contents/{skill_path}" | grep '"name"'
```

### Phase 4: Determine install directory

Pattern: `~/.claude/skills/{owner}-{name}/`

Create it:
```bash
mkdir -p ~/.claude/skills/{owner}-{name}/scripts
```

### Phase 5: Download all files (batch curl -o)

CRITICAL: All downloads use `curl -o` to write to disk. NOTHING goes to stdout.

For agentskill.sh: files are embedded in the API response. Extract with a script OR use GitHub raw URLs constructed from metadata.

For GitHub:
```bash
# Construct raw URL base
base="https://raw.githubusercontent.com/{owner}/{repo}/{branch}"

# Download each file to the corresponding path
curl -sL "$base/skills/{name}/SKILL.md" -o "~/.claude/skills/{owner}-{name}/SKILL.md"
curl -sL "$base/skills/{name}/scripts/foo.py" -o "~/.claude/skills/{owner}-{name}/scripts/foo.py"
# ... etc
```

Run downloads in parallel when there are no dependencies between files (all independent curl calls).

### Phase 6: Handle dependencies

Check SKILL.md for setup instructions (read it from disk, not stdout):

```bash
# Check for package.json, requirements.txt, or install commands
head -50 ~/.claude/skills/{owner}-{name}/SKILL.md
```

If `npm install` is needed: `cd ~/.claude/skills/{owner}-{name} && npm install`
If `pip install` is needed: `pip install {packages}`

Run installs in background if they take long (npm/pip).

### Phase 7: Minimal verification

```bash
# Just list files - one command, brief output
find ~/.claude/skills/{owner}-{name} -type f | sort
```

Report to user: "Installed {N} files to ~/.claude/skills/{owner}-{name}/"

## Anti-patterns (NEVER do these)

- **NEVER** `curl | head` or `curl | grep` on large files — pipe to file, grep the file
- **NEVER** `curl` to stdout for file downloads — always `-o`
- **NEVER** `WebFetch` for GitHub/agentskill.sh content — use `curl` directly
- **NEVER** read downloaded source files into context unless there's a specific question
- **NEVER** scrape HTML pages to find file structure — use APIs
- **NEVER** `cat` downloaded files — they're on disk, no need to re-read

## Platform-specific notes

### agentskill.sh
- API endpoint: `https://agentskill.sh/api/skills/{owner}%2F{name}`
- API returns all skill files embedded in JSON (large). Prefer using GitHub raw URLs if `githubOwner`/`githubRepo`/`githubPath`/`githubBranch` fields are present.

### GitHub (anthropics/skills style)
- Repo: `github.com/anthropics/skills`
- Structure: `skills/{name}/SKILL.md`, `skills/{name}/scripts/`, `skills/{name}/LICENSE.txt`
- Use `api.github.com/repos/{owner}/{repo}/contents/skills/{name}` to list files
- Use `raw.githubusercontent.com/{owner}/{repo}/{branch}/skills/{name}/...` to download

### NeverSight (learn-skills.dev style)
- Skills are NOT stored as individual files per skill in the repo
- The `learn-skills.dev` repo contains a monolithic `data/skills.json` (4.3MB) with all skills embedded
- For individual skills: use agentskill.sh API endpoint (which has the full file contents)
- Fallback: create package.json and scripts manually if source is unavailable
