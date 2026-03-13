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

# Matplotlib global font settings
plt.rcParams['font.family'] = ['sans-serif']
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans', 'Arial', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False


class GDSMultiStitcherApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("GDS MERGER 1.0")

        window_width = 1150
        window_height = 720  # 稍微增加高度以容纳新按键
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x_cordinate = int((screen_width / 2) - (window_width / 2))
        y_cordinate = int((screen_height / 2) - (window_height / 2))
        self.root.geometry(f"{window_width}x{window_height}+{x_cordinate}+{y_cordinate}")

        # Core data structure
        self.gds_list = []

        # Interaction state variables
        self.current_selected_idx = -1
        self.dragging_idx = -1
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.rect_start_x = 0
        self.rect_start_y = 0

        # UI Variables
        self.block_width_var = tk.StringVar(value="5000.0")
        self.block_height_var = tk.StringVar(value="5000.0")
        self.block_width = 5000.0
        self.block_height = 5000.0

        self.top_cell_name_var = tk.StringVar(value="MERGED_CHIP")
        self.status_var = tk.StringVar(value="Ready: Please add GDS files.")
        self.color_palette = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22',
                              '#17becf']

        self.setup_ui()

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ==================== Left Panel: Controls ====================
        left_frame = ttk.Frame(main_frame, width=380)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)

        # 1. File List Management
        list_frame = ttk.LabelFrame(left_frame, text="1. GDS File List", padding=10)
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
                                  selectbackground="#0078D7")
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox.yview)

        # 1b. Block Size & Zoom Settings
        block_settings_frame = ttk.LabelFrame(left_frame, text="1b. Block Size & Zoom Settings", padding=10)
        block_settings_frame.pack(fill=tk.X, pady=(0, 10))

        width_frame = ttk.Frame(block_settings_frame)
        width_frame.pack(fill=tk.X, pady=2)
        ttk.Label(width_frame, text="Block Width (μm):").pack(side=tk.LEFT)
        self.entry_block_width = ttk.Entry(width_frame, textvariable=self.block_width_var, width=15)
        self.entry_block_width.pack(side=tk.LEFT, padx=(5, 15))

        height_frame = ttk.Frame(block_settings_frame)
        height_frame.pack(fill=tk.X, pady=2)
        ttk.Label(height_frame, text="Block Height (μm):").pack(side=tk.LEFT)
        self.entry_block_height = ttk.Entry(height_frame, textvariable=self.block_height_var, width=15)
        self.entry_block_height.pack(side=tk.LEFT, padx=5)

        self.apply_block_size_btn = ttk.Button(block_settings_frame, text="Apply Block Size",
                                               command=self.update_block_size)
        self.apply_block_size_btn.pack(fill=tk.X, pady=(5, 5))

        # --- 新增 Zoom Fit 按钮 ---
        self.zoom_fit_btn = ttk.Button(block_settings_frame, text="🔍 Zoom Fit (Show Block)",
                                       command=lambda: self.draw_preview(reset_view=True))
        self.zoom_fit_btn.pack(fill=tk.X, pady=(0, 0))

        # 2. Export Settings
        output_frame = ttk.LabelFrame(left_frame, text="2. Export Settings", padding=10)
        output_frame.pack(fill=tk.X, pady=(0, 10))

        cell_frame = ttk.Frame(output_frame)
        cell_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(cell_frame, text="Merged Top Cell Name:").pack(side=tk.LEFT)
        ttk.Entry(cell_frame, textvariable=self.top_cell_name_var, width=15).pack(side=tk.LEFT, fill=tk.X, expand=True,
                                                                                  padx=5)

        self.run_btn = ttk.Button(output_frame, text="💾 Export Merged GDS", command=self.execute_stitch)
        self.run_btn.pack(fill=tk.X, pady=(10, 0), ipady=5)

        # ==================== Right Panel: Canvas Preview ====================
        right_frame = ttk.LabelFrame(main_frame, text="Interactive Canvas (Drag Move, Scroll Zoom, Right-Click Options)",
                                     padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.figure = plt.Figure(figsize=(6, 5), dpi=100)
        self.figure.subplots_adjust(right=0.75)

        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, right_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.ax.text(0.5, 0.5, 'Right-Click a block to Copy/Rotate/Flip', ha='center', va='center', color='gray',
                     fontsize=12)
        self.ax.set_xticks([])
        self.ax.set_yticks([])

        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('button_release_event', self.on_release)
        self.canvas.mpl_connect('scroll_event', self.on_scroll)

        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=2)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def extract_base_bbox(self, filepath):
        layout = db.Layout()
        layout.read(filepath)
        if len(layout.top_cells()) == 0:
            raise ValueError("No top cells found in GDS.")
        return layout.top_cells()[0].dbbox()

    def update_block_size(self):
        try:
            w, h = float(self.block_width_var.get()), float(self.block_height_var.get())
            if w <= 0 or h <= 0: raise ValueError("Must be positive.")
            self.block_width, self.block_height = w, h
            self.status_var.set(f"Block size updated to {w}x{h} μm.")
            self.draw_preview(reset_view=True)
        except ValueError as e:
            messagebox.showerror("Format Error", str(e))

    def add_gds(self):
        filepaths = filedialog.askopenfilenames(title="Select GDS Files", filetypes=[("GDS Files", "*.gds")])
        for filepath in filepaths:
            try:
                base_bbox = self.extract_base_bbox(filepath)
                gds_info = {
                    'path': filepath, 'name': os.path.basename(filepath),
                    'base_bbox': base_bbox, 'trans': db.DTrans(),
                    'offset_x': 0.0, 'offset_y': 0.0,
                    'color': self.color_palette[len(self.gds_list) % len(self.color_palette)],
                    'patch': None, 'texts': {}
                }
                self.gds_list.append(gds_info)
                self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {gds_info['name']}")
            except Exception as e:
                messagebox.showerror("Read Error", f"Cannot read {filepath}:\n{str(e)}")

        if filepaths:
            self.draw_preview(reset_view=True)

    def show_context_menu(self, idx):
        menu = tk.Menu(self.root, tearoff=0, font=("Arial", 10))
        gds_name = self.gds_list[idx]['name']
        menu.add_command(label=f"Selected: {gds_name}", state=tk.DISABLED)
        menu.add_separator()
        menu.add_command(label="📋 Duplicate (Copy)", command=lambda: self.action_duplicate(idx))
        menu.add_separator()
        menu.add_command(label="🔄 Rotate 90° CCW", command=lambda: self.action_rotate_ccw(idx))
        menu.add_command(label="↻ Rotate 90° CW", command=lambda: self.action_rotate_cw(idx))
        menu.add_separator()
        menu.add_command(label="↔️ Flip Horizontal (L-R)", command=lambda: self.action_flip_horizontal(idx))
        menu.add_command(label="↕️ Flip Vertical (T-B)", command=lambda: self.action_flip_vertical(idx))
        menu.add_separator()
        menu.add_command(label="❌ Delete Block", command=lambda: self.action_delete(idx))
        x, y = self.root.winfo_pointerxy()
        menu.post(x, y)

    def action_duplicate(self, idx):
        orig = self.gds_list[idx]
        shift = self.block_width * 0.05 if self.block_width > 0 else 500
        new_gds = {
            'path': orig['path'], 'name': orig['name'] + "_copy",
            'base_bbox': orig['base_bbox'], 'trans': orig['trans'] * db.DTrans(),
            'offset_x': orig['offset_x'] + shift, 'offset_y': orig['offset_y'] - shift,
            'color': orig['color'], 'patch': None, 'texts': {}
        }
        self.gds_list.append(new_gds)
        self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {new_gds['name']}")
        self.draw_preview()

    def action_rotate_ccw(self, idx):
        self.gds_list[idx]['trans'] = db.DTrans(1, False, 0, 0) * self.gds_list[idx]['trans']
        self.draw_preview()

    def action_rotate_cw(self, idx):
        self.gds_list[idx]['trans'] = db.DTrans(3, False, 0, 0) * self.gds_list[idx]['trans']
        self.draw_preview()

    def action_flip_horizontal(self, idx):
        self.gds_list[idx]['trans'] = db.DTrans(2, True, 0, 0) * self.gds_list[idx]['trans']
        self.draw_preview()

    def action_flip_vertical(self, idx):
        self.gds_list[idx]['trans'] = db.DTrans(0, True, 0, 0) * self.gds_list[idx]['trans']
        self.draw_preview()

    def action_delete(self, idx):
        del self.gds_list[idx]
        self.listbox.delete(0, tk.END)
        for i, gds in enumerate(self.gds_list):
            self.listbox.insert(tk.END, f"[{i + 1}] {gds['name']}")
        self.draw_preview(reset_view=True)

    def action_delete_selected(self):
        selection = self.listbox.curselection()
        if selection: self.action_delete(selection[0])

    def draw_preview(self, reset_view=False):
        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()
        self.ax.clear()

        # 背景主框
        rect_block = patches.Rectangle((0, 0), self.block_width, self.block_height,
                                       linewidth=5, edgecolor='black', facecolor='none', zorder=1)
        self.ax.add_patch(rect_block)

        if not self.gds_list:
            self.ax.text(self.block_width / 2, self.block_height / 2, 'Add GDS Files\nScroll: Zoom | Drag: Move',
                         ha='center', va='center', color='gray', fontsize=12)
            reset_view = True

        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            start_x, start_y = t_box.left + gds['offset_x'], t_box.bottom + gds['offset_y']
            rect = patches.Rectangle((start_x, start_y), t_box.width(), t_box.height(),
                                     linewidth=2, edgecolor=gds['color'], facecolor=gds['color'],
                                     alpha=0.3, zorder=10)
            self.ax.add_patch(rect)
            gds['patch'] = rect

            # NSEW 文本绘制
            bbox = gds['base_bbox']
            cx, cy = (bbox.left + bbox.right) / 2, (bbox.bottom + bbox.top) / 2
            pts = {'N': db.DPoint(cx, bbox.top), 'S': db.DPoint(cx, bbox.bottom),
                   'E': db.DPoint(bbox.right, cy), 'W': db.DPoint(bbox.left, cy)}

            box_min_edge = min(t_box.width(), t_box.height())
            canvas_min_edge = min(self.block_width, self.block_height)
            ratio = box_min_edge / canvas_min_edge if canvas_min_edge > 0 else 1.0
            dynamic_fontsize = max(4, min(40, int(4 + 10 * ratio)))

            box_l, box_r = start_x, start_x + t_box.width()
            box_b, box_t = start_y, start_y + t_box.height()
            tol = box_min_edge * 0.001

            for label, pt in pts.items():
                t_pt = gds['trans'] * pt
                wx, wy = t_pt.x + gds['offset_x'], t_pt.y + gds['offset_y']
                ha, va = 'center', 'center'
                if abs(wx - box_r) < tol: ha = 'right'
                elif abs(wx - box_l) < tol: ha = 'left'
                if abs(wy - box_t) < tol: va = 'top'
                elif abs(wy - box_b) < tol: va = 'bottom'

                txt = self.ax.text(wx, wy, label, ha=ha, va=va, color='black',
                                   fontweight='bold', fontsize=dynamic_fontsize, zorder=100)
                gds['texts'][label] = txt

        if reset_view:
            pad_x, pad_y = self.block_width * 0.1, self.block_height * 0.1
            self.ax.set_xlim(0 - pad_x, self.block_width + pad_x)
            self.ax.set_ylim(0 - pad_y, self.block_height + pad_y)
        else:
            self.ax.set_xlim(cur_xlim)
            self.ax.set_ylim(cur_ylim)

        self.ax.set_aspect('equal', adjustable='datalim')
        self.ax.grid(True, linestyle='--', alpha=0.3)
        self.canvas.draw()

    def on_scroll(self, event):
        if event.inaxes != self.ax: return
        base_scale = 1.2
        scale_factor = 1/base_scale if event.button == 'up' else base_scale
        xdata, ydata = event.xdata, event.ydata
        cur_xlim, cur_ylim = self.ax.get_xlim(), self.ax.get_ylim()
        self.ax.set_xlim([xdata - (xdata - cur_xlim[0]) * scale_factor, xdata + (cur_xlim[1] - xdata) * scale_factor])
        self.ax.set_ylim([ydata - (ydata - cur_ylim[0]) * scale_factor, ydata + (cur_ylim[1] - ydata) * scale_factor])
        self.canvas.draw_idle()

    def on_press(self, event):
        if event.inaxes != self.ax: return
        for idx in range(len(self.gds_list)-1, -1, -1):
            gds = self.gds_list[idx]
            if gds['patch'] and gds['patch'].contains(event)[0]:
                if event.button in [2, 3]: self.show_context_menu(idx)
                elif event.button == 1:
                    self.dragging_idx = idx
                    self.drag_start_x, self.drag_start_y = event.xdata, event.ydata
                    self.rect_start_x, self.rect_start_y = gds['patch'].get_x(), gds['patch'].get_y()
                    gds['patch'].set_alpha(0.6)
                    self.listbox.selection_clear(0, tk.END)
                    self.listbox.selection_set(idx)
                    self.canvas.draw_idle()
                return

    def on_motion(self, event):
        if self.dragging_idx == -1 or event.inaxes != self.ax: return
        gds = self.gds_list[self.dragging_idx]
        new_x = self.rect_start_x + (event.xdata - self.drag_start_x)
        new_y = self.rect_start_y + (event.ydata - self.drag_start_y)
        gds['patch'].set_x(new_x)
        gds['patch'].set_y(new_y)
        t_box = gds['trans'] * gds['base_bbox']
        gds['offset_x'], gds['offset_y'] = new_x - t_box.left, new_y - t_box.bottom
        if 'texts' in gds:
            bbox = gds['base_bbox']
            cx, cy = (bbox.left + bbox.right) / 2, (bbox.bottom + bbox.top) / 2
            pts = {'N': db.DPoint(cx, bbox.top), 'S': db.DPoint(cx, bbox.bottom),
                   'E': db.DPoint(bbox.right, cy), 'W': db.DPoint(bbox.left, cy)}
            for label, txt in gds['texts'].items():
                t_pt = gds['trans'] * pts[label]
                txt.set_position((t_pt.x + gds['offset_x'], t_pt.y + gds['offset_y']))
        self.canvas.draw_idle()

    def on_release(self, event):
        if self.dragging_idx != -1:
            self.gds_list[self.dragging_idx]['patch'].set_alpha(0.3)
            self.dragging_idx = -1
            self.canvas.draw_idle()

    def execute_stitch(self):
        if not self.gds_list: return
        out_path = filedialog.asksaveasfilename(defaultextension=".gds", filetypes=[("GDS Files", "*.gds")])
        if not out_path: return
        try:
            layout = db.Layout()
            merged_top = layout.create_cell(self.top_cell_name_var.get() or "MERGED")
            cache = {}
            for gds in self.gds_list:
                if gds['path'] not in cache:
                    old_idx = [c.cell_index() for c in layout.top_cells()]
                    layout.read(gds['path'])
                    new_top = [c for c in layout.top_cells() if c.cell_index() not in old_idx and c.name != merged_top.name]
                    if new_top: cache[gds['path']] = new_top[0].cell_index()
                if gds['path'] in cache:
                    final_trans = db.DTrans(gds['offset_x'], gds['offset_y']) * gds['trans']
                    merged_top.insert(db.DCellInstArray(cache[gds['path']], final_trans))
            layout.write(out_path)
            messagebox.showinfo("Success", "GDS Exported!")
        except Exception as e:
            messagebox.showerror("Error", str(e))

if __name__ == "__main__":
    app = GDSMultiStitcherApp()
    app.root.mainloop()