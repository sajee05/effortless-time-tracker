# main_tracker.py
import os, sys, json, sqlite3, threading, calendar, asyncio, websockets
from datetime import datetime, timedelta, date
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item
from tkinter import (
    Tk, Toplevel, Frame, Label, Button, Spinbox, messagebox,
    scrolledtext, font, ttk, filedialog, Canvas, LEFT, RIGHT, TOP, BOTTOM, X, Y, BOTH, E, W, N, S, NW
)
from tkcalendar import DateEntry
import keyboard
from playsound import playsound
import webbrowser

# --- Matplotlib (optional import for charting) ---
try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.ticker import FuncFormatter
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("Matplotlib not found. Charting features will be disabled. To enable, run: pip install matplotlib")

# ─────────── Globals ────────────────────────────────────────────────────
APP_NAME = "StudyTracker"
DB_FILE  = "study_tracker.db"

TIMER_RUNNING = False
START_TIME    = None

ROOT            = None          # hidden Tk root
SETTINGS_WINDOW = None          # single settings window instance
TRAY_ICON       = None          # pystray.Icon instance

# --- OBS Overlay / WebSocket Globals ---
OBS_OUTPUT_FILE = "obs_display.html"
WEBSOCKET_PORT = 8765
CONNECTED_CLIENTS = set()
WEBSOCKET_EVENT_LOOP = None

# ─────────── WebSocket Server (for OBS) ────────────────────────────────
async def broadcast_update(message: str):
    """Sends a message to all connected WebSocket clients."""
    if CONNECTED_CLIENTS:
        # Create a list of tasks to send messages to all clients
        tasks = [asyncio.create_task(client.send(message)) for client in CONNECTED_CLIENTS]
        # Wait for all tasks to complete
        if tasks:
            await asyncio.wait(tasks)


async def obs_websocket_handler(websocket, path):
    """Handles new client connections."""
    CONNECTED_CLIENTS.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        CONNECTED_CLIENTS.remove(websocket)

def start_websocket_server():
    """Runs the WebSocket server in its own thread."""
    global WEBSOCKET_EVENT_LOOP
    WEBSOCKET_EVENT_LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(WEBSOCKET_EVENT_LOOP)
    
    start_server = websockets.serve(obs_websocket_handler, "localhost", WEBSOCKET_PORT)
    
    WEBSOCKET_EVENT_LOOP.run_until_complete(start_server)
    WEBSOCKET_EVENT_LOOP.run_forever()

# ─────────── Database helpers ───────────────────────────────────────────
def init_db() -> None:
    with sqlite3.connect(DB_FILE) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time   TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL)''')

def add_log(start: datetime, end: datetime, seconds: int) -> None:
    with sqlite3.connect(DB_FILE) as con:
        con.execute("INSERT INTO logs(start_time,end_time,duration_seconds) VALUES(?,?,?)",
                    (start.isoformat(), end.isoformat(), seconds))

def get_all_logs(limit: int = 0) -> list[sqlite3.Row]:
    with sqlite3.connect(DB_FILE) as con:
        con.row_factory = sqlite3.Row
        query = "SELECT * FROM logs ORDER BY start_time DESC"
        if limit > 0:
            query += f" LIMIT {limit}"
        return con.execute(query).fetchall()

def get_daily_summary(year: int) -> dict[date, int]:
    """Returns a dictionary mapping dates to total seconds studied for a given year."""
    query = """
    SELECT date(start_time) as log_date, SUM(duration_seconds) as total_seconds
    FROM logs
    WHERE strftime('%Y', start_time) = ?
    GROUP BY log_date
    """
    with sqlite3.connect(DB_FILE) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(query, (str(year),)).fetchall()
        return {datetime.strptime(r['log_date'], '%Y-%m-%d').date(): r['total_seconds'] for r in rows}

# ─────────── Utility ────────────────────────────────────────────────────
def hms(sec: int | float) -> str:
    sec = int(round(sec))
    h, m = divmod(sec, 3600)
    m, s = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def play_sound(name: str) -> None:
    def _run():
        path = os.path.join(os.path.dirname(sys.argv[0]), name)
        if os.path.exists(path):
            try: playsound(path)
            except Exception as e: print(f"[sound error] {e}")
    threading.Thread(target=_run, daemon=True).start()

# ─────────── Hot-key handler ────────────────────────────────────────────
def schedule_toggle_timer():
    """Schedules the toggle_timer function to be run in the main Tk thread for thread safety."""
    if ROOT:
        ROOT.after(0, toggle_timer)

def toggle_timer() -> None:
    """Toggles the timer state. MUST be run in the main Tkinter thread."""
    global TIMER_RUNNING, START_TIME
    TIMER_RUNNING = not TIMER_RUNNING

    update_tray_icon()

    if SETTINGS_WINDOW and SETTINGS_WINDOW.winfo_exists():
        ROOT.after(0, SETTINGS_WINDOW.update_all_views)

    if TIMER_RUNNING:
        START_TIME = datetime.now()
        play_sound("start.mp3")
    else:
        if START_TIME:
            end = datetime.now()
            add_log(START_TIME, end, int((end - START_TIME).total_seconds()))
            START_TIME = None
            play_sound("stop.mp3")
            if SETTINGS_WINDOW and SETTINGS_WINDOW.winfo_exists():
                ROOT.after(100, SETTINGS_WINDOW.update_all_views)

# ─────────── Statistics ────────────────────────────────────────────────
def calc_stats() -> dict:
    rows = get_all_logs()
    if not rows:
        return {k: (0 if 'streak' in k else "00:00:00") for k in [
            'total_hours', 'today_hours', 'weekly_hours', 'monthly_hours',
            'daily_avg', 'weekly_avg', 'monthly_avg', 'current_streak',
            'longest_streak']} | {k:0 for k in ['total_sec', 'today_sec', 'week_sec', 'month_sec']}

    total_sec = sum(r['duration_seconds'] for r in rows)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    today_sec = sum(r['duration_seconds'] for r in rows if datetime.fromisoformat(r['start_time']).date() == today)
    week_sec = sum(r['duration_seconds'] for r in rows if datetime.fromisoformat(r['start_time']).date() >= week_start)
    month_sec = sum(r['duration_seconds'] for r in rows if datetime.fromisoformat(r['start_time']).date() >= month_start)

    dates = {datetime.fromisoformat(r['start_time']).date() for r in rows}
    weeks = {(d.year, d.isocalendar()[1]) for d in dates}
    months = {(d.year, d.month) for d in dates}

    daily_avg = total_sec / len(dates) if dates else 0
    weekly_avg = total_sec / len(weeks) if weeks else 0
    monthly_avg = total_sec / len(months) if months else 0

    # Streaks
    seq = sorted(dates)
    longest = 1 if seq else 0
    current = 0
    if seq and seq[-1] in {today, today - timedelta(days=1)}:
        current = 1
        for i in range(len(seq) - 1, 0, -1):
            if seq[i] - seq[i - 1] == timedelta(days=1): current += 1
            else: break
    if seq and seq[-1] != today and seq[-1] != today - timedelta(days=1): current = 0

    tmp = 1
    for i in range(len(seq) - 1):
        if seq[i+1] - seq[i] == timedelta(days=1):
            tmp += 1
            longest = max(longest, tmp)
        else: tmp = 1

    return dict(
        total_hours=hms(total_sec), today_hours=hms(today_sec),
        weekly_hours=hms(week_sec), monthly_hours=hms(month_sec),
        daily_avg=hms(daily_avg), weekly_avg=hms(weekly_avg),
        monthly_avg=hms(monthly_avg),
        total_sec=total_sec, today_sec=today_sec, week_sec=week_sec, month_sec=month_sec,
        current_streak=current, longest_streak=longest
    )

# ─────────── GUI (Settings window) ─────────────────────────────────────
class SettingsWindow(Toplevel):
    # --- UI Theme ---
    BG_COLOR = "#2E2E2E"
    CONTENT_BG = "#3C3C3C"
    TEXT_COLOR = "#FFFFFF"
    ACCENT_COLOR = "#1E90FF"
    BTN_COLOR = "#4A4A4A"
    HEATMAP_COLORS = ["#444444", "#196127", "#239a3b", "#7bc96f", "#c6e48b"]

    def __init__(self, master: Tk):
        super().__init__(master)
        self.title(f"{APP_NAME} Dashboard")
        self.geometry("1100x680")
        self.configure(bg=self.BG_COLOR)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.overrideredirect(True)
        self._offset_x = 0
        self._offset_y = 0
        self.zoom_level = 0
        self._setup_fonts()
        self.heatmap_year = date.today().year
        self.chart_date = date.today()
        self.chart_mode = "Weekly"
        self._init_layout()
        self._init_styles()
        self._bind_events()
        self.show_frame("Stats")
        self.update_all_views()

    def _setup_fonts(self):
        self.f_title = font.Font(family="Helvetica", size=16 + self.zoom_level, weight="bold")
        self.f_head = font.Font(family="Helvetica", size=10 + self.zoom_level, weight="bold")
        self.f_body = font.Font(family="Helvetica", size=10 + self.zoom_level)
        self.f_small = font.Font(family="Helvetica", size=8 + self.zoom_level)

    def _init_styles(self):
        s = ttk.Style()
        s.theme_use('clam')
        s.configure("TButton", background=self.BTN_COLOR, foreground=self.TEXT_COLOR, borderwidth=0, focusthickness=3, focuscolor='none')
        s.map("TButton", background=[('active', self.ACCENT_COLOR)])
        s.configure("Treeview.Heading", font=self.f_head, background=self.BTN_COLOR, foreground=self.TEXT_COLOR, relief="flat")
        s.map("Treeview.Heading", background=[('active', self.BTN_COLOR)])
        s.configure("Treeview", background=self.CONTENT_BG, foreground=self.TEXT_COLOR, fieldbackground=self.CONTENT_BG, font=self.f_body, rowheight=25 + self.zoom_level * 2)
        s.map('Treeview', background=[('selected', self.ACCENT_COLOR)])
        s.configure("TCombobox", fieldbackground=self.BTN_COLOR, background=self.BTN_COLOR, foreground=self.TEXT_COLOR, arrowcolor=self.TEXT_COLOR, selectbackground=self.BTN_COLOR, selectforeground=self.TEXT_COLOR)

    def _init_layout(self):
        title_bar = Frame(self, bg=self.BG_COLOR, relief='raised', bd=0)
        title_bar.pack(side=TOP, fill=X)
        lbl_title = Label(title_bar, text=f" {APP_NAME} Dashboard", bg=self.BG_COLOR, fg=self.TEXT_COLOR, font=self.f_head)
        lbl_title.pack(side=LEFT, padx=10)
        btn_close = Button(title_bar, text='✕', bg=self.BG_COLOR, fg=self.TEXT_COLOR, command=self.withdraw, relief='flat', font=self.f_head)
        btn_close.pack(side=RIGHT, padx=5)
        title_bar.bind('<ButtonPress-1>', self.click_window)
        title_bar.bind('<B1-Motion>', self.drag_window)
        lbl_title.bind('<ButtonPress-1>', self.click_window)
        lbl_title.bind('<B1-Motion>', self.drag_window)
        main_frame = Frame(self, bg=self.BG_COLOR, padx=1, pady=1)
        main_frame.pack(fill=BOTH, expand=True)
        nav_frame = Frame(main_frame, bg=self.BG_COLOR, width=180, padx=5, pady=10)
        nav_frame.pack(side=LEFT, fill=Y)
        nav_frame.pack_propagate(False)
        self.content_frame = Frame(main_frame, bg=self.CONTENT_BG)
        self.content_frame.pack(side=RIGHT, fill=BOTH, expand=True, padx=(0, 5), pady=(0, 5))
        nav_items = [("Stats", "📈"), ("Recent Logs", "📋"), ("Manual Log", "⏳"), ("Export / Import", "💾"), ("Instructions", "💡")]
        self.nav_buttons = {}
        for name, icon in nav_items:
            key = name.replace(" / ", "_").replace(" ", "_")
            btn = Button(nav_frame, text=f" {icon} {name}", font=self.f_head, bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat", anchor="w", padx=10, pady=10, command=lambda k=key: self.show_frame(k))
            btn.pack(fill=X, pady=3)
            self.nav_buttons[key] = btn
        self.frames = {}
        for name, _ in nav_items:
            key = name.replace(" / ", "_").replace(" ", "_")
            frame = Frame(self.content_frame, bg=self.CONTENT_BG, padx=20, pady=20)
            frame.grid(row=0, column=0, sticky="nsew")
            self.frames[key] = frame
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)
        self._make_stats_page()
        self._make_recent_logs_page()
        self._make_manual_log_page()
        self._make_export_import_page()
        self._make_instructions_page()

    def _bind_events(self):
        self.bind('<Control-plus>', self.zoom_in)
        self.bind('<Control-equal>', self.zoom_in)
        self.bind('<Control-minus>', self.zoom_out)
        self.bind('<Control-0>', self.zoom_reset)
        self.bind('<Control-MouseWheel>', self._handle_scroll_zoom)

    def _make_stats_page(self):
        f = self.frames["Stats"]
        top_bar = Frame(f, bg=self.CONTENT_BG)
        top_bar.pack(fill=X, pady=(0, 15))
        self.lbl_cur = Label(top_bar, font=self.f_title, bg=self.CONTENT_BG, fg=self.TEXT_COLOR)
        self.lbl_cur.pack(side=LEFT, padx=(0, 50))
        self.lbl_long = Label(top_bar, font=self.f_title, bg=self.CONTENT_BG, fg=self.TEXT_COLOR)
        self.lbl_long.pack(side=LEFT, padx=(0, 50))
        self.lbl_total = Label(top_bar, font=self.f_title, bg=self.CONTENT_BG, fg=self.TEXT_COLOR)
        self.lbl_total.pack(side=RIGHT)
        self.stats_tree = ttk.Treeview(f, columns=("Total", "Average"), show="tree headings", height=3)
        self.stats_tree.heading("#0", text="Timeframe")
        self.stats_tree.heading("Total", text="Total Time Logged")
        self.stats_tree.heading("Average", text="Historical Daily Average")
        self.stats_tree.column("#0", width=250, anchor="w", stretch=False)
        self.stats_tree.column("Total", width=200, anchor="center", stretch=True)
        self.stats_tree.column("Average", width=250, anchor="center", stretch=True)
        self.stats_tree.pack(fill=X, pady=(5, 15))
        self._make_heatmap(f)
        if MATPLOTLIB_AVAILABLE: self._make_barchart(f)
        else: Label(f, text="Install 'matplotlib' to enable charts.", font=self.f_head, bg=self.CONTENT_BG, fg="orange").pack(pady=20)

    def _make_heatmap(self, parent):
        # Outer frame for the border. Light blue as requested.
        border_frame = Frame(parent, bg="#87CEFA", bd=0) # LightSkyBlue
        border_frame.pack(fill=X, pady=(10, 15))

        # Original frame, now inside the border frame with a 1px margin to create the border
        hm_frame = Frame(border_frame, bg=self.CONTENT_BG)
        hm_frame.pack(fill=BOTH, expand=True, padx=1, pady=1)

        nav = Frame(hm_frame, bg=self.CONTENT_BG)
        nav.pack(fill=X, padx=5, pady=5)
        Button(nav, text="⬅️", font=self.f_body, command=lambda: self._navigate_heatmap(-1), bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat").pack(side=LEFT)
        self.heatmap_year_label = Label(nav, text=str(self.heatmap_year), font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR)
        self.heatmap_year_label.pack(side=LEFT, padx=10)
        Button(nav, text="➡️", font=self.f_body, command=lambda: self._navigate_heatmap(1), bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat").pack(side=LEFT)
        Label(nav, text="Less", font=self.f_small, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack(side=RIGHT, padx=(10, 2))
        for color in self.HEATMAP_COLORS:
            Label(nav, text="■", font=self.f_body, bg=self.CONTENT_BG, fg=color).pack(side=RIGHT)
        Label(nav, text="More", font=self.f_small, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack(side=RIGHT, padx=(2, 0))

        # The canvas height is increased slightly to give the squares more room.
        self.heatmap_canvas = Canvas(hm_frame, bg=self.CONTENT_BG, height=145, highlightthickness=0)
        self.heatmap_canvas.pack(fill=X, pady=(5, 5), padx=5)

    def _make_barchart(self, parent):
        bc_frame = Frame(parent, bg=self.CONTENT_BG)
        bc_frame.pack(fill=BOTH, expand=True)
        nav = Frame(bc_frame, bg=self.CONTENT_BG)
        nav.pack(fill=X, pady=(0, 5))
        Button(nav, text="⬅️", font=self.f_body, command=lambda: self._navigate_chart(-1), bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat").pack(side=LEFT)
        self.chart_period_label = Label(nav, text="", font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR, width=35)
        self.chart_period_label.pack(side=LEFT, padx=10)
        Button(nav, text="➡️", font=self.f_body, command=lambda: self._navigate_chart(1), bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat").pack(side=LEFT)
        self.chart_mode_cb = ttk.Combobox(nav, values=["Weekly", "Monthly"], state="readonly", width=10, font=self.f_body)
        self.chart_mode_cb.set(self.chart_mode)
        self.chart_mode_cb.pack(side=RIGHT)
        self.chart_mode_cb.bind("<<ComboboxSelected>>", self._on_chart_mode_change)
        self.fig = Figure(figsize=(5, 3), dpi=100, facecolor=self.CONTENT_BG)
        self.ax = self.fig.add_subplot(111)
        self.chart_canvas = FigureCanvasTkAgg(self.fig, master=bc_frame)
        self.chart_canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self.fig.canvas.mpl_connect('button_press_event', self._on_chart_click)

    def _make_recent_logs_page(self):
        f = self.frames["Recent_Logs"]
        cols = ("ID", "Date", "Start Time", "End Time", "Duration")
        self.log_tree = ttk.Treeview(f, columns=cols, show="headings")
        for col in cols: self.log_tree.heading(col, text=col)
        self.log_tree.column("ID", width=50, anchor="center")
        self.log_tree.column("Date", width=120, anchor="center")
        self.log_tree.column("Start Time", width=120, anchor="center")
        self.log_tree.column("End Time", width=120, anchor="center")
        self.log_tree.column("Duration", width=120, anchor="center")
        self.log_tree.pack(side=TOP, fill=BOTH, expand=True)
        btn_frame = Frame(f, bg=self.CONTENT_BG, pady=10)
        btn_frame.pack(fill=X)
        Button(btn_frame, text="Edit Selected Log", font=self.f_head, command=self._edit_log, bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat", padx=10, pady=5).pack(side=LEFT, padx=5)
        Button(btn_frame, text="Delete Selected Log", font=self.f_head, command=self._delete_log, bg="#DC3545", fg=self.TEXT_COLOR, relief="flat", padx=10, pady=5).pack(side=LEFT, padx=5)

    def _make_manual_log_page(self):
        f = self.frames["Manual_Log"]
        Label(f, text="Manually Add / Deduct Time", font=self.f_title, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack(pady=20)
        Label(f, text="Select Date:", font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack()
        self.cal = DateEntry(f, width=12, background=self.ACCENT_COLOR, foreground=self.TEXT_COLOR, borderwidth=2, date_pattern="yyyy-mm-dd")
        self.cal.pack(pady=(0, 20))
        Label(f, text="Minutes:", font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack()
        self.spin = Spinbox(f, from_=0, to=1440, width=10, font=self.f_body)
        self.spin.pack(pady=(0, 25))
        bar = Frame(f, bg=self.CONTENT_BG); bar.pack()
        Button(bar, text="Add ➕", font=self.f_head, bg="#28A745", fg="white", relief="flat", padx=10, pady=5, command=lambda: self._manual_op("add")).pack(side="left", padx=10)
        Button(bar, text="Deduct ➖", font=self.f_head, bg="#DC3545", fg="white", relief="flat", padx=10, pady=5, command=lambda: self._manual_op("deduct")).pack(side="left", padx=10)

    def _make_export_import_page(self):
        f = self.frames["Export_Import"]
        Label(f, text="Export / Import Logs", font=self.f_title, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack(pady=20)
        Button(f, text="Export to JSON", font=self.f_head, bg=self.ACCENT_COLOR, fg="white", relief="flat", padx=15, pady=10, command=self.export_json).pack(pady=10)
        Button(f, text="Import from JSON", font=self.f_head, bg="#6f42c1", fg="white", relief="flat", padx=15, pady=10, command=self.import_json).pack(pady=10)

    def _make_instructions_page(self):
        f = self.frames["Instructions"]
        Label(f, text="How To Use", font=self.f_title, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack(pady=20)
        info_text = scrolledtext.ScrolledText(f, wrap="word", bg=self.CONTENT_BG, fg=self.TEXT_COLOR, relief="flat", font=self.f_body, borderwidth=0)
        info = ("•  Hot-key (Alt + Shift + 1) to start or stop the study timer.\n\n"
                "•  The timer runs in the background. You can close this window.\n\n"
                "•  Left-click the book icon (📖) in your system tray to open this dashboard.\n\n"
                "•  Zoom: Ctrl + Mouse Wheel, Ctrl & +/-, Ctrl & 0 to reset.\n\n"
                "•  Stats, charts, and logs update automatically.\n\n"
                "•  You can manually add or remove time, and manage individual logs.\n\n"
                "•  To make the app run on startup, place a shortcut in the Windows startup folder (Win+R → `shell:startup`).\n\n"
                "Check out my blog: ")
        info_text.insert("1.0", info)
        blog_url = "[https://thekingofweirdtimes.blogspot.com](https://thekingofweirdtimes.blogspot.com)"
        link_start = info_text.index("end-1c")
        info_text.insert("end", blog_url)
        link_end = info_text.index("end-1c")
        info_text.tag_add("link", link_start, link_end)
        info_text.tag_config("link", foreground="cyan", underline=True)
        info_text.tag_bind("link", "<Button-1>", lambda e, url=blog_url: webbrowser.open(url))
        info_text.tag_bind("link", "<Enter>", lambda e: info_text.config(cursor="hand2"))
        info_text.tag_bind("link", "<Leave>", lambda e: info_text.config(cursor=""))
        info_text.config(state="disabled")
        info_text.pack(fill=BOTH, expand=True, padx=10)
        self.instr_text = info_text

    def update_all_views(self):
        if not self.winfo_exists(): return
        self.update_stats_page()
        self.update_recent_logs_page()

    def update_stats_page(self):
        s = calc_stats()
        self.lbl_cur.config(text=f"Current Streak 🔥: {s['current_streak']} days")
        self.lbl_long.config(text=f"Longest Streak: {s['longest_streak']} days")
        self.lbl_total.config(text=f"Total Studied: {s['total_hours']}")
        for i in self.stats_tree.get_children(): self.stats_tree.delete(i)
        today_str = f"Today ({date.today():%b %d, %Y})"
        self.stats_tree.insert('', 'end', text=today_str, values=(s['today_hours'], s['daily_avg']))
        self.stats_tree.insert('', 'end', text="This Week", values=(s['weekly_hours'], s['weekly_avg']))
        self.stats_tree.insert('', 'end', text="This Month", values=(s['monthly_hours'], s['monthly_avg']))
        self._update_heatmap()
        if MATPLOTLIB_AVAILABLE: self._update_barchart()

    def _update_heatmap(self):
        self.heatmap_year_label.config(text=str(self.heatmap_year))
        self.heatmap_canvas.delete("all")
        data = get_daily_summary(self.heatmap_year)
        max_val = max(data.values()) if data else 1
        def get_color(val):
            if val == 0: return self.HEATMAP_COLORS[0]
            p = val / max_val
            if p < 0.25: return self.HEATMAP_COLORS[1]
            if p < 0.50: return self.HEATMAP_COLORS[2]
            if p < 0.75: return self.HEATMAP_COLORS[3]
            return self.HEATMAP_COLORS[4]
        start_day, total_days = calendar.monthrange(self.heatmap_year, 1)
        first_day_weekday = date(self.heatmap_year, 1, 1).weekday()
        box_size, gap = 17, 3
        for day_of_year in range(1, 366 if calendar.isleap(self.heatmap_year) else 365):
            current_date = date(self.heatmap_year, 1, 1) + timedelta(days=day_of_year - 1)
            seconds = data.get(current_date, 0)
            color = get_color(seconds)
            day_index = first_day_weekday + day_of_year - 1
            col = day_index // 7
            row = day_index % 7
            x1 = col * (box_size + gap) + gap
            y1 = row * (box_size + gap) + gap
            x2 = x1 + box_size
            y2 = y1 + box_size
            self.heatmap_canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline=self.BG_COLOR)

    def _update_barchart(self):
        self.ax.clear()
        self.ax.set_facecolor(self.CONTENT_BG)
        self.ax.tick_params(colors=self.TEXT_COLOR, which='both', labelsize=self.f_small.cget('size'))
        for spine in self.ax.spines.values(): spine.set_edgecolor(self.TEXT_COLOR)
        if self.chart_mode == "Weekly": self._plot_weekly_data()
        else: self._plot_monthly_data()
        self.fig.tight_layout(pad=2)
        self.chart_canvas.draw()

    def _plot_weekly_data(self):
        start_of_week = self.chart_date - timedelta(days=self.chart_date.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        self.chart_period_label.config(text=f"{start_of_week:%b %d, %Y} - {end_of_week:%b %d, %Y}")
        dates = [start_of_week + timedelta(days=i) for i in range(7)]
        logs = get_all_logs()
        data = {d: 0 for d in dates}
        for log in logs:
            log_date = datetime.fromisoformat(log['start_time']).date()
            if start_of_week <= log_date <= end_of_week: data[log_date] += log['duration_seconds']
        labels = [d.strftime("%a") for d in dates]
        values = [v / 3600 for v in data.values()]
        self.ax.bar(labels, values, color=self.ACCENT_COLOR)
        self.ax.set_title("Weekly Study Time", color=self.TEXT_COLOR, fontdict={'size': self.f_head.cget('size')})
        self.ax.set_ylabel("Hours", color=self.TEXT_COLOR, fontdict={'size': self.f_body.cget('size')})
        self.ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{y:.1f}'))

    def _plot_monthly_data(self):
        month_start = self.chart_date.replace(day=1)
        self.chart_period_label.config(text=f"{month_start:%B %Y}")
        logs = get_all_logs()
        week_starts = []
        d = month_start
        while d.weekday() != 0: d -= timedelta(days=1)
        while d.year < month_start.year or (d.year == month_start.year and d.month <= month_start.month):
             week_starts.append(d)
             d += timedelta(days=7)
        weekly_totals = {ws: 0 for ws in week_starts}
        for log in logs:
             log_date = datetime.fromisoformat(log['start_time']).date()
             if log_date.year == month_start.year and log_date.month == month_start.month:
                 ws = log_date - timedelta(days=log_date.weekday())
                 if ws in weekly_totals: weekly_totals[ws] += log['duration_seconds']
        labels = [f"W {i+1}\n({ws:%b %d})" for i, ws in enumerate(weekly_totals)]
        values = [v / 3600 for v in weekly_totals.values()]
        self.ax.bar(labels, values, color=self.ACCENT_COLOR)
        self.ax.set_title("Monthly Study Time", color=self.TEXT_COLOR, fontdict={'size': self.f_head.cget('size')})
        self.ax.set_ylabel("Hours per Week", color=self.TEXT_COLOR, fontdict={'size': self.f_body.cget('size')})
        self.ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{y:.1f}'))
        self.monthly_chart_week_starts = list(weekly_totals.keys())

    def update_recent_logs_page(self):
        for i in self.log_tree.get_children(): self.log_tree.delete(i)
        for log in get_all_logs(limit=25):
            start = datetime.fromisoformat(log['start_time'])
            end = datetime.fromisoformat(log['end_time'])
            self.log_tree.insert('', 'end', values=(log['id'], start.strftime('%Y-%m-%d'), start.strftime('%H:%M:%S'), end.strftime('%H:%M:%S'), hms(log['duration_seconds'])))

    def click_window(self, event):
        self._offset_x = event.x
        self._offset_y = event.y

    def drag_window(self, event):
        x = self.winfo_pointerx() - self._offset_x
        y = self.winfo_pointery() - self._offset_y
        self.geometry(f'+{x}+{y}')

    def show_frame(self, name: str):
        for key, btn in self.nav_buttons.items():
            btn.config(bg=self.ACCENT_COLOR if key == name else self.BTN_COLOR)
        self.frames[name].tkraise()
        if name == "Stats": self.update_stats_page()
        if name == "Recent_Logs": self.update_recent_logs_page()

    def _navigate_heatmap(self, direction: int):
        self.heatmap_year += direction
        self._update_heatmap()

    def _navigate_chart(self, direction: int):
        if self.chart_mode == "Weekly": self.chart_date += timedelta(days=7 * direction)
        else:
            new_month = self.chart_date.month + direction
            new_year = self.chart_date.year
            if new_month > 12: new_month, new_year = 1, new_year + 1
            if new_month < 1: new_month, new_year = 12, new_year - 1
            self.chart_date = self.chart_date.replace(year=new_year, month=new_month, day=1)
        self._update_barchart()

    def _on_chart_mode_change(self, event=None):
        self.chart_mode = self.chart_mode_cb.get()
        self.chart_date = date.today()
        self._update_barchart()

    def _on_chart_click(self, event):
        if self.chart_mode != "Monthly" or event.xdata is None: return
        bar_index = int(round(event.xdata))
        if 0 <= bar_index < len(self.monthly_chart_week_starts):
            self.chart_date = self.monthly_chart_week_starts[bar_index]
            self.chart_mode = "Weekly"
            self.chart_mode_cb.set("Weekly")
            self._update_barchart()

    def _delete_log(self):
        selected_item = self.log_tree.focus()
        if not selected_item:
            messagebox.showwarning("No Selection", "Please select a log to delete.", parent=self)
            return
        log_id = self.log_tree.item(selected_item)['values'][0]
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete log ID {log_id}?", parent=self):
            with sqlite3.connect(DB_FILE) as con: con.execute("DELETE FROM logs WHERE id=?", (log_id,))
            self.update_all_views()

    def _edit_log(self):
        selected_item = self.log_tree.focus()
        if not selected_item:
            messagebox.showwarning("No Selection", "Please select a log to edit.", parent=self)
            return
        log_id = self.log_tree.item(selected_item)['values'][0]
        dlg = Toplevel(self)
        dlg.title(f"Edit Log {log_id}")
        dlg.geometry("300x150")
        dlg.configure(bg=self.CONTENT_BG)
        dlg.transient(self)
        dlg.grab_set()
        Label(dlg, text="Minutes to Add/Deduct:", font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack(pady=10)
        spin = Spinbox(dlg, from_=-1440, to=1440, width=10, font=self.f_body)
        spin.pack()
        def apply_change():
            try:
                mins_to_change = int(spin.get())
                secs_to_change = mins_to_change * 60
                with sqlite3.connect(DB_FILE) as con:
                    cur = con.cursor()
                    log = cur.execute("SELECT * FROM logs WHERE id=?", (log_id,)).fetchone()
                    if not log: return
                    new_duration = log[3] + secs_to_change
                    if new_duration <= 0: cur.execute("DELETE FROM logs WHERE id=?", (log_id,))
                    else:
                        new_end = (datetime.fromisoformat(log[1]) + timedelta(seconds=secs_to_change)).isoformat()
                        cur.execute("UPDATE logs SET end_time=?, duration_seconds=? WHERE id=?", (new_end, new_duration, log_id))
                self.update_all_views()
                dlg.destroy()
            except ValueError: messagebox.showerror("Invalid Input", "Please enter a valid integer.", parent=dlg)
        Button(dlg, text="Apply Changes", command=apply_change, font=self.f_head, bg=self.ACCENT_COLOR, fg=self.TEXT_COLOR, relief="flat", padx=10, pady=5).pack(pady=15)

    def _manual_op(self, mode: str):
        try:
            mins = int(self.spin.get())
            if mins <= 0: raise ValueError
        except ValueError:
            messagebox.showwarning("Invalid Input", "Enter a positive integer.", parent=self)
            return
        target_date = self.cal.get_date()
        sec = mins * 60
        if mode == "add":
            start = datetime.combine(target_date, datetime.min.time())
            add_log(start, start + timedelta(seconds=sec), sec)
        else:
            logs = [l for l in get_all_logs() if datetime.fromisoformat(l['start_time']).date() == target_date]
            if not logs:
                messagebox.showinfo("Info", f"No sessions on {target_date}.", parent=self)
                return
            rem = sec
            with sqlite3.connect(DB_FILE) as con:
                cur = con.cursor()
                for l in sorted(logs, key=lambda r: r['start_time'], reverse=True):
                    if rem <= 0: break
                    if rem >= l['duration_seconds']:
                        cur.execute("DELETE FROM logs WHERE id=?", (l['id'],))
                        rem -= l['duration_seconds']
                    else:
                        new_dur = l['duration_seconds'] - rem
                        new_end = (datetime.fromisoformat(l['start_time']) + timedelta(seconds=new_dur)).isoformat()
                        cur.execute("UPDATE logs SET duration_seconds=?, end_time=? WHERE id=?", (new_dur, new_end, l['id']))
                        rem = 0
        self.update_all_views()
        self.spin.delete(0, 'end'); self.spin.insert(0, '0')

    def export_json(self):
        data = [dict(r) for r in get_all_logs()]
        if not data:
            messagebox.showinfo("Export", "No data to export.", parent=self)
            return
        fname = filedialog.asksaveasfilename(parent=self, title="Save Export File", defaultextension=".json", filetypes=[("JSON files", "*.json")], initialfile=f"study_logs_{datetime.now():%Y%m%d}.json")
        if not fname: return
        try:
            with open(fname, 'w', encoding='utf-8') as fp: json.dump(data, fp, indent=4)
            messagebox.showinfo("Export Successful", f"Data saved to {os.path.basename(fname)}", parent=self)
        except Exception as e: messagebox.showerror("Export Error", str(e), parent=self)

    def import_json(self):
        path = filedialog.askopenfilename(parent=self, title="Choose JSON file", filetypes=[("JSON files", "*.json")])
        if not path: return
        if not messagebox.askyesno("Confirm Import", "This will add the sessions from the file to your current logs. Continue?", parent=self): return
        try:
            with open(path, 'r', encoding='utf-8') as fp: recs = json.load(fp)
            added_count = 0
            with sqlite3.connect(DB_FILE) as con:
                for r in recs:
                    if all(k in r for k in ("start_time", "end_time", "duration_seconds")):
                        try:
                            con.execute("INSERT INTO logs(start_time, end_time, duration_seconds) VALUES(?,?,?)", (r["start_time"], r["end_time"], int(r["duration_seconds"])))
                            added_count += 1
                        except sqlite3.Error: pass
            self.update_all_views()
            messagebox.showinfo("Import Complete", f"Successfully imported {added_count} log entries.", parent=self)
        except Exception as e: messagebox.showerror("Import Error", str(e), parent=self)

    def _handle_scroll_zoom(self, event):
        if event.delta > 0: self.zoom_in()
        else: self.zoom_out()

    def zoom_in(self, event=None):
        if self.zoom_level < 5:
            self.zoom_level += 1
            self._apply_zoom()

    def zoom_out(self, event=None):
        if self.zoom_level > -5:
            self.zoom_level -= 1
            self._apply_zoom()

    def zoom_reset(self, event=None):
        self.zoom_level = 0
        self._apply_zoom()

    def _apply_zoom(self):
        self._setup_fonts()
        self._init_styles()
        for w in self.winfo_children(): self._update_widget_fonts(w)
        self.update_all_views()

    def _update_widget_fonts(self, parent_widget):
        for w in parent_widget.winfo_children():
            widget_type = w.winfo_class()
            try:
                if widget_type == 'Label':
                    current_size = font.Font(font=w.cget('font')).cget('size')
                    if current_size >= 15: w.config(font=self.f_title)
                    elif current_size >= 9: w.config(font=self.f_head)
                    else: w.config(font=self.f_small)
                elif widget_type in ('Button', 'Spinbox', 'TCombobox', 'ScrolledText'): w.config(font=self.f_body if widget_type != 'Button' else self.f_head)
            except Exception: pass
            if w.winfo_children(): self._update_widget_fonts(w)
        if hasattr(self, 'instr_text'): self.instr_text.config(font=self.f_body)
        self.stats_tree.heading("#0", text="Timeframe")
        self.stats_tree.heading("Total", text="Total Time Logged")
        self.stats_tree.heading("Average", text="Historical Daily Average")

# ─────────── OBS Overlay Data Handling ──────────────────────────────────
def periodic_data_update():
    """Calculates stats and schedules them to be broadcast over WebSocket."""
    # 1. Calculate all necessary values
    stats = calc_stats()
    current_session_sec = 0
    if TIMER_RUNNING and START_TIME:
        current_session_sec = (datetime.now() - START_TIME).total_seconds()

    today_total_sec = stats.get('today_sec', 0) + current_session_sec
    streak = stats.get('current_streak', 0)

    # 2. Create data payload
    payload = {
        "sessionTime": hms(current_session_sec),
        "todayTime": hms(today_total_sec),
        "streak": f"{streak} 🔥",
        "isTimerRunning": TIMER_RUNNING
    }
    
    # 3. Broadcast the data via the WebSocket server's event loop
    if WEBSOCKET_EVENT_LOOP:
        message = json.dumps(payload)
        asyncio.run_coroutine_threadsafe(broadcast_update(message), WEBSOCKET_EVENT_LOOP)

    # 4. Schedule the next update
    if ROOT and ROOT.winfo_exists():
        ROOT.after(1000, periodic_data_update)

def create_obs_html_file():
    """Creates the obs_display.html file ONCE at startup."""
    html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Study Tracker OBS</title>
    <style>
        @import url('[https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap](https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap)');
        body {{
            background-color: transparent;
            font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
            color: white;
            margin: 0;
            padding: 20px;
            text-shadow: 0 0 8px rgba(0,0,0,0.7);
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}
        .container {{
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 12px;
        }}
        .stat-block {{
            background-color: rgba(20, 20, 20, 0.45);
            border-radius: 14px;
            padding: 8px 18px;
            text-align: left;
            min-width: 230px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            transition: all 0.3s ease;
        }}
        .value {{
            font-size: 34px;
            font-weight: 700;
            line-height: 1.1;
            letter-spacing: -1px;
        }}
        .label {{
            font-size: 14px;
            font-weight: 400;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            opacity: 0.85;
        }}
        .timer-active .value {{
             color: #A7F3D0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div id="session-block" class="stat-block">
            <div id="session-value" class="value">00:00:00</div>
            <div class="label">Session</div>
        </div>
        <div class="stat-block">
            <div id="today-value" class="value">00:00:00</div>
            <div class="label">Today's Total</div>
        </div>
        <div class="stat-block">
            <div id="streak-value" class="value">0 🔥</div>
            <div class="label">Current Streak</div>
        </div>
    </div>
    <script>
        function connect() {{
            const ws = new WebSocket('ws://localhost:{WEBSOCKET_PORT}');

            ws.onmessage = function(event) {{
                const data = JSON.parse(event.data);
                document.getElementById('session-value').innerText = data.sessionTime;
                document.getElementById('today-value').innerText = data.todayTime;
                document.getElementById('streak-value').innerHTML = data.streak; // Use innerHTML for emoji

                const sessionBlock = document.getElementById('session-block');
                if (data.isTimerRunning) {{
                    sessionBlock.classList.add('timer-active');
                }} else {{
                    sessionBlock.classList.remove('timer-active');
                }}
            }};

            ws.onclose = function(e) {{
                console.log('Socket is closed. Reconnecting in 1 second.', e.reason);
                setTimeout(function() {{
                    connect();
                }}, 1000);
            }};

            ws.onerror = function(err) {{
                console.error('Socket encountered error: ', err.message, 'Closing socket');
                ws.close();
            }};
        }}
        connect();
    </script>
</body>
</html>
"""
    try:
        with open(OBS_OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_content.strip())
    except Exception as e:
        print(f"[OBS HTML Error] Could not write to {OBS_OUTPUT_FILE}: {e}")

# ─────────── Tray icon setup ────────────────────────────────────────────
def update_tray_icon():
    if TRAY_ICON: TRAY_ICON.icon = make_icon(is_active=TIMER_RUNNING)

def make_icon(is_active: bool = False):
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    try:
        font_obj = ImageFont.truetype("seguiemj.ttf", 50)
    except IOError:
        try: font_obj = ImageFont.truetype("Symbola.ttf", 50)
        except IOError: font_obj = ImageFont.load_default()
    draw.text((5, 0), "📖", font=font_obj, fill="white")
    if is_active: draw.ellipse((48, 5, 60, 17), fill='red', outline='white')
    return image

def _show_settings(_icon=None, _item=None):
    global SETTINGS_WINDOW
    if not SETTINGS_WINDOW or not SETTINGS_WINDOW.winfo_exists():
        SETTINGS_WINDOW = SettingsWindow(ROOT)
    SETTINGS_WINDOW.deiconify()
    SETTINGS_WINDOW.lift()
    SETTINGS_WINDOW.focus_force()
    SETTINGS_WINDOW.update_all_views()

def _on_quit(icon, _item):
    if TIMER_RUNNING: schedule_toggle_timer() # Use scheduler for thread safety
    if WEBSOCKET_EVENT_LOOP: WEBSOCKET_EVENT_LOOP.call_soon_threadsafe(WEBSOCKET_EVENT_LOOP.stop)
    icon.stop()
    ROOT.quit()

def _update_tooltip():
    if TRAY_ICON and TRAY_ICON.visible:
        stats = calc_stats()
        current_session_sec = (datetime.now() - START_TIME).total_seconds() if TIMER_RUNNING and START_TIME else 0
        today_sec = stats.get('today_sec', 0) + current_session_sec
        week_sec = stats.get('week_sec', 0) + current_session_sec
        running_status = f"Studying: {hms(current_session_sec)}\n" if TIMER_RUNNING else "Timer stopped.\n"
        TRAY_ICON.title = f"{running_status}Streak: {stats['current_streak']}🔥 | Today: {hms(today_sec)} | Week: {hms(week_sec)}"
    ROOT.after(1000, _update_tooltip)

def setup_tray():
    global ROOT, TRAY_ICON
    ROOT = Tk()
    ROOT.withdraw()

    # BUG FIX: Move hotkey registration here, after ROOT is created.
    # This ensures ROOT.after() will work correctly from the keyboard thread.
    try:
        keyboard.add_hotkey("alt+shift+1", schedule_toggle_timer, suppress=False)
    except Exception as e:
        print(f"Could not set hotkey (requires admin/root privileges): {e}")

    try: icon_image = make_icon(is_active=TIMER_RUNNING)
    except Exception as e:
        print(f"Could not create emoji icon: {e}. Using fallback.")
        icon_image = Image.new("RGB", (64, 64), "black")
        draw = ImageDraw.Draw(icon_image)
        draw.rectangle((16, 16, 48, 48), fill="blue")
    menu = pystray.Menu(item('Dashboard', _show_settings, default=True), item('Quit', _on_quit))
    icon = pystray.Icon(APP_NAME, icon_image, APP_NAME, menu)
    TRAY_ICON = icon
    def _on_left_click(icon, item, status): _show_settings()
    if hasattr(icon, 'activator'): icon.activator = _on_left_click
    elif hasattr(icon, 'when_clicked'):
        def _when_clicked_adapter(icon, button, pressed):
            if not pressed and str(button).lower().find('left') != -1: _show_settings()
        icon.when_clicked = _when_clicked_adapter
    threading.Thread(target=icon.run, daemon=True).start()
    ROOT.after(1000, _update_tooltip)
    ROOT.after(100, periodic_data_update) 
    ROOT.mainloop()

# ─────────── Main ───────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    create_obs_html_file() # Create the HTML file once on startup
    threading.Thread(target=start_websocket_server, daemon=True).start()

    # Hotkey registration moved to setup_tray() to fix a threading bug.

    try:
        from gtts import gTTS
        from pydub import AudioSegment
        for fn, txt in (("start.mp3", "Timer started"), ("stop.mp3", "Timer stopped")):
            if not os.path.exists(fn):
                print(f"Creating dummy sound file: {fn}")
                tts = gTTS(txt)
                tmp = f"_{fn}"
                tts.save(tmp)
                AudioSegment.from_mp3(tmp).export(fn, format="mp3")
                os.remove(tmp)
    except Exception as e:
        print(f"Could not create audio files (gTTS/pydub needed): {e}")

    print(f"{APP_NAME} running. Press Alt+Shift+1 to toggle timer.")
    obs_file_path = os.path.abspath(OBS_OUTPUT_FILE)
    print("\n" + "="*50)
    print("🔴 OBS INTEGRATION IS ACTIVE")
    print("  (Now using reliable WebSockets!)")
    print(f"  1. Open OBS Studio.")
    print(f"  2. Add/check your 'Browser' source.")
    print(f"  3. Ensure 'Local file' points to:")
    print(f"     {obs_file_path}")
    print(f"  4. IMPORTANT: If it still misbehaves, right-click the source -> Properties -> click 'Refresh cache of current page'. You should only need to do this once.")
    print("="*50 + "\n")
    
    setup_tray()
