# main_tracker.py
import os, sys, json, sqlite3, threading, calendar
from datetime import datetime, timedelta, date
from collections import defaultdict
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

# Optional import for charting
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
OBS_OUTPUT_FILE = "obs_display.html"

TIMER_RUNNING = False
START_TIME    = None

ROOT            = None
SETTINGS_WINDOW = None
TRAY_ICON       = None

# ─────────── Database helpers ───────────────────────────────────────────
def init_db() -> None:
    with sqlite3.connect(DB_FILE) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time   TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL)''')
        # Rewards persistence
        con.execute('''CREATE TABLE IF NOT EXISTS rewards(
            box_id INTEGER PRIMARY KEY,
            text TEXT NOT NULL DEFAULT '',
            claim_count INTEGER NOT NULL DEFAULT 0,
            last_claimed TEXT
        )''')
        # Ensure 4 reward rows exist (robust against older schema)
        try:
            for i in range(1, 5):
                con.execute("INSERT OR IGNORE INTO rewards(box_id, text, claim_count, last_claimed) VALUES (?, '', 0, NULL)", (i,))
        except sqlite3.OperationalError:
            # Migrate old/mismatched rewards table to the correct schema
            try:
                con.execute("ALTER TABLE rewards RENAME TO rewards_backup")
            except sqlite3.OperationalError:
                con.execute("DROP TABLE IF EXISTS rewards")
            con.execute('''CREATE TABLE IF NOT EXISTS rewards(
                box_id INTEGER PRIMARY KEY,
                text TEXT NOT NULL DEFAULT '',
                claim_count INTEGER NOT NULL DEFAULT 0,
                last_claimed TEXT
            )''')
            for i in range(1, 5):
                con.execute("INSERT OR IGNORE INTO rewards(box_id, text, claim_count, last_claimed) VALUES (?, '', 0, NULL)", (i,))
            # Clean up backup table if it exists
            try:
                con.execute("DROP TABLE IF EXISTS rewards_backup")
            except sqlite3.OperationalError:
                pass

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

# Rewards helpers
def get_rewards() -> dict:
    with sqlite3.connect(DB_FILE) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM rewards ORDER BY box_id").fetchall()
        return {
            r['box_id']: {
                'text': r['text'],
                'claim_count': r['claim_count'],
                'last_claimed': r['last_claimed']
            } for r in rows
        }

def set_reward_text(box_id: int, text: str) -> None:
    with sqlite3.connect(DB_FILE) as con:
        con.execute("UPDATE rewards SET text=? WHERE box_id=?", (text, box_id))

def record_reward_claim(box_id: int) -> None:
    with sqlite3.connect(DB_FILE) as con:
        con.execute(
            "UPDATE rewards SET claim_count = claim_count + 1, last_claimed = ? WHERE box_id = ?",
            (datetime.now().isoformat(), box_id)
        )

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
def toggle_timer() -> None:
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

# ─────────── Statistics Calculation ──────────────────────────────────
def calc_stats() -> dict:
    rows = get_all_logs()
    if not rows:
        # Default empty values
        default_hms = "00:00:00"
        return {
            'total_hours': default_hms, 'today_hours': default_hms, 'weekly_hours': default_hms, 'monthly_hours': default_hms,
            'daily_avg': default_hms, 'weekly_avg': default_hms, 'monthly_avg': default_hms, 'avg_session_duration': default_hms,
            'current_streak': 0, 'longest_streak': 0, 'total_sec': 0, 'today_sec': 0, 'week_sec': 0, 'month_sec': 0,
            'total_study_days': 0, 'busiest_day': "N/A", 'consistency_percent': 0.0
        }

    total_sec = sum(r['duration_seconds'] for r in rows)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    today_sec = sum(r['duration_seconds'] for r in rows if datetime.fromisoformat(r['start_time']).date() == today)
    week_sec = sum(r['duration_seconds'] for r in rows if datetime.fromisoformat(r['start_time']).date() >= week_start)
    month_sec = sum(r['duration_seconds'] for r in rows if datetime.fromisoformat(r['start_time']).date() >= month_start)

    dates = sorted({datetime.fromisoformat(r['start_time']).date() for r in rows})
    weeks = {(d.year, d.isocalendar()[1]) for d in dates}
    months = {(d.year, d.month) for d in dates}

    daily_avg = total_sec / len(dates) if dates else 0
    weekly_avg = total_sec / len(weeks) if weeks else 0
    monthly_avg = total_sec / len(months) if months else 0

    # Streaks
    longest = 1 if dates else 0
    current = 0
    if dates:
        if dates[-1] == today or dates[-1] == today - timedelta(days=1):
            current = 1
            for i in range(len(dates) - 1, 0, -1):
                if dates[i] - dates[i - 1] == timedelta(days=1):
                    current += 1
                else:
                    break
        if dates[-1] != today and dates[-1] != today - timedelta(days=1):
            current = 0

        tmp = 1
        for i in range(len(dates) - 1):
            if dates[i+1] - dates[i] == timedelta(days=1):
                tmp += 1
            else:
                tmp = 1
            longest = max(longest, tmp)

    # Extra Stats Calculation
    total_study_days = len(dates)
    avg_session_duration = total_sec / len(rows) if rows else 0
    
    day_totals = defaultdict(int)
    for r in rows:
        day_name = datetime.fromisoformat(r['start_time']).strftime('%A')
        day_totals[day_name] += r['duration_seconds']
    busiest_day = max(day_totals, key=day_totals.get) if day_totals else "N/A"

    consistency_percent = 0.0
    if dates:
        days_since_start = (today - dates[0]).days + 1
        if days_since_start > 0:
            consistency_percent = (len(dates) / days_since_start) * 100

    return {
        'total_hours': hms(total_sec), 'today_hours': hms(today_sec), 'weekly_hours': hms(week_sec), 'monthly_hours': hms(month_sec),
        'daily_avg': hms(daily_avg), 'weekly_avg': hms(weekly_avg), 'monthly_avg': hms(monthly_avg),
        'total_sec': total_sec, 'today_sec': today_sec, 'week_sec': week_sec, 'month_sec': month_sec,
        'current_streak': current, 'longest_streak': longest,
        'total_study_days': total_study_days,
        'avg_session_duration': hms(avg_session_duration),
        'busiest_day': busiest_day,
        'consistency_percent': round(consistency_percent, 2)
    }

# ─────────── GUI (Dashboard Window) ──────────────────────────────────
class SettingsWindow(Toplevel):
    BG_COLOR = "#141518"
    CONTENT_BG = "#1C1E22"
    TEXT_COLOR = "#EAEAEA"
    ACCENT_COLOR = "#1E90FF"
    BTN_COLOR = "#2A2D33"
    BORDER_COLOR = "#3A3F46"
    HEATMAP_COLORS = ["#2a2a2a", "#196127", "#239a3b", "#7bc96f", "#c6e48b"]

    def __init__(self, master: Tk):
        super().__init__(master)
        self.title(f"{APP_NAME} Dashboard")
        self.geometry("1200x760")
        self.configure(bg=self.BG_COLOR)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.overrideredirect(True)
        self._offset_x = 0
        self._offset_y = 0
        self.is_fullscreen = False
        self._prev_geometry = None

        self.zoom_level = 0
        self._setup_fonts()

        self.heatmap_year = date.today().year
        self.chart_date = date.today()
        self.chart_mode = "Weekly"

        self._init_styles()
        self._init_layout()
        self._bind_events()

        self.show_frame("Stats")
        self.update_all_views()

    def _setup_fonts(self):
        self.f_title = font.Font(family="Helvetica", size=18 + self.zoom_level, weight="bold")
        self.f_h1 = font.Font(family="Helvetica", size=14 + self.zoom_level, weight="bold")
        self.f_head = font.Font(family="Helvetica", size=11 + self.zoom_level, weight="bold")
        self.f_body = font.Font(family="Helvetica", size=10 + self.zoom_level)
        self.f_small = font.Font(family="Helvetica", size=9 + self.zoom_level)
        self.f_stat_val = font.Font(family="Helvetica", size=22 + self.zoom_level, weight="bold")
        self.f_stat_label = font.Font(family="Helvetica", size=9 + self.zoom_level)

    def _init_styles(self):
        s = ttk.Style()
        s.theme_use('clam')
        # General widget styles
        s.configure("TButton", background=self.BTN_COLOR, foreground=self.TEXT_COLOR, borderwidth=0, focusthickness=3, focuscolor='none', font=self.f_head, padding=6)
        s.map("TButton", background=[('active', self.ACCENT_COLOR)])
        s.configure("Treeview.Heading", font=self.f_head, background=self.BTN_COLOR, foreground=self.TEXT_COLOR, relief="flat")
        s.map("Treeview.Heading", background=[('active', self.BTN_COLOR)])
        s.configure("Treeview", background=self.CONTENT_BG, foreground=self.TEXT_COLOR, fieldbackground=self.CONTENT_BG, font=self.f_body, rowheight=25 + self.zoom_level * 2, borderwidth=0)
        s.map('Treeview', background=[('selected', self.ACCENT_COLOR)])
        s.configure("TCombobox", fieldbackground=self.BTN_COLOR, background=self.BTN_COLOR, foreground=self.TEXT_COLOR, arrowcolor=self.TEXT_COLOR, selectbackground=self.BTN_COLOR, selectforeground=self.TEXT_COLOR, font=self.f_body)

        # Single-border LabelFrame (remove double borders)
        s.configure("Box.TLabelframe", background=self.CONTENT_BG, relief="solid", borderwidth=1)
        s.configure("Box.TLabelframe.Label", background=self.CONTENT_BG, foreground=self.TEXT_COLOR, font=self.f_h1, padding=(10, 5))

        # Accent progress bar for Rewards
        s.configure("Accent.Horizontal.TProgressbar",
                    troughcolor=self.CONTENT_BG,
                    bordercolor=self.CONTENT_BG,
                    background=self.ACCENT_COLOR,
                    lightcolor=self.ACCENT_COLOR,
                    darkcolor=self.ACCENT_COLOR)

    def _init_layout(self):
        title_bar = Frame(self, bg=self.BG_COLOR, relief='flat', bd=0)
        title_bar.pack(side=TOP, fill=X)
        lbl_title = Label(title_bar, text=f" {APP_NAME} Dashboard", bg=self.BG_COLOR, fg=self.TEXT_COLOR, font=self.f_head)
        lbl_title.pack(side=LEFT, padx=10, pady=6)

        # Fullscreen button
        btn_full = Button(title_bar, text='⛶', bg=self.BG_COLOR, fg=self.TEXT_COLOR, command=self.toggle_fullscreen, relief='flat', font=self.f_head)
        btn_full.pack(side=RIGHT, padx=5)
        btn_close = Button(title_bar, text='✕', bg=self.BG_COLOR, fg=self.TEXT_COLOR, command=self.withdraw, relief='flat', font=self.f_head)
        btn_close.pack(side=RIGHT, padx=5)
        
        title_bar.bind('<ButtonPress-1>', self.click_window)
        title_bar.bind('<B1-Motion>', self.drag_window)
        lbl_title.bind('<ButtonPress-1>', self.click_window)
        lbl_title.bind('<B1-Motion>', self.drag_window)

        main_frame = Frame(self, bg=self.BG_COLOR, padx=6, pady=6)
        main_frame.pack(fill=BOTH, expand=True)

        nav_frame = Frame(main_frame, bg=self.BG_COLOR, width=190, padx=5, pady=10)
        nav_frame.pack(side=LEFT, fill=Y)
        nav_frame.pack_propagate(False)

        self.content_frame = Frame(main_frame, bg=self.BG_COLOR)
        self.content_frame.pack(side=RIGHT, fill=BOTH, expand=True)

        # Added Rewards to navigation
        nav_items = [("Stats", "📈"), ("Rewards", "🏆"), ("Recent Logs", "📋"), ("Manual Log", "⏳"), ("Export / Import", "💾"), ("Instructions", "💡")]
        self.nav_buttons = {}
        for name, icon in nav_items:
            key = name.replace(" / ", "_").replace(" ", "_")
            btn = Button(nav_frame, text=f" {icon} {name}", font=self.f_head, bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat", anchor="w", padx=10, pady=10, command=lambda k=key: self.show_frame(k))
            btn.pack(fill=X, pady=3)
            self.nav_buttons[key] = btn

        self.frames = {}
        for name, _ in nav_items:
            key = name.replace(" / ", "_").replace(" ", "_")
            frame = Frame(self.content_frame, bg=self.BG_COLOR, padx=10, pady=10)
            frame.grid(row=0, column=0, sticky="nsew")
            self.frames[key] = frame

        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)

        self._make_stats_page()
        self._make_rewards_page()
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
        # Fullscreen shortcuts
        self.bind('<F11>', lambda e: self.toggle_fullscreen())
        self.bind('<Escape>', lambda e: self.exit_fullscreen())

    # ───────── Stats page with scroll + enlarged breakdown ─────────
    def _make_stats_page(self):
        base = self.frames["Stats"]

        # Scrollable container for stats (adds vertical scrolling)
        wrapper = Frame(base, bg=self.BG_COLOR)
        wrapper.pack(fill=BOTH, expand=True)
        self.stats_canvas = Canvas(wrapper, bg=self.BG_COLOR, highlightthickness=0)
        vscroll = ttk.Scrollbar(wrapper, orient='vertical', command=self.stats_canvas.yview)
        self.stats_canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side=RIGHT, fill=Y)
        self.stats_canvas.pack(side=LEFT, fill=BOTH, expand=True)

        inner = Frame(self.stats_canvas, bg=self.BG_COLOR)
        self.stats_canvas_window = self.stats_canvas.create_window((0, 0), window=inner, anchor='nw')

        def _on_inner_config(event=None):
            self.stats_canvas.configure(scrollregion=self.stats_canvas.bbox('all'))
            self.stats_canvas.itemconfigure(self.stats_canvas_window, width=self.stats_canvas.winfo_width())
        inner.bind('<Configure>', _on_inner_config)

        def _wheel(e):
            if e.delta:
                self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), 'units')
        self.stats_canvas.bind_all('<MouseWheel>', _wheel)

        f = inner
        f.columnconfigure(1, weight=1)

        # --- Top Row Frames ---
        top_left_frame = Frame(f, bg=self.BG_COLOR)
        top_left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        top_right_frame = Frame(f, bg=self.BG_COLOR)
        top_right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        # --- Bottom Row Frame (Spanning) ---
        bottom_frame = Frame(f, bg=self.BG_COLOR)
        bottom_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        bottom_frame.columnconfigure(0, weight=1)
        bottom_frame.grid_rowconfigure(1, weight=1, minsize=320)  # Ensure enlarged Study Breakdown

        # --- Box 1: Key Metrics ---
        lf_key = ttk.LabelFrame(top_left_frame, text="Key Metrics", style="Box.TLabelframe")
        lf_key.pack(fill=BOTH, expand=True, pady=(0, 5))
        self.lbl_total = Label(lf_key, font=self.f_stat_val, bg=self.CONTENT_BG, fg=self.TEXT_COLOR)
        self.lbl_total.pack(pady=(15,0))
        Label(lf_key, text="TOTAL TIME STUDIED", font=self.f_stat_label, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack()
        key_grid = Frame(lf_key, bg=self.CONTENT_BG)
        key_grid.pack(pady=10, fill=X, expand=True)
        key_grid.columnconfigure((0,1), weight=1)
        self.lbl_cur = Label(key_grid, font=self.f_stat_val, bg=self.CONTENT_BG, fg=self.TEXT_COLOR)
        self.lbl_cur.grid(row=0, column=0)
        Label(key_grid, text="CURRENT STREAK", font=self.f_stat_label, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).grid(row=1, column=0)
        self.lbl_long = Label(key_grid, font=self.f_stat_val, bg=self.CONTENT_BG, fg=self.TEXT_COLOR)
        self.lbl_long.grid(row=0, column=1)
        Label(key_grid, text="LONGEST STREAK", font=self.f_stat_label, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).grid(row=1, column=1)

        # --- Box 2: Deeper Insights ---
        lf_insights = ttk.LabelFrame(top_left_frame, text="Deeper Insights", style="Box.TLabelframe")
        lf_insights.pack(fill=BOTH, expand=True, pady=(5,0))
        insights_grid = Frame(lf_insights, bg=self.CONTENT_BG, padx=10, pady=10)
        insights_grid.pack(fill=BOTH, expand=True)
        insights_grid.columnconfigure(1, weight=1)
        insight_labels = ["Total Study Days:", "Avg Session:", "Busiest Day:", "Consistency:"]
        self.insight_vars = {}
        for i, label_text in enumerate(insight_labels):
            Label(insights_grid, text=label_text, font=self.f_body, bg=self.CONTENT_BG, fg=self.TEXT_COLOR, anchor='w').grid(row=i, column=0, sticky='w', pady=2)
            self.insight_vars[label_text] = Label(insights_grid, text="-", font=self.f_body, bg=self.CONTENT_BG, fg=self.TEXT_COLOR, anchor='e')
            self.insight_vars[label_text].grid(row=i, column=1, sticky='e', pady=2, padx=(10,0))
        
        # --- Box 3: Time Summary Table ---
        lf_summary = ttk.LabelFrame(top_right_frame, text="Time Summary", style="Box.TLabelframe")
        lf_summary.pack(fill=BOTH, expand=True)
        self.stats_tree = ttk.Treeview(lf_summary, columns=("Total", "Average"), show="tree headings", height=4)
        self.stats_tree.heading("#0", text="Timeframe")
        self.stats_tree.heading("Total", text="Total Time Logged")
        self.stats_tree.heading("Average", text="Daily Average")
        self.stats_tree.column("#0", width=180, anchor="w")
        self.stats_tree.column("Total", width=150, anchor="center")
        self.stats_tree.column("Average", width=150, anchor="center")
        self.stats_tree.pack(fill=BOTH, expand=True, padx=5, pady=5)
        
        # --- Box 4 & 5 (Bottom) ---
        self._make_heatmap(bottom_frame)
        if MATPLOTLIB_AVAILABLE: self._make_barchart(bottom_frame)
        else:
            Label(bottom_frame, text="Install 'matplotlib' to enable charts.", font=self.f_head, bg=self.CONTENT_BG, fg="orange").grid(row=1, column=0, sticky="nsew", pady=20)

    def _make_heatmap(self, parent):
        lf_heatmap = ttk.LabelFrame(parent, text="Contribution Heatmap", style="Box.TLabelframe")
        lf_heatmap.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        
        content = Frame(lf_heatmap, bg=self.CONTENT_BG, padx=5, pady=5)
        content.pack(fill=X, expand=True)

        nav = Frame(content, bg=self.CONTENT_BG)
        nav.pack(fill=X)
        Button(nav, text="⬅️", font=self.f_body, command=lambda: self._navigate_heatmap(-1), bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat").pack(side=LEFT)
        self.heatmap_year_label = Label(nav, text=str(self.heatmap_year), font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR)
        self.heatmap_year_label.pack(side=LEFT, padx=10)
        Button(nav, text="➡️", font=self.f_body, command=lambda: self._navigate_heatmap(1), bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat").pack(side=LEFT)
        Label(nav, text="Less", font=self.f_small, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack(side=RIGHT, padx=(10, 2))
        for color in self.HEATMAP_COLORS: Label(nav, text="■", font=self.f_body, bg=self.CONTENT_BG, fg=color).pack(side=RIGHT)
        Label(nav, text="More", font=self.f_small, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack(side=RIGHT, padx=(2, 0))
        
        # Single border canvas
        self.heatmap_canvas = Canvas(content, bg=self.CONTENT_BG, height=150, highlightthickness=0)
        self.heatmap_canvas.pack(fill=X, expand=True, pady=(5,0))

    def _make_barchart(self, parent):
        lf_barchart = ttk.LabelFrame(parent, text="Study Breakdown", style="Box.TLabelframe")
        lf_barchart.grid(row=1, column=0, sticky="nsew")
        
        content = Frame(lf_barchart, bg=self.CONTENT_BG, padx=5, pady=5)
        content.pack(fill=BOTH, expand=True)

        nav = Frame(content, bg=self.CONTENT_BG)
        nav.pack(fill=X, pady=(0, 5))
        Button(nav, text="⬅️", font=self.f_body, command=lambda: self._navigate_chart(-1), bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat").pack(side=LEFT)
        self.chart_period_label = Label(nav, text="", font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR, width=35)
        self.chart_period_label.pack(side=LEFT, padx=10, fill=X, expand=True)
        Button(nav, text="➡️", font=self.f_body, command=lambda: self._navigate_chart(1), bg=self.BTN_COLOR, fg=self.TEXT_COLOR, relief="flat").pack(side=LEFT)
        self.chart_mode_cb = ttk.Combobox(nav, values=["Weekly", "Monthly"], state="readonly", width=10, font=self.f_body)
        self.chart_mode_cb.set(self.chart_mode)
        self.chart_mode_cb.pack(side=RIGHT)
        self.chart_mode_cb.bind("<<ComboboxSelected>>", self._on_chart_mode_change)

        # Enlarged figure
        self.fig = Figure(figsize=(8.5, 4.2), dpi=100, facecolor=self.CONTENT_BG)
        self.ax = self.fig.add_subplot(111)
        self.chart_canvas = FigureCanvasTkAgg(self.fig, master=content)
        self.chart_canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self.fig.canvas.mpl_connect('button_press_event', self._on_chart_click)

    # ───────── Rewards Page ─────────
    def _make_rewards_page(self):
        f = self.frames["Rewards"]
        f.columnconfigure(0, weight=1)

        lf = ttk.LabelFrame(f, text="Rewards Center", style="Box.TLabelframe")
        lf.grid(row=0, column=0, sticky="nsew")
        container = Frame(lf, bg=self.CONTENT_BG, padx=10, pady=10)
        container.pack(fill=BOTH, expand=True)

        # Header stats
        header = Frame(container, bg=self.CONTENT_BG)
        header.pack(fill=X, pady=(0,10))
        self.lbl_coin_info = Label(header, text="🪙 Coins: 0", font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR)
        self.lbl_coin_info.pack(side=LEFT)
        self.lbl_streak_info = Label(header, text="🔥 Streak: 0", font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR)
        self.lbl_streak_info.pack(side=LEFT, padx=20)
        Label(header, text="(1 hour = 1 🪙)", font=self.f_small, bg=self.CONTENT_BG, fg="#9aa0a6").pack(side=LEFT, padx=10)

        # Grid for 4 reward boxes
        grid = Frame(container, bg=self.CONTENT_BG)
        grid.pack(fill=BOTH, expand=True)
        for c in range(2): grid.columnconfigure(c, weight=1)

        self.reward_boxes = {}  # box_id -> widgets dict
        reqs = {
            1: {"coins": 50, "streak": 0, "label": "Tier 1"},
            2: {"coins": 100, "streak": 0, "label": "Tier 2"},
            3: {"coins": 200, "streak": 0, "label": "Tier 3"},
            4: {"coins": 0, "streak": 14, "label": "14-Day Streak"}
        }

        def build_box(parent, box_id, row, col):
            box = ttk.LabelFrame(parent, text=f"{reqs[box_id]['label']}", style="Box.TLabelframe")
            box.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            parent.grid_rowconfigure(row, weight=1)
            parent.grid_columnconfigure(col, weight=1)

            inner = Frame(box, bg=self.CONTENT_BG, padx=8, pady=8)
            inner.pack(fill=BOTH, expand=True)

            # Requirement line
            req_text = ""
            if reqs[box_id]['coins'] > 0:
                req_text = f"Requires: {reqs[box_id]['coins']} 🪙"
            else:
                req_text = f"Requires: {reqs[box_id]['streak']} 🔥 streak"
            lbl_req = Label(inner, text=req_text, font=self.f_small, bg=self.CONTENT_BG, fg="#AEB4BA")
            lbl_req.pack(anchor='w')

            # Reward text (editable)
            entry_frame = Frame(inner, bg=self.CONTENT_BG)
            entry_frame.pack(fill=X, pady=(6, 4))
            ent = ttk.Entry(entry_frame, font=self.f_body)
            ent.pack(side=LEFT, fill=X, expand=True)
            ent.state(['readonly'])  # start read-only

            edit_btn = Button(entry_frame, text="✏️", bg=self.BTN_COLOR, fg=self.TEXT_COLOR, font=self.f_head, relief="flat",
                              command=lambda bid=box_id: self._toggle_edit_reward(bid))
            edit_btn.pack(side=LEFT, padx=(6,0))

            # Progress
            prog = ttk.Progressbar(inner, style="Accent.Horizontal.TProgressbar", orient='horizontal', mode='determinate', length=100)
            prog.pack(fill=X, pady=(4, 2))
            prog_lbl = Label(inner, text="", font=self.f_small, bg=self.CONTENT_BG, fg="#AEB4BA")
            prog_lbl.pack(anchor='w')

            # Action row
            action_row = Frame(inner, bg=self.CONTENT_BG)
            action_row.pack(fill=X, pady=(6, 0))
            claim_btn = Button(action_row, text="🎁 Claim", bg=self.ACCENT_COLOR, fg="white", font=self.f_head, relief="flat",
                               command=lambda bid=box_id: self._claim_reward(bid))
            claim_btn.pack(side=RIGHT)
            last_lbl = Label(action_row, text="", font=self.f_small, bg=self.CONTENT_BG, fg="#AEB4BA")
            last_lbl.pack(side=LEFT)

            # Lock overlay marker
            lock_lbl = Label(inner, text="🔒", font=self.f_h1, bg=self.CONTENT_BG, fg="#ffcc66")

            self.reward_boxes[box_id] = {
                "frame": box,
                "entry": ent,
                "edit_btn": edit_btn,
                "progress": prog,
                "progress_lbl": prog_lbl,
                "claim_btn": claim_btn,
                "lock_lbl": lock_lbl,
                "last_lbl": last_lbl
            }

        build_box(grid, 1, 0, 0)
        build_box(grid, 2, 0, 1)
        build_box(grid, 3, 1, 0)
        build_box(grid, 4, 1, 1)

    def _toggle_edit_reward(self, box_id: int):
        rb = self.reward_boxes[box_id]
        ent: ttk.Entry = rb["entry"]
        if 'readonly' in ent.state():
            ent.state(['!readonly'])
            rb["edit_btn"].config(text="💾")
            ent.focus_set()
            ent.icursor('end')
        else:
            text = ent.get().strip()
            set_reward_text(box_id, text)
            ent.state(['readonly'])
            rb["edit_btn"].config(text="✏️")
            messagebox.showinfo("Saved", "Reward updated.", parent=self)

    def _claim_reward(self, box_id: int):
        record_reward_claim(box_id)
        messagebox.showinfo("Claimed", "Reward marked as claimed! 🎉", parent=self)
        self.update_rewards_page()

    # ───────── Recent Logs ─────────
    def _make_recent_logs_page(self):
        f = self.frames["Recent_Logs"]
        lf = ttk.LabelFrame(f, text="Recent Activity", style="Box.TLabelframe")
        lf.pack(fill=BOTH, expand=True)

        cols = ("ID", "Date", "Start Time", "End Time", "Duration")
        self.log_tree = ttk.Treeview(lf, columns=cols, show="headings")
        for col in cols: self.log_tree.heading(col, text=col)
        self.log_tree.column("ID", width=50, anchor="center")
        self.log_tree.column("Date", width=120, anchor="center")
        self.log_tree.column("Start Time", width=120, anchor="center")
        self.log_tree.column("End Time", width=120, anchor="center")
        self.log_tree.column("Duration", width=120, anchor="center")
        self.log_tree.pack(side=TOP, fill=BOTH, expand=True, padx=5, pady=5)

        btn_frame = Frame(lf, bg=self.CONTENT_BG, pady=10)
        btn_frame.pack(fill=X)
        ttk.Button(btn_frame, text="Edit Selected Log", command=self._edit_log).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Delete Selected Log", command=self._delete_log, style="TButton").pack(side=LEFT, padx=5)

    def _make_manual_log_page(self):
        f = self.frames["Manual_Log"]
        lf = ttk.LabelFrame(f, text="Manual Time Entry", style="Box.TLabelframe")
        lf.pack(fill=BOTH, expand=True)
        content = Frame(lf, bg=self.CONTENT_BG)
        content.pack(fill=BOTH, expand=True, pady=20)
        
        Label(content, text="Select Date:", font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack()
        self.cal = DateEntry(content, width=12, background=self.ACCENT_COLOR, foreground=self.TEXT_COLOR, borderwidth=2, date_pattern="yyyy-mm-dd", font=self.f_body)
        self.cal.pack(pady=(5, 20))
        Label(content, text="Duration (Minutes):", font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack()
        self.spin = Spinbox(content, from_=0, to=1440, width=10, font=self.f_body)
        self.spin.pack(pady=(5, 25))
        bar = Frame(content, bg=self.CONTENT_BG); bar.pack()
        ttk.Button(bar, text="Add Time ➕", command=lambda: self._manual_op("add")).pack(side="left", padx=10)
        ttk.Button(bar, text="Deduct Time ➖", command=lambda: self._manual_op("deduct")).pack(side="left", padx=10)

    def _make_export_import_page(self):
        f = self.frames["Export_Import"]
        lf = ttk.LabelFrame(f, text="Data Management", style="Box.TLabelframe")
        lf.pack(fill=BOTH, expand=True)
        content = Frame(lf, bg=self.CONTENT_BG)
        content.pack(fill=BOTH, expand=True, pady=30)
        
        ttk.Button(content, text="Export Logs to JSON", command=self.export_json).pack(pady=10, ipadx=10, ipady=5)
        ttk.Button(content, text="Import Logs from JSON", command=self.import_json).pack(pady=10, ipadx=10, ipady=5)

    def _make_instructions_page(self):
        f = self.frames["Instructions"]
        lf = ttk.LabelFrame(f, text="How To Use", style="Box.TLabelframe")
        lf.pack(fill=BOTH, expand=True)
        
        info_text = scrolledtext.ScrolledText(lf, wrap="word", bg=self.CONTENT_BG, fg=self.TEXT_COLOR, relief="flat", font=self.f_body, borderwidth=0, padx=10)
        
        info = ("•  Hot-key (Alt + Shift + 1) to start or stop the study timer.\n\n"
                "•  The timer runs in the background. You can close this window.\n\n"
                "•  Left-click the book icon (📖) in your system tray to open this dashboard.\n\n"
                "•  Zoom: Ctrl + Mouse Wheel, Ctrl & +/-, Ctrl & 0 to reset.\n\n"
                "•  F11 toggles fullscreen. Esc exits fullscreen.\n\n"
                "•  Stats, charts, and logs update automatically.\n\n"
                "•  You can manually add or remove time, and manage individual logs.\n\n"
                "•  Rewards: 1 hour = 1 🪙. Edit reward boxes, unlock with coins or streak, and claim.\n\n"
                "•  To make the app run on startup, place a shortcut in the Windows startup folder (Win+R → `shell:startup`).\n\n"
                "Check out my blog: ")
        
        info_text.insert("1.0", info)
        
        blog_url = "https://thekingofweirdtimes.blogspot.com"
        link_start = info_text.index("end-1c")
        info_text.insert("end", blog_url)
        link_end = info_text.index("end-1c")
        
        info_text.tag_add("link", link_start, link_end)
        info_text.tag_config("link", foreground="cyan", underline=True)
        info_text.tag_bind("link", "<Button-1>", lambda e, url=blog_url: webbrowser.open(url))
        info_text.tag_bind("link", "<Enter>", lambda e: info_text.config(cursor="hand2"))
        info_text.tag_bind("link", "<Leave>", lambda e: info_text.config(cursor=""))

        info_text.config(state="disabled")
        info_text.pack(fill=BOTH, expand=True, padx=10, pady=10)
        self.instr_text = info_text

    def update_all_views(self):
        if not self.winfo_exists(): return
        self.update_stats_page()
        self.update_rewards_page()
        self.update_recent_logs_page()

    def update_stats_page(self):
        s = calc_stats()
        self.lbl_cur.config(text=f"{s['current_streak']} 🔥")
        self.lbl_long.config(text=f"{s['longest_streak']}")
        self.lbl_total.config(text=f"{s['total_hours']}")

        self.insight_vars["Total Study Days:"].config(text=str(s['total_study_days']))
        self.insight_vars["Avg Session:"].config(text=s['avg_session_duration'])
        self.insight_vars["Busiest Day:"].config(text=s['busiest_day'])
        self.insight_vars["Consistency:"].config(text=f"{s['consistency_percent']}%")

        for i in self.stats_tree.get_children(): self.stats_tree.delete(i)
        today_str = f"Today ({date.today():%b %d})"
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

        first_day_weekday = date(self.heatmap_year, 1, 1).weekday()
        box_size, gap = 17, 3
        days_in_year = 366 if calendar.isleap(self.heatmap_year) else 365
        for day_of_year in range(1, days_in_year + 1):
            current_date = date(self.heatmap_year, 1, 1) + timedelta(days=day_of_year - 1)
            seconds = data.get(current_date, 0)
            color = get_color(seconds)
            day_index = first_day_weekday + day_of_year - 1
            col = day_index // 7
            row = day_index % 7
            x1, y1 = col * (box_size + gap) + gap, row * (box_size + gap) + gap
            x2, y2 = x1 + box_size, y1 + box_size
            self.heatmap_canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline=self.CONTENT_BG)

    def _update_barchart(self):
        self.ax.clear()
        self.ax.set_facecolor(self.CONTENT_BG)
        self.ax.tick_params(colors=self.TEXT_COLOR, which='both', labelsize=self.f_small.cget('size'))
        for spine in self.ax.spines.values(): spine.set_edgecolor(self.TEXT_COLOR)
        self.ax.grid(axis='y', linestyle='--', color='#5a5f66', alpha=0.3)

        if self.chart_mode == "Weekly": self._plot_weekly_data()
        else: self._plot_monthly_data()

        self.fig.tight_layout(pad=2.0)
        self.chart_canvas.draw()

    def _plot_weekly_data(self):
        start_of_week = self.chart_date - timedelta(days=self.chart_date.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        self.chart_period_label.config(text=f"{start_of_week:%b %d, %Y} - {end_of_week:%b %d, %Y}")

        dates = [start_of_week + timedelta(days=i) for i in range(7)]
        logs = get_all_logs()
        data = {d: sum(log['duration_seconds'] for log in logs if datetime.fromisoformat(log['start_time']).date() == d) for d in dates}

        labels = [d.strftime("%a") for d in dates]
        values = [v / 3600 for v in data.values()]
        bars = self.ax.bar(labels, values, color=self.ACCENT_COLOR)
        self.ax.set_title("Weekly Study Time", color=self.TEXT_COLOR, fontdict={'size': self.f_head.cget('size')})
        self.ax.set_ylabel("Hours", color=self.TEXT_COLOR, fontdict={'size': self.f_body.cget('size')})
        self.ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{y:.1f}'))

        for b, v in zip(bars, values):
            self.ax.annotate(f"{v:.1f}", xy=(b.get_x()+b.get_width()/2, b.get_height()),
                             xytext=(0, 5), textcoords="offset points", ha='center', va='bottom', color=self.TEXT_COLOR, fontsize=self.f_small.cget('size'))

    def _plot_monthly_data(self):
        month_start = self.chart_date.replace(day=1)
        self.chart_period_label.config(text=f"{month_start:%B %Y}")
        logs = get_all_logs()
        d = month_start
        while d.weekday() != 0: d -= timedelta(days=1)
        week_starts = []
        while d.year < month_start.year or (d.year == month_start.year and d.month <= month_start.month):
             week_starts.append(d)
             d += timedelta(days=7)
        
        weekly_totals = {ws: sum(l['duration_seconds'] for l in logs if (datetime.fromisoformat(l['start_time']).date() - timedelta(days=datetime.fromisoformat(l['start_time']).date().weekday())) == ws and datetime.fromisoformat(l['start_time']).date().month == month_start.month) for ws in week_starts}

        labels = [f"W {i+1}\n({ws:%b %d})" for i, ws in enumerate(weekly_totals)]
        values = [v / 3600 for v in weekly_totals.values()]
        bars = self.ax.bar(labels, values, color=self.ACCENT_COLOR)
        self.ax.set_title("Monthly Study Time", color=self.TEXT_COLOR, fontdict={'size': self.f_head.cget('size')})
        self.ax.set_ylabel("Hours per Week", color=self.TEXT_COLOR, fontdict={'size': self.f_body.cget('size')})
        self.ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{y:.1f}'))
        for b, v in zip(bars, values):
            self.ax.annotate(f"{v:.1f}", xy=(b.get_x()+b.get_width()/2, b.get_height()),
                             xytext=(0, 5), textcoords="offset points", ha='center', va='bottom', color=self.TEXT_COLOR, fontsize=self.f_small.cget('size'))
        self.monthly_chart_week_starts = list(weekly_totals.keys())

    # Rewards page updater
    def update_rewards_page(self):
        # Coins calculation: 1 hour = 1 coin (include current session time)
        stats = calc_stats()
        current_session_sec = (datetime.now() - START_TIME).total_seconds() if TIMER_RUNNING and START_TIME else 0
        total_sec_with_session = stats.get('total_sec', 0) + int(current_session_sec)
        coins = total_sec_with_session // 3600
        streak = stats.get('current_streak', 0)

        self.lbl_coin_info.config(text=f"🪙 Coins: {coins}")
        self.lbl_streak_info.config(text=f"🔥 Streak: {streak}")

        rewards = get_rewards()
        reqs = {
            1: {"coins": 50, "streak": 0},
            2: {"coins": 100, "streak": 0},
            3: {"coins": 200, "streak": 0},
            4: {"coins": 0, "streak": 14}
        }

        for box_id, widgets in self.reward_boxes.items():
            rdata = rewards.get(box_id, {'text': '', 'claim_count': 0, 'last_claimed': None})
            ent: ttk.Entry = widgets["entry"]

            # populate entry (keep current editing state)
            if 'readonly' in ent.state():
                ent.state(['!readonly'])
                ent.delete(0, 'end')
                ent.insert(0, rdata['text'])
                ent.state(['readonly'])

            # lock logic
            need_coins = reqs[box_id]["coins"]
            need_streak = reqs[box_id]["streak"]
            unlocked = (coins >= need_coins) and (streak >= need_streak)

            # progress values and text
            if need_coins > 0:
                cur = min(coins, need_coins)
                maxv = max(need_coins, 1)
                widgets["progress"]["maximum"] = maxv
                widgets["progress"]["value"] = cur
                widgets["progress_lbl"].config(text=f"Progress: {cur}/{need_coins} 🪙")
            else:
                cur = min(streak, need_streak)
                maxv = max(need_streak, 1)
                widgets["progress"]["maximum"] = maxv
                widgets["progress"]["value"] = cur
                widgets["progress_lbl"].config(text=f"Progress: {cur}/{need_streak} 🔥")

            # last claimed info
            last_text = f"Claims: {rdata.get('claim_count', 0)}"
            if rdata.get('last_claimed'):
                try:
                    dt = datetime.fromisoformat(rdata['last_claimed'])
                    last_text += f" • Last: {dt.strftime('%Y-%m-%d %H:%M')}"
                except Exception:
                    pass
            widgets["last_lbl"].config(text=last_text)

            # lock indicator and button states
            if unlocked:
                try: widgets["lock_lbl"].pack_forget()
                except Exception: pass
                widgets["claim_btn"].config(state="normal", bg=self.ACCENT_COLOR, fg="white")
            else:
                try:
                    widgets["lock_lbl"].pack_forget()
                except Exception:
                    pass
                widgets["lock_lbl"].pack(anchor='ne')
                widgets["claim_btn"].config(state="disabled", bg=self.BTN_COLOR, fg="#888888")

    def update_recent_logs_page(self):
        for i in self.log_tree.get_children(): self.log_tree.delete(i)
        for log in get_all_logs(limit=100):
            start = datetime.fromisoformat(log['start_time'])
            end = datetime.fromisoformat(log['end_time'])
            self.log_tree.insert('', 'end', values=(log['id'], start.strftime('%Y-%m-%d'), start.strftime('%H:%M:%S'), end.strftime('%H:%M:%S'), hms(log['duration_seconds'])))

    def click_window(self, event):
        self._offset_x, self._offset_y = event.x, event.y

    def drag_window(self, event):
        if not self.is_fullscreen:
            self.geometry(f'+{self.winfo_pointerx() - self._offset_x}+{self.winfo_pointery() - self._offset_y}')

    def toggle_fullscreen(self):
        self.is_fullscreen = not self.is_fullscreen
        if self.is_fullscreen:
            self._prev_geometry = self.geometry()
            try:
                self.attributes('-fullscreen', True)
            except Exception:
                self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        else:
            self.exit_fullscreen()

    def exit_fullscreen(self):
        if self.is_fullscreen:
            try:
                self.attributes('-fullscreen', False)
            except Exception:
                pass
            if self._prev_geometry:
                self.geometry(self._prev_geometry)
        self.is_fullscreen = False

    def show_frame(self, name: str):
        for key, btn in self.nav_buttons.items():
            btn.config(bg=self.ACCENT_COLOR if key == name else self.BTN_COLOR)
        self.frames[name].tkraise()
        if name == "Stats": self.update_stats_page()
        if name == "Recent_Logs": self.update_recent_logs_page()
        if name == "Rewards": self.update_rewards_page()

    def _navigate_heatmap(self, direction: int):
        self.heatmap_year += direction
        self._update_heatmap()

    def _navigate_chart(self, direction: int):
        if self.chart_mode == "Weekly":
            self.chart_date += timedelta(days=7 * direction)
        else:
            new_month, new_year = self.chart_date.month + direction, self.chart_date.year
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
        if not selected_item: return messagebox.showwarning("No Selection", "Please select a log to delete.", parent=self)
        log_id = self.log_tree.item(selected_item)['values'][0]
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete log ID {log_id}?", parent=self):
            with sqlite3.connect(DB_FILE) as con: con.execute("DELETE FROM logs WHERE id=?", (log_id,))
            self.update_all_views()

    def _edit_log(self):
        selected_item = self.log_tree.focus()
        if not selected_item: return messagebox.showwarning("No Selection", "Please select a log to edit.", parent=self)
        log_id = self.log_tree.item(selected_item)['values'][0]
        dlg = Toplevel(self); dlg.title(f"Edit Log {log_id}"); dlg.geometry("300x150"); dlg.configure(bg=self.CONTENT_BG); dlg.transient(self); dlg.grab_set()
        Label(dlg, text="Minutes to Add/Deduct:", font=self.f_head, bg=self.CONTENT_BG, fg=self.TEXT_COLOR).pack(pady=10)
        spin = Spinbox(dlg, from_=-1440, to=1440, width=10, font=self.f_body); spin.pack()
        def apply_change():
            try:
                secs_to_change = int(spin.get()) * 60
                with sqlite3.connect(DB_FILE) as con:
                    cur = con.cursor()
                    log = cur.execute("SELECT id, start_time, end_time, duration_seconds FROM logs WHERE id=?", (log_id,)).fetchone()
                    if not log: return
                    new_duration = log[3] + secs_to_change
                    if new_duration <= 0: cur.execute("DELETE FROM logs WHERE id=?", (log_id,))
                    else:
                        new_end = (datetime.fromisoformat(log[1]) + timedelta(seconds=new_duration)).isoformat()
                        cur.execute("UPDATE logs SET end_time=?, duration_seconds=? WHERE id=?", (new_end, new_duration, log_id))
                self.update_all_views(); dlg.destroy()
            except ValueError: messagebox.showerror("Invalid Input", "Please enter a valid integer.", parent=dlg)
        ttk.Button(dlg, text="Apply Changes", command=apply_change).pack(pady=15)

    def _manual_op(self, mode: str):
        try:
            mins = int(self.spin.get()); sec = mins * 60
            if mins <= 0: raise ValueError
        except ValueError: return messagebox.showwarning("Invalid Input", "Enter a positive integer.", parent=self)
        target_date = self.cal.get_date()
        if mode == "add": add_log(datetime.combine(target_date, datetime.min.time()), datetime.combine(target_date, datetime.min.time()) + timedelta(seconds=sec), sec)
        else: # deduct
            logs = [l for l in get_all_logs() if datetime.fromisoformat(l['start_time']).date() == target_date]
            if not logs: return messagebox.showinfo("Info", f"No sessions on {target_date}.", parent=self)
            rem = sec
            with sqlite3.connect(DB_FILE) as con:
                for l in sorted(logs, key=lambda r: r['start_time'], reverse=True):
                    if rem <= 0: break
                    if rem >= l['duration_seconds']: con.execute("DELETE FROM logs WHERE id=?", (l['id'],)); rem -= l['duration_seconds']
                    else:
                        new_dur = l['duration_seconds'] - rem
                        new_end = (datetime.fromisoformat(l['start_time']) + timedelta(seconds=new_dur)).isoformat()
                        con.execute("UPDATE logs SET duration_seconds=?, end_time=? WHERE id=?", (new_dur, new_end, l['id'])); rem = 0
        self.update_all_views(); self.spin.delete(0, 'end'); self.spin.insert(0, '0')

    def export_json(self):
        data = [dict(r) for r in get_all_logs()]
        if not data: return messagebox.showinfo("Export", "No data to export.", parent=self)
        fname = filedialog.asksaveasfilename(parent=self, title="Save Export File", defaultextension=".json", filetypes=[("JSON files", "*.json")], initialfile=f"study_logs_{datetime.now():%Y%m%d}.json")
        if fname:
            try:
                with open(fname, 'w', encoding='utf-8') as fp: json.dump(data, fp, indent=4)
                messagebox.showinfo("Export Successful", f"Data saved to {os.path.basename(fname)}", parent=self)
            except Exception as e: messagebox.showerror("Export Error", str(e), parent=self)

    def import_json(self):
        path = filedialog.askopenfilename(parent=self, title="Choose JSON file", filetypes=[("JSON files", "*.json")])
        if not path or not messagebox.askyesno("Confirm Import", "This will add sessions from the file to your current logs. Continue?", parent=self): return
        try:
            with open(path, 'r', encoding='utf-8') as fp: recs = json.load(fp)
            added_count = 0
            with sqlite3.connect(DB_FILE) as con:
                for r in recs:
                    if all(k in r for k in ("start_time", "end_time", "duration_seconds")):
                        try: con.execute("INSERT INTO logs(start_time, end_time, duration_seconds) VALUES(?,?,?)", (r["start_time"], r["end_time"], int(r["duration_seconds"]))); added_count += 1
                        except sqlite3.Error: pass
            self.update_all_views()
            messagebox.showinfo("Import Complete", f"Successfully imported {added_count} log entries.", parent=self)
        except Exception as e: messagebox.showerror("Import Error", str(e), parent=self)

    def _handle_scroll_zoom(self, event):
        if event.delta > 0: self.zoom_in()
        else: self.zoom_out()

    def zoom_in(self, event=None):
        if self.zoom_level < 5: self.zoom_level += 1; self._apply_zoom()

    def zoom_out(self, event=None):
        if self.zoom_level > -5: self.zoom_level -= 1; self._apply_zoom()

    def zoom_reset(self, event=None):
        self.zoom_level = 0; self._apply_zoom()

    def _apply_zoom(self):
        self._setup_fonts(); self._init_styles()
        for w in self.winfo_children(): self._update_widget_fonts(w)
        self.update_all_views()

    def _update_widget_fonts(self, parent_widget):
        # This is a basic recursive font update, may need refinement for complex widgets
        for w in parent_widget.winfo_children():
            try:
                if 'font' in w.keys():
                    if 'heading' in str(w.cget('style')).lower() or 'title' in str(w.cget('font')).lower(): w.config(font=self.f_h1)
                    elif 'button' in str(w.winfo_class()).lower(): w.config(font=self.f_head)
                    else: w.config(font=self.f_body)
            except Exception: pass
            if w.winfo_children(): self._update_widget_fonts(w)

# ─────────── OBS Overlay ──────────────────────────────────────────
def update_obs_output():
    stats = calc_stats()
    current_session_sec = (datetime.now() - START_TIME).total_seconds() if TIMER_RUNNING and START_TIME else 0
    today_total_sec = stats.get('today_sec', 0) + current_session_sec
    timer_str, today_total_str, streak_str = hms(current_session_sec), hms(today_total_sec), str(stats.get('current_streak', 0))

    # FIX: Replaced JavaScript reload with a meta refresh tag, which is often more stable in OBS.
    html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="1">
    <title>Study Tracker OBS</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap');
        body {{
            background-color: transparent; font-family: 'Inter', sans-serif;
            color: white; margin: 0; padding: 20px; text-shadow: 0 0 8px rgba(0,0,0,0.7);
        }}
        .container {{ display: flex; flex-direction: column; align-items: flex-start; gap: 12px; }}
        .stat-block {{
            background-color: rgba(20, 20, 20, 0.45); border-radius: 14px;
            padding: 8px 18px; min-width: 230px;
            border: 1px solid rgba(255, 255, 255, 0.1); backdrop-filter: blur(10px);
        }}
        .value {{ font-size: 34px; font-weight: 700; line-height: 1.1; letter-spacing: -1px; }}
        .label {{ font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.85; }}
        .timer-active .value {{ color: #A7F3D0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="stat-block {'timer-active' if TIMER_RUNNING else ''}">
            <div class="value">{timer_str}</div><div class="label">Session</div>
        </div>
        <div class="stat-block">
            <div class="value">{today_total_str}</div><div class="label">Today's Total</div>
        </div>
        <div class="stat-block">
            <div class="value">{streak_str} 🔥</div><div class="label">Current Streak</div>
        </div>
    </div>
</body>
</html>
"""
    try:
        with open(OBS_OUTPUT_FILE, 'w', encoding='utf-8') as f: f.write(html_content.strip())
    except Exception as e: print(f"[OBS Output Error] Could not write to {OBS_OUTPUT_FILE}: {e}")
    if ROOT and ROOT.winfo_exists(): ROOT.after(1000, update_obs_output)

# ─────────── Tray Icon Setup ────────────────────────────────────────────
def update_tray_icon():
    if TRAY_ICON: TRAY_ICON.icon = make_icon(is_active=TIMER_RUNNING)

def make_icon(is_active: bool = False):
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    try: font_obj = ImageFont.truetype("seguiemj.ttf", 50)
    except IOError: font_obj = ImageFont.load_default()
    draw.text((5, 0), "📖", font=font_obj, fill="white")
    if is_active:
        # FIX: Made the red dot indicator larger for better visibility
        draw.ellipse((46, 3, 62, 19), fill='red', outline='white')
    return image

def _show_settings(_icon=None, _item=None):
    global SETTINGS_WINDOW
    if not SETTINGS_WINDOW or not SETTINGS_WINDOW.winfo_exists(): SETTINGS_WINDOW = SettingsWindow(ROOT)
    SETTINGS_WINDOW.deiconify(); SETTINGS_WINDOW.lift(); SETTINGS_WINDOW.focus_force(); SETTINGS_WINDOW.update_all_views()

def _on_quit(icon, _item):
    if TIMER_RUNNING: toggle_timer()
    icon.stop(); ROOT.quit()

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
    ROOT = Tk(); ROOT.withdraw()
    icon_image = make_icon(is_active=TIMER_RUNNING)
    menu = pystray.Menu(item('Dashboard', _show_settings, default=True), item('Quit', _on_quit))
    icon = pystray.Icon(APP_NAME, icon_image, APP_NAME, menu)
    TRAY_ICON = icon
    threading.Thread(target=icon.run, daemon=True).start()
    ROOT.after(1000, _update_tooltip)
    ROOT.after(100, update_obs_output) 
    ROOT.mainloop()

# ─────────── Main Execution ──────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    try: keyboard.add_hotkey("alt+shift+1", toggle_timer, suppress=False)
    except Exception as e: print(f"Could not set hotkey (requires admin/root privileges): {e}")

    try:
        from gtts import gTTS
        from pydub import AudioSegment
        for fn, txt in (("start.mp3", "Timer started"), ("stop.mp3", "Timer stopped")):
            if not os.path.exists(fn):
                print(f"Creating dummy sound file: {fn}")
                tts = gTTS(txt); tmp = f"_{fn}"; tts.save(tmp)
                AudioSegment.from_mp3(tmp).export(fn, format="mp3"); os.remove(tmp)
    except Exception as e: print(f"Could not create audio files (gTTS/pydub needed): {e}")

    print(f"{APP_NAME} running. Press Alt+Shift+1 to toggle timer.")
    
    obs_file_path = os.path.abspath(OBS_OUTPUT_FILE)
    print("\n" + "="*50)
    print("🔴 OBS INTEGRATION IS ACTIVE")
    print("  1. In OBS, add a new 'Browser' source to your scene.")
    print("  2. Check the 'Local file' box.")
    print("  3. Browse and select this file:")
    print(f"     {obs_file_path}")
    print("  4. Set Width and Height (e.g., 300x300).")
    print("  5. To refresh, you can toggle the source's visibility.")
    print("="*50 + "\n")
    
    setup_tray()
