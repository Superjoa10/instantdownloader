# Changelog

## 1.0.0 - 2026-11-10

### Added

- Dedicated local Instagram session with manual browser login, explicit
  verification, connection testing, cancellation and session deletion.
- Authenticated Reels discovery through native browser pagination, preserving
  partial results and distinguishing bounded completion from source exhaustion.
- Publication-date lookup and inclusive UTC date selection in the desktop app
  and CLI, with unknown dates excluded from date filters.
- CLI automation commands: session management, profile discover, filter-list
  and download-list, with versioned manifests and atomic checkpoints.
- Structured JSON results and reports for URL, Excel, profile and queue flows;
  progress on stderr and documented process exit codes.
- Portable agent skill and CLI automation documentation, without global agent
  installation or configuration changes.

### Fixed

- Removed the premature three-minute cutoff while Reels discovery progresses.
- Improved login, Feed/Reels and local/provider limit diagnostics.
- Improved H.264/AAC MP4 compatibility, color-range normalization and full
  decode validation before committing converted output.
- Successful compatible downloads now remove their intermediate source file;
  failures before cleanup preserve it. No retroactive media-folder cleanup.
- Profile table dates, column alignment and minimum-window layout.
- Filtering before enqueue, explicit acceptance of incomplete manifests and
  protection against reports overwriting their input files.

### Known Limitations

- Profile discovery is bounded; no persistent page-cursor resume or guarantee
  of a complete inaccessible/private archive. Feed still uses the legacy path
  and can fail independently of Reels.
- Discovery-session cookies are not automatically used for downloads.
- Publication dates may remain unavailable. Date lookup can be slow for large
  lists and stops on access restrictions.
- queue run processes all pending tasks in its chosen data directory.
- Windows execution, packaged desktop binaries and bundled FFmpeg distribution
  have not been validated. This release distributes source, not installers.
- Agent skill installation/discovery in Codex and Claude remains separate from
  the portable instructions included in this repository.
