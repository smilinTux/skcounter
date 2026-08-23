export const HELP_TEXT = `SKCounter

Governed local token usage accounting for SK fleet harnesses.

Usage:
  skcounter                         Show the local model report
  skcounter models [filters]        Show usage by model
  skcounter monthly [filters]       Show usage by day
  skcounter hourly [filters]        Show usage by hour
  skcounter clients [--json]        Show detected local clients
  skcounter graph [filters]         Export contribution graph JSON
  skcounter time-metrics [filters]  Show local session time metrics
  skcounter report [filters]        Show a local task report without LLM summarization
  skcounter pricing MODEL           Look up model pricing metadata
  skcounter snapshot [range]        Emit a normalized aggregate snapshot as JSON
  skcounter collect [range]         Write a snapshot to the local durable observation store
  skcounter doctor [--json]         Check backend and local source discovery
  skcounter backend [--json]        Show the selected provider adapter
  skcounter --version               Show facade and backend versions

Policy:
  SKCounter is local-only by default. Social submission, autosubmit, account,
  credential, remote synchronization, subprocess capture, and upstream TUI
  commands are blocked. Unknown backend commands fail closed.

Collection options:
  --since YYYY-MM-DD
  --until YYYY-MM-DD
  --output-dir PATH                 Override the local observation root for collect
`;
