---
name: New Language Adapter Request
about: Request statistical benchmark and hotspot-scan support for a new programming language
title: '[ADAPTER] '
labels: enhancement
assignees: ''
---

**Requested Language / Runtime:**
[e.g. Ruby, PHP, Elixir, Swift, C/C++]

**High-Resolution Monotonic Timer & Memory APIs:**
- Monotonic clock API: [e.g. `Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond)`]
- Peak memory / allocation API:

**Isolation & Sandbox Requirements:**
- Cache directories to redirect into `.bughunt/.cache/`:
- Does the runtime require a full sandbox copy (`S/sandbox-opt/`) or can it import via environment variables?

**Example Dual Commands:**
- Bash:
- PowerShell:
