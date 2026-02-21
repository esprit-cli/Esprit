# Video Export Feature — Design Document

**Date:** 2026-02-18  
**Status:** Approved  

## Summary

After a scan completes, users can export a cinematic MP4 replay of the entire session. The replay animates the Web GUI (3-panel dashboard) in a macOS window with smooth, clean transitions — no glitch effects. Built on Playwright native video recording + FFmpeg.

## Design Decisions

- **UI**: Web GUI replay (not TUI) — existing HTML/CSS/JS is already polished
- **Rendering**: Playwright `record_video_dir` → `.webm` → FFmpeg → `.mp4`
- **Style**: Smooth, minimalistic, clean. No glitch effects. macOS window chrome, dark theme
- **Audio**: Web Audio API ambient drone + tool ping + vuln thud + resolution chord
- **Trigger**: Both TUI (ExportScreen modal) and CLI (`esprit export-video`)
- **Dependencies**: `playwright` (optional dep), `ffmpeg` (system, checked at runtime)

## Visual Design

- **Background**: Dark animated radial gradient, subtle noise texture
- **Window**: macOS chrome (traffic lights, title bar), `border-radius: 12px`, cyan glow shadow
- **Intro**: Fade-in logo → subtitle → window drops in with spring physics. No glitch.
- **Panels**: Agents tree (left), terminal feed (center), vulnerabilities (right)
- **Animations**: Agent spawn fade-in, terminal line stream, vuln card slam-in with shockwave
- **Outro**: Panels dim → summary card rises → fade to black → Esprit logo

## Speed / Settings

User-configurable before rendering:
- **Speed multiplier**: 5x / 10x / 20x / 50x (default 10x)
- **Resolution**: 1080p / 720p (default 1080p)
- **Output path**: defaults to `esprit_runs/<run_id>/replay.mp4`

## Files

| File | Change |
|------|--------|
| `esprit/reporting/video_exporter.py` | New — `VideoExporter` class |
| `esprit/reporting/templates/video_replay.html.jinja` | New — cinematic replay template |
| `esprit/interface/tui.py` | Add `VideoExportScreen` modal + `export_video` action |
| `esprit/interface/assets/tui_styles.tcss` | Add styles for video export modal |
| `esprit/interface/cli.py` | Add `export-video` subcommand |
| `pyproject.toml` | Add `[video]` optional dep group |
