# main_tracker.py
import os, sys, json, sqlite3, threading
from datetime import datetime, timedelta, date
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item
from tkinter import (
    Tk, Toplevel, Frame, Label, Button, Spinbox, messagebox,
    scrolledtext, font, ttk, filedialog
)
from tkcalendar import DateEntry
import keyboard
from playsound import playsound

# ─────────── Globals ────────────────────────────────────────────────────
APP_NAME = "StudyTracker"
DB_FILE  = "study_tracker.db"

TIMER_RUNNING = False
START_TIME    = None

ROOT            = None          # hidden Tk root
SETTINGS_WINDOW = None          # single settings window instance
TRAY_ICON       = None          # pystray.Icon instance


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
        con.execute("INSERT INTO logs(start_time,end_time,duration_seconds)"
                    " VALUES(?,?,?)",
                    (start.isoformat(), end.isoformat(), seconds))


def get_all_logs() -> list[sqlite3.Row]:
    with sqlite3.connect(DB_FILE) as con:
        con.row_factory = sqlite3.Row
        return con.execute("SELECT * FROM logs ORDER BY start_time DESC").fetchall()


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
            except Exception as e: print("[sound]", e)
    threading.Thread(target=_run, daemon=True).start()


# ─────────── Hot-key handler ────────────────────────────────────────────
def toggle_timer() -> None:
    global TIMER_RUNNING, START_TIME
    TIMER_RUNNING = not TIMER_RUNNING

    # Stats refresh (if panel visible)
    if SETTINGS_WINDOW and SETTINGS_WINDOW.winfo_exists():
        ROOT.after(0, SETTINGS_WINDOW.update_stats)

    if TIMER_RUNNING:
        START_TIME = datetime.now()
        play_sound("start.mp3")
    else:
        if START_TIME:
            end = datetime.now()
            add_log(START_TIME, end, int((end - START_TIME).total_seconds()))
            START_TIME = None
            play_sound("stop.mp3")


# ─────────── Statistics ────────────────────────────────────────────────
def calc_stats() -> dict:
    rows = get_all_logs()
    if not rows:
        zero = "00:00:00"
        return dict(total_hours=zero, today_hours=zero, weekly_hours=zero,
                    monthly_hours=zero, daily_avg=zero, weekly_avg=zero,
                    monthly_avg=zero, current_streak=0, longest_streak=0)

    total_sec = sum(r['duration_seconds'] for r in rows)
    today      = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start= today.replace(day=1)

    def filt(pred):
        return sum(r['duration_seconds'] for r in rows if pred(r))

    today_sec  = filt(lambda r: datetime.fromisoformat(r['start_time']).date()==today)
    week_sec   = filt(lambda r: datetime.fromisoformat(r['start_time']).date()>=week_start)
    month_sec  = filt(lambda r: datetime.fromisoformat(r['start_time']).date()>=month_start)

    dates   = {datetime.fromisoformat(r['start_time']).date() for r in rows}
    weeks   = {(d.year, d.isocalendar()[1]) for d in dates}
    months  = {(d.year, d.month) for d in dates}

    daily_avg   = total_sec / len(dates)
    weekly_avg  = total_sec / len(weeks)
    monthly_avg = total_sec / len(months)

    # streaks
    seq = sorted(dates)
    longest = 1 if seq else 0
    current = 0
    if seq and seq[-1] in {today, today - timedelta(days=1)}:
        current = 1
        for i in range(len(seq)-1, 0, -1):
            if seq[i]-seq[i-1]==timedelta(days=1):
                current += 1
            else: break
    tmp = 1
    for i in range(len(seq)-1):
        if seq[i+1]-seq[i]==timedelta(days=1):
            tmp += 1; longest=max(longest,tmp)
        else: tmp=1

    return dict(
        total_hours=hms(total_sec),
        today_hours=hms(today_sec), weekly_hours=hms(week_sec),
        monthly_hours=hms(month_sec),
        daily_avg=hms(daily_avg), weekly_avg=hms(weekly_avg),
        monthly_avg=hms(monthly_avg),
        current_streak=current, longest_streak=longest
    )


# ─────────── GUI  (Settings window) ─────────────────────────────────────
class SettingsWindow(Toplevel):
    def __init__(self, master: Tk):
        super().__init__(master)
        self.title(f"{APP_NAME} Settings")
        self.geometry("700x560")
        self.configure(bg="#2E2E2E")
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.transient(master)

        # fonts
        f_title = font.Font(family="Helvetica", size=16, weight="bold")
        f_head  = font.Font(family="Helvetica", size=10, weight="bold")
        f_body  = font.Font(family="Helvetica", size=10)

        main = Frame(self, bg="#2E2E2E", padx=15, pady=15)
        main.pack(fill="both", expand=True)

        # navigation buttons
        nav = Frame(main, bg="#2E2E2E"); nav.pack(fill="x", pady=(0,15))
        btns = [("Stats","📈"),("Export Stats","📩"),
                ("Manual Log","⏳"),("Notification Sound","🔊"),
                ("Instructions","💟")]
        self.frames={}
        for i,(txt,ico) in enumerate(btns):
            Button(nav, text=f"{txt}\n{ico}",
                   font=f_head, bg="#4A4A4A", fg="white",
                   relief="flat", width=13, height=3,
                   command=lambda n=txt.replace(" ","_"): self.show_frame(n)
            ).grid(row=0,column=i,padx=5)

        cont=Frame(main,bg="#3C3C3C",relief="sunken",borderwidth=1)
        cont.pack(fill="both",expand=True)
        cont.grid_rowconfigure(0,weight=1); cont.grid_columnconfigure(0,weight=1)
        for nm in ("Stats","Export_Stats","Manual_Log","Notification_Sound","Instructions"):
            fr=Frame(cont,bg="#3C3C3C"); fr.grid(row=0,column=0,sticky="nsew"); self.frames[nm]=fr

        self._make_stats(f_title,f_head)
        self._make_export(f_title,f_head)
        self._make_manual(f_title,f_head,f_body)
        self._make_sound(f_title,f_body,f_head)
        self._make_instr(f_title,f_body)

        self.show_frame("Stats")

    # ── Stats tab
    def _make_stats(self, f_title, f_head):
        f=self.frames["Stats"]; f.configure(padx=20,pady=20)
        top=Frame(f,bg="#3C3C3C"); top.pack(fill="x",pady=(0,20))
        self.lbl_cur=Label(top,font=f_title,bg="#3C3C3C",fg="white"); self.lbl_cur.pack(side="left",padx=(0,50))
        self.lbl_long=Label(top,font=f_title,bg="#3C3C3C",fg="white"); self.lbl_long.pack(side="left")

        ttk.Style().configure("Treeview.Heading",font=f_head,background="#4A4A4A",foreground="white")
        self.tree=ttk.Treeview(f,columns=("Total","Average"),show="tree headings",height=4)
        self.tree.heading("#0",text="Timeframe")
        self.tree.heading("Total",text="Period Total (HH:MM:SS)")
        self.tree.heading("Average",text="Overall Average (HH:MM:SS)")
        self.tree.column("#0",width=200,anchor="w"); self.tree.column("Total",width=200,anchor="center"); self.tree.column("Average",width=200,anchor="center")
        self.tree.pack(fill="x",pady=(0,20))

        self.lbl_total=Label(f,font=f_title,bg="#3C3C3C",fg="white"); self.lbl_total.pack()

    def update_stats(self):
        s=calc_stats()
        self.lbl_cur.config(text=f"Current Streak 🔥: {s['current_streak']}")
        self.lbl_long.config(text=f"Longest Streak: {s['longest_streak']}")
        self.lbl_total.config(text=f"Total Time Studied: {s['total_hours']}")
        for i in self.tree.get_children(): self.tree.delete(i)
        t=date.today().strftime("%B %d, %Y")
        self.tree.insert('', 'end', text=f" {t}", values=(s['today_hours'],s['daily_avg']))
        self.tree.insert('', 'end', text=" This Week", values=(s['weekly_hours'],s['weekly_avg']))
        self.tree.insert('', 'end', text=" This Month",values=(s['monthly_hours'],s['monthly_avg']))

    # ── Export / Import
    def _make_export(self,f_title,f_head):
        f=self.frames["Export_Stats"]; f.configure(padx=20,pady=20)
        Label(f,text="Export / Import Logs",font=f_title,bg="#3C3C3C",fg="white").pack(pady=(20,10))
        Button(f,text="Export to JSON",font=f_head,bg="#1E90FF",fg="white",relief="flat",padx=15,pady=10,command=self.export_json).pack(pady=5)
        Button(f,text="Import from JSON",font=f_head,bg="#6f42c1",fg="white",relief="flat",padx=15,pady=10,command=self.import_json).pack(pady=5)

    def export_json(self):
        data=[dict(r) for r in get_all_logs()]
        if not data: messagebox.showinfo("Export","No data to export.",parent=self); return
        fname=f"study_logs_{datetime.now():%Y%m%d_%H%M%S}.json"
        try:
            with open(fname,'w',encoding='utf-8') as fp: json.dump(data,fp,indent=4)
            messagebox.showinfo("Export",f"Saved → {fname}",parent=self)
        except Exception as e: messagebox.showerror("Export error",str(e),parent=self)

    def import_json(self):
        path=filedialog.askopenfilename(parent=self,title="Choose JSON file",filetypes=[("JSON","*.json")])
        if not path: return
        try:
            with open(path,encoding='utf-8') as fp: recs=json.load(fp)
            add=0
            with sqlite3.connect(DB_FILE) as con:
                cur=con.cursor()
                for r in recs:
                    if not all(k in r for k in ("start_time","end_time","duration_seconds")): continue
                    try:
                        cur.execute("INSERT INTO logs(start_time,end_time,duration_seconds) VALUES(?,?,?)",
                                    (r["start_time"],r["end_time"],int(r["duration_seconds"])))
                        add+=1
                    except Exception: pass
                con.commit()
            self.update_stats()
            messagebox.showinfo("Import",f"Imported {add} sessions.",parent=self)
        except Exception as e: messagebox.showerror("Import error",str(e),parent=self)

    # ── Manual log
    def _make_manual(self,f_title,f_head,f_body):
        f=self.frames["Manual_Log"]; f.configure(padx=20,pady=20)
        Label(f,text="Manually Add / Deduct Time",font=f_title,bg="#3C3C3C",fg="white").pack(pady=(20,20))
        Label(f,text="Select Date:",font=f_head,bg="#3C3C3C",fg="white").pack()
        self.cal=DateEntry(f,width=12,background="blue",foreground="white",borderwidth=2,date_pattern="yyyy-mm-dd"); self.cal.pack(pady=(0,20))
        Label(f,text="Minutes:",font=f_head,bg="#3C3C3C",fg="white").pack()
        self.spin=Spinbox(f,from_=0,to=1440,width=10,font=f_body); self.spin.pack(pady=(0,25))
        bar=Frame(f,bg="#3C3C3C"); bar.pack()
        Button(bar,text="Add ➕",font=f_head,bg="#28A745",fg="white",relief="flat",padx=10,pady=5,command=lambda:self._manual("add")).pack(side="left",padx=10)
        Button(bar,text="Deduct ➖",font=f_head,bg="#DC3545",fg="white",relief="flat",padx=10,pady=5,command=lambda:self._manual("deduct")).pack(side="left",padx=10)

    def _manual(self,mode:str):
        try:
            mins=int(self.spin.get())
            if mins<=0: raise ValueError
        except ValueError:
            messagebox.showwarning("Invalid","Enter a positive integer.",parent=self); return
        target=self.cal.get_date(); sec=mins*60
        if mode=="add":
            start=datetime.combine(target,datetime.min.time()); add_log(start,start+timedelta(seconds=sec),sec)
        else:
            logs=[l for l in get_all_logs() if datetime.fromisoformat(l['start_time']).date()==target]
            if not logs: messagebox.showinfo("Info",f"No sessions on {target}.",parent=self); return
            rem=sec
            with sqlite3.connect(DB_FILE) as con:
                cur=con.cursor()
                for l in sorted(logs,key=lambda r:r['start_time'],reverse=True):
                    if rem<=0: break
                    if rem>=l['duration_seconds']:
                        cur.execute("DELETE FROM logs WHERE id=?",(l['id'],)); rem-=l['duration_seconds']
                    else:
                        new_dur=l['duration_seconds']-rem
                        new_end=(datetime.fromisoformat(l['start_time'])+timedelta(seconds=new_dur)).isoformat()
                        cur.execute("UPDATE logs SET duration_seconds=?,end_time=? WHERE id=?",(new_dur,new_end,l['id']))
                        rem=0
                con.commit()
        self.update_stats()
        self.spin.delete(0,'end'); self.spin.insert(0,'0')

    # ── Sound tab
    def _make_sound(self,f_title,f_body,f_head):
        f=self.frames["Notification_Sound"]; f.configure(padx=20,pady=20)
        Label(f,text="Change Notification Sounds",font=f_title,bg="#3C3C3C",fg="white").pack(pady=(20,10))
        Label(f,text="Replace 'start.mp3' and 'stop.mp3' in the program folder with any MP3 you like.",font=f_body,bg="#3C3C3C",fg="white",wraplength=480,justify="left").pack(pady=(0,25))
        Button(f,text="Open Application Folder",font=f_head,bg="#17A2B8",fg="white",relief="flat",padx=15,pady=10,command=self._open_folder).pack()

    def _open_folder(self):
        path=os.path.dirname(os.path.abspath(sys.argv[0]))
        try:
            if sys.platform=="win32": os.startfile(path)
            elif sys.platform=="darwin": os.system(f'open "{path}"')
            else: os.system(f'xdg-open "{path}"')
        except Exception as e: messagebox.showerror("Error",str(e),parent=self)

    # ── Instructions tab
    def _make_instr(self,f_title,f_body):
        f=self.frames["Instructions"]; f.configure(padx=20,pady=20)
        Label(f,text="Quick Start",font=f_title,bg="#3C3C3C",fg="white").pack(pady=(20,10))
        info=("•  Alt + Shift + 1 → start / stop timer\n"
              "•  Left-click tray-icon → Settings\n"
              "•  Win+R → shell:startup → put shortcut here for auto-start")
        txt=scrolledtext.ScrolledText(f,wrap="word",bg="#3C3C3C",fg="white",relief="flat",font=f_body,borderwidth=0)
        txt.insert("1.0",info+"\n\nFollow my blog: ")
        s=txt.index("end-1c"); url="https://thekingofweirdtimes.blogspot.com/"
        txt.insert("end",url); e=txt.index("end-1c")
        txt.tag_add("link",s,e); txt.tag_config("link",foreground="cyan",underline=True)
        txt.tag_bind("link","<Button-1>",lambda _:__import__("webbrowser").open(url))
        txt.config(state="disabled"); txt.pack(fill="both",expand=True)

    # ─────────
    def show_frame(self,name:str):
        self.frames[name].tkraise()
        if name=="Stats": self.update_stats()


# ─────────── Tray icon setup ────────────────────────────────────────────
def make_icon(w:int,h:int,c1:str,c2:str):
    img=Image.new("RGB",(w,h),c1); d=ImageDraw.Draw(img)
    d.rectangle((w//2,0,w,h//2),fill=c2); d.rectangle((0,h//2,w//2,h),fill=c2); return img


def _show_settings(_icon=None,_item=None):
    global SETTINGS_WINDOW
    if not SETTINGS_WINDOW or not SETTINGS_WINDOW.winfo_exists():
        SETTINGS_WINDOW=SettingsWindow(ROOT)
    SETTINGS_WINDOW.deiconify(); SETTINGS_WINDOW.lift(); SETTINGS_WINDOW.focus_force()


def _on_quit(icon,_item):
    if TIMER_RUNNING: toggle_timer()
    icon.stop(); ROOT.quit()


def _update_tooltip():
    if TRAY_ICON:
        if TIMER_RUNNING and START_TIME:
            TRAY_ICON.title=f"Studying {hms((datetime.now()-START_TIME).total_seconds())}"
        else:
            TRAY_ICON.title="Timer stopped"
    ROOT.after(1000,_update_tooltip)


def setup_tray():
    global ROOT,TRAY_ICON
    ROOT=Tk(); ROOT.withdraw()

    img=make_icon(64,64,"#2E2E2E","#1E90FF")
    menu=pystray.Menu(item('Settings',_show_settings,default=True), item('Quit',_on_quit))
    icon=pystray.Icon(APP_NAME,img,APP_NAME,menu)
    TRAY_ICON=icon

    # single-left click handler (works with all pystray versions)
    def _onclick(icon,button,pressed):
        try: name=button.name.lower()
        except AttributeError: name=str(button).lower()
        if not pressed and "left" in name:
            _show_settings()

    icon.when_clicked=_onclick

    threading.Thread(target=icon.run,daemon=True).start()
    ROOT.after(1000,_update_tooltip)   # start tooltip updater
    ROOT.mainloop()


# ─────────── Main ───────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    keyboard.add_hotkey("alt+shift+1", toggle_timer, suppress=False)

    # create dummy sounds if missing
    try:
        from gtts import gTTS
        from pydub import AudioSegment
        for fn,txt in (("start.mp3","Timer started"),("stop.mp3","Timer stopped")):
            if not os.path.exists(fn):
                tts=gTTS(txt); tmp=f"_{fn}"; tts.save(tmp)
                AudioSegment.from_mp3(tmp).export(fn,format="mp3"); os.remove(tmp)
    except Exception: pass

    print(f"{APP_NAME} running. Press Alt+Shift+1 to toggle timer.")
    setup_tray()
