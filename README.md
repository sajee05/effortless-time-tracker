# StudyTracker -Py

<img width="1170" height="654" alt="image" src="https://github.com/user-attachments/assets/fe66ee87-c447-4862-9c7c-d1db8b7e5c4c" />


## The problem

Most study-timer utilities require too many clicks, stay in the way and still do not provide useful statistics. When you are concentrating you should be able to start or stop a session instantly, keep the desktop clean and review your progress only when you want to.

## The solution

StudyTracker is a tiny **Python tray-app** that

- starts / stops with one global shortcut (`Alt + Shift + 1`)
- lives in the system tray – one left-click opens its Settings panel
- keeps every session in a lightweight SQLite file  
  (so nothing is lost between reboots)
- shows detailed per-day, per-week and per-month numbers
- Maintains your streak information
- lets you manually correct logs and import / export them as JSON
- plays a short sound on start / stop which you can replace freely

## Quick start (Windows)

1. Install the requirements  
   `pip install pystray pillow keyboard playsound tkcalendar`

2. Place `tracker.py` in a folder anywhere (I prefer you put it in the documents folder) and run it with `python tracker.py` 
   
   - A tray icon appears near the clock – that means the tracker is running.

3. Press **Alt + Shift + 1** to start timing, press it again to stop. A sound confirms both actions.

4. Tray icon:
   
   1. Hovering on the icon shows current time.
   
   2. Left-click the tray icon to open Settings:
      
      - view statistics
      - export / import logs (JSON)
      - add or deduct minutes manually
      - change the two MP3 files
      - read a short instruction page
   
   3. Right click -> Exit -to Close the timer.

5. Want StudyTracker on every boot (WINDOWS)?  
   Press **Win + R**, type `shell:startup`, press Enter and drop a shortcut to `main_tracker.py` (or its compiled `.exe`) in that folder.

## macOS / Linux

The script works as long as  
`python3`, `pip`, `tk`, `pystray`, `keyboard` (requires sudo on Linux for global hot-keys) and `playsound` are available.  
Replace step 1 with `pip3 …` and launch with `python3 main_tracker.py`.

The autostart step varies by desktop environment (login items, `.desktop` files, etc.) but the rest behaves the same.

Check out my blog: https://thekingofweirdtimes.blogspot.com/

Enjoy tracking!

---

## Changelog:

### 03-AUG-25
**✨ Major New Features**
- Complete UI Overhaul: The app has been redesigned from the ground up with a modern, professional look inspired by Apple's design philosophy. It's now a sleek, frameless window that you can drag around your screen.
- Advanced Visual Stats: The "Stats" page now features two powerful visualizations:
- A GitHub-style Heatmap to show your study consistency over the entire year.
- An interactive Bar Chart to compare your study hours weekly or monthly. You can even click on a week in the monthly view to drill down into the details!
- Recent Logs Manager: A new "Recent Logs" tab shows your last 25 study sessions. You can now easily select any log to edit the time or delete it completely.
- Zoom & Accessibility: The entire interface now supports zooming! Use Ctrl + Mouse Wheel or Ctrl +/- to make everything bigger or smaller, perfect for any screen resolution.

✅ **Key Improvements**
- Clearer Stats: The numerical stats table is back and better than ever, with clearer labels for "Today," "This Week," and "This Month" to make your progress easy to understand at a glance.
- Richer Tooltip: Hovering over the tray icon now gives you a more detailed summary, including your current streak, today's total time, and this week's total time.
- Production Ready: The code has been polished and reviewed for bugs to ensure a smooth, stable experience.

Hope you enjoy the new and improved StudyTracker. Thankyou!

---

# 🏷️
ime-tracker, study-timer, productivity-tool, python, focus-timer, cross-platform, hotkey-automation, statistics, streaks, heatmap, minimalist, distraction-free, system-tray-app, gui, study-time-tracker
