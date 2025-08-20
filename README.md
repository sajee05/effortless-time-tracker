# StudyTracker

## DEVELOPMENT CLOSED. 🔒
I switched to TickTick, which already has this shortcut feature and the overall app is much more optimised. Thank you.

<img width="1365" height="753" alt="image" src="https://github.com/user-attachments/assets/e427dd63-6f32-4030-a9f9-315b4092c1dd" />

(Swipe down for more screenshots) A minimalist study timer that stays out of your way while tracking everything that matters. 

## Why StudyTracker?

Most study timers are distracting. They need too many clicks, clutter your screen, and still miss important features. StudyTracker is different - simple/ no extra efforts of manually turning on the timer/ if you're a streamer- no extra effort of going to OBS and starting/stopping the timer each time.

## Features

### Core
- **One-key control** - Start/stop with `Alt + Shift + 1` from anywhere
- **System tray app** - Runs quietly in the background, click to open dashboard
- **Auto-saves everything** - SQLite database preserves all sessions between reboots
- **Sound feedback** - Subtle audio cues when starting/stopping (customizable)

### Analytics
- **Comprehensive stats** - Daily, weekly, monthly breakdowns with averages
- **GitHub-style heatmap** - Visualize your consistency across the year
- **Interactive charts** - Weekly/monthly bar charts with drill-down capability
- **Streak tracking** - Current and longest streaks to keep you motivated

### Rewards System
- **Earn coins** - 1 hour of study = 1 coin
- **Custom rewards** - Set your own rewards at 50, 100, 200 coins or 14-day streak
- **Progress tracking** - Visual progress bars show how close you are to each reward

### Stream Integration
- **OBS overlay** - Real-time HTML display for streamers
- **Live updates** - Shows current session, today's total, and streak
- **Clean design** - Minimal, professional overlay that fits any stream aesthetic

### Quality of Life
- **Manual adjustments** - Add/remove time for any date
- **Log management** - Edit or delete individual sessions
- **Import/Export** - JSON backup and restore
- **Fullscreen mode** - F11 for distraction-free viewing
- **Zoom support** - Ctrl + scroll to adjust interface size
- **Dark theme** - Easy on the eyes during long study sessions

## Installation

### Requirements
```bash
pip install pystray pillow keyboard playsound tkcalendar matplotlib
```

### Quick Start
1. Download `tracker.py` to any folder
2. Run with `python tracker.py`
3. Press `Alt + Shift + 1` to start timing
4. Left-click the tray icon (📖) to view your dashboard

### Auto-start on Windows
1. Press `Win + R`
2. Type `shell:startup` and press Enter
3. Drop a shortcut to `tracker.py` in that folder

### macOS/Linux
Use `pip3` and `python3` instead. For Linux, you'll need sudo for global hotkeys. Auto-start varies by desktop environment.

## OBS Setup

1. Run the script and copy the `obs_display.html` path from the console
2. In OBS, add a new Browser Source
3. Check "Local file" and paste the path
4. Set dimensions to 300x300
5. Position wherever you like

## Usage Tips

- **Tray icon** - Red dot appears when timer is active
- **Hover tooltip** - Shows current session time and stats
- **Right-click** - Quick access to exit
- **Rewards** - Click the pencil icon to set custom rewards for yourself

Check out my blog: https://thekingofweirdtimes.blogspot.com/

---

# Screenshots

<img width="1365" height="767" alt="image" src="https://github.com/user-attachments/assets/8560bc07-0a83-4048-b619-f2de5d76c38a" />
<img width="419" height="172" alt="image" src="https://github.com/user-attachments/assets/97c77a9b-db3f-4bc4-a5a5-14ecf5c1021c" />
<img width="1365" height="767" alt="image" src="https://github.com/user-attachments/assets/446ccc12-3f98-4960-aba9-3a3c5c10d3fe" />
<img width="1365" height="767" alt="image" src="https://github.com/user-attachments/assets/8ef4572e-c4a9-4c47-89ee-63cf52c61710" />
<img width="1365" height="767" alt="image" src="https://github.com/user-attachments/assets/7bcb6e64-beb8-4874-b849-8396a35bbb37" />

## Tags
Time-tracker, study-timer, productivity-tool, python, focus-timer, cross-platform, hotkey-automation, statistics, streaks, heatmap, minimalist, distraction-free, system-tray-app, gui, study-time-tracker
