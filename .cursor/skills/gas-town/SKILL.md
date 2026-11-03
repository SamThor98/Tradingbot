---
name: gas-town
description: >-
  Multi-agent orchestration with Gas Town (gt CLI): Mayor coordination, convoys,
  beads issue tracking, and Cursor agent dispatch. Use when planning multi-step
  work across agents, assigning tasks with gt sling, monitoring agent health,
  or setting up a Gas Town workspace for this repo.
---

# Gas Town Orchestration

Gas Town coordinates multiple AI agents with persistent, git-backed work state.
Use the **`cursor`** agent preset to dispatch Cursor agents from `gt`.

## Prerequisites

- `gt` on PATH (`npm install -g @gastown/gt`, or binary in `%USERPROFILE%\bin`)
- `bd` on PATH (`npm install -g @beads/bd`, or binary in `%USERPROFILE%\bin`)
- Git 2.25+

Verify:

```powershell
gt version
bd version
```

## First-time workspace setup

```powershell
# Create a town (HQ) — pick a path outside the repo if you prefer
gt install $env:USERPROFILE\gt --git
cd $env:USERPROFILE\gt

# Register this repo as a rig
gt rig add tradingbot https://github.com/<you>/TradingBot.git

# Create your crew workspace
gt crew add <yourname> --rig tradingbot

# Health check
gt doctor
```

## Daily orchestration commands

| Command | Purpose |
|---------|---------|
| `gt mayor attach` | Start the Mayor (primary coordinator session) |
| `gt sling <bead-id> <rig> --agent cursor` | Assign work to a Cursor agent |
| `gt convoy list` | List active work convoys |
| `gt agents` | List running agents |
| `gt feed` | Real-time activity feed (TUI) |
| `gt feed --problems` | Stuck-agent detection view |
| `gt prime` | Context recovery inside an existing session |
| `gt doctor` | Validate town/rig health |

## Cursor preset

- Preset id: **`cursor`** (`GT_AGENT=cursor`)
- Process name: **`cursor-agent`**
- Dispatch example: `gt sling gt-abc12 tradingbot --agent cursor`
- After hook/config changes: `gt up --restore`

## Beads (issue tracking)

Beads store work as structured, git-backed issues under `.beads/`:

```powershell
bd init --quiet          # in a project (if not already initialized)
bd create "Task title"   # create an issue
bd list                  # list open issues
```

Link bead IDs (e.g. `gt-abc12`) to convoys and sling them to agents.

## When to use Gas Town vs single Cursor session

- **Single session**: one focused task, you are driving interactively
- **Gas Town**: parallel workstreams, crash recovery, convoy tracking, Mayor
  breaking epics into beads and spawning polecats

## References

- Docs: https://docs.gastownhall.ai/
- Repo: https://github.com/gastownhall/gastown
- Beads: https://gastownhall.github.io/beads/
