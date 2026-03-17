import os
import math
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import matplotlib

matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.patches as patches

import klayout.db as db

# Matplotlib 全局字体设置
plt.rcParams['font.family'] = ['sans-serif']
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans', 'Arial', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False


class GDSMultiStitcherApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("GDS MERGER 1.0")

        window_width = 1150
        window_height = 860
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x_cordinate = int((screen_width / 2) - (window_width / 2))
        y_cordinate = int((screen_height / 2) - (window_height / 2))
        self.root.geometry(f"{window_width}x{window_height}+{x_cordinate}+{y_cordinate}")

        self.gds_list = []
        self.dragging_idx = -1
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.rect_start_x = 0
        self.rect_start_y = 0

        self.guide_lines = []

        self.measure_mode_var = tk.BooleanVar(value=False)
        self.measure_start_pt = None
        self.measure_line = None
        self.measure_text = None
        self.measure_state = 0
        self.snap_indicator = None

        self.measurements = []

        self.block_width_var = tk.StringVar(value="5000.0")
        self.block_height_var = tk.StringVar(value="5000.0")
        self.block_width = 5000.0
        self.block_height = 5000.0

        self.selected_x_var = tk.StringVar(value="0.0")
        self.selected_y_var = tk.StringVar(value="0.0")
        self.anchor_var = tk.StringVar(value="Bottom-Left")
        self.anchor_options = ["Bottom-Left", "Bottom-Right", "Top-Left", "Top-Right", "Center"]

        self.top_cell_name_var = tk.StringVar(value="MERGED_CHIP")
        self.status_var = tk.StringVar(value="Ready: Please add GDS files.")
        self.color_palette = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22',
                              '#17becf']

        self.setup_ui()
        self.draw_preview(reset_view=True)

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        left_frame = ttk.Frame(main_frame, width=380)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)

        list_frame = ttk.LabelFrame(left_frame, text="1. GDS File List (Ctrl/Shift to Multi-select)", padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        btn_frame = ttk.Frame(list_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(btn_frame, text="➕ Add GDS", command=self.add_gds).pack(side=tk.LEFT, fill=tk.X, expand=True,
                                                                           padx=(0, 2))
        ttk.Button(btn_frame, text="➖ Remove Selected", command=self.action_delete_selected).pack(side=tk.LEFT,
                                                                                                  fill=tk.X,
                                                                                                  expand=True,
                                                                                                  padx=(2, 0))

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, font=('Arial', 10),
                                  selectbackground="#0078D7", exportselection=False, selectmode=tk.EXTENDED)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox.yview)

        self.listbox.bind('<<ListboxSelect>>', self.on_listbox_select)

        block_settings_frame = ttk.LabelFrame(left_frame, text="1b. Block Size & Zoom Settings", padding=10)
        block_settings_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(block_settings_frame, text="Block Width/Height (μm):").pack(anchor=tk.W)
        entry_f = ttk.Frame(block_settings_frame)
        entry_f.pack(fill=tk.X)
        ttk.Entry(entry_f, textvariable=self.block_width_var, width=10).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(entry_f, text=" x ").pack(side=tk.LEFT)
        ttk.Entry(entry_f, textvariable=self.block_height_var, width=10).pack(side=tk.LEFT, fill=tk.X, expand=True)

        btn_f2 = ttk.Frame(block_settings_frame)
        btn_f2.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(btn_f2, text="Apply Size", command=self.update_block_size).pack(side=tk.LEFT, fill=tk.X, expand=True,
                                                                                   padx=(0, 2))
        ttk.Button(btn_f2, text="🔍 Zoom Fit", command=lambda: self.draw_preview(reset_view=True)).pack(side=tk.LEFT,
                                                                                                       fill=tk.X,
                                                                                                       expand=True,
                                                                                                       padx=(2, 2))

        ttk.Checkbutton(btn_f2, text="📏 Measure", style="Toolbutton", variable=self.measure_mode_var,
                        command=self.on_measure_toggle).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 2))
        ttk.Button(btn_f2, text="🗑️ Clear", command=self.action_clear_measurements).pack(side=tk.LEFT, fill=tk.X,
                                                                                         expand=True, padx=(0, 0))

        pos_frame = ttk.LabelFrame(left_frame, text="1c. Selected GDS Position (μm)", padding=10)
        pos_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(pos_frame, text="Anchor:").grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        anchor_cb = ttk.Combobox(pos_frame, textvariable=self.anchor_var, values=self.anchor_options, state="readonly",
                                 width=15)
        anchor_cb.grid(row=0, column=1, columnspan=3, sticky=tk.W, pady=(0, 5))
        anchor_cb.bind("<<ComboboxSelected>>", self.on_anchor_change)

        ttk.Label(pos_frame, text="X:").grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(pos_frame, textvariable=self.selected_x_var, width=12).grid(row=1, column=1, padx=(0, 10),
                                                                              sticky=tk.W)
        ttk.Label(pos_frame, text="Y:").grid(row=1, column=2, sticky=tk.W)
        ttk.Entry(pos_frame, textvariable=self.selected_y_var, width=12).grid(row=1, column=3, sticky=tk.W)

        ttk.Button(pos_frame, text="Apply Position", command=self.apply_manual_position).grid(row=2, column=0,
                                                                                              columnspan=4,
                                                                                              pady=(10, 0),
                                                                                              sticky=tk.EW)

        align_frame = ttk.LabelFrame(left_frame, text="1d. Align & Distribute (Select Multiple)", padding=10)
        align_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Button(align_frame, text="⇤ Align Left", command=lambda: self.align_selected('left')).grid(row=0, column=0,
                                                                                                       sticky=tk.EW,
                                                                                                       padx=2, pady=2)
        ttk.Button(align_frame, text="⇹ Align Center X", command=lambda: self.align_selected('center_x')).grid(row=0,
                                                                                                               column=1,
                                                                                                               sticky=tk.EW,
                                                                                                               padx=2,
                                                                                                               pady=2)
        ttk.Button(align_frame, text="⇥ Align Right", command=lambda: self.align_selected('right')).grid(row=0,
                                                                                                         column=2,
                                                                                                         sticky=tk.EW,
                                                                                                         padx=2, pady=2)

        ttk.Button(align_frame, text="⇡ Align Top", command=lambda: self.align_selected('top')).grid(row=1, column=0,
                                                                                                     sticky=tk.EW,
                                                                                                     padx=2, pady=2)
        ttk.Button(align_frame, text="↕ Align Center Y", command=lambda: self.align_selected('center_y')).grid(row=1,
                                                                                                               column=1,
                                                                                                               sticky=tk.EW,
                                                                                                               padx=2,
                                                                                                               pady=2)
        ttk.Button(align_frame, text="⇣ Align Bottom", command=lambda: self.align_selected('bottom')).grid(row=1,
                                                                                                           column=2,
                                                                                                           sticky=tk.EW,
                                                                                                           padx=2,
                                                                                                           pady=2)

        ttk.Button(align_frame, text="𝌸 Distribute H (Equal Gap)", command=lambda: self.distribute_selected('h')).grid(
            row=2, column=0, columnspan=2, sticky=tk.EW, padx=2, pady=2)
        ttk.Button(align_frame, text="𝌆 Distribute V", command=lambda: self.distribute_selected('v')).grid(row=2,
                                                                                                           column=2,
                                                                                                           columnspan=1,
                                                                                                           sticky=tk.EW,
                                                                                                           padx=2,
                                                                                                           pady=2)

        for i in range(3):
            align_frame.columnconfigure(i, weight=1)

        output_frame = ttk.LabelFrame(left_frame, text="2. Export Settings", padding=10)
        output_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(output_frame, text="Merged Top Cell Name:").pack(anchor=tk.W)
        ttk.Entry(output_frame, textvariable=self.top_cell_name_var).pack(fill=tk.X, pady=5)
        ttk.Button(output_frame, text="💾 Export Merged GDS", command=self.execute_stitch).pack(fill=tk.X, pady=5,
                                                                                               ipady=5)

        right_frame = ttk.LabelFrame(main_frame, text="Interactive Canvas", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

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

    def get_bbox(self, gds):
        t_box = gds['trans'] * gds['base_bbox']
        l = t_box.left + gds['offset_x']
        r = t_box.right + gds['offset_x']
        b = t_box.bottom + gds['offset_y']
        t = t_box.top + gds['offset_y']
        return l, r, b, t

    def align_selected(self, mode):
        selection = self.listbox.curselection()
        if len(selection) < 2:
            messagebox.showwarning("Warning", "Please select at least 2 GDS files (Ctrl/Shift + Click) to align.")
            return

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
            overall_l = min(b[0] for b in bboxes)
            overall_r = max(b[1] for b in bboxes)
            target = (overall_l + overall_r) / 2
            for i in selection:
                bbox = self.get_bbox(self.gds_list[i])
                cy = (bbox[2] + bbox[3]) / 2
                self.set_anchor_coords(self.gds_list[i], 'Center', target, cy)
        elif mode == 'bottom':
            target = min(b[2] for b in bboxes)
            for i in selection: self.set_anchor_coords(self.gds_list[i], 'Bottom-Left',
                                                       self.get_bbox(self.gds_list[i])[0], target)
        elif mode == 'top':
            target = max(b[3] for b in bboxes)
            for i in selection: self.set_anchor_coords(self.gds_list[i], 'Top-Left', self.get_bbox(self.gds_list[i])[0],
                                                       target)
        elif mode == 'center_y':
            overall_b = min(b[2] for b in bboxes)
            overall_t = max(b[3] for b in bboxes)
            target = (overall_b + overall_t) / 2
            for i in selection:
                bbox = self.get_bbox(self.gds_list[i])
                cx = (bbox[0] + bbox[1]) / 2
                self.set_anchor_coords(self.gds_list[i], 'Center', cx, target)

        self.draw_preview(reset_view=False)
        self.on_listbox_select()
        self.status_var.set(f"Successfully aligned {len(selection)} items ({mode}).")

    def distribute_selected(self, axis):
        selection = self.listbox.curselection()
        if len(selection) < 3:
            messagebox.showwarning("Warning", "Please select at least 3 GDS files to distribute evenly.")
            return

        items = []
        for i in selection:
            gds = self.gds_list[i]
            l, r, b, t = self.get_bbox(gds)
            items.append({'idx': i, 'gds': gds, 'l': l, 'r': r, 'b': b, 't': t, 'w': r - l, 'h': t - b})

        if axis == 'h':
            items.sort(key=lambda item: item['l'])
            L_bound = items[0]['l']
            R_bound = items[-1]['r']
            total_w = sum(item['w'] for item in items)
            gap = (R_bound - L_bound - total_w) / (len(items) - 1)

            cur_x = L_bound
            for item in items:
                self.set_anchor_coords(item['gds'], 'Bottom-Left', cur_x, item['b'])
                cur_x += item['w'] + gap

        elif axis == 'v':
            items.sort(key=lambda item: item['b'])
            B_bound = items[0]['b']
            T_bound = items[-1]['t']
            total_h = sum(item['h'] for item in items)
            gap = (T_bound - B_bound - total_h) / (len(items) - 1)

            cur_y = B_bound
            for item in items:
                self.set_anchor_coords(item['gds'], 'Bottom-Left', item['l'], cur_y)
                cur_y += item['h'] + gap

        self.draw_preview(reset_view=False)
        self.on_listbox_select()
        self.status_var.set(f"Distributed {len(selection)} items with equal spacing ({axis}).")

    def on_measure_toggle(self):
        if self.measure_mode_var.get():
            self.status_var.set("Measure Mode ON: Click once to set start point, click again to finish.")
            self.listbox.selection_clear(0, tk.END)
        else:
            self.status_var.set("Measure Mode OFF.")
            self.clear_active_measurement()
            self.canvas.draw_idle()

    def action_clear_measurements(self):
        self.measurements.clear()
        self.clear_active_measurement()
        self.draw_preview(reset_view=False)
        self.status_var.set("All measurements cleared.")

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

    def get_anchor_coords(self, gds, anchor_type):
        t_box = gds['trans'] * gds['base_bbox']
        ox, oy = gds['offset_x'], gds['offset_y']
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

    def on_anchor_change(self, event=None):
        self.on_listbox_select()

    def apply_manual_position(self):
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a GDS from the list first.")
            return
        idx = selection[0]
        try:
            new_x = float(self.selected_x_var.get())
            new_y = float(self.selected_y_var.get())
            anchor = self.anchor_var.get()
            self.set_anchor_coords(self.gds_list[idx], anchor, new_x, new_y)
            self.draw_preview(reset_view=False)
            self.status_var.set(f"{anchor} of {self.gds_list[idx]['name']} updated to ({new_x}, {new_y})")
        except ValueError:
            messagebox.showerror("Error", "Invalid coordinate values. Please enter numbers.")

    def parse_gds_info(self, filepath):
        layout = db.Layout()
        layout.read(filepath)
        top_cell = layout.top_cells()[0]
        base_bbox = top_cell.dbbox()

        region = db.Region()
        for li in layout.layer_indexes():
            region.insert(top_cell.begin_shapes_rec(li))

        region.merge()
        region = region.hulls()

        dbu = layout.dbu
        trans = db.DCplxTrans(dbu)
        true_polygons = []
        for poly in region.each():
            dpoly = db.DPolygon(poly).transformed(trans)
            pts = [(pt.x, pt.y) for pt in dpoly.each_point_hull()]
            if pts:
                true_polygons.append(pts)

        return base_bbox, true_polygons

    def update_block_size(self):
        try:
            self.block_width = float(self.block_width_var.get())
            self.block_height = float(self.block_height_var.get())
            self.draw_preview(reset_view=True)
        except:
            messagebox.showerror("Error", "Invalid dimensions")

    def add_gds(self):
        paths = filedialog.askopenfilenames(filetypes=[("GDS Files", "*.gds")])
        for p in paths:
            try:
                base_name = os.path.splitext(os.path.basename(p))[0]
                self.status_var.set(f"Parsing true contour for {base_name}... Please wait.")
                self.root.update()

                base_bbox, true_polygons = self.parse_gds_info(p)

                gds_info = {
                    'path': p, 'name': base_name,
                    'base_bbox': base_bbox, 'trans': db.DTrans(),
                    'offset_x': 0.0, 'offset_y': 0.0,
                    'color': self.color_palette[len(self.gds_list) % len(self.color_palette)],
                    'patch': None, 'center_text': None,
                    'true_polygons': true_polygons,
                    'poly_patches': []
                }
                self.gds_list.append(gds_info)
                self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {base_name}")
                self.status_var.set("Ready.")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        if paths: self.draw_preview(reset_view=True)

    def action_delete_selected(self):
        selection = self.listbox.curselection()
        if selection:
            for idx in sorted(selection, reverse=True):
                del self.gds_list[idx]
            self.listbox.delete(0, tk.END)
            for i, gds in enumerate(self.gds_list):
                self.listbox.insert(tk.END, f"[{i + 1}] {gds['name']}")
            self.selected_x_var.set("0.0")
            self.selected_y_var.set("0.0")
            self.draw_preview(reset_view=False)

    def draw_preview(self, reset_view=False):
        cur_xlim, cur_ylim = self.ax.get_xlim(), self.ax.get_ylim()
        self.ax.clear()

        self.measure_line = None
        self.measure_text = None
        self.snap_indicator = None
        self.measure_state = 0
        self.measure_start_pt = None

        for spine in self.ax.spines.values(): spine.set_visible(False)

        grid_color = '#cccccc'
        grid_alpha = 0.4
        self.ax.tick_params(axis='both', which='both', length=4, width=0.8, direction='out', colors=grid_color,
                            labelcolor='#999999')
        self.ax.set_axisbelow(True)

        ### 修改的部分：专业化的 Wafer Block 设计 ###
        block_rect = patches.Rectangle((0, 0), self.block_width, self.block_height,
                                       linewidth=1.5, edgecolor='#2c3e50', facecolor='#f4f7f9',
                                       linestyle='-.', zorder=0)
        self.ax.add_patch(block_rect)

        # 原点标识
        self.ax.plot(0, 0, marker='+', color='#2c3e50', markersize=15, markeredgewidth=1.5, zorder=1)

        # 边界尺寸提示
        if self.block_width > 0 and self.block_height > 0:
            self.ax.text(0, self.block_height + self.block_height * 0.01,
                         f'Wafer Block ({self.block_width} x {self.block_height} um)',
                         ha='left', va='bottom', color='#2c3e50', fontsize=10, fontweight='bold', alpha=0.7)

        if not self.gds_list:
            self.ax.text(self.block_width / 2, self.block_height / 2, 'No GDS Loaded', ha='center', va='center',
                         color='#bbbbbb', fontsize=12)

        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            sx, sy = t_box.left + gds['offset_x'], t_box.bottom + gds['offset_y']
            w, h = t_box.width(), t_box.height()

            rect = patches.Rectangle((sx, sy), w, h, linewidth=0.5, edgecolor=gds['color'], facecolor=gds['color'],
                                     alpha=0.2, zorder=10)
            self.ax.add_patch(rect)
            gds['patch'] = rect
            gds['poly_patches'] = []

            for pts in gds['true_polygons']:
                transformed_pts = []
                for px, py in pts:
                    t_pt = gds['trans'] * db.DPoint(px, py)
                    transformed_pts.append((t_pt.x + gds['offset_x'], t_pt.y + gds['offset_y']))

                poly_patch = patches.Polygon(transformed_pts, closed=True, fill=False, edgecolor=gds['color'],
                                             linestyle='--', linewidth=0.8, alpha=0.9, zorder=15)
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
            self.ax.plot([x0, x1], [y0, y1], color='#FF1493', linestyle='--', linewidth=2, zorder=300)
            dist = math.hypot(x1 - x0, y1 - y0)
            dx = abs(x1 - x0)
            dy = abs(y1 - y0)
            self.ax.text(x1, y1, f" L: {dist:.2f}\n dx: {dx:.2f}\n dy: {dy:.2f}", color='#FF1493', fontsize=10,
                         fontweight='bold', zorder=301,
                         bbox=dict(facecolor='white', alpha=0.8, edgecolor='none', pad=2))

        if reset_view:
            self.ax.set_xlim(-self.block_width * 0.1, self.block_width * 1.1)
            self.ax.set_ylim(-self.block_height * 0.1, self.block_height * 1.1)
        else:
            self.ax.set_xlim(cur_xlim);
            self.ax.set_ylim(cur_ylim)

        self.ax.set_aspect('equal', adjustable='datalim')
        self.ax.grid(True, linestyle='-', color=grid_color, alpha=grid_alpha)
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
        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()
        snap_thresh_x = (cur_xlim[1] - cur_xlim[0]) * 0.02
        snap_thresh_y = (cur_ylim[1] - cur_ylim[0]) * 0.02

        best_x, best_y = x, y
        min_dx, min_dy = snap_thresh_x, snap_thresh_y
        snapped_x, snapped_y = False, False

        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            ox, oy = gds['offset_x'], gds['offset_y']
            l, r = t_box.left + ox, t_box.right + ox
            b, t = t_box.bottom + oy, t_box.top + oy
            cx, cy = (l + r) / 2, (b + t) / 2

            for px in [l, r, cx]:
                if abs(x - px) < min_dx:
                    min_dx = abs(x - px)
                    best_x = px
                    snapped_x = True

            for py in [b, t, cy]:
                if abs(y - py) < min_dy:
                    min_dy = abs(y - py)
                    best_y = py
                    snapped_y = True

        return best_x, best_y, snapped_x, snapped_y

    def on_press(self, event):
        if not event.inaxes: return

        if self.measure_mode_var.get() and event.button == 1:
            snap_x, snap_y, _, _ = self.get_snapped_coordinate(event.xdata, event.ydata)

            if self.measure_state == 0:
                self.clear_active_measurement()
                self.measure_start_pt = (snap_x, snap_y)

                self.measure_line, = self.ax.plot([snap_x, snap_x], [snap_y, snap_y], color='#FF1493', linestyle='--',
                                                  linewidth=2, zorder=300)
                self.measure_text = self.ax.text(snap_x, snap_y, '', color='#FF1493', fontsize=10, fontweight='bold',
                                                 zorder=301,
                                                 bbox=dict(facecolor='white', alpha=0.8, edgecolor='none', pad=2))
                self.snap_indicator, = self.ax.plot([snap_x], [snap_y], marker='+', color='red', markersize=12,
                                                    markeredgewidth=2, zorder=305)
                self.measure_state = 1
            elif self.measure_state == 1:
                x0, y0 = self.measure_start_pt
                self.measurements.append({'x0': x0, 'y0': y0, 'x1': snap_x, 'y1': snap_y})

                self.measure_state = 0
                if self.snap_indicator:
                    self.snap_indicator.set_data([], [])

                self.measure_line = None
                self.measure_text = None

            self.canvas.draw_idle()
            return

        for line in self.guide_lines: line.remove()
        self.guide_lines.clear()

        for i in range(len(self.gds_list) - 1, -1, -1):
            if self.gds_list[i]['patch'].contains(event)[0]:
                if event.button in [2, 3]:
                    self.show_context_menu(i)
                elif event.button == 1:
                    self.dragging_idx = i
                    self.drag_start_x, self.drag_start_y = event.xdata, event.ydata
                    self.rect_start_x, self.rect_start_y = self.gds_list[i]['patch'].get_x(), self.gds_list[i][
                        'patch'].get_y()
                    self.gds_list[i]['patch'].set_alpha(0.6)
                    self.listbox.selection_clear(0, tk.END)
                    self.listbox.selection_set(i)
                    self.on_listbox_select()
                return

    def on_motion(self, event):
        if not event.inaxes: return

        if self.measure_mode_var.get():
            snap_x, snap_y, sn_x, sn_y = self.get_snapped_coordinate(event.xdata, event.ydata)

            if not self.snap_indicator:
                self.snap_indicator, = self.ax.plot([snap_x], [snap_y], marker='+', color='red', markersize=12,
                                                    markeredgewidth=2, zorder=305)
            else:
                if self.measure_state == 0 or (self.measure_state == 1):
                    self.snap_indicator.set_data([snap_x], [snap_y])

            for line in self.guide_lines: line.remove()
            self.guide_lines.clear()

            if sn_x: self.guide_lines.append(
                self.ax.axvline(x=snap_x, color='#00CED1', linestyle=':', linewidth=1.5, zorder=200))
            if sn_y: self.guide_lines.append(
                self.ax.axhline(y=snap_y, color='#00CED1', linestyle=':', linewidth=1.5, zorder=200))

            if self.measure_state == 1 and self.measure_start_pt is not None:
                x0, y0 = self.measure_start_pt
                self.measure_line.set_data([x0, snap_x], [y0, snap_y])
                dist = math.hypot(snap_x - x0, snap_y - y0)
                dx = abs(snap_x - x0)
                dy = abs(snap_y - y0)

                self.measure_text.set_position((snap_x, snap_y))
                self.measure_text.set_text(f" L: {dist:.2f}\n dx: {dx:.2f}\n dy: {dy:.2f}")

            self.canvas.draw_idle()
            return

        if self.dragging_idx == -1: return

        for line in self.guide_lines: line.remove()
        self.guide_lines.clear()

        gds = self.gds_list[self.dragging_idx]
        t_box = gds['trans'] * gds['base_bbox']

        nx_proposed = self.rect_start_x + (event.xdata - self.drag_start_x)
        ny_proposed = self.rect_start_y + (event.ydata - self.drag_start_y)
        temp_ox = nx_proposed - t_box.left
        temp_oy = ny_proposed - t_box.bottom

        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()
        snap_thresh_x = (cur_xlim[1] - cur_xlim[0]) * 0.02
        snap_thresh_y = (cur_ylim[1] - cur_ylim[0]) * 0.02

        drag_x_pois, drag_y_pois = self.get_pois(gds, temp_ox, temp_oy)

        best_snap_x = None
        best_snap_y = None
        min_dx = snap_thresh_x
        min_dy = snap_thresh_y
        snap_shift_x = 0
        snap_shift_y = 0

        for i, other_gds in enumerate(self.gds_list):
            if i == self.dragging_idx: continue
            other_x_pois, other_y_pois = self.get_pois(other_gds)

            for dx in drag_x_pois:
                for ox in other_x_pois:
                    if abs(dx - ox) < min_dx: min_dx, best_snap_x, snap_shift_x = abs(dx - ox), ox, ox - dx

            for dy in drag_y_pois:
                for oy in other_y_pois:
                    if abs(dy - oy) < min_dy: min_dy, best_snap_y, snap_shift_y = abs(dy - oy), oy, oy - dy

        final_ox = temp_ox + snap_shift_x
        final_oy = temp_oy + snap_shift_y

        gds['offset_x'], gds['offset_y'] = final_ox, final_oy
        nx_final, ny_final = t_box.left + final_ox, t_box.bottom + final_oy
        gds['patch'].set_x(nx_final);
        gds['patch'].set_y(ny_final)

        for pts, poly_patch in gds['poly_patches']:
            new_transformed_pts = []
            for px, py in pts:
                t_pt = gds['trans'] * db.DPoint(px, py)
                new_transformed_pts.append((t_pt.x + final_ox, t_pt.y + final_oy))
            poly_patch.set_xy(new_transformed_pts)

        if gds['center_text']: gds['center_text'].set_position(
            (nx_final + t_box.width() / 2, ny_final + t_box.height() / 2))

        if best_snap_x is not None: self.guide_lines.append(
            self.ax.axvline(x=best_snap_x, color='#FF8C00', linestyle='--', linewidth=1.5, zorder=200))
        if best_snap_y is not None: self.guide_lines.append(
            self.ax.axhline(y=best_snap_y, color='#FF8C00', linestyle='--', linewidth=1.5, zorder=200))

        selection = self.listbox.curselection()
        if selection and selection[0] == self.dragging_idx:
            anchor = self.anchor_var.get()
            x, y = self.get_anchor_coords(gds, anchor)
            self.selected_x_var.set(f"{x:.3f}");
            self.selected_y_var.set(f"{y:.3f}")

        self.canvas.draw_idle()

    def on_release(self, event):
        if self.measure_mode_var.get() and event.button == 1:
            return

        if self.dragging_idx != -1:
            self.gds_list[self.dragging_idx]['patch'].set_alpha(0.2)
            self.dragging_idx = -1
            for line in self.guide_lines: line.remove()
            self.guide_lines.clear()
            self.canvas.draw_idle()

    def show_context_menu(self, idx):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Duplicate {self.gds_list[idx]['name']}", command=lambda: self.action_duplicate(idx))
        menu.add_separator()
        menu.add_command(label="Rotate 90 CCW", command=lambda: self.action_rotate_ccw(idx))
        menu.add_command(label="Rotate 90 CW", command=lambda: self.action_rotate_cw(idx))
        menu.add_command(label="Flip H", command=lambda: self.action_flip_horizontal(idx))
        menu.add_command(label="Flip V", command=lambda: self.action_flip_vertical(idx))
        menu.post(int(self.root.winfo_pointerx()), int(self.root.winfo_pointery()))

    def action_duplicate(self, idx):
        o = self.gds_list[idx]
        new_gds = {
            'path': o['path'], 'name': o['name'],
            'base_bbox': o['base_bbox'], 'trans': o['trans'] * db.DTrans(),
            'offset_x': o['offset_x'] + 200, 'offset_y': o['offset_y'] - 200,
            'color': o['color'], 'patch': None, 'center_text': None,
            'true_polygons': o['true_polygons'],
            'poly_patches': []
        }
        self.gds_list.append(new_gds)
        self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {o['name']}")
        self.draw_preview()

    def action_rotate_ccw(self, i):
        self.gds_list[i]['trans'] = db.DTrans(1, False, 0, 0) * self.gds_list[i][
            'trans']; self.draw_preview(); self.on_listbox_select()

    def action_rotate_cw(self, i):
        self.gds_list[i]['trans'] = db.DTrans(3, False, 0, 0) * self.gds_list[i][
            'trans']; self.draw_preview(); self.on_listbox_select()

    def action_flip_horizontal(self, i):
        self.gds_list[i]['trans'] = db.DTrans(2, True, 0, 0) * self.gds_list[i][
            'trans']; self.draw_preview(); self.on_listbox_select()

    def action_flip_vertical(self, i):
        self.gds_list[i]['trans'] = db.DTrans(0, True, 0, 0) * self.gds_list[i][
            'trans']; self.draw_preview(); self.on_listbox_select()

    def execute_stitch(self):
        if not self.gds_list: return
        out_p = filedialog.asksaveasfilename(defaultextension=".gds")
        if not out_p: return
        try:
            layout = db.Layout();
            merged = layout.create_cell(self.top_cell_name_var.get() or "MERGED")
            cache = {}
            for g in self.gds_list:
                if g['path'] not in cache:
                    old = [c.cell_index() for c in layout.top_cells()]
                    layout.read(g['path'])
                    new = [c for c in layout.top_cells() if c.cell_index() not in old and c.name != merged.name]
                    if new: cache[g['path']] = new[0].cell_index()
                merged.insert(db.DCellInstArray(cache[g['path']], db.DTrans(g['offset_x'], g['offset_y']) * g['trans']))
            layout.write(out_p);
            messagebox.showinfo("OK", "Merged Success!")
        except Exception as e:
            messagebox.showerror("Error", str(e))


if __name__ == "__main__":
    GDSMultiStitcherApp().root.mainloop()