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
        window_height = 720
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

        self.block_width_var = tk.StringVar(value="5000.0")
        self.block_height_var = tk.StringVar(value="5000.0")
        self.block_width = 5000.0
        self.block_height = 5000.0

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

        # 1. GDS File List
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

        ttk.Label(block_settings_frame, text="Block Width/Height (μm):").pack(anchor=tk.W)
        entry_f = ttk.Frame(block_settings_frame)
        entry_f.pack(fill=tk.X)
        ttk.Entry(entry_f, textvariable=self.block_width_var, width=10).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(entry_f, text=" x ").pack(side=tk.LEFT)
        ttk.Entry(entry_f, textvariable=self.block_height_var, width=10).pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(block_settings_frame, text="Apply Block Size", command=self.update_block_size).pack(fill=tk.X,
                                                                                                       pady=(5, 2))
        ttk.Button(block_settings_frame, text="🔍 Zoom Fit (Show Block)",
                   command=lambda: self.draw_preview(reset_view=True)).pack(fill=tk.X)

        # 2. Export Settings
        output_frame = ttk.LabelFrame(left_frame, text="2. Export Settings", padding=10)
        output_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(output_frame, text="Merged Top Cell Name:").pack(anchor=tk.W)
        ttk.Entry(output_frame, textvariable=self.top_cell_name_var).pack(fill=tk.X, pady=5)
        ttk.Button(output_frame, text="💾 Export Merged GDS", command=self.execute_stitch).pack(fill=tk.X, pady=5,
                                                                                               ipady=5)

        # Right Panel
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

    def extract_base_bbox(self, filepath):
        layout = db.Layout()
        layout.read(filepath)
        return layout.top_cells()[0].dbbox()

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
                gds_info = {
                    'path': p, 'name': base_name,
                    'base_bbox': self.extract_base_bbox(p), 'trans': db.DTrans(),
                    'offset_x': 0.0, 'offset_y': 0.0,
                    'color': self.color_palette[len(self.gds_list) % len(self.color_palette)],
                    'patch': None, 'texts': {}, 'center_text': None
                }
                self.gds_list.append(gds_info)
                self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {base_name}")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        if paths: self.draw_preview(reset_view=True)

    def action_delete_selected(self):
        selection = self.listbox.curselection()
        if selection:
            idx = selection[0]
            del self.gds_list[idx]
            self.listbox.delete(0, tk.END)
            for i, gds in enumerate(self.gds_list):
                self.listbox.insert(tk.END, f"[{i + 1}] {gds['name']}")
            self.draw_preview(reset_view=False)

    def draw_preview(self, reset_view=False):
        cur_xlim, cur_ylim = self.ax.get_xlim(), self.ax.get_ylim()
        self.ax.clear()

        # --- 核心修改：隐藏轴线，并将刻度颜色/透明度设为与网格一致 ---
        for spine in self.ax.spines.values():
            spine.set_visible(False)

        # 统一颜色
        grid_color = '#cccccc'
        grid_alpha = 0.4

        # 设置刻度线与刻度标签的颜色与透明度
        self.ax.tick_params(axis='both', which='both',
                            length=4,
                            width=0.8,
                            direction='out',
                            colors=grid_color,  # 刻度线颜色
                            labelcolor='#999999')  # 刻度文本颜色（略深一点保证可读性）

        # 绘制 Block 红框
        self.ax.add_patch(patches.Rectangle((0, 0), self.block_width, self.block_height, linewidth=2, edgecolor='red',
                                            facecolor='none', zorder=1))

        if not self.gds_list:
            self.ax.text(self.block_width / 2, self.block_height / 2, 'No GDS Loaded', ha='center', va='center',
                         color='#bbbbbb', fontsize=12)

        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            sx, sy = t_box.left + gds['offset_x'], t_box.bottom + gds['offset_y']
            w, h = t_box.width(), t_box.height()

            rect = patches.Rectangle((sx, sy), w, h, linewidth=1.5, edgecolor=gds['color'], facecolor=gds['color'],
                                     alpha=0.3, zorder=10)
            self.ax.add_patch(rect)
            gds['patch'] = rect
            gds['texts'] = {}

            box_min = min(w, h)
            ratio = box_min / min(self.block_width, self.block_height) if min(self.block_width,
                                                                              self.block_height) > 0 else 1.0
            dynamic_fs = max(6, min(35, int(6 + 18 * ratio)))

            bbox = gds['base_bbox']
            cx_l, cy_l = (bbox.left + bbox.right) / 2, (bbox.bottom + bbox.top) / 2
            pts = {'N': (cx_l, bbox.top), 'S': (cx_l, bbox.bottom), 'E': (bbox.right, cy_l), 'W': (bbox.left, cy_l)}

            for label, pt_coords in pts.items():
                t_pt = gds['trans'] * db.DPoint(*pt_coords)
                wx, wy = t_pt.x + gds['offset_x'], t_pt.y + gds['offset_y']
                ha, va = 'center', 'center'
                if abs(wx - (sx + w)) < box_min * 0.01:
                    ha = 'right'
                elif abs(wx - sx) < box_min * 0.01:
                    ha = 'left'
                if abs(wy - (sy + h)) < box_min * 0.01:
                    va = 'top'
                elif abs(wy - sy) < box_min * 0.01:
                    va = 'bottom'
                gds['texts'][label] = self.ax.text(wx, wy, label, ha=ha, va=va, fontsize=dynamic_fs, fontweight='bold',
                                                   zorder=100)

            gds['center_text'] = self.ax.text(sx + w / 2, sy + h / 2, gds['name'], ha='center', va='center',
                                              fontsize=dynamic_fs, color='black', fontweight='bold', alpha=0.7,
                                              zorder=90)

        if reset_view:
            self.ax.set_xlim(-self.block_width * 0.1, self.block_width * 1.1)
            self.ax.set_ylim(-self.block_height * 0.1, self.block_height * 1.1)
        else:
            self.ax.set_xlim(cur_xlim);
            self.ax.set_ylim(cur_ylim)

        self.ax.set_aspect('equal', adjustable='datalim')
        # 统一网格样式
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

    def on_press(self, event):
        if not event.inaxes: return
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
                return

    def on_motion(self, event):
        if self.dragging_idx == -1 or not event.inaxes: return
        gds = self.gds_list[self.dragging_idx]
        nx = self.rect_start_x + (event.xdata - self.drag_start_x)
        ny = self.rect_start_y + (event.ydata - self.drag_start_y)
        gds['patch'].set_x(nx);
        gds['patch'].set_y(ny)
        t_box = gds['trans'] * gds['base_bbox']
        gds['offset_x'], gds['offset_y'] = nx - t_box.left, ny - t_box.bottom

        bbox = gds['base_bbox']
        pts = {'N': ((bbox.left + bbox.right) / 2, bbox.top), 'S': ((bbox.left + bbox.right) / 2, bbox.bottom),
               'E': (bbox.right, (bbox.top + bbox.bottom) / 2), 'W': (bbox.left, (bbox.top + bbox.bottom) / 2)}
        for label, txt in gds['texts'].items():
            t_pt = gds['trans'] * db.DPoint(*pts[label])
            txt.set_position((t_pt.x + gds['offset_x'], t_pt.y + gds['offset_y']))
        if gds['center_text']:
            gds['center_text'].set_position((nx + t_box.width() / 2, ny + t_box.height() / 2))
        self.canvas.draw_idle()

    def on_release(self, event):
        if self.dragging_idx != -1:
            self.gds_list[self.dragging_idx]['patch'].set_alpha(0.3)
            self.dragging_idx = -1
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
            'color': o['color'], 'patch': None, 'texts': {}, 'center_text': None
        }
        self.gds_list.append(new_gds)
        self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {o['name']}")
        self.draw_preview()

    def action_rotate_ccw(self, i):
        self.gds_list[i]['trans'] = db.DTrans(1, False, 0, 0) * self.gds_list[i]['trans']; self.draw_preview()

    def action_rotate_cw(self, i):
        self.gds_list[i]['trans'] = db.DTrans(3, False, 0, 0) * self.gds_list[i]['trans']; self.draw_preview()

    def action_flip_horizontal(self, i):
        self.gds_list[i]['trans'] = db.DTrans(2, True, 0, 0) * self.gds_list[i]['trans']; self.draw_preview()

    def action_flip_vertical(self, i):
        self.gds_list[i]['trans'] = db.DTrans(0, True, 0, 0) * self.gds_list[i]['trans']; self.draw_preview()

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