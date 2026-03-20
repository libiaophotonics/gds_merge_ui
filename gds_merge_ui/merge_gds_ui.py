import os
import math
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- 尝试导入拖拽库，如果没安装也能正常运行（只是没拖拽功能） ---
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_SUPPORTED = True
except ImportError:
    DND_SUPPORTED = False

import matplotlib

matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.patches as patches
import matplotlib.colors as mcolors

import klayout.db as db

# Matplotlib 全局字体设置
plt.rcParams['font.family'] = ['sans-serif']
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans', 'Arial', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False


class GDSMultiStitcherApp:
    def __init__(self):
        # 如果支持拖拽，使用 TkinterDnD 的根窗口
        if DND_SUPPORTED:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()

        self.root.title("GDS MERGER Pro - DnD & Staggered Dummy Fill")

        window_width = 1280
        window_height = 900
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x_cordinate = int((screen_width / 2) - (window_width / 2))
        y_cordinate = int((screen_height / 2) - (window_height / 2))
        self.root.geometry(f"{window_width}x{window_height}+{x_cordinate}+{y_cordinate}")

        self.gds_list, self.measurements, self.undo_stack, self.guide_lines, self.overlap_patches = [], [], [], [], []
        self.dragging_idx = -1
        self.drag_start_x, self.drag_start_y, self.rect_start_x, self.rect_start_y = 0, 0, 0, 0
        self.drag_start_offsets = {}
        self.drag_snapshot_taken = False

        self.measure_mode_var = tk.BooleanVar(value=False)
        self.measure_start_pt, self.measure_line, self.measure_text, self.snap_indicator = None, None, None, None
        self.measure_state = 0

        self.block_width_var, self.block_height_var = tk.StringVar(value="5000.0"), tk.StringVar(value="5000.0")
        self.block_width, self.block_height = 5000.0, 5000.0

        self.selected_x_var, self.selected_y_var = tk.StringVar(value="0.0"), tk.StringVar(value="0.0")
        self.anchor_var = tk.StringVar(value="Bottom-Left")
        self.anchor_options = ["Bottom-Left", "Bottom-Right", "Top-Left", "Top-Right", "Center"]

        self.align_options_map = {
            "⇤ Align Left": "left", "⇹ Align Center X": "center_x", "⇥ Align Right": "right",
            "⇡ Align Top": "top", "↕ Align Center Y": "center_y", "⇣ Align Bottom": "bottom",
            "𝌸 Distribute H": "dist_h", "𝌆 Distribute V": "dist_v"
        }
        self.align_var = tk.StringVar(value="⇤ Align Left")

        # 性能与画布功能变量
        self.show_overlap_var, self.bbox_only_var = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        self.grid_snap_var, self.grid_size_var = tk.BooleanVar(value=False), tk.StringVar(value="10.0")

        # --- Dummy Fill 设置变量 ---
        self.enable_dummy_var = tk.BooleanVar(value=False)
        self.staggered_var = tk.BooleanVar(value=True)  # 新增：交错填充开关
        self.dummy_layer_var = tk.StringVar(value="1")
        self.dummy_datatype_var = tk.StringVar(value="0")
        self.dummy_size_var = tk.StringVar(value="5.0")
        self.dummy_spacing_var = tk.StringVar(value="5.0")
        self.dummy_margin_var = tk.StringVar(value="3.0")

        self.top_cell_name_var = tk.StringVar(value="MERGED_CHIP")
        if DND_SUPPORTED:
            self.status_var = tk.StringVar(value="Ready: Drag and Drop GDS files into the list!")
        else:
            self.status_var = tk.StringVar(value="Ready: Please add GDS files manually (DnD not installed).")

        self.color_palette = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22',
                              '#17becf']

        self.setup_ui()
        self.draw_preview(reset_view=True)

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ================= 左侧控制面板 (分上下容器防挤压) =================
        left_frame = ttk.Frame(main_frame, width=380)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)

        top_container = ttk.Frame(left_frame)
        top_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        bottom_container = ttk.Frame(left_frame)
        bottom_container.pack(side=tk.BOTTOM, fill=tk.X)

        # 1a. 工程读写
        proj_frame = ttk.Frame(top_container)
        proj_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(proj_frame, text="📂 Load Project", command=self.action_load_project).pack(side=tk.LEFT, fill=tk.X,
                                                                                             expand=True, padx=(0, 2))
        ttk.Button(proj_frame, text="💾 Save Project", command=self.action_save_project).pack(side=tk.LEFT, fill=tk.X,
                                                                                             expand=True, padx=(2, 0))

        # GDS 列表
        list_title = "1. GDS File List (Drag & Drop Here)" if DND_SUPPORTED else "1. GDS File List"
        list_frame = ttk.LabelFrame(top_container, text=list_title, padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        btn_frame = ttk.Frame(list_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(btn_frame, text="➕ Add GDS", command=self.add_gds).pack(side=tk.LEFT, fill=tk.X, expand=True,
                                                                           padx=(0, 2))
        ttk.Button(btn_frame, text="➖ Remove", command=self.action_delete_selected).pack(side=tk.LEFT, fill=tk.X,
                                                                                         expand=True, padx=(2, 0))

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, font=('Arial', 10),
                                  selectbackground="#0078D7", exportselection=False, selectmode=tk.EXTENDED)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind('<<ListboxSelect>>', self.on_listbox_select)

        if DND_SUPPORTED:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind('<<Drop>>', self.on_file_drop)

        # 1b. Block 尺寸设置
        block_settings_frame = ttk.LabelFrame(bottom_container, text="1b. Block Size", padding=5)
        block_settings_frame.pack(fill=tk.X, pady=(0, 5))
        entry_f = ttk.Frame(block_settings_frame)
        entry_f.pack(fill=tk.X)
        ttk.Label(entry_f, text="W x H:").pack(side=tk.LEFT)
        ttk.Entry(entry_f, textvariable=self.block_width_var, width=8).pack(side=tk.LEFT, fill=tk.X, expand=True,
                                                                            padx=2)
        ttk.Entry(entry_f, textvariable=self.block_height_var, width=8).pack(side=tk.LEFT, fill=tk.X, expand=True,
                                                                             padx=2)
        ttk.Button(entry_f, text="Apply", command=self.update_block_size, width=6).pack(side=tk.LEFT)

        # 1c. 坐标位置调整
        pos_frame = ttk.LabelFrame(bottom_container, text="1c. Position (um)", padding=5)
        pos_frame.pack(fill=tk.X, pady=(0, 5))
        pos_row1 = ttk.Frame(pos_frame)
        pos_row1.pack(fill=tk.X)
        ttk.Label(pos_row1, text="Anchor:").pack(side=tk.LEFT)
        anchor_cb = ttk.Combobox(pos_row1, textvariable=self.anchor_var, values=self.anchor_options, state="readonly",
                                 width=12)
        anchor_cb.pack(side=tk.LEFT, padx=5)
        anchor_cb.bind("<<ComboboxSelected>>", self.on_anchor_change)
        pos_row2 = ttk.Frame(pos_frame)
        pos_row2.pack(fill=tk.X, pady=2)
        ttk.Label(pos_row2, text="X:").pack(side=tk.LEFT)
        ttk.Entry(pos_row2, textvariable=self.selected_x_var, width=10).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(pos_row2, text="Y:").pack(side=tk.LEFT)
        ttk.Entry(pos_row2, textvariable=self.selected_y_var, width=10).pack(side=tk.LEFT)
        ttk.Button(pos_row2, text="Apply", command=self.apply_manual_position).pack(side=tk.RIGHT)

        # 2. 导出设置与 Dummy Fill (全新紧凑版，包含交错式开关)
        output_frame = ttk.LabelFrame(bottom_container, text="2. Export & Post-Processing", padding=5)
        output_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(output_frame, text="Merged Cell Name:").pack(anchor=tk.W)
        ttk.Entry(output_frame, textvariable=self.top_cell_name_var).pack(fill=tk.X, pady=(0, 5))

        dummy_f = ttk.Frame(output_frame)
        dummy_f.pack(fill=tk.X, pady=(0, 5))

        # Row 0: 开关
        ttk.Checkbutton(dummy_f, text="Enable Dummy Fill", variable=self.enable_dummy_var).grid(row=0, column=0,
                                                                                                columnspan=2,
                                                                                                sticky=tk.W)
        ttk.Checkbutton(dummy_f, text="Staggered(砌砖)", variable=self.staggered_var).grid(row=0, column=2,
                                                                                           columnspan=2, sticky=tk.W,
                                                                                           padx=2)

        # Row 1: 图层与 Datatype
        ttk.Label(dummy_f, text="Layer:").grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(dummy_f, textvariable=self.dummy_layer_var, width=5).grid(row=1, column=1, padx=2)
        ttk.Label(dummy_f, text="DT:").grid(row=1, column=2, sticky=tk.E)
        ttk.Entry(dummy_f, textvariable=self.dummy_datatype_var, width=5).grid(row=1, column=3, padx=2)

        # Row 2: 尺寸与间距
        ttk.Label(dummy_f, text="Size(um):").grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(dummy_f, textvariable=self.dummy_size_var, width=6).grid(row=2, column=1, padx=2)
        ttk.Label(dummy_f, text="Spc:").grid(row=2, column=2, sticky=tk.E)
        ttk.Entry(dummy_f, textvariable=self.dummy_spacing_var, width=6).grid(row=2, column=3, padx=2)

        # Row 3: 避让边界
        ttk.Label(dummy_f, text="Margin(um):").grid(row=3, column=0, columnspan=2, sticky=tk.W)
        ttk.Entry(dummy_f, textvariable=self.dummy_margin_var, width=8).grid(row=3, column=2, columnspan=2, sticky=tk.W,
                                                                             padx=2)

        ttk.Button(output_frame, text="💾 Export Merged GDS", command=self.execute_stitch, style="Accent.TButton").pack(
            fill=tk.X, pady=(5, 0), ipady=5)

        # ================= 右侧交互面板 (画布区域) =================
        right_frame = ttk.LabelFrame(main_frame, text="Interactive Canvas", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        canvas_toolbar_1 = ttk.Frame(right_frame)
        canvas_toolbar_1.pack(side=tk.TOP, fill=tk.X, pady=(0, 2))
        ttk.Button(canvas_toolbar_1, text="↩️ Undo", command=self.action_undo).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(canvas_toolbar_1, text="🔍 Zoom Fit", command=lambda: self.draw_preview(reset_view=True)).pack(
            side=tk.LEFT, padx=2)
        ttk.Separator(canvas_toolbar_1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=2)
        ttk.Checkbutton(canvas_toolbar_1, text="📏 Measure", style="Toolbutton", variable=self.measure_mode_var,
                        command=self.on_measure_toggle).pack(side=tk.LEFT, padx=2)
        ttk.Button(canvas_toolbar_1, text="🗑️ Clear", command=self.action_clear_measurements).pack(side=tk.LEFT, padx=2)
        ttk.Separator(canvas_toolbar_1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=2)
        self.btn_overlap = ttk.Checkbutton(canvas_toolbar_1, text="🔴 Overlaps", style="Toolbutton",
                                           variable=self.show_overlap_var, command=self.on_overlap_toggle)
        self.btn_overlap.pack(side=tk.LEFT, padx=2)
        self.btn_bbox = ttk.Checkbutton(canvas_toolbar_1, text="✅ BBox Only", style="Toolbutton",
                                        variable=self.bbox_only_var, command=self.on_bbox_toggle)
        self.btn_bbox.pack(side=tk.LEFT, padx=2)

        canvas_toolbar_2 = ttk.Frame(right_frame)
        canvas_toolbar_2.pack(side=tk.TOP, fill=tk.X, pady=(2, 5))
        ttk.Label(canvas_toolbar_2, text="Align:").pack(side=tk.LEFT, padx=(0, 2))
        align_cb = ttk.Combobox(canvas_toolbar_2, textvariable=self.align_var,
                                values=list(self.align_options_map.keys()), state="readonly", width=16)
        align_cb.pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(canvas_toolbar_2, text="▶ Go", command=self.execute_align).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Separator(canvas_toolbar_2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=2)
        ttk.Checkbutton(canvas_toolbar_2, text="🌐 Snap", style="Toolbutton", variable=self.grid_snap_var).pack(
            side=tk.LEFT, padx=2)
        ttk.Label(canvas_toolbar_2, text="Size:").pack(side=tk.LEFT, padx=(2, 2))
        ttk.Entry(canvas_toolbar_2, textvariable=self.grid_size_var, width=6).pack(side=tk.LEFT, padx=2)

        self.figure = plt.Figure(figsize=(6, 5), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, right_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('button_release_event', self.on_release)
        self.canvas.mpl_connect('scroll_event', self.on_scroll)

        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    # --- 拖拽事件处理 ---
    def on_file_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        added = False
        for p in files:
            if p.lower().endswith('.gds'):
                self.process_single_gds(p)
                added = True
        if added: self.draw_preview(reset_view=True)

    def process_single_gds(self, filepath):
        try:
            self.save_snapshot()
            base_name = os.path.splitext(os.path.basename(filepath))[0]
            base_bbox, true_polygons = self.parse_gds_info(filepath)
            gds_info = {'path': filepath, 'name': base_name, 'base_bbox': base_bbox, 'trans': db.DTrans(),
                        'offset_x': 0.0, 'offset_y': 0.0,
                        'color': self.color_palette[len(self.gds_list) % len(self.color_palette)],
                        'patch': None, 'center_text': None, 'true_polygons': true_polygons, 'poly_patches': []}
            self.gds_list.append(gds_info)
            self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {base_name}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load {filepath}:\n{str(e)}")

    def add_gds(self):
        paths = filedialog.askopenfilenames(filetypes=[("GDS Files", "*.gds")])
        added = False
        for p in paths:
            self.process_single_gds(p)
            added = True
        if added: self.draw_preview(reset_view=True)

    # --- UI 动态切换与覆盖检测 ---
    def on_bbox_toggle(self):
        if self.bbox_only_var.get():
            self.btn_bbox.config(text="✅ BBox Only"); self.status_var.set("Performance Mode ON.")
        else:
            self.btn_bbox.config(text="🔲 Full Detail"); self.status_var.set("Rendering full polygons...")
        self.draw_preview(reset_view=False)

    def on_overlap_toggle(self):
        if self.show_overlap_var.get():
            self.btn_overlap.config(text="🔴 Overlaps: ON")
        else:
            self.btn_overlap.config(text="⭕ Overlaps: OFF")
        self.draw_overlaps()
        self.canvas.draw_idle()

    def draw_overlaps(self):
        for p in getattr(self, 'overlap_patches', []):
            try:
                p.remove()
            except:
                pass
        self.overlap_patches.clear()
        if not self.show_overlap_var.get(): return
        n = len(self.gds_list)
        tol = 1e-5
        for i in range(n):
            for j in range(i + 1, n):
                l1, r1, b1, t1 = self.get_bbox(self.gds_list[i])
                l2, r2, b2, t2 = self.get_bbox(self.gds_list[j])
                if (l1 < r2 - tol) and (r1 > l2 + tol) and (b1 < t2 - tol) and (t1 > b2 + tol):
                    il, ir, ib, it = max(l1, l2), min(r1, r2), max(b1, b2), min(t1, t2)
                    rect = patches.Rectangle((il, ib), ir - il, it - ib, linewidth=1.5, edgecolor='red',
                                             facecolor='red', alpha=0.5, hatch='///', zorder=250)
                    self.ax.add_patch(rect)
                    self.overlap_patches.append(rect)

    # --- 项目存档与测量功能 ---
    def save_snapshot(self):
        snapshot = {'gds_list': [], 'measurements': [dict(m) for m in self.measurements]}
        for gds in self.gds_list:
            trans_copy = gds['trans'] * db.DTrans()
            snap_gds = {'path': gds['path'], 'name': gds['name'], 'base_bbox': gds['base_bbox'], 'trans': trans_copy,
                        'offset_x': gds['offset_x'], 'offset_y': gds['offset_y'], 'color': gds['color'],
                        'true_polygons': gds['true_polygons']}
            snapshot['gds_list'].append(snap_gds)
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > 30: self.undo_stack.pop(0)

    def action_undo(self):
        if not self.undo_stack: return
        snapshot = self.undo_stack.pop()
        self.gds_list.clear()
        self.listbox.delete(0, tk.END)
        for item in snapshot['gds_list']:
            gds_info = {'path': item['path'], 'name': item['name'], 'base_bbox': item['base_bbox'],
                        'trans': item['trans'],
                        'offset_x': item['offset_x'], 'offset_y': item['offset_y'], 'color': item['color'],
                        'patch': None, 'center_text': None, 'true_polygons': item['true_polygons'], 'poly_patches': []}
            self.gds_list.append(gds_info)
            self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {item['name']}")
        self.measurements = snapshot.get('measurements', [])
        self.clear_active_measurement()
        self.draw_preview(reset_view=False)

    def action_save_project(self):
        if not self.gds_list: return
        filepath = filedialog.asksaveasfilename(defaultextension=".gdsprj", filetypes=[("GDS Project", "*.gdsprj")])
        if not filepath: return
        try:
            project_data = {"block_width": self.block_width, "block_height": self.block_height,
                            "top_cell_name": self.top_cell_name_var.get(), "measurements": self.measurements,
                            "gds_items": []}
            for gds in self.gds_list:
                item = {"path": gds["path"], "name": gds["name"], "offset_x": gds["offset_x"],
                        "offset_y": gds["offset_y"], "color": gds["color"], "trans_rot": gds["trans"].rot,
                        "trans_mirror": gds["trans"].is_mirror(), "trans_dx": gds["trans"].disp.x,
                        "trans_dy": gds["trans"].disp.y}
                project_data["gds_items"].append(item)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(project_data, f, indent=4)
            messagebox.showinfo("Success", "Project saved!")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def action_load_project(self):
        filepath = filedialog.askopenfilename(filetypes=[("GDS Project", "*.gdsprj")])
        if not filepath: return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                project_data = json.load(f)
            self.gds_list.clear()
            self.listbox.delete(0, tk.END)
            self.measurements = project_data.get("measurements", [])
            self.clear_active_measurement()
            self.undo_stack.clear()
            self.block_width_var.set(str(project_data.get("block_width", 5000.0)))
            self.block_height_var.set(str(project_data.get("block_height", 5000.0)))
            self.top_cell_name_var.set(project_data.get("top_cell_name", "MERGED_CHIP"))
            self.update_block_size()
            for item in project_data.get("gds_items", []):
                path, name = item.get("path"), item.get("name")
                if not os.path.exists(path): continue
                base_bbox, true_polygons = self.parse_gds_info(path)
                trans = db.DTrans(item["trans_rot"], item["trans_mirror"], item["trans_dx"], item["trans_dy"])
                gds_info = {'path': path, 'name': name, 'base_bbox': base_bbox, 'trans': trans,
                            'offset_x': item["offset_x"], 'offset_y': item["offset_y"], 'color': item["color"],
                            'patch': None, 'center_text': None, 'true_polygons': true_polygons, 'poly_patches': []}
                self.gds_list.append(gds_info)
                self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {name}")
            self.draw_preview(reset_view=True)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def update_canvas_selection(self):
        selected_indices = self.listbox.curselection()
        for i, gds in enumerate(self.gds_list):
            if gds['patch']:
                if i in selected_indices:
                    gds['patch'].set_alpha(0.6); gds['patch'].set_linewidth(2.0)
                else:
                    gds['patch'].set_alpha(0.2); gds['patch'].set_linewidth(0.5)
        self.canvas.draw_idle()

    def get_bbox(self, gds):
        t_box = gds['trans'] * gds['base_bbox']
        return t_box.left + gds['offset_x'], t_box.right + gds['offset_x'], t_box.bottom + gds['offset_y'], t_box.top + \
               gds['offset_y']

    def execute_align(self):
        selected_option = self.align_var.get()
        mode = self.align_options_map.get(selected_option)
        if mode in ['left', 'right', 'center_x', 'bottom', 'top', 'center_y']:
            self.align_selected(mode)
        elif mode == 'dist_h':
            self.distribute_selected('h')
        elif mode == 'dist_v':
            self.distribute_selected('v')

    def align_selected(self, mode):
        selection = self.listbox.curselection()
        if len(selection) < 2: return
        self.save_snapshot()
        bboxes = [self.get_bbox(self.gds_list[i]) for i in selection]
        if mode == 'left':
            target = min(b[0] for b in bboxes)
            for i in selection: self.set_anchor_coords(self.gds_list[i], 'Bottom-Left', target,
                                                       self.get_bbox(self.gds_list[i])[2])
        elif mode == 'right':
            target = max(b[1] for b in bboxes)
            for i in selection: self.set_anchor_coords(self.gds_list[i], 'Bottom-Right', target,
                                                       self.get_bbox(self.gds_list[i])[2])
        elif mode == 'center_x':
            target = (min(b[0] for b in bboxes) + max(b[1] for b in bboxes)) / 2
            for i in selection: self.set_anchor_coords(self.gds_list[i], 'Center', target, (
                        self.get_bbox(self.gds_list[i])[2] + self.get_bbox(self.gds_list[i])[3]) / 2)
        elif mode == 'bottom':
            target = min(b[2] for b in bboxes)
            for i in selection: self.set_anchor_coords(self.gds_list[i], 'Bottom-Left',
                                                       self.get_bbox(self.gds_list[i])[0], target)
        elif mode == 'top':
            target = max(b[3] for b in bboxes)
            for i in selection: self.set_anchor_coords(self.gds_list[i], 'Top-Left', self.get_bbox(self.gds_list[i])[0],
                                                       target)
        elif mode == 'center_y':
            target = (min(b[2] for b in bboxes) + max(b[3] for b in bboxes)) / 2
            for i in selection: self.set_anchor_coords(self.gds_list[i], 'Center', (
                        self.get_bbox(self.gds_list[i])[0] + self.get_bbox(self.gds_list[i])[1]) / 2, target)
        self.draw_preview(reset_view=False)
        self.on_listbox_select()

    def distribute_selected(self, axis):
        selection = self.listbox.curselection()
        if len(selection) < 3: return
        self.save_snapshot()
        items = [{'idx': i, 'gds': self.gds_list[i], 'l': self.get_bbox(self.gds_list[i])[0],
                  'r': self.get_bbox(self.gds_list[i])[1], 'b': self.get_bbox(self.gds_list[i])[2],
                  't': self.get_bbox(self.gds_list[i])[3]} for i in selection]
        for item in items: item['w'], item['h'] = item['r'] - item['l'], item['t'] - item['b']

        if axis == 'h':
            items.sort(key=lambda item: item['l'])
            gap = (items[-1]['r'] - items[0]['l'] - sum(item['w'] for item in items)) / (len(items) - 1)
            cur_x = items[0]['l']
            for item in items:
                self.set_anchor_coords(item['gds'], 'Bottom-Left', cur_x, item['b'])
                cur_x += item['w'] + gap
        elif axis == 'v':
            items.sort(key=lambda item: item['b'])
            gap = (items[-1]['t'] - items[0]['b'] - sum(item['h'] for item in items)) / (len(items) - 1)
            cur_y = items[0]['b']
            for item in items:
                self.set_anchor_coords(item['gds'], 'Bottom-Left', item['l'], cur_y)
                cur_y += item['h'] + gap
        self.draw_preview(reset_view=False)
        self.on_listbox_select()

    def on_measure_toggle(self):
        if self.measure_mode_var.get():
            self.status_var.set("Measure Mode ON: Click once to start, click again to finish.")
            self.listbox.selection_clear(0, tk.END)
            self.update_canvas_selection()
        else:
            self.status_var.set("Measure Mode OFF.")
            self.clear_active_measurement()
            self.canvas.draw_idle()

    def action_clear_measurements(self):
        self.save_snapshot()
        self.measurements.clear()
        self.clear_active_measurement()
        self.draw_preview(reset_view=False)

    def clear_active_measurement(self):
        if self.measure_line:
            try:
                self.measure_line.remove()
            except:
                pass
            self.measure_line = None
        if self.measure_text:
            try:
                self.measure_text.remove()
            except:
                pass
            self.measure_text = None
        if self.snap_indicator:
            try:
                self.snap_indicator.remove()
            except:
                pass
            self.snap_indicator = None
        for line in self.guide_lines:
            try:
                line.remove()
            except:
                pass
        self.guide_lines.clear()
        self.measure_state = 0
        self.measure_start_pt = None

    def get_anchor_coords(self, gds, anchor_type, temp_ox=None, temp_oy=None):
        t_box = gds['trans'] * gds['base_bbox']
        ox = gds['offset_x'] if temp_ox is None else temp_ox
        oy = gds['offset_y'] if temp_oy is None else temp_oy
        if anchor_type == "Bottom-Left":
            return t_box.left + ox, t_box.bottom + oy
        elif anchor_type == "Bottom-Right":
            return t_box.right + ox, t_box.bottom + oy
        elif anchor_type == "Top-Left":
            return t_box.left + ox, t_box.top + oy
        elif anchor_type == "Top-Right":
            return t_box.right + ox, t_box.top + oy
        elif anchor_type == "Center":
            return (t_box.left + t_box.right) / 2 + ox, (t_box.bottom + t_box.top) / 2 + oy
        return ox, oy

    def set_anchor_coords(self, gds, anchor_type, target_x, target_y):
        t_box = gds['trans'] * gds['base_bbox']
        if anchor_type == "Bottom-Left":
            gds['offset_x'], gds['offset_y'] = target_x - t_box.left, target_y - t_box.bottom
        elif anchor_type == "Bottom-Right":
            gds['offset_x'], gds['offset_y'] = target_x - t_box.right, target_y - t_box.bottom
        elif anchor_type == "Top-Left":
            gds['offset_x'], gds['offset_y'] = target_x - t_box.left, target_y - t_box.top
        elif anchor_type == "Top-Right":
            gds['offset_x'], gds['offset_y'] = target_x - t_box.right, target_y - t_box.top
        elif anchor_type == "Center":
            gds['offset_x'], gds['offset_y'] = target_x - (t_box.left + t_box.right) / 2, target_y - (
                        t_box.bottom + t_box.top) / 2

    def on_listbox_select(self, event=None):
        selection = self.listbox.curselection()
        if selection:
            idx = selection[0]
            gds = self.gds_list[idx]
            anchor = self.anchor_var.get()
            x, y = self.get_anchor_coords(gds, anchor)
            self.selected_x_var.set(f"{x:.3f}")
            self.selected_y_var.set(f"{y:.3f}")
            if self.measure_mode_var.get():
                self.measure_mode_var.set(False)
                self.on_measure_toggle()
        self.update_canvas_selection()

    def on_anchor_change(self, event=None):
        self.on_listbox_select()

    def apply_manual_position(self):
        selection = self.listbox.curselection()
        if not selection: return
        self.save_snapshot()
        idx = selection[0]
        try:
            new_x, new_y = float(self.selected_x_var.get()), float(self.selected_y_var.get())
            self.set_anchor_coords(self.gds_list[idx], self.anchor_var.get(), new_x, new_y)
            self.draw_preview(reset_view=False)
        except ValueError:
            pass

    def parse_gds_info(self, filepath):
        layout = db.Layout()
        layout.read(filepath)
        top_cell = layout.top_cells()[0]
        base_bbox = top_cell.dbbox()
        region = db.Region()
        for li in layout.layer_indexes(): region.insert(top_cell.begin_shapes_rec(li))
        region.merge()
        region = region.hulls()
        dbu = layout.dbu
        trans = db.DCplxTrans(dbu)
        true_polygons = []
        for poly in region.each():
            dpoly = db.DPolygon(poly).transformed(trans)
            pts = [(pt.x, pt.y) for pt in dpoly.each_point_hull()]
            if pts: true_polygons.append(pts)
        return base_bbox, true_polygons

    def update_block_size(self):
        try:
            self.block_width, self.block_height = float(self.block_width_var.get()), float(self.block_height_var.get())
            self.draw_preview(reset_view=True)
        except:
            pass

    def action_delete_selected(self):
        selection = self.listbox.curselection()
        if selection:
            self.save_snapshot()
            for idx in sorted(selection, reverse=True): del self.gds_list[idx]
            self.listbox.delete(0, tk.END)
            for i, gds in enumerate(self.gds_list): self.listbox.insert(tk.END, f"[{i + 1}] {gds['name']}")
            self.selected_x_var.set("0.0");
            self.selected_y_var.set("0.0")
            self.draw_preview(reset_view=False)

    def draw_preview(self, reset_view=False):
        cur_xlim, cur_ylim = self.ax.get_xlim(), self.ax.get_ylim()
        self.ax.clear()

        self.measure_line, self.measure_text, self.snap_indicator, self.measure_state, self.measure_start_pt = None, None, None, 0, None

        for spine in self.ax.spines.values(): spine.set_visible(False)

        grid_color, grid_alpha = '#cccccc', 0.4
        self.ax.tick_params(axis='both', which='both', length=4, width=0.8, direction='out', colors=grid_color,
                            labelcolor='#999999')
        self.ax.set_axisbelow(True)

        block_rect = patches.Rectangle((0, 0), self.block_width, self.block_height, linewidth=1.5, edgecolor='#2c3e50',
                                       facecolor='#f4f7f9', linestyle='-.', zorder=0)
        self.ax.add_patch(block_rect)
        self.ax.plot(0, 0, marker='+', color='#2c3e50', markersize=15, markeredgewidth=1.5, zorder=1)

        if not self.gds_list:
            self.ax.text(self.block_width / 2, self.block_height / 2, 'No GDS Loaded', ha='center', va='center',
                         color='#bbbbbb', fontsize=12)

        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            sx, sy = t_box.left + gds['offset_x'], t_box.bottom + gds['offset_y']
            w, h = t_box.width(), t_box.height()

            rect = patches.Rectangle((sx, sy), w, h, linewidth=0.5, edgecolor=gds['color'], facecolor=gds['color'],
                                     alpha=0.6, zorder=10)
            self.ax.add_patch(rect)
            gds['patch'] = rect
            gds['poly_patches'] = []

            if not self.bbox_only_var.get():
                for pts in gds['true_polygons']:
                    transformed_pts = []
                    for px, py in pts:
                        t_pt = gds['trans'] * db.DPoint(px, py)
                        transformed_pts.append((t_pt.x + gds['offset_x'], t_pt.y + gds['offset_y']))
                    edge_c = mcolors.to_rgba(gds['color'], alpha=0.7)
                    face_c = mcolors.to_rgba('black', alpha=0.3)
                    poly_patch = patches.Polygon(transformed_pts, closed=True, fill=True, facecolor=face_c,
                                                 edgecolor=edge_c, linestyle='-', linewidth=0.8, zorder=15)
                    self.ax.add_patch(poly_patch)
                    gds['poly_patches'].append((pts, poly_patch))

            box_min = min(w, h)
            ratio = box_min / min(self.block_width, self.block_height) if min(self.block_width,
                                                                              self.block_height) > 0 else 1.0
            dynamic_fs = max(6, min(35, int(6 + 18 * ratio)))
            gds['center_text'] = self.ax.text(sx + w / 2, sy + h / 2, gds['name'], ha='center', va='center',
                                              fontsize=dynamic_fs, color='black', fontweight='bold', alpha=0.7,
                                              zorder=90)

        for m in self.measurements:
            x0, y0, x1, y1 = m['x0'], m['y0'], m['x1'], m['y1']
            self.ax.plot([x0, x1], [y0, y1], color='#FF1493', linestyle='--', linewidth=1, zorder=300)
            dist = math.hypot(x1 - x0, y1 - y0)
            self.ax.text(x1, y1, f" L: {dist:.2f}\n dx: {abs(x1 - x0):.2f}\n dy: {abs(y1 - y0):.2f}", color='#FF1493',
                         fontsize=10, fontweight='bold', zorder=301,
                         bbox=dict(facecolor='white', alpha=0.8, edgecolor='none', pad=2))

        self.update_canvas_selection()

        if reset_view:
            self.ax.set_xlim(-self.block_width * 0.1, self.block_width * 1.1)
            self.ax.set_ylim(-self.block_height * 0.1, self.block_height * 1.1)
        else:
            self.ax.set_xlim(cur_xlim)
            self.ax.set_ylim(cur_ylim)

        self.ax.set_aspect('equal', adjustable='datalim')
        self.ax.grid(True, linestyle='-', color=grid_color, alpha=grid_alpha)

        self.draw_overlaps()
        self.canvas.draw()

    def on_scroll(self, event):
        if not event.inaxes: return
        scale = 1 / 1.2 if event.button == 'up' else 1.2
        cur_x, cur_y = self.ax.get_xlim(), self.ax.get_ylim()
        self.ax.set_xlim(
            [event.xdata - (event.xdata - cur_x[0]) * scale, event.xdata + (cur_x[1] - event.xdata) * scale])
        self.ax.set_ylim(
            [event.ydata - (event.ydata - cur_y[0]) * scale, event.ydata + (cur_y[1] - event.ydata) * scale])
        self.canvas.draw_idle()

    def get_pois(self, gds, temp_ox=None, temp_oy=None):
        t_box = gds['trans'] * gds['base_bbox']
        ox = gds['offset_x'] if temp_ox is None else temp_ox
        oy = gds['offset_y'] if temp_oy is None else temp_oy
        l, r = t_box.left + ox, t_box.right + ox
        b, t = t_box.bottom + oy, t_box.top + oy
        return [l, r, (l + r) / 2], [b, t, (b + t) / 2]

    def get_snapped_coordinate(self, x, y):
        cur_xlim, cur_ylim = self.ax.get_xlim(), self.ax.get_ylim()
        snap_thresh_x, snap_thresh_y = (cur_xlim[1] - cur_xlim[0]) * 0.02, (cur_ylim[1] - cur_ylim[0]) * 0.02
        best_x, best_y = x, y
        min_dx, min_dy = snap_thresh_x, snap_thresh_y
        snapped_x, snapped_y = False, False

        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            ox, oy = gds['offset_x'], gds['offset_y']
            l, r, b, t = t_box.left + ox, t_box.right + ox, t_box.bottom + oy, t_box.top + oy
            cx, cy = (l + r) / 2, (b + t) / 2

            for px in [l, r, cx]:
                if abs(x - px) < min_dx: min_dx, best_x, snapped_x = abs(x - px), px, True
            for py in [b, t, cy]:
                if abs(y - py) < min_dy: min_dy, best_y, snapped_y = abs(y - py), py, True

        return best_x, best_y, snapped_x, snapped_y

    def on_press(self, event):
        if not event.inaxes: return

        if self.measure_mode_var.get() and event.button == 1:
            snap_x, snap_y, _, _ = self.get_snapped_coordinate(event.xdata, event.ydata)
            if self.measure_state == 0:
                self.clear_active_measurement()
                self.measure_start_pt = (snap_x, snap_y)
                self.measure_line, = self.ax.plot([snap_x, snap_x], [snap_y, snap_y], color='#FF1493', linestyle='--',
                                                  linewidth=1, zorder=300)
                self.measure_text = self.ax.text(snap_x, snap_y, '', color='#FF1493', fontsize=10, fontweight='bold',
                                                 zorder=301,
                                                 bbox=dict(facecolor='white', alpha=0.8, edgecolor='none', pad=2))
                self.snap_indicator, = self.ax.plot([snap_x], [snap_y], marker='+', color='red', markersize=12,
                                                    markeredgewidth=2, zorder=305)
                self.measure_state = 1
            elif self.measure_state == 1:
                self.save_snapshot()
                x0, y0 = self.measure_start_pt
                self.measurements.append({'x0': x0, 'y0': y0, 'x1': snap_x, 'y1': snap_y})
                self.measure_state = 0
                if self.snap_indicator: self.snap_indicator.set_data([], [])
                self.measure_line, self.measure_text = None, None
            self.canvas.draw_idle()
            return

        for line in self.guide_lines: line.remove()
        self.guide_lines.clear()

        clicked_idx = -1
        for i in range(len(self.gds_list) - 1, -1, -1):
            if self.gds_list[i]['patch'].contains(event)[0]:
                clicked_idx = i
                break

        if clicked_idx != -1:
            if event.button in [2, 3]:
                self.show_context_menu(clicked_idx)
                return
            elif event.button == 1:
                self.dragging_idx = clicked_idx
                self.drag_start_x, self.drag_start_y = event.xdata, event.ydata
                self.rect_start_x, self.rect_start_y = self.gds_list[clicked_idx]['patch'].get_x(), \
                self.gds_list[clicked_idx]['patch'].get_y()

                current_selection = list(self.listbox.curselection())
                if event.key in ['control', 'ctrl']:
                    if clicked_idx in current_selection:
                        self.listbox.selection_clear(clicked_idx)
                    else:
                        self.listbox.selection_set(clicked_idx)
                else:
                    if clicked_idx not in current_selection:
                        self.listbox.selection_clear(0, tk.END)
                        self.listbox.selection_set(clicked_idx)

                self.drag_start_offsets = {}
                for idx in self.listbox.curselection():
                    self.drag_start_offsets[idx] = (self.gds_list[idx]['offset_x'], self.gds_list[idx]['offset_y'])
                self.on_listbox_select()
                return
        else:
            if event.button == 1 and event.key not in ['control', 'ctrl']:
                self.listbox.selection_clear(0, tk.END)
                self.update_canvas_selection()

    def on_motion(self, event):
        if not event.inaxes: return

        if self.measure_mode_var.get():
            snap_x, snap_y, sn_x, sn_y = self.get_snapped_coordinate(event.xdata, event.ydata)
            if not self.snap_indicator:
                self.snap_indicator, = self.ax.plot([snap_x], [snap_y], marker='+', color='red', markersize=12,
                                                    markeredgewidth=2, zorder=305)
            else:
                if self.measure_state in [0, 1]: self.snap_indicator.set_data([snap_x], [snap_y])
            for line in self.guide_lines: line.remove()
            self.guide_lines.clear()
            if sn_x: self.guide_lines.append(
                self.ax.axvline(x=snap_x, color='#00CED1', linestyle=':', linewidth=1.5, zorder=200))
            if sn_y: self.guide_lines.append(
                self.ax.axhline(y=snap_y, color='#00CED1', linestyle=':', linewidth=1.5, zorder=200))
            if self.measure_state == 1 and self.measure_start_pt is not None:
                x0, y0 = self.measure_start_pt
                self.measure_line.set_data([x0, snap_x], [y0, snap_y])
                self.measure_text.set_position((snap_x, snap_y))
                self.measure_text.set_text(
                    f" L: {math.hypot(snap_x - x0, snap_y - y0):.2f}\n dx: {abs(snap_x - x0):.2f}\n dy: {abs(snap_y - y0):.2f}")
            self.canvas.draw_idle()
            return

        if self.dragging_idx == -1: return

        if not self.drag_snapshot_taken:
            self.save_snapshot()
            self.drag_snapshot_taken = True

        for line in self.guide_lines: line.remove()
        self.guide_lines.clear()

        handle_gds = self.gds_list[self.dragging_idx]
        t_box = handle_gds['trans'] * handle_gds['base_bbox']

        nx_proposed = self.rect_start_x + (event.xdata - self.drag_start_x)
        ny_proposed = self.rect_start_y + (event.ydata - self.drag_start_y)
        temp_ox, temp_oy = nx_proposed - t_box.left, ny_proposed - t_box.bottom

        best_snap_x, best_snap_y, is_grid_snapped = None, None, False

        if self.grid_snap_var.get():
            try:
                g_size = float(self.grid_size_var.get())
                if g_size > 0:
                    anchor = self.anchor_var.get()
                    curr_x, curr_y = self.get_anchor_coords(handle_gds, anchor, temp_ox=temp_ox, temp_oy=temp_oy)
                    snap_x, snap_y = round(curr_x / g_size) * g_size, round(curr_y / g_size) * g_size

                    if anchor == "Bottom-Left":
                        temp_ox, temp_oy = snap_x - t_box.left, snap_y - t_box.bottom
                    elif anchor == "Bottom-Right":
                        temp_ox, temp_oy = snap_x - t_box.right, snap_y - t_box.bottom
                    elif anchor == "Top-Left":
                        temp_ox, temp_oy = snap_x - t_box.left, snap_y - t_box.top
                    elif anchor == "Top-Right":
                        temp_ox, temp_oy = snap_x - t_box.right, snap_y - t_box.top
                    elif anchor == "Center":
                        temp_ox, temp_oy = snap_x - (t_box.left + t_box.right) / 2, snap_y - (
                                    t_box.bottom + t_box.top) / 2
                    is_grid_snapped = True
            except ValueError:
                pass

        if not is_grid_snapped:
            cur_xlim, cur_ylim = self.ax.get_xlim(), self.ax.get_ylim()
            snap_thresh_x, snap_thresh_y = (cur_xlim[1] - cur_xlim[0]) * 0.02, (cur_ylim[1] - cur_ylim[0]) * 0.02
            drag_x_pois, drag_y_pois = self.get_pois(handle_gds, temp_ox, temp_oy)
            min_dx, min_dy = snap_thresh_x, snap_thresh_y
            snap_shift_x, snap_shift_y = 0, 0

            for i, other_gds in enumerate(self.gds_list):
                if i in self.drag_start_offsets: continue
                other_x_pois, other_y_pois = self.get_pois(other_gds)
                for dx in drag_x_pois:
                    for ox in other_x_pois:
                        if abs(dx - ox) < min_dx: min_dx, best_snap_x, snap_shift_x = abs(dx - ox), ox, ox - dx
                for dy in drag_y_pois:
                    for oy in other_y_pois:
                        if abs(dy - oy) < min_dy: min_dy, best_snap_y, snap_shift_y = abs(dy - oy), oy, oy - dy

            temp_ox += snap_shift_x
            temp_oy += snap_shift_y

        final_ox, final_oy = temp_ox, temp_oy
        delta_x = final_ox - self.drag_start_offsets[self.dragging_idx][0]
        delta_y = final_oy - self.drag_start_offsets[self.dragging_idx][1]

        for idx in self.drag_start_offsets:
            gds = self.gds_list[idx]
            new_ox = self.drag_start_offsets[idx][0] + delta_x
            new_oy = self.drag_start_offsets[idx][1] + delta_y
            gds['offset_x'], gds['offset_y'] = new_ox, new_oy

            b = gds['trans'] * gds['base_bbox']
            nx_final, ny_final = b.left + new_ox, b.bottom + new_oy
            gds['patch'].set_x(nx_final)
            gds['patch'].set_y(ny_final)

            for pts, poly_patch in gds['poly_patches']:
                new_transformed_pts = []
                for px, py in pts:
                    t_pt = gds['trans'] * db.DPoint(px, py)
                    new_transformed_pts.append((t_pt.x + new_ox, t_pt.y + new_oy))
                poly_patch.set_xy(new_transformed_pts)

            if gds['center_text']: gds['center_text'].set_position(
                (nx_final + b.width() / 2, ny_final + b.height() / 2))

        if not is_grid_snapped:
            if best_snap_x is not None: self.guide_lines.append(
                self.ax.axvline(x=best_snap_x, color='#FF8C00', linestyle='--', linewidth=1.5, zorder=200))
            if best_snap_y is not None: self.guide_lines.append(
                self.ax.axhline(y=best_snap_y, color='#FF8C00', linestyle='--', linewidth=1.5, zorder=200))

        selection = self.listbox.curselection()
        if selection and selection[0] == self.dragging_idx:
            anchor = self.anchor_var.get()
            x, y = self.get_anchor_coords(handle_gds, anchor)
            self.selected_x_var.set(f"{x:.3f}")
            self.selected_y_var.set(f"{y:.3f}")

        self.draw_overlaps()
        self.canvas.draw_idle()

    def on_release(self, event):
        if self.measure_mode_var.get() and event.button == 1: return
        if self.dragging_idx != -1:
            self.dragging_idx = -1
            self.drag_snapshot_taken = False
            self.drag_start_offsets.clear()
            for line in self.guide_lines: line.remove()
            self.guide_lines.clear()
            self.update_canvas_selection()
            self.canvas.draw_idle()

    def show_context_menu(self, idx):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Duplicate {self.gds_list[idx]['name']}", command=lambda: self.action_duplicate(idx))
        menu.add_command(label="Create Array (Step & Repeat)...", command=lambda: self.action_create_array(idx))
        menu.add_separator()
        menu.add_command(label="Rotate 90 CCW", command=lambda: self.action_rotate_ccw(idx))
        menu.add_command(label="Rotate 90 CW", command=lambda: self.action_rotate_cw(idx))
        menu.add_command(label="Flip H", command=lambda: self.action_flip_horizontal(idx))
        menu.add_command(label="Flip V", command=lambda: self.action_flip_vertical(idx))
        menu.post(int(self.root.winfo_pointerx()), int(self.root.winfo_pointery()))

    def action_duplicate(self, idx):
        self.save_snapshot()
        o = self.gds_list[idx]
        new_gds = {'path': o['path'], 'name': o['name'], 'base_bbox': o['base_bbox'], 'trans': o['trans'] * db.DTrans(),
                   'offset_x': o['offset_x'] + 200, 'offset_y': o['offset_y'] - 200, 'color': o['color'], 'patch': None,
                   'center_text': None, 'true_polygons': o['true_polygons'], 'poly_patches': []}
        self.gds_list.append(new_gds)
        self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {o['name']}")
        self.draw_preview()

    def action_create_array(self, idx):
        dialog = tk.Toplevel(self.root)
        dialog.title("Create Array (Step & Repeat)")
        dialog.geometry("320x220")
        dialog.grab_set()

        ttk.Label(dialog, text="Rows (Y Axis):").grid(row=0, column=0, padx=15, pady=10, sticky=tk.W)
        rows_var = tk.StringVar(value="2")
        ttk.Entry(dialog, textvariable=rows_var, width=15).grid(row=0, column=1)

        ttk.Label(dialog, text="Columns (X Axis):").grid(row=1, column=0, padx=15, pady=10, sticky=tk.W)
        cols_var = tk.StringVar(value="2")
        ttk.Entry(dialog, textvariable=cols_var, width=15).grid(row=1, column=1)

        ttk.Label(dialog, text="Spacing X (um):").grid(row=2, column=0, padx=15, pady=10, sticky=tk.W)
        spc_x_var = tk.StringVar(value="1000")
        ttk.Entry(dialog, textvariable=spc_x_var, width=15).grid(row=2, column=1)

        ttk.Label(dialog, text="Spacing Y (um):").grid(row=3, column=0, padx=15, pady=10, sticky=tk.W)
        spc_y_var = tk.StringVar(value="1000")
        ttk.Entry(dialog, textvariable=spc_y_var, width=15).grid(row=3, column=1)

        def on_ok():
            try:
                rows, cols = int(rows_var.get()), int(cols_var.get())
                spc_x, spc_y = float(spc_x_var.get()), float(spc_y_var.get())
                if rows < 1 or cols < 1: return messagebox.showwarning("Warning", "Rows and Columns must be >= 1")
                self.save_snapshot()
                o = self.gds_list[idx]
                for r in range(rows):
                    for c in range(cols):
                        if r == 0 and c == 0: continue
                        new_gds = {'path': o['path'], 'name': f"{o['name']}_R{r}C{c}", 'base_bbox': o['base_bbox'],
                                   'trans': o['trans'] * db.DTrans(), 'offset_x': o['offset_x'] + c * spc_x,
                                   'offset_y': o['offset_y'] + r * spc_y, 'color': o['color'], 'patch': None,
                                   'center_text': None, 'true_polygons': o['true_polygons'], 'poly_patches': []}
                        self.gds_list.append(new_gds)
                        self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {new_gds['name']}")
                self.draw_preview()
                self.status_var.set(f"Array created: {rows}x{cols} for {o['name']}")
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Error", "Please enter valid numeric values.")

        ttk.Button(dialog, text="Generate Array", command=on_ok).grid(row=4, column=0, columnspan=2, pady=15)

    def action_rotate_ccw(self, i):
        self.save_snapshot(); self.gds_list[i]['trans'] = db.DTrans(1, False, 0, 0) * self.gds_list[i][
            'trans']; self.draw_preview(); self.on_listbox_select()

    def action_rotate_cw(self, i):
        self.save_snapshot(); self.gds_list[i]['trans'] = db.DTrans(3, False, 0, 0) * self.gds_list[i][
            'trans']; self.draw_preview(); self.on_listbox_select()

    def action_flip_horizontal(self, i):
        self.save_snapshot(); self.gds_list[i]['trans'] = db.DTrans(2, True, 0, 0) * self.gds_list[i][
            'trans']; self.draw_preview(); self.on_listbox_select()

    def action_flip_vertical(self, i):
        self.save_snapshot(); self.gds_list[i]['trans'] = db.DTrans(0, True, 0, 0) * self.gds_list[i][
            'trans']; self.draw_preview(); self.on_listbox_select()

    # ================= 核心：导出并执行交错式 Dummy Fill 算法 =================
    def execute_stitch(self):
        if not self.gds_list: return
        out_p = filedialog.asksaveasfilename(defaultextension=".gds")
        if not out_p: return
        try:
            self.status_var.set("Processing and merging GDS files...")
            self.root.update()

            target_layout = db.Layout()
            merged_top = target_layout.create_cell(self.top_cell_name_var.get() or "MERGED")
            cache = {}

            # 1. 执行拼版逻辑
            for idx, g in enumerate(self.gds_list):
                file_path = g['path']

                if file_path not in cache:
                    if not os.path.exists(file_path):
                        messagebox.showwarning("File Missing", f"源文件已被移动或删除，将被跳过导出：\n{file_path}")
                        cache[file_path] = None
                        continue

                    src_layout = db.Layout()
                    src_layout.read(file_path)
                    if idx == 0: target_layout.dbu = src_layout.dbu
                    src_top = src_layout.top_cells()[0]

                    prefix = f"chip{idx}_"
                    for cell in src_layout.each_cell(): cell.name = prefix + cell.name

                    new_cell = target_layout.create_cell(src_top.name)
                    new_cell.copy_tree(src_top)
                    cache[file_path] = new_cell.cell_index()

                if cache[file_path] is not None:
                    merged_top.insert(
                        db.DCellInstArray(cache[file_path], db.DTrans(g['offset_x'], g['offset_y']) * g['trans']))

            # 2. 执行交错式 (Staggered) Dummy Fill 计算逻辑
            if self.enable_dummy_var.get():
                self.status_var.set("Calculating Dummy Fill... This may take a moment.")
                self.root.update()

                dbu = target_layout.dbu
                layer_num = int(self.dummy_layer_var.get())
                datatype_num = int(self.dummy_datatype_var.get())
                size_um = float(self.dummy_size_var.get())
                space_um = float(self.dummy_spacing_var.get())
                margin_um = float(self.dummy_margin_var.get())

                layer_info = db.LayerInfo(layer_num, datatype_num)
                layer_idx = target_layout.layer(layer_info)

                keep_out_region = db.Region(merged_top.begin_shapes_rec(layer_idx))
                margin_dbu = int(margin_um / dbu)
                keep_out_region.size(margin_dbu)
                keep_out_region.merge()

                dummy_region = db.Region()
                box_size_dbu = int(size_um / dbu)
                box_pitch_dbu = int((size_um + space_um) / dbu)

                fill_area = db.Box(0, 0, int(self.block_width / dbu), int(self.block_height / dbu))

                # --- 核心砌砖算法 ---
                y = fill_area.bottom
                row_index = 0

                while y + box_size_dbu <= fill_area.top:
                    # 如果勾选了 Staggered 且当前是奇数行，X轴起始向右偏移半个间距
                    if self.staggered_var.get() and (row_index % 2 != 0):
                        x_offset = int(box_pitch_dbu / 2)
                    else:
                        x_offset = 0

                    x = fill_area.left + x_offset

                    while x + box_size_dbu <= fill_area.right:
                        dummy_region.insert(db.Box(x, y, x + box_size_dbu, y + box_size_dbu))
                        x += box_pitch_dbu

                    y += box_pitch_dbu
                    row_index += 1
                # ---------------------

                # 找出所有与避让区有任何接触的 Dummy 方块，然后从总网格中把它们整块剔除
                final_dummy_region = dummy_region - dummy_region.interacting(keep_out_region)
                merged_top.shapes(layer_idx).insert(final_dummy_region)

            self.status_var.set("Writing to disk...")
            self.root.update()
            target_layout.write(out_p)
            self.status_var.set("Ready")
            messagebox.showinfo("OK", "Merged Success!\n导出合并成功！")
        except Exception as e:
            self.status_var.set("Ready")
            messagebox.showerror("Error", f"Failed to export merged GDS:\n{str(e)}")


if __name__ == "__main__":
    app = GDSMultiStitcherApp()
    app.root.mainloop()