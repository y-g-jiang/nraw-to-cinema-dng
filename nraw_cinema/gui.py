import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class Application:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.events = queue.Queue()
        self.pending_close = False
        self.preview = None
        root.title("NRAW → CinemaDNG · 无损 Bayer 序列")
        root.geometry("960x730")
        root.minsize(820, 650)
        root.configure(background="#f3f5f8")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5f8")
        style.configure("TLabel", background="#f3f5f8", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", padding=(13, 8), font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"), foreground="#10213b")
        style.configure("Note.TLabel", foreground="#52627a")
        style.configure("Accent.TButton", background="#1c61d4", foreground="white")
        style.map("Accent.TButton", background=[("disabled", "#8b9bb3"), ("active", "#154cb1")])
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="NRAW → CinemaDNG", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="保留原始 RGGB · 逐帧白平衡 / 黑位 · 写入后像素回读校验", style="Note.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 18))
        self.fields = {}
        definitions = [("source", "输入 NEV", "file"), ("sdk", "RED SDK DLL", "file"),
                       ("profile", "同机型参考 DNG", "file"), ("output", "输出序列文件夹", "directory")]
        self.editable = []
        for row, (name, label, kind) in enumerate(definitions, 2):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=5)
            variable = tk.StringVar()
            self.fields[name] = variable
            entry = ttk.Entry(frame, textvariable=variable, font=("Microsoft YaHei UI", 10))
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 8), ipady=6)
            button = ttk.Button(frame, text="选择…", command=lambda key=name, mode=kind: self.browse(key, mode))
            button.grid(row=row, column=2, sticky="ew")
            self.editable.extend((entry, button))
        note = "参考 DNG：用 Adobe DNG Converter 转换同机型的一张 NEF；继承颜色配置，不复制照片白平衡。\nSDK 由你自行提供，程序会核验版本。不会下载 / 修改 DLL，不附带相机素材。"
        ttk.Label(frame, text=note, style="Note.TLabel", wraplength=860).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 16))
        options = ttk.Frame(frame)
        options.grid(row=7, column=0, columnspan=3, sticky="ew")
        self.start = tk.StringVar(value="1")
        self.count = tk.StringVar(value="")
        self.step = tk.StringVar(value="1")
        for column, (label, variable, width) in enumerate((("起始帧（从 1 起）", self.start, 8), ("帧数（空白为全部）", self.count, 10), ("抽帧间隔", self.step, 6))):
            panel = ttk.Frame(options)
            panel.grid(row=0, column=column, sticky="w", padx=(0, 22))
            ttk.Label(panel, text=label).pack(anchor="w")
            entry = ttk.Entry(panel, textvariable=variable, width=width)
            entry.pack(anchor="w", pady=(5, 0), ipady=5)
            self.editable.append(entry)
        self.resume = tk.BooleanVar(value=False)
        resume = ttk.Checkbutton(options, text="校验后续转", variable=self.resume)
        resume.grid(row=0, column=3, sticky="s", pady=8)
        self.editable.append(resume)
        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(20, 12))
        self.inspect_button = ttk.Button(actions, text="检查素材与空间", command=lambda: self.run("inspect"))
        self.inspect_button.pack(side="left")
        self.convert_button = ttk.Button(actions, text="开始转换", style="Accent.TButton", command=lambda: self.run("convert"))
        self.convert_button.pack(side="left", padx=10)
        self.cancel_button = ttk.Button(actions, text="当前帧完成后停止", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left")
        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.grid(row=9, column=0, columnspan=3, sticky="ew")
        self.status = tk.StringVar(value="就绪 · 首次建议只转 3 帧检查")
        ttk.Label(frame, textvariable=self.status).grid(row=10, column=0, columnspan=3, sticky="w", pady=(7, 10))
        self.log = tk.Text(frame, height=7, wrap="word", font=("Microsoft YaHei UI", 10), relief="flat", background="white", padx=12, pady=10, state="disabled")
        self.log.grid(row=11, column=0, columnspan=3, sticky="nsew")
        frame.rowconfigure(11, weight=1)
        ttk.Label(frame, text="无压缩 DNG，约 宽 × 高 × 2 字节/帧。音频、源时间码与镜头几何校正不导出。", style="Note.TLabel").grid(row=12, column=0, columnspan=3, sticky="w", pady=(12, 0))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def browse(self, name, kind):
        if kind == "directory":
            value = filedialog.askdirectory(title="选择空的序列输出文件夹", mustexist=False)
        else:
            patterns = {"source": ("Nikon N-RAW", "*.nev"), "sdk": ("RED decoder", "REDR3D-x64.dll"), "profile": ("Camera DNG", "*.dng")}
            value = filedialog.askopenfilename(filetypes=[patterns[name], ("All files", "*.*")])
        if value:
            self.fields[name].set(value)

    def arguments(self, command):
        source, sdk, profile = (self.fields[name].get().strip() for name in ("source", "sdk", "profile"))
        if not all((source, sdk, profile)):
            raise ValueError("请选择 NEV、SDK DLL 和同机型参考 DNG")
        start, step = int(self.start.get()) - 1, int(self.step.get())
        count = int(self.count.get()) if self.count.get().strip() else None
        if start < 0 or step < 1 or (count is not None and count < 1):
            raise ValueError("起始帧、帧数和间隔必须为正整数")
        prefix = [str(Path(sys.executable).with_name("NRAW-to-CinemaDNG-worker.exe"))] if getattr(sys, "frozen", False) else [sys.executable, "-m", "nraw_cinema"]
        arguments = [*prefix, command, source, "--sdk", sdk, "--profile", profile, "--start", str(start), "--step", str(step)]
        if count is not None:
            arguments += ["--count", str(count)]
        if command == "convert":
            output = self.fields["output"].get().strip()
            if not output:
                raise ValueError("请选择输出序列文件夹")
            arguments += ["--output", output, "--cancel-stdin"]
            if self.resume.get():
                arguments.append("--resume")
        return arguments

    def run(self, command):
        if self.process is not None:
            return
        try:
            arguments = self.arguments(command)
            if command == "convert" and not self.count.get().strip():
                if not messagebox.askyesno("导出全部帧", "当前选择全部帧。无压缩 CinemaDNG 可能占用大量磁盘空间。\n建议先点“检查素材与空间”。确认开始？"):
                    return
            self.process = subprocess.Popen(arguments, cwd=str(Path(__file__).resolve().parent.parent),
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as error:
            messagebox.showerror("无法开始", str(error))
            return
        self.set_busy(True)
        self.status.set("正在检查…" if command == "inspect" else "转换中…")
        self.progress["value"] = 0
        process = self.process
        def collect():
            for line in process.stdout:
                try:
                    self.events.put(json.loads(line))
                except json.JSONDecodeError:
                    self.events.put(dict(event="log", message=line.rstrip()))
            self.events.put(dict(event="exit", code=process.wait()))
        threading.Thread(target=collect, daemon=True).start()

    def set_busy(self, busy):
        for widget in [*self.editable, self.inspect_button, self.convert_button]:
            widget.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")

    def append(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 250:
            self.log.delete("1.0", "51.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def poll(self):
        while not self.events.empty():
            event = self.events.get_nowait()
            kind = event.get("event")
            if kind == "progress":
                self.progress["maximum"] = event["total"]
                self.progress["value"] = event["completed"]
                self.status.set(f"已校验 {event['completed']} / {event['total']} 帧")
            elif kind == "inspection":
                self.status.set("素材检查通过")
                self.append(f"{event['model']} · {event['raw_size'][0]} × {event['raw_size'][1]} · {event['source_rate']} fps\n选择 {event['selected']} / {event['frames']} 帧 · 预计上限 {event['estimated_bytes'] / 1024**3:.2f} GiB\n参考配置 {event['profile']} · 白位 {event['white']} · 黑位 {event['black']}")
            elif kind == "complete":
                self.status.set(f"完成：{event['completed']} 帧，每帧均已回读校验")
                self.append(f"输出：{event['output']}")
            elif kind == "cancelled":
                self.status.set("已停止；相同设置可校验后续转")
            elif kind == "error":
                self.status.set("失败；原始素材未修改")
                self.append(event["message"])
            elif kind == "log":
                self.append(event["message"])
            elif kind == "exit":
                if event["code"] not in (0, 2):
                    self.status.set(f"任务失败（退出码 {event['code']}），请查看日志")
                if self.process:
                    self.process.stdin.close()
                    self.process.stdout.close()
                self.process = None
                self.set_busy(False)
                if self.pending_close:
                    self.root.destroy()
                    return
        self.root.after(100, self.poll)

    def cancel(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write("cancel\n")
                self.process.stdin.flush()
                self.status.set("等待当前帧写入与校验结束…")
                self.cancel_button.configure(state="disabled")
            except (OSError, ValueError):
                pass

    def close(self):
        if self.process:
            self.pending_close = True
            self.cancel()
        else:
            self.root.destroy()


def launch():
    root = tk.Tk()
    Application(root)
    root.mainloop()
