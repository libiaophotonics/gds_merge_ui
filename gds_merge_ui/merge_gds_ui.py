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
from matplotlib.textpath import TextPath
from matplotlib.patches import PathPatch

import klayout.db as db

# Matplotlib 全局字体设置
plt.rcParams['font.family'] = ['sans-serif']
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans', 'Arial', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False


class GDSMultiStitcherApp:
    def __init__(self):
        if DND_SUPPORTED:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()

        self.root.title("GDS MERGER Pro - Advanced Mask Prep Tool")
        try:
            self.root.state('zoomed')
        except tk.TclError:
            try:
                self.root.attributes('-zoomed', True)
            except tk.TclError:
                self.root.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")

        self.gds_list, self.measurements, self.undo_stack, self.guide_lines, self.overlap_patches = [], [], [], [], []
        self.user_texts = []
        self.user_shapes = []
        self.crop_box = None

        self.dragging_type = None
        self.dragging_idx = -1
        self.drag_start_x, self.drag_start_y, self.rect_start_x, self.rect_start_y = 0, 0, 0, 0
        self.drag_start_offsets = {}
        self.drag_snapshot_taken = False
        self.layer_mapping = {}

        self.measure_mode_var = tk.BooleanVar(value=False)
        self.measure_start_pt, self.measure_line, self.measure_text, self.snap_indicator = None, None, None, None
        self.measure_state = 0

        self.draw_mode = None
        self.draw_points = []
        self.draw_current_props = {}
        self.temp_draw_preview = None

        self.ctrl_pressed = False
        self.last_mouse_event = None

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

        self.show_overlap_var, self.bbox_only_var = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        self.grid_snap_var, self.grid_size_var = tk.BooleanVar(value=False), tk.StringVar(value="10.0")

        self.enable_dummy_var, self.staggered_var = tk.BooleanVar(value=False), tk.BooleanVar(value=True)
        self.dummy_layer_var, self.dummy_datatype_var = tk.StringVar(value="1"), tk.StringVar(value="0")
        self.dummy_size_var, self.dummy_spacing_var = tk.StringVar(value="5.0"), tk.StringVar(value="5.0")
        self.dummy_margin_var = tk.StringVar(value="3.0")

        self.enable_seal_var = tk.BooleanVar(value=False)
        self.seal_layer_var, self.seal_datatype_var = tk.StringVar(value="10"), tk.StringVar(value="0")
        self.seal_width_var, self.seal_margin_var = tk.StringVar(value="20.0"), tk.StringVar(value="0.0")

        self.top_cell_name_var = tk.StringVar(value="MERGED_CHIP")
        if DND_SUPPORTED:
            self.status_var = tk.StringVar(value="Ready: Drag and Drop GDS files into the list!")
        else:
            self.status_var = tk.StringVar(value="Ready: Please add GDS files manually.")

        self.color_palette = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22',
                              '#17becf']

        self.setup_ui()
        self.draw_preview(reset_view=True)

    def get_selected_indices(self):
        return [self.tree.index(item) for item in self.tree.selection()]

    def clear_selection(self):
        self.tree.selection_remove(self.tree.selection())

    def set_selection(self, idx):
        self.clear_selection()
        items = self.tree.get_children()
        if 0 <= idx < len(items):
            self.tree.selection_set(items[idx])

    def toggle_selection(self, idx):
        items = self.tree.get_children()
        if 0 <= idx < len(items):
            item = items[idx]
            if item in self.tree.selection():
                self.tree.selection_remove(item)
            else:
                self.tree.selection_add(item)

    def refresh_gds_list_ui(self):
        for item in self.tree.get_children(): self.tree.delete(item)
        for item in self.layer_tree.get_children(): self.layer_tree.delete(item)

        global_layers = set()
        for i, gds in enumerate(self.gds_list):
            self.tree.insert("", tk.END, values=(f"[{i + 1}] {gds['name']}",))
            global_layers.update(gds.get('layers', []))

        for l, d in sorted(list(global_layers)):
            self.layer_tree.insert("", tk.END, values=(f"{l}/{d}",))

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        left_frame = ttk.Frame(main_frame, width=360)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)

        top_container = ttk.Frame(left_frame)
        top_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        bottom_container = ttk.Frame(left_frame)
        bottom_container.pack(side=tk.BOTTOM, fill=tk.X)

        proj_frame = ttk.Frame(top_container)
        proj_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(proj_frame, text="📂 Load Project", command=self.action_load_project).pack(side=tk.LEFT, fill=tk.X,
                                                                                             expand=True, padx=(0, 2))
        ttk.Button(proj_frame, text="💾 Save Project", command=self.action_save_project).pack(side=tk.LEFT, fill=tk.X,
                                                                                             expand=True, padx=(2, 0))

        gds_list_frame = ttk.LabelFrame(top_container, text="1a. GDS Files", padding=2)
        gds_list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        btn_frame = ttk.Frame(gds_list_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 2))
        ttk.Button(btn_frame, text="➕ Add", command=self.add_gds).pack(side=tk.LEFT, fill=tk.X, expand=True,
                                                                       padx=(0, 1))
        ttk.Button(btn_frame, text="➖ Del", command=self.action_delete_selected).pack(side=tk.LEFT, fill=tk.X,
                                                                                      expand=True, padx=(1, 0))

        tree_frame = ttk.Frame(gds_list_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        scrollbar_x = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL)
        self.tree = ttk.Treeview(tree_frame, columns=("Name",), show="headings", selectmode="extended",
                                 yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        self.tree.heading("Name", text="GDS Name")
        self.tree.column("Name", width=120, anchor=tk.W, minwidth=100)

        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y);
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_y.config(command=self.tree.yview);
        scrollbar_x.config(command=self.tree.xview)

        self.tree.bind('<<TreeviewSelect>>', self.on_listbox_select)
        if DND_SUPPORTED:
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind('<<Drop>>', self.on_file_drop)

        block_settings_frame = ttk.LabelFrame(bottom_container, text="1b. Total Block Size (um)", padding=5)
        block_settings_frame.pack(fill=tk.X, pady=(0, 5))
        entry_f = ttk.Frame(block_settings_frame)
        entry_f.pack(fill=tk.X)
        ttk.Label(entry_f, text="W x H:").pack(side=tk.LEFT)
        ttk.Entry(entry_f, textvariable=self.block_width_var, width=8).pack(side=tk.LEFT, fill=tk.X, expand=True,
                                                                            padx=2)
        ttk.Entry(entry_f, textvariable=self.block_height_var, width=8).pack(side=tk.LEFT, fill=tk.X, expand=True,
                                                                             padx=2)
        ttk.Button(entry_f, text="Apply", command=self.update_block_size, width=6).pack(side=tk.LEFT)

        self.nb = ttk.Notebook(bottom_container)
        self.nb.pack(fill=tk.X, pady=(0, 5))
        tab_pos, tab_finish, tab_export = ttk.Frame(self.nb, padding=5), ttk.Frame(self.nb, padding=5), ttk.Frame(
            self.nb, padding=5)
        self.nb.add(tab_pos, text="🎯 Pos");
        self.nb.add(tab_finish, text="✨ Finish");
        self.nb.add(tab_export, text="💾 Export")

        pos_row1 = ttk.Frame(tab_pos);
        pos_row1.pack(fill=tk.X)
        ttk.Label(pos_row1, text="Anchor:").pack(side=tk.LEFT)
        anchor_cb = ttk.Combobox(pos_row1, textvariable=self.anchor_var, values=self.anchor_options, state="readonly",
                                 width=12)
        anchor_cb.pack(side=tk.LEFT, padx=5);
        anchor_cb.bind("<<ComboboxSelected>>", self.on_anchor_change)
        pos_row2 = ttk.Frame(tab_pos);
        pos_row2.pack(fill=tk.X, pady=5)
        ttk.Label(pos_row2, text="X:").pack(side=tk.LEFT);
        ttk.Entry(pos_row2, textvariable=self.selected_x_var, width=10).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(pos_row2, text="Y:").pack(side=tk.LEFT);
        ttk.Entry(pos_row2, textvariable=self.selected_y_var, width=10).pack(side=tk.LEFT)
        ttk.Button(pos_row2, text="Apply", command=self.apply_manual_position).pack(side=tk.RIGHT)

        dummy_f = ttk.LabelFrame(tab_finish, text="1. Dummy Fill (密度填充)", padding=5);
        dummy_f.pack(fill=tk.X, pady=(0, 5))
        ttk.Checkbutton(dummy_f, text="Enable", variable=self.enable_dummy_var).grid(row=0, column=0, sticky=tk.W)
        ttk.Checkbutton(dummy_f, text="Staggered(砌砖)", variable=self.staggered_var).grid(row=0, column=1,
                                                                                           columnspan=3, sticky=tk.W,
                                                                                           padx=5)
        ttk.Label(dummy_f, text="Lyr:").grid(row=1, column=0, sticky=tk.W);
        ttk.Entry(dummy_f, textvariable=self.dummy_layer_var, width=4).grid(row=1, column=1)
        ttk.Label(dummy_f, text="DT:").grid(row=1, column=2, sticky=tk.W);
        ttk.Entry(dummy_f, textvariable=self.dummy_datatype_var, width=4).grid(row=1, column=3)
        ttk.Label(dummy_f, text="Size:").grid(row=2, column=0, sticky=tk.W);
        ttk.Entry(dummy_f, textvariable=self.dummy_size_var, width=4).grid(row=2, column=1)
        ttk.Label(dummy_f, text="Spc:").grid(row=2, column=2, sticky=tk.W);
        ttk.Entry(dummy_f, textvariable=self.dummy_spacing_var, width=4).grid(row=2, column=3)
        ttk.Label(dummy_f, text="Margin:").grid(row=3, column=0, sticky=tk.W);
        ttk.Entry(dummy_f, textvariable=self.dummy_margin_var, width=4).grid(row=3, column=1)

        seal_f = ttk.LabelFrame(tab_finish, text="2. Seal Ring (划片保护环)", padding=5);
        seal_f.pack(fill=tk.X, pady=(0, 5))
        ttk.Checkbutton(seal_f, text="Enable Seal", variable=self.enable_seal_var).grid(row=0, column=0, columnspan=4,
                                                                                        sticky=tk.W)
        ttk.Label(seal_f, text="Lyr:").grid(row=1, column=0, sticky=tk.W);
        ttk.Entry(seal_f, textvariable=self.seal_layer_var, width=4).grid(row=1, column=1)
        ttk.Label(seal_f, text="DT:").grid(row=1, column=2, sticky=tk.W);
        ttk.Entry(seal_f, textvariable=self.seal_datatype_var, width=4).grid(row=1, column=3)
        ttk.Label(seal_f, text="Width:").grid(row=2, column=0, sticky=tk.W);
        ttk.Entry(seal_f, textvariable=self.seal_width_var, width=4).grid(row=2, column=1)
        ttk.Label(seal_f, text="Dist:").grid(row=2, column=2, sticky=tk.W);
        ttk.Entry(seal_f, textvariable=self.seal_margin_var, width=4).grid(row=2, column=3)

        ttk.Label(tab_export, text="Merged Cell Name:").pack(anchor=tk.W);
        ttk.Entry(tab_export, textvariable=self.top_cell_name_var).pack(fill=tk.X, pady=(0, 10))
        ttk.Button(tab_export, text="🛠️ Layer Mapping", command=self.open_layer_mapping_dialog).pack(fill=tk.X,
                                                                                                     pady=(0, 15))
        ttk.Button(tab_export, text="💾 EXPORT GDS", command=self.execute_stitch, style="Accent.TButton").pack(fill=tk.X,
                                                                                                              ipady=8)

        right_frame = ttk.Frame(main_frame, width=150)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right_frame.pack_propagate(False)

        layer_list_frame = ttk.LabelFrame(right_frame, text="Layers", padding=2)
        layer_list_frame.pack(fill=tk.BOTH, expand=True)

        l_btn_frame = ttk.Frame(layer_list_frame)
        l_btn_frame.pack(fill=tk.X, pady=(0, 2))
        ttk.Button(l_btn_frame, text="🔄 Refresh", command=self.refresh_gds_list_ui).pack(fill=tk.X, expand=True)

        layer_tree_frame = ttk.Frame(layer_list_frame)
        layer_tree_frame.pack(fill=tk.BOTH, expand=True)

        l_scroll_y = ttk.Scrollbar(layer_tree_frame, orient=tk.VERTICAL)
        self.layer_tree = ttk.Treeview(layer_tree_frame, columns=("LD",), show="headings", selectmode="none",
                                       yscrollcommand=l_scroll_y.set)
        self.layer_tree.heading("LD", text="L / D")
        self.layer_tree.column("LD", width=60, anchor=tk.CENTER, minwidth=40)
        l_scroll_y.pack(side=tk.RIGHT, fill=tk.Y);
        self.layer_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        l_scroll_y.config(command=self.layer_tree.yview)

        center_frame = ttk.LabelFrame(main_frame, text="Interactive Canvas", padding=5)
        center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        canvas_toolbar_1 = ttk.Frame(center_frame)
        canvas_toolbar_1.pack(side=tk.TOP, fill=tk.X, pady=(0, 2))
        ttk.Button(canvas_toolbar_1, text="↩️ Undo", command=self.action_undo).pack(side=tk.LEFT, padx=1)
        ttk.Button(canvas_toolbar_1, text="🔍 Fit", command=lambda: self.draw_preview(reset_view=True)).pack(
            side=tk.LEFT, padx=1)
        ttk.Separator(canvas_toolbar_1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=3, pady=2)
        ttk.Checkbutton(canvas_toolbar_1, text="📏 Measure", style="Toolbutton", variable=self.measure_mode_var,
                        command=self.on_measure_toggle).pack(side=tk.LEFT, padx=1)
        ttk.Separator(canvas_toolbar_1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=3, pady=2)
        self.btn_overlap = ttk.Checkbutton(canvas_toolbar_1, text="🔴 Overlap", style="Toolbutton",
                                           variable=self.show_overlap_var, command=self.on_overlap_toggle)
        self.btn_overlap.pack(side=tk.LEFT, padx=1)
        self.btn_bbox = ttk.Checkbutton(canvas_toolbar_1, text="✅ BBox", style="Toolbutton",
                                        variable=self.bbox_only_var, command=self.on_bbox_toggle)
        self.btn_bbox.pack(side=tk.LEFT, padx=1)
        ttk.Separator(canvas_toolbar_1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=3, pady=2)
        ttk.Checkbutton(canvas_toolbar_1, text="🌐 Snap", style="Toolbutton", variable=self.grid_snap_var).pack(
            side=tk.LEFT, padx=1)
        ttk.Entry(canvas_toolbar_1, textvariable=self.grid_size_var, width=5).pack(side=tk.LEFT, padx=1)

        canvas_toolbar_draw = ttk.Frame(center_frame)
        canvas_toolbar_draw.pack(side=tk.TOP, fill=tk.X, pady=(2, 5))
        ttk.Button(canvas_toolbar_draw, text="📝 Text", command=self.action_add_text_dialog).pack(side=tk.LEFT, padx=1)
        ttk.Button(canvas_toolbar_draw, text="🔲 Box", command=lambda: self.action_add_shape_dialog('box')).pack(
            side=tk.LEFT, padx=1)
        ttk.Button(canvas_toolbar_draw, text="🔶 Poly", command=lambda: self.action_add_shape_dialog('polygon')).pack(
            side=tk.LEFT, padx=1)
        ttk.Button(canvas_toolbar_draw, text="〰️ Path", command=lambda: self.action_add_shape_dialog('path')).pack(
            side=tk.LEFT, padx=1)
        ttk.Button(canvas_toolbar_draw, text="⚄ ViaArray",
                   command=lambda: self.action_add_shape_dialog('via_array')).pack(side=tk.LEFT, padx=1)
        ttk.Button(canvas_toolbar_draw, text="✂️ Crop", command=self.action_draw_crop_box).pack(side=tk.LEFT, padx=1)
        ttk.Button(canvas_toolbar_draw, text="🗑️ Clear", command=self.action_clear_annotations).pack(side=tk.LEFT,
                                                                                                     padx=1)
        ttk.Separator(canvas_toolbar_draw, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=3, pady=2)
        align_cb = ttk.Combobox(canvas_toolbar_draw, textvariable=self.align_var,
                                values=list(self.align_options_map.keys()), state="readonly", width=12)
        align_cb.pack(side=tk.LEFT, padx=1)
        ttk.Button(canvas_toolbar_draw, text="▶ Align", command=self.execute_align).pack(side=tk.LEFT, padx=1)

        self.figure = plt.Figure(figsize=(6, 5), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, center_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('button_release_event', self.on_release)
        self.canvas.mpl_connect('scroll_event', self.on_scroll)
        self.canvas.mpl_connect('key_press_event', self.on_key_press)
        self.canvas.mpl_connect('key_release_event', self.on_key_release)

        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def on_key_press(self, event):
        if event.key in ['control', 'ctrl']:
            self.ctrl_pressed = True
            if self.last_mouse_event and self.last_mouse_event.inaxes:
                self.on_motion(self.last_mouse_event)

    def on_key_release(self, event):
        if event.key in ['control', 'ctrl']:
            self.ctrl_pressed = False
            if self.last_mouse_event and self.last_mouse_event.inaxes:
                self.on_motion(self.last_mouse_event)

    def on_file_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        added = False
        for p in files:
            if p.lower().endswith('.gds'): self.process_single_gds(p); added = True
        if added: self.draw_preview(reset_view=True)

    def process_single_gds(self, filepath):
        try:
            self.save_snapshot()
            base_name = os.path.splitext(os.path.basename(filepath))[0]
            base_bbox, true_polygons, layers = self.parse_gds_info(filepath)

            cx_block, cy_block = self.block_width / 2.0, self.block_height / 2.0
            cx_gds, cy_gds = (base_bbox.left + base_bbox.right) / 2.0, (base_bbox.bottom + base_bbox.top) / 2.0
            init_offset_x, init_offset_y = cx_block - cx_gds, cy_block - cy_gds

            gds_info = {'path': filepath, 'name': base_name, 'base_bbox': base_bbox, 'trans': db.DTrans(),
                        'offset_x': init_offset_x, 'offset_y': init_offset_y,
                        'color': self.color_palette[len(self.gds_list) % len(self.color_palette)],
                        'patch': None, 'center_text': None, 'true_polygons': true_polygons, 'poly_patches': [],
                        'layers': layers}
            self.gds_list.append(gds_info)
            self.refresh_gds_list_ui()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load {filepath}:\n{str(e)}")

    def add_gds(self):
        paths = filedialog.askopenfilenames(filetypes=[("GDS Files", "*.gds")])
        added = False
        for p in paths: self.process_single_gds(p); added = True
        if added: self.draw_preview(reset_view=True)

    def on_bbox_toggle(self):
        self.btn_bbox.config(text="✅ BBox" if self.bbox_only_var.get() else "🔲 Full")
        self.draw_preview(reset_view=False)

    def on_overlap_toggle(self):
        self.btn_overlap.config(text="🔴 Overlaps: ON" if self.show_overlap_var.get() else "⭕ Overlaps: OFF")
        self.draw_overlaps();
        self.canvas.draw_idle()

    def draw_overlaps(self):
        for p in getattr(self, 'overlap_patches', []):
            try:
                p.remove()
            except:
                pass
        self.overlap_patches.clear()
        if not self.show_overlap_var.get(): return
        n = len(self.gds_list);
        tol = 1e-5
        for i in range(n):
            for j in range(i + 1, n):
                l1, r1, b1, t1 = self.get_bbox(self.gds_list[i])
                l2, r2, b2, t2 = self.get_bbox(self.gds_list[j])
                if (l1 < r2 - tol) and (r1 > l2 + tol) and (b1 < t2 - tol) and (t1 > b2 + tol):
                    il, ir, ib, it = max(l1, l2), min(r1, r2), max(b1, b2), min(t1, t2)
                    rect = patches.Rectangle((il, ib), ir - il, it - ib, linewidth=1.5, edgecolor='red',
                                             facecolor='red', alpha=0.5, hatch='///', zorder=250)
                    self.ax.add_patch(rect);
                    self.overlap_patches.append(rect)

    def save_snapshot(self):
        clean_texts = [{k: v for k, v in t.items() if k != 'text_obj'} for t in self.user_texts]
        clean_shapes = [{k: v for k, v in s.items() if k != 'patch'} for s in self.user_shapes]
        snapshot = {'gds_list': [], 'measurements': [dict(m) for m in self.measurements], 'user_texts': clean_texts,
                    'user_shapes': clean_shapes, 'crop_box': self.crop_box.copy() if self.crop_box else None}
        for gds in self.gds_list:
            trans_copy = gds['trans'] * db.DTrans()
            snap_gds = {'path': gds['path'], 'name': gds['name'], 'base_bbox': gds['base_bbox'], 'trans': trans_copy,
                        'offset_x': gds['offset_x'], 'offset_y': gds['offset_y'], 'color': gds['color'],
                        'true_polygons': gds['true_polygons'], 'layers': gds.get('layers', [])}
            snapshot['gds_list'].append(snap_gds)
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > 30: self.undo_stack.pop(0)

    def action_undo(self):
        if not self.undo_stack: return
        snapshot = self.undo_stack.pop()
        self.gds_list.clear()
        for item in snapshot['gds_list']:
            gds_info = {'path': item['path'], 'name': item['name'], 'base_bbox': item['base_bbox'],
                        'trans': item['trans'], 'offset_x': item['offset_x'], 'offset_y': item['offset_y'],
                        'color': item['color'], 'patch': None, 'center_text': None,
                        'true_polygons': item['true_polygons'], 'poly_patches': [], 'layers': item.get('layers', [])}
            self.gds_list.append(gds_info)
        self.refresh_gds_list_ui()
        self.measurements = snapshot.get('measurements', [])
        self.user_texts = snapshot.get('user_texts', [])
        self.user_shapes = snapshot.get('user_shapes', [])
        self.crop_box = snapshot.get('crop_box')
        self.clear_active_measurement()
        self.draw_preview(reset_view=False)

    def action_save_project(self):
        if not self.gds_list: return
        filepath = filedialog.asksaveasfilename(defaultextension=".gdsprj", filetypes=[("GDS Project", "*.gdsprj")])
        if not filepath: return
        try:
            serializable_mapping = {str(k): v for k, v in self.layer_mapping.items()}
            clean_texts = [{k: v for k, v in t.items() if k != 'text_obj'} for t in self.user_texts]
            clean_shapes = [{k: v for k, v in s.items() if k != 'patch'} for s in self.user_shapes]
            project_data = {"block_width": self.block_width, "block_height": self.block_height,
                            "top_cell_name": self.top_cell_name_var.get(), "measurements": self.measurements,
                            "user_texts": clean_texts, "user_shapes": clean_shapes, "crop_box": self.crop_box,
                            "layer_mapping": serializable_mapping, "gds_items": []}
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
            self.measurements = project_data.get("measurements", [])
            self.user_texts = project_data.get("user_texts", [])
            self.user_shapes = project_data.get("user_shapes", [])
            self.crop_box = project_data.get("crop_box")

            saved_mapping = project_data.get("layer_mapping", {})
            self.layer_mapping = {eval(k_str): tuple(v) for k_str, v in saved_mapping.items()}

            self.clear_active_measurement();
            self.undo_stack.clear()
            self.block_width_var.set(str(project_data.get("block_width", 5000.0)))
            self.block_height_var.set(str(project_data.get("block_height", 5000.0)))
            self.top_cell_name_var.set(project_data.get("top_cell_name", "MERGED_CHIP"))
            self.update_block_size()
            for item in project_data.get("gds_items", []):
                path, name = item.get("path"), item.get("name")
                if not os.path.exists(path): continue
                base_bbox, true_polygons, layers = self.parse_gds_info(path)
                trans = db.DTrans(item["trans_rot"], item["trans_mirror"], item["trans_dx"], item["trans_dy"])
                gds_info = {'path': path, 'name': name, 'base_bbox': base_bbox, 'trans': trans,
                            'offset_x': item["offset_x"], 'offset_y': item["offset_y"], 'color': item["color"],
                            'patch': None, 'center_text': None, 'true_polygons': true_polygons, 'poly_patches': [],
                            'layers': layers}
                self.gds_list.append(gds_info)
            self.refresh_gds_list_ui()
            self.draw_preview(reset_view=True)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def edit_text_dialog(self, idx):
        ut = self.user_texts[idx]
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Text")
        dialog.geometry("260x220")
        dialog.transient(self.root);
        dialog.grab_set()

        t_var, s_var, l_var, dt_var = tk.StringVar(value=ut['text']), tk.StringVar(value=str(ut['size'])), tk.StringVar(
            value=str(ut['layer'])), tk.StringVar(value=str(ut['dt']))

        ttk.Label(dialog, text="Text String:").grid(row=0, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=t_var, width=15).grid(row=0, column=1)
        ttk.Label(dialog, text="Size (um):").grid(row=1, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=s_var, width=15).grid(row=1, column=1)
        ttk.Label(dialog, text="Layer:").grid(row=2, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=l_var, width=15).grid(row=2, column=1)
        ttk.Label(dialog, text="Datatype:").grid(row=3, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=dt_var, width=15).grid(row=3, column=1)

        def on_ok():
            try:
                self.save_snapshot()
                ut['text'], ut['size'], ut['layer'], ut['dt'] = t_var.get(), float(s_var.get()), int(l_var.get()), int(
                    dt_var.get())
                tp = TextPath((ut['x'], ut['y']), ut['text'], size=ut['size'])
                ut['text_obj'].set_path(tp)
                self.canvas.draw_idle();
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Error", "Invalid numeric value.")

        ttk.Button(dialog, text="Update", command=on_ok).grid(row=4, column=0, columnspan=2, pady=10)

    def edit_shape_dialog(self, idx):
        s = self.user_shapes[idx]
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Edit {s['type'].capitalize()}")
        dialog.geometry("260x280")
        dialog.transient(self.root);
        dialog.grab_set()

        l_var, dt_var = tk.StringVar(value=str(s['layer'])), tk.StringVar(value=str(s['dt']))
        ttk.Label(dialog, text="Layer:").grid(row=0, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=l_var, width=15).grid(row=0, column=1)
        ttk.Label(dialog, text="Datatype:").grid(row=1, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=dt_var, width=15).grid(row=1, column=1)

        w_var, box_w_var, box_h_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        min_x, min_y = 0, 0

        if s['type'] == 'path':
            w_var.set(str(s.get('width', 20.0)))
            ttk.Label(dialog, text="Width (um):").grid(row=2, column=0, padx=15, pady=10, sticky=tk.W);
            ttk.Entry(dialog, textvariable=w_var, width=15).grid(row=2, column=1)
        elif s['type'] == 'box':
            pts = s['points']
            min_x, min_y = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
            box_w_var.set(str(abs(pts[1][0] - pts[0][0])))
            box_h_var.set(str(abs(pts[1][1] - pts[0][1])))
            ttk.Label(dialog, text="Width (um):").grid(row=2, column=0, padx=15, pady=10, sticky=tk.W);
            ttk.Entry(dialog, textvariable=box_w_var, width=15).grid(row=2, column=1)
            ttk.Label(dialog, text="Height (um):").grid(row=3, column=0, padx=15, pady=10, sticky=tk.W);
            ttk.Entry(dialog, textvariable=box_h_var, width=15).grid(row=3, column=1)
        elif s['type'] == 'via_array':
            w_var.set(str(s.get('via_w', 1.0)));
            box_w_var.set(str(s.get('via_h', 1.0)))
            px_var, py_var = tk.StringVar(value=str(s.get('pitch_x', 2.0))), tk.StringVar(
                value=str(s.get('pitch_y', 2.0)))
            ttk.Label(dialog, text="Via WxH (um):").grid(row=2, column=0, padx=15, pady=5, sticky=tk.W)
            f1 = ttk.Frame(dialog);
            f1.grid(row=2, column=1)
            ttk.Entry(f1, textvariable=w_var, width=6).pack(side=tk.LEFT, padx=1);
            ttk.Entry(f1, textvariable=box_w_var, width=6).pack(side=tk.LEFT, padx=1)
            ttk.Label(dialog, text="Pitch XxY (um):").grid(row=3, column=0, padx=15, pady=5, sticky=tk.W)
            f2 = ttk.Frame(dialog);
            f2.grid(row=3, column=1)
            ttk.Entry(f2, textvariable=px_var, width=6).pack(side=tk.LEFT, padx=1);
            ttk.Entry(f2, textvariable=py_var, width=6).pack(side=tk.LEFT, padx=1)

        def on_ok():
            try:
                self.save_snapshot()
                s['layer'], s['dt'] = int(l_var.get()), int(dt_var.get())
                if s['type'] == 'path':
                    s['width'] = float(w_var.get())
                elif s['type'] == 'box':
                    nw, nh = float(box_w_var.get()), float(box_h_var.get())
                    s['points'] = [(min_x, min_y), (min_x + nw, min_y + nh)]
                elif s['type'] == 'via_array':
                    s['via_w'], s['via_h'] = float(w_var.get()), float(box_w_var.get())
                    s['pitch_x'], s['pitch_y'] = float(px_var.get()), float(py_var.get())
                self.draw_preview(reset_view=False);
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Error", "Invalid numeric value.")

        ttk.Button(dialog, text="Update", command=on_ok).grid(row=5, column=0, columnspan=2, pady=10)

    def action_add_text_dialog(self):
        self.cancel_draw_mode()
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Text")
        dialog.geometry("260x220")
        dialog.transient(self.root);
        dialog.grab_set()

        t_var, s_var, l_var, dt_var = tk.StringVar(value="CHIP_LABEL"), tk.StringVar(value="100.0"), tk.StringVar(
            value="10"), tk.StringVar(value="0")
        ttk.Label(dialog, text="Text String:").grid(row=0, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=t_var, width=15).grid(row=0, column=1)
        ttk.Label(dialog, text="Size (um):").grid(row=1, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=s_var, width=15).grid(row=1, column=1)
        ttk.Label(dialog, text="Layer:").grid(row=2, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=l_var, width=15).grid(row=2, column=1)
        ttk.Label(dialog, text="Datatype:").grid(row=3, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=dt_var, width=15).grid(row=3, column=1)

        def on_ok():
            try:
                self.draw_current_props = {'text': t_var.get(), 'size': float(s_var.get()), 'layer': int(l_var.get()),
                                           'dt': int(dt_var.get())}
                self.draw_mode = 'text';
                self.measure_mode_var.set(False);
                self.status_var.set("Text Mode: Click on Canvas to place the text.")
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Error", "Invalid numeric value.")

        ttk.Button(dialog, text="Place on Canvas", command=on_ok).grid(row=4, column=0, columnspan=2, pady=10)

    def action_add_shape_dialog(self, shape_type):
        self.cancel_draw_mode()
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Add {shape_type.capitalize()}")
        dialog.geometry("260x250")
        dialog.transient(self.root);
        dialog.grab_set()

        l_var, dt_var, w_var = tk.StringVar(value="10"), tk.StringVar(value="0"), tk.StringVar(value="20.0")
        ttk.Label(dialog, text="Layer:").grid(row=0, column=0, padx=15, pady=5, sticky=tk.W);
        ttk.Entry(dialog, textvariable=l_var, width=15).grid(row=0, column=1)
        ttk.Label(dialog, text="Datatype:").grid(row=1, column=0, padx=15, pady=5, sticky=tk.W);
        ttk.Entry(dialog, textvariable=dt_var, width=15).grid(row=1, column=1)

        vw_var, vh_var, px_var, py_var = tk.StringVar(value="1.0"), tk.StringVar(value="1.0"), tk.StringVar(
            value="2.0"), tk.StringVar(value="2.0")
        if shape_type == 'path':
            ttk.Label(dialog, text="Width (um):").grid(row=2, column=0, padx=15, pady=5, sticky=tk.W);
            ttk.Entry(dialog, textvariable=w_var, width=15).grid(row=2, column=1)
        elif shape_type == 'via_array':
            ttk.Label(dialog, text="Via WxH (um):").grid(row=2, column=0, padx=15, pady=5, sticky=tk.W)
            f1 = ttk.Frame(dialog);
            f1.grid(row=2, column=1)
            ttk.Entry(f1, textvariable=vw_var, width=6).pack(side=tk.LEFT, padx=1);
            ttk.Entry(f1, textvariable=vh_var, width=6).pack(side=tk.LEFT, padx=1)
            ttk.Label(dialog, text="Pitch XxY (um):").grid(row=3, column=0, padx=15, pady=5, sticky=tk.W)
            f2 = ttk.Frame(dialog);
            f2.grid(row=3, column=1)
            ttk.Entry(f2, textvariable=px_var, width=6).pack(side=tk.LEFT, padx=1);
            ttk.Entry(f2, textvariable=py_var, width=6).pack(side=tk.LEFT, padx=1)

        def on_ok():
            try:
                self.draw_current_props = {'type': shape_type, 'layer': int(l_var.get()), 'dt': int(dt_var.get())}
                if shape_type == 'path':
                    self.draw_current_props['width'] = float(w_var.get())
                elif shape_type == 'via_array':
                    self.draw_current_props.update(
                        {'via_w': float(vw_var.get()), 'via_h': float(vh_var.get()), 'pitch_x': float(px_var.get()),
                         'pitch_y': float(py_var.get())})

                self.draw_mode = shape_type;
                self.draw_points = [];
                self.measure_mode_var.set(False)
                if shape_type in ['box', 'via_array']:
                    self.status_var.set(f"{shape_type.capitalize()} Mode: Click to start, click to end.")
                else:
                    self.status_var.set(
                        f"{shape_type.capitalize()} Mode: Left click points, Right click to finish. (Hold Ctrl for Ortho)")
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Error", "Invalid numeric value.")

        ttk.Button(dialog, text="Start Drawing", command=on_ok).grid(row=5, column=0, columnspan=2, pady=15)

    def action_draw_crop_box(self):
        self.cancel_draw_mode()
        self.draw_mode = 'crop'
        self.draw_points = []
        self.measure_mode_var.set(False)
        self.status_var.set("Crop Mode: Click to define top-left, click again for bottom-right.")

    def cancel_draw_mode(self):
        self.draw_mode = None;
        self.draw_points = []
        if self.temp_draw_preview:
            try:
                self.temp_draw_preview.remove()
            except:
                pass
            self.temp_draw_preview = None
        self.canvas.draw_idle();
        self.status_var.set("Ready")

    def open_layer_mapping_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Global Layer Mapping");
        dialog.geometry("450x450")
        dialog.transient(self.root);
        dialog.grab_set()

        all_src_layers = set()
        for gds in self.gds_list: all_src_layers.update(gds.get('layers', []))
        for sl in sorted(list(all_src_layers)):
            if sl not in self.layer_mapping: self.layer_mapping[sl] = sl

        columns = ("Src Layer", "Src DT", "Dst Layer", "Dst DT")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", selectmode="browse")
        tree.heading("Src Layer", text="原 Layer");
        tree.heading("Src DT", text="原 Datatype")
        tree.heading("Dst Layer", text="新 Layer");
        tree.heading("Dst DT", text="新 Datatype")
        for col in columns: tree.column(col, width=80, anchor=tk.CENTER)
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        def refresh_tree():
            for item in tree.get_children(): tree.delete(item)
            for sl, sdt in sorted(self.layer_mapping.keys()): tree.insert("", tk.END, values=(sl, sdt,
                                                                                              self.layer_mapping[
                                                                                                  (sl, sdt)][0],
                                                                                              self.layer_mapping[
                                                                                                  (sl, sdt)][1]))

        refresh_tree()

        edit_f = ttk.LabelFrame(dialog, text="编辑选中的映射", padding=10)
        edit_f.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Label(edit_f, text="新 Layer:").grid(row=0, column=0, padx=5, pady=5)
        tgt_l_var, tgt_dt_var = tk.StringVar(), tk.StringVar()
        ttk.Entry(edit_f, textvariable=tgt_l_var, width=8).grid(row=0, column=1)
        ttk.Label(edit_f, text="新 DT:").grid(row=0, column=2, padx=5, pady=5)
        ttk.Entry(edit_f, textvariable=tgt_dt_var, width=8).grid(row=0, column=3)

        def on_select(event):
            sel = tree.selection()
            if sel: tgt_l_var.set(tree.item(sel[0], "values")[2]); tgt_dt_var.set(tree.item(sel[0], "values")[3])

        tree.bind("<<TreeviewSelect>>", on_select)

        def update_mapping():
            sel = tree.selection()
            if not sel: return
            try:
                item = tree.item(sel[0])
                self.layer_mapping[(int(item["values"][0]), int(item["values"][1]))] = (int(tgt_l_var.get()),
                                                                                        int(tgt_dt_var.get()))
                refresh_tree()
            except ValueError:
                messagebox.showerror("Error", "Layer 和 Datatype 必须是整数！")

        ttk.Button(edit_f, text="✅ 更新", command=update_mapping).grid(row=0, column=4, padx=15)
        ttk.Button(dialog, text="关闭并保存", command=dialog.destroy).pack(pady=10)

    def update_canvas_selection(self):
        selected_indices = self.get_selected_indices()
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
        mode = self.align_options_map.get(self.align_var.get())
        if mode in ['left', 'right', 'center_x', 'bottom', 'top', 'center_y']:
            self.align_selected(mode)
        elif mode in ['dist_h', 'dist_v']:
            self.distribute_selected(mode[-1])

    def align_selected(self, mode):
        selection = self.get_selected_indices()
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
        self.draw_preview(reset_view=False);
        self.on_listbox_select()

    def distribute_selected(self, axis):
        selection = self.get_selected_indices()
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
            for item in items: self.set_anchor_coords(item['gds'], 'Bottom-Left', cur_x, item['b']); cur_x += item[
                                                                                                                  'w'] + gap
        elif axis == 'v':
            items.sort(key=lambda item: item['b'])
            gap = (items[-1]['t'] - items[0]['b'] - sum(item['h'] for item in items)) / (len(items) - 1)
            cur_y = items[0]['b']
            for item in items: self.set_anchor_coords(item['gds'], 'Bottom-Left', item['l'], cur_y); cur_y += item[
                                                                                                                  'h'] + gap
        self.draw_preview(reset_view=False);
        self.on_listbox_select()

    def on_measure_toggle(self):
        if self.measure_mode_var.get():
            self.cancel_draw_mode()
            self.status_var.set("Measure Mode ON: Click once to start, click again to finish. (Hold Ctrl for Ortho)")
            self.clear_selection();
            self.update_canvas_selection()
        else:
            self.status_var.set("Measure Mode OFF.")
            self.clear_active_measurement();
            self.canvas.draw_idle()

    def action_clear_annotations(self):
        self.save_snapshot()
        self.measurements.clear()
        self.user_texts.clear()
        self.user_shapes.clear()
        self.crop_box = None
        self.clear_active_measurement()
        self.draw_preview(reset_view=False)

    def clear_active_measurement(self):
        for item in [self.measure_line, self.measure_text, self.snap_indicator, self.temp_draw_preview]:
            if item:
                try:
                    item.remove()
                except:
                    pass
        self.measure_line, self.measure_text, self.snap_indicator, self.temp_draw_preview = None, None, None, None
        for line in self.guide_lines:
            try:
                line.remove()
            except:
                pass
        self.guide_lines.clear()
        self.measure_state = 0;
        self.measure_start_pt = None

    def get_anchor_coords(self, gds, anchor_type, temp_ox=None, temp_oy=None):
        t_box = gds['trans'] * gds['base_bbox']
        ox, oy = (gds['offset_x'] if temp_ox is None else temp_ox), (gds['offset_y'] if temp_oy is None else temp_oy)
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
        selection = self.get_selected_indices()
        if selection:
            x, y = self.get_anchor_coords(self.gds_list[selection[0]], self.anchor_var.get())
            self.selected_x_var.set(f"{x:.3f}");
            self.selected_y_var.set(f"{y:.3f}")
            if self.measure_mode_var.get(): self.measure_mode_var.set(False); self.on_measure_toggle()
        self.update_canvas_selection()

    def on_anchor_change(self, event=None):
        self.on_listbox_select()

    def apply_manual_position(self):
        selection = self.get_selected_indices()
        if not selection: return
        self.save_snapshot()
        try:
            self.set_anchor_coords(self.gds_list[selection[0]], self.anchor_var.get(), float(self.selected_x_var.get()),
                                   float(self.selected_y_var.get()))
            self.draw_preview(reset_view=False)
        except ValueError:
            pass

    def parse_gds_info(self, filepath):
        layout = db.Layout();
        layout.read(filepath)
        top_cell = layout.top_cells()[0]
        base_bbox = top_cell.dbbox()
        layers = [(layout.get_info(li).layer, layout.get_info(li).datatype) for li in layout.layer_indexes()]
        region = db.Region()
        for li in layout.layer_indexes(): region.insert(top_cell.begin_shapes_rec(li))
        region.merge();
        region = region.hulls()
        trans = db.DCplxTrans(layout.dbu)
        true_polygons = [[(pt.x, pt.y) for pt in db.DPolygon(poly).transformed(trans).each_point_hull()] for poly in
                         region.each() if list(db.DPolygon(poly).transformed(trans).each_point_hull())]
        return base_bbox, true_polygons, layers

    def update_block_size(self):
        try:
            self.block_width, self.block_height = float(self.block_width_var.get()), float(self.block_height_var.get())
            self.draw_preview(reset_view=True)
        except:
            pass

    def action_delete_selected(self):
        selection = self.get_selected_indices()
        if selection:
            self.save_snapshot()
            for idx in sorted(selection, reverse=True): del self.gds_list[idx]
            self.refresh_gds_list_ui()
            self.selected_x_var.set("0.0");
            self.selected_y_var.set("0.0")
            self.draw_preview(reset_view=False)

    def draw_preview(self, reset_view=False):
        cur_xlim, cur_ylim = self.ax.get_xlim(), self.ax.get_ylim()
        self.ax.clear()
        self.clear_active_measurement()

        for spine in self.ax.spines.values(): spine.set_visible(False)
        self.ax.tick_params(axis='both', which='both', length=4, width=0.8, direction='out', colors='#cccccc',
                            labelcolor='#999999')
        self.ax.set_axisbelow(True)

        self.ax.add_patch(
            patches.Rectangle((0, 0), self.block_width, self.block_height, linewidth=1.5, edgecolor='#2c3e50',
                              facecolor='#f4f7f9', linestyle='-.', zorder=0))
        self.ax.plot(0, 0, marker='+', color='#2c3e50', markersize=15, markeredgewidth=1.5, zorder=1)

        if not self.gds_list: self.ax.text(self.block_width / 2, self.block_height / 2, 'No GDS Loaded', ha='center',
                                           va='center', color='#bbbbbb', fontsize=12)

        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            sx, sy = t_box.left + gds['offset_x'], t_box.bottom + gds['offset_y']
            w, h = t_box.width(), t_box.height()

            rect = patches.Rectangle((sx, sy), w, h, linewidth=0.5, edgecolor=gds['color'], facecolor=gds['color'],
                                     alpha=0.6, zorder=10)
            self.ax.add_patch(rect)
            gds['patch'] = rect;
            gds['poly_patches'] = []

            if not self.bbox_only_var.get():
                for pts in gds['true_polygons']:
                    transformed_pts = [((gds['trans'] * db.DPoint(px, py)).x + gds['offset_x'],
                                        (gds['trans'] * db.DPoint(px, py)).y + gds['offset_y']) for px, py in pts]
                    poly_patch = patches.Polygon(transformed_pts, closed=True, fill=True,
                                                 facecolor=mcolors.to_rgba('black', alpha=0.3),
                                                 edgecolor=mcolors.to_rgba(gds['color'], alpha=0.7), linestyle='-',
                                                 linewidth=0.8, zorder=15)
                    self.ax.add_patch(poly_patch)
                    gds['poly_patches'].append((pts, poly_patch))

            ratio = min(w, h) / min(self.block_width, self.block_height) if min(self.block_width,
                                                                                self.block_height) > 0 else 1.0
            gds['center_text'] = self.ax.text(sx + w / 2, sy + h / 2, gds['name'], ha='center', va='center',
                                              fontsize=max(6, min(35, int(6 + 18 * ratio))), color='black',
                                              fontweight='bold', alpha=0.7, zorder=90)

        for m in self.measurements:
            self.ax.plot([m['x0'], m['x1']], [m['y0'], m['y1']], color='#FF1493', linestyle='--', linewidth=1,
                         zorder=300)
            self.ax.text(m['x1'], m['y1'],
                         f" L: {math.hypot(m['x1'] - m['x0'], m['y1'] - m['y0']):.2f}\n dx: {abs(m['x1'] - m['x0']):.2f}\n dy: {abs(m['y1'] - m['y0']):.2f}",
                         color='#FF1493', fontsize=10, fontweight='bold', zorder=301,
                         bbox=dict(facecolor='white', alpha=0.8, edgecolor='none', pad=2))

        for ut in self.user_texts:
            tp = TextPath((ut['x'], ut['y']), ut['text'], size=ut['size'])
            text_patch = PathPatch(tp, facecolor='#0044cc', edgecolor='none', zorder=250, alpha=0.8)
            self.ax.add_patch(text_patch)
            ut['text_obj'] = text_patch

        for s in self.user_shapes:
            if s['type'] in ['box', 'via_array']:
                pts = s['points']
                x0, y0 = pts[0];
                x1, y1 = pts[1]
                fc = '#FF8C00' if s['type'] == 'box' else 'none'
                ec = '#FF8C00' if s['type'] == 'box' else '#00CED1'
                hatch = None if s['type'] == 'box' else '..'
                rect = patches.Rectangle((min(x0, x1), min(y0, y1)), abs(x1 - x0), abs(y1 - y0),
                                         fill=(s['type'] == 'box'), facecolor=fc, hatch=hatch, alpha=0.5, edgecolor=ec,
                                         zorder=240, linewidth=2)
                self.ax.add_patch(rect);
                s['patch'] = rect
            elif s['type'] == 'polygon':
                poly = patches.Polygon(s['points'], closed=True, fill=True, facecolor='#32CD32', alpha=0.5,
                                       edgecolor='#32CD32', zorder=240)
                self.ax.add_patch(poly);
                s['patch'] = poly
            elif s['type'] == 'path':
                path_points = [db.DPoint(x, y) for x, y in s['points']]
                if len(path_points) >= 2:
                    dpath = db.DPath(path_points, s['width'])
                    hull_pts = [(pt.x, pt.y) for pt in dpath.polygon().each_point_hull()]
                    poly = patches.Polygon(hull_pts, closed=True, fill=True, facecolor='#9370DB', alpha=0.5,
                                           edgecolor='#9370DB', zorder=240)
                    self.ax.add_patch(poly);
                    s['patch'] = poly

        if self.crop_box:
            pts = self.crop_box
            x0, y0 = pts[0];
            x1, y1 = pts[1]
            rect = patches.Rectangle((min(x0, x1), min(y0, y1)), abs(x1 - x0), abs(y1 - y0), fill=False,
                                     edgecolor='red', linestyle='--', linewidth=3, zorder=400)
            self.ax.add_patch(rect)

        self.update_canvas_selection()

        if reset_view:
            self.ax.set_xlim(-self.block_width * 0.1, self.block_width * 1.1)
            self.ax.set_ylim(-self.block_height * 0.1, self.block_height * 1.1)
        else:
            self.ax.set_xlim(cur_xlim);
            self.ax.set_ylim(cur_ylim)

        self.ax.set_aspect('equal', adjustable='datalim')
        self.ax.grid(True, linestyle='-', color='#cccccc', alpha=0.4)
        self.draw_overlaps();
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
        ox, oy = (gds['offset_x'] if temp_ox is None else temp_ox), (gds['offset_y'] if temp_oy is None else temp_oy)
        return [t_box.left + ox, t_box.right + ox, (t_box.left + t_box.right) / 2 + ox], [t_box.bottom + oy,
                                                                                          t_box.top + oy, (
                                                                                                      t_box.bottom + t_box.top) / 2 + oy]

    def get_snapped_coordinate(self, x, y):
        cur_xlim, cur_ylim = self.ax.get_xlim(), self.ax.get_ylim()
        best_x, best_y = x, y
        min_dx, min_dy = (cur_xlim[1] - cur_xlim[0]) * 0.02, (cur_ylim[1] - cur_ylim[0]) * 0.02
        snapped_x, snapped_y = False, False
        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            ox, oy = gds['offset_x'], gds['offset_y']
            for px in [t_box.left + ox, t_box.right + ox, (t_box.left + t_box.right) / 2 + ox]:
                if abs(x - px) < min_dx: min_dx, best_x, snapped_x = abs(x - px), px, True
            for py in [t_box.bottom + oy, t_box.top + oy, (t_box.bottom + t_box.top) / 2 + oy]:
                if abs(y - py) < min_dy: min_dy, best_y, snapped_y = abs(y - py), py, True
        return best_x, best_y, snapped_x, snapped_y

    def on_press(self, event):
        if not event.inaxes: return
        self.last_mouse_event = event

        if event.dblclick and event.button == 1:
            for i in range(len(self.user_texts) - 1, -1, -1):
                if 'text_obj' in self.user_texts[i] and self.user_texts[i]['text_obj']:
                    cont, _ = self.user_texts[i]['text_obj'].contains(event)
                    if cont: self.edit_text_dialog(i); return
            for i in range(len(self.user_shapes) - 1, -1, -1):
                if 'patch' in self.user_shapes[i] and self.user_shapes[i]['patch']:
                    cont, _ = self.user_shapes[i]['patch'].contains(event)
                    if cont: self.edit_shape_dialog(i); return
            return

        snap_x, snap_y, _, _ = self.get_snapped_coordinate(event.xdata, event.ydata)

        if (self.measure_mode_var.get() and self.measure_state == 1 and self.measure_start_pt) or \
                (self.draw_mode in ['polygon', 'path'] and self.draw_points):
            if self.ctrl_pressed or getattr(event, 'key', None) in ['control', 'ctrl']:
                if self.measure_mode_var.get():
                    last_x, last_y = self.measure_start_pt
                else:
                    last_x, last_y = self.draw_points[-1]
                if abs(snap_x - last_x) > abs(snap_y - last_y):
                    snap_y = last_y
                else:
                    snap_x = last_x

        if self.draw_mode is not None:
            if self.draw_mode == 'text':
                if event.button == 1:
                    self.save_snapshot()
                    new_text = self.draw_current_props.copy()
                    new_text['x'], new_text['y'] = snap_x, snap_y
                    self.user_texts.append(new_text)
                    self.cancel_draw_mode()
                    self.status_var.set("Text added.")
                    self.draw_preview(reset_view=False)
                return

            elif self.draw_mode in ['box', 'via_array', 'crop']:
                if event.button == 1:
                    if not self.draw_points:
                        self.draw_points.append((snap_x, snap_y))
                    else:
                        self.draw_points.append((snap_x, snap_y)); self.finalize_shape()
                return

            elif self.draw_mode in ['polygon', 'path']:
                if event.button == 1:
                    self.draw_points.append((snap_x, snap_y))
                elif event.button == 3:
                    if len(self.draw_points) >= (3 if self.draw_mode == 'polygon' else 2):
                        self.finalize_shape()
                    else:
                        self.cancel_draw_mode()
                return

        if self.measure_mode_var.get() and event.button == 1:
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
                self.measurements.append(
                    {'x0': self.measure_start_pt[0], 'y0': self.measure_start_pt[1], 'x1': snap_x, 'y1': snap_y})
                self.measure_state = 0
                if self.snap_indicator: self.snap_indicator.set_data([], [])
                self.measure_line, self.measure_text = None, None
            self.canvas.draw_idle();
            return

        for line in self.guide_lines: line.remove()
        self.guide_lines.clear()

        for i in range(len(self.user_texts) - 1, -1, -1):
            if 'text_obj' in self.user_texts[i] and self.user_texts[i]['text_obj']:
                cont, _ = self.user_texts[i]['text_obj'].contains(event)
                if cont and event.button == 1:
                    self.dragging_type = 'text'
                    self.dragging_idx = i
                    self.drag_start_x, self.drag_start_y = event.xdata, event.ydata
                    self.rect_start_x, self.rect_start_y = self.user_texts[i]['x'], self.user_texts[i]['y']
                    return

        for i in range(len(self.user_shapes) - 1, -1, -1):
            if 'patch' in self.user_shapes[i] and self.user_shapes[i]['patch']:
                cont, _ = self.user_shapes[i]['patch'].contains(event)
                if cont and event.button == 1:
                    self.dragging_type = 'shape'
                    self.dragging_idx = i
                    self.drag_start_x, self.drag_start_y = event.xdata, event.ydata
                    self.drag_start_offsets = list(self.user_shapes[i]['points'])
                    return

        clicked_idx = next(
            (i for i in range(len(self.gds_list) - 1, -1, -1) if self.gds_list[i]['patch'].contains(event)[0]), -1)
        if clicked_idx != -1:
            if event.button in [2, 3]:
                self.show_context_menu(clicked_idx); return
            elif event.button == 1:
                self.dragging_type = 'gds'
                self.dragging_idx = clicked_idx
                self.drag_start_x, self.drag_start_y = event.xdata, event.ydata
                self.rect_start_x, self.rect_start_y = self.gds_list[clicked_idx]['patch'].get_x(), \
                self.gds_list[clicked_idx]['patch'].get_y()
                current_selection = self.get_selected_indices()
                if event.key in ['control', 'ctrl']:
                    self.toggle_selection(clicked_idx)
                elif clicked_idx not in current_selection:
                    self.set_selection(clicked_idx)
                self.drag_start_offsets = {idx: (self.gds_list[idx]['offset_x'], self.gds_list[idx]['offset_y']) for idx
                                           in self.get_selected_indices()}
                self.on_listbox_select();
                return

        elif event.button == 1 and event.key not in ['control', 'ctrl']:
            self.clear_selection();
            self.update_canvas_selection()

    def finalize_shape(self):
        self.save_snapshot()
        if self.draw_mode == 'crop':
            self.crop_box = list(self.draw_points)
        else:
            new_shape = self.draw_current_props.copy()
            new_shape['points'] = list(self.draw_points)
            self.user_shapes.append(new_shape)
        self.cancel_draw_mode()
        self.draw_preview(reset_view=False)

    def on_motion(self, event):
        if not event.inaxes: return
        self.last_mouse_event = event
        snap_x, snap_y, sn_x, sn_y = self.get_snapped_coordinate(event.xdata, event.ydata)

        if (self.measure_mode_var.get() and self.measure_state == 1 and self.measure_start_pt) or \
                (self.draw_mode in ['polygon', 'path'] and self.draw_points):
            if self.ctrl_pressed or getattr(event, 'key', None) in ['control', 'ctrl']:
                if self.measure_mode_var.get():
                    last_x, last_y = self.measure_start_pt
                else:
                    last_x, last_y = self.draw_points[-1]
                if abs(snap_x - last_x) > abs(snap_y - last_y):
                    snap_y = last_y
                else:
                    snap_x = last_x

        if self.draw_mode is not None:
            if self.draw_mode == 'text':
                if not self.temp_draw_preview:
                    tp = TextPath((snap_x, snap_y), self.draw_current_props['text'],
                                  size=self.draw_current_props['size'])
                    self.temp_draw_preview = PathPatch(tp, facecolor='#0044cc', edgecolor='none', zorder=350, alpha=0.5)
                    self.ax.add_patch(self.temp_draw_preview)
                else:
                    self.temp_draw_preview.set_path(TextPath((snap_x, snap_y), self.draw_current_props['text'],
                                                             size=self.draw_current_props['size']))
            elif self.draw_mode in ['box', 'via_array', 'crop'] and len(self.draw_points) == 1:
                x0, y0 = self.draw_points[0]
                if not self.temp_draw_preview:
                    fc = 'none' if self.draw_mode in ['via_array', 'crop'] else '#FF8C00'
                    ec = 'red' if self.draw_mode == 'crop' else (
                        '#00CED1' if self.draw_mode == 'via_array' else '#FF8C00')
                    ls = '--' if self.draw_mode == 'crop' else '-'
                    hatch = '..' if self.draw_mode == 'via_array' else None
                    self.temp_draw_preview = patches.Rectangle((min(x0, snap_x), min(y0, snap_y)), abs(snap_x - x0),
                                                               abs(snap_y - y0), fill=(self.draw_mode == 'box'),
                                                               facecolor=fc, edgecolor=ec, linestyle=ls, hatch=hatch,
                                                               alpha=0.5, linewidth=2)
                    self.ax.add_patch(self.temp_draw_preview)
                else:
                    self.temp_draw_preview.set_bounds(min(x0, snap_x), min(y0, snap_y), abs(snap_x - x0),
                                                      abs(snap_y - y0))
            elif self.draw_mode in ['polygon', 'path'] and len(self.draw_points) > 0:
                pts = self.draw_points + [(snap_x, snap_y)]
                if self.draw_mode == 'polygon':
                    xs, ys = zip(*pts)
                    if not self.temp_draw_preview:
                        self.temp_draw_preview, = self.ax.plot(xs, ys, color='red', linestyle='--', linewidth=2)
                    else:
                        self.temp_draw_preview.set_data(xs, ys)
                elif self.draw_mode == 'path':
                    path_points = [db.DPoint(x, y) for x, y in pts]
                    if len(path_points) >= 2:
                        hull_pts = [(pt.x, pt.y) for pt in
                                    db.DPath(path_points, self.draw_current_props['width']).polygon().each_point_hull()]
                        if not self.temp_draw_preview:
                            self.temp_draw_preview = patches.Polygon(hull_pts, closed=True, fill=True, facecolor='red',
                                                                     alpha=0.3, edgecolor='red')
                            self.ax.add_patch(self.temp_draw_preview)
                        else:
                            self.temp_draw_preview.set_xy(hull_pts)
            self.canvas.draw_idle();
            return

        if self.measure_mode_var.get():
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
            self.canvas.draw_idle();
            return

        if self.dragging_type is None: return

        if not self.drag_snapshot_taken: self.save_snapshot(); self.drag_snapshot_taken = True

        dx_raw = event.xdata - self.drag_start_x
        dy_raw = event.ydata - self.drag_start_y

        if self.dragging_type == 'text':
            nx, ny = self.rect_start_x + dx_raw, self.rect_start_y + dy_raw
            if self.grid_snap_var.get():
                try:
                    g_size = float(self.grid_size_var.get())
                    if g_size > 0: nx, ny = round(nx / g_size) * g_size, round(ny / g_size) * g_size
                except ValueError:
                    pass
            self.user_texts[self.dragging_idx]['x'], self.user_texts[self.dragging_idx]['y'] = nx, ny
            tp = TextPath((nx, ny), self.user_texts[self.dragging_idx]['text'],
                          size=self.user_texts[self.dragging_idx]['size'])
            self.user_texts[self.dragging_idx]['text_obj'].set_path(tp)
            self.canvas.draw_idle();
            return

        if self.dragging_type == 'shape':
            s = self.user_shapes[self.dragging_idx]
            dx, dy = dx_raw, dy_raw
            if self.grid_snap_var.get():
                try:
                    g_size = float(self.grid_size_var.get())
                    base_px, base_py = self.drag_start_offsets[0]
                    nx, ny = round((base_px + dx) / g_size) * g_size, round((base_py + dy) / g_size) * g_size
                    dx, dy = nx - base_px, ny - base_py
                except ValueError:
                    pass

            new_pts = [(ox + dx, oy + dy) for ox, oy in self.drag_start_offsets]
            s['points'] = new_pts
            if s['type'] in ['box', 'via_array']:
                x0, y0 = new_pts[0];
                x1, y1 = new_pts[1]
                s['patch'].set_bounds(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            elif s['type'] == 'polygon':
                s['patch'].set_xy(new_pts)
            elif s['type'] == 'path':
                path_points = [db.DPoint(x, y) for x, y in new_pts]
                if len(path_points) >= 2:
                    hull_pts = [(pt.x, pt.y) for pt in db.DPath(path_points, s['width']).polygon().each_point_hull()]
                    s['patch'].set_xy(hull_pts)
            self.canvas.draw_idle();
            return

        for line in self.guide_lines: line.remove()
        self.guide_lines.clear()

        handle_gds = self.gds_list[self.dragging_idx]
        t_box = handle_gds['trans'] * handle_gds['base_bbox']
        temp_ox, temp_oy = self.rect_start_x + dx_raw - t_box.left, self.rect_start_y + dy_raw - t_box.bottom

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
            drag_x_pois, drag_y_pois = self.get_pois(handle_gds, temp_ox, temp_oy)
            min_dx, min_dy = (self.ax.get_xlim()[1] - self.ax.get_xlim()[0]) * 0.02, (
                        self.ax.get_ylim()[1] - self.ax.get_ylim()[0]) * 0.02
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
            temp_ox += snap_shift_x;
            temp_oy += snap_shift_y

        delta_x, delta_y = temp_ox - self.drag_start_offsets[self.dragging_idx][0], temp_oy - \
                           self.drag_start_offsets[self.dragging_idx][1]

        for idx in self.drag_start_offsets:
            gds = self.gds_list[idx]
            new_ox, new_oy = self.drag_start_offsets[idx][0] + delta_x, self.drag_start_offsets[idx][1] + delta_y
            gds['offset_x'], gds['offset_y'] = new_ox, new_oy
            nx_final, ny_final = (gds['trans'] * gds['base_bbox']).left + new_ox, (
                        gds['trans'] * gds['base_bbox']).bottom + new_oy

            gds['patch'].set_x(nx_final);
            gds['patch'].set_y(ny_final)
            for pts, poly_patch in gds['poly_patches']:
                poly_patch.set_xy(
                    [((gds['trans'] * db.DPoint(px, py)).x + new_ox, (gds['trans'] * db.DPoint(px, py)).y + new_oy) for
                     px, py in pts])
            if gds['center_text']: gds['center_text'].set_position(
                (nx_final + (gds['trans'] * gds['base_bbox']).width() / 2,
                 ny_final + (gds['trans'] * gds['base_bbox']).height() / 2))

        if not is_grid_snapped:
            if best_snap_x is not None: self.guide_lines.append(
                self.ax.axvline(x=best_snap_x, color='#FF8C00', linestyle='--', linewidth=1.5, zorder=200))
            if best_snap_y is not None: self.guide_lines.append(
                self.ax.axhline(y=best_snap_y, color='#FF8C00', linestyle='--', linewidth=1.5, zorder=200))

        if self.get_selected_indices() and self.get_selected_indices()[0] == self.dragging_idx:
            x, y = self.get_anchor_coords(handle_gds, self.anchor_var.get())
            self.selected_x_var.set(f"{x:.3f}");
            self.selected_y_var.set(f"{y:.3f}")

        self.draw_overlaps();
        self.canvas.draw_idle()

    def on_release(self, event):
        if self.measure_mode_var.get() and event.button == 1: return

        if self.dragging_type is not None:
            self.dragging_type = None;
            self.dragging_idx = -1;
            self.drag_snapshot_taken = False;
            self.drag_start_offsets.clear()
            for line in self.guide_lines: line.remove()
            self.guide_lines.clear();
            self.update_canvas_selection();
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
        self.save_snapshot();
        o = self.gds_list[idx]
        self.gds_list.append(
            {'path': o['path'], 'name': o['name'], 'base_bbox': o['base_bbox'], 'trans': o['trans'] * db.DTrans(),
             'offset_x': o['offset_x'] + 200, 'offset_y': o['offset_y'] - 200, 'color': o['color'], 'patch': None,
             'center_text': None, 'true_polygons': o['true_polygons'], 'poly_patches': [],
             'layers': o.get('layers', [])})
        self.refresh_gds_list_ui()
        self.draw_preview()

    def action_create_array(self, idx):
        dialog = tk.Toplevel(self.root)
        dialog.title("Create Array");
        dialog.geometry("320x220");
        dialog.grab_set()
        rows_var, cols_var, spc_x_var, spc_y_var = tk.StringVar(value="2"), tk.StringVar(value="2"), tk.StringVar(
            value="1000"), tk.StringVar(value="1000")
        ttk.Label(dialog, text="Rows (Y):").grid(row=0, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=rows_var, width=15).grid(row=0, column=1)
        ttk.Label(dialog, text="Cols (X):").grid(row=1, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=cols_var, width=15).grid(row=1, column=1)
        ttk.Label(dialog, text="Space X(um):").grid(row=2, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=spc_x_var, width=15).grid(row=2, column=1)
        ttk.Label(dialog, text="Space Y(um):").grid(row=3, column=0, padx=15, pady=10, sticky=tk.W);
        ttk.Entry(dialog, textvariable=spc_y_var, width=15).grid(row=3, column=1)

        def on_ok():
            try:
                r, c, sx, sy = int(rows_var.get()), int(cols_var.get()), float(spc_x_var.get()), float(spc_y_var.get())
                if r < 1 or c < 1: return messagebox.showwarning("Warning", "Rows and Columns must be >= 1")
                self.save_snapshot();
                o = self.gds_list[idx]
                for i in range(r):
                    for j in range(c):
                        if i == 0 and j == 0: continue
                        self.gds_list.append(
                            {'path': o['path'], 'name': f"{o['name']}_R{i}C{j}", 'base_bbox': o['base_bbox'],
                             'trans': o['trans'] * db.DTrans(), 'offset_x': o['offset_x'] + j * sx,
                             'offset_y': o['offset_y'] + i * sy, 'color': o['color'], 'patch': None,
                             'center_text': None, 'true_polygons': o['true_polygons'], 'poly_patches': [],
                             'layers': o.get('layers', [])})
                self.refresh_gds_list_ui()
                self.draw_preview();
                self.status_var.set(f"Array created: {r}x{c} for {o['name']}");
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

    # ================= 核心：导出与所有后处理 =================
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

            # --- 1. 执行拼版 ---
            for idx, g in enumerate(self.gds_list):
                file_path = g['path']
                if file_path not in cache:
                    if not os.path.exists(file_path):
                        messagebox.showwarning("File Missing", f"源文件已被移动或删除，将被跳过导出：\n{file_path}")
                        cache[file_path] = None
                        continue
                    src_layout = db.Layout();
                    src_layout.read(file_path)
                    if idx == 0: target_layout.dbu = src_layout.dbu
                    src_top = src_layout.top_cells()[0]
                    for cell in src_layout.each_cell(): cell.name = f"chip{idx}_" + cell.name
                    new_cell = target_layout.create_cell(src_top.name);
                    new_cell.copy_tree(src_top)
                    cache[file_path] = new_cell.cell_index()

                if cache[file_path] is not None:
                    merged_top.insert(
                        db.DCellInstArray(cache[file_path], db.DTrans(g['offset_x'], g['offset_y']) * g['trans']))

            # --- 2. 图层映射 ---
            if getattr(self, 'layer_mapping', None):
                self.status_var.set("Applying Layer Mapping...")
                self.root.update()
                for (sl, sdt), (tl, tdt) in self.layer_mapping.items():
                    if (sl, sdt) == (tl, tdt): continue
                    src_info, tgt_info = db.LayerInfo(sl, sdt), db.LayerInfo(tl, tdt)
                    src_idx = target_layout.find_layer(src_info)
                    if src_idx is not None:
                        tgt_idx = target_layout.layer(tgt_info)
                        if src_idx != tgt_idx:
                            for cell in target_layout.each_cell():
                                cell.shapes(tgt_idx).insert(cell.shapes(src_idx))
                                cell.shapes(src_idx).clear()
                            try:
                                target_layout.delete_layer(src_idx)
                            except Exception:
                                pass

            dbu = target_layout.dbu

            # --- 3. 保护环 ---
            if self.enable_seal_var.get():
                self.status_var.set("Generating Seal Ring...")
                self.root.update()
                seal_idx = target_layout.layer(
                    db.LayerInfo(int(self.seal_layer_var.get()), int(self.seal_datatype_var.get())))
                s_width_dbu, s_margin_dbu = int(float(self.seal_width_var.get()) / dbu), int(
                    float(self.seal_margin_var.get()) / dbu)
                w_dbu, h_dbu = int(self.block_width / dbu), int(self.block_height / dbu)
                outer_box = db.Box(s_margin_dbu, s_margin_dbu, w_dbu - s_margin_dbu, h_dbu - s_margin_dbu)
                inner_box = db.Box(s_margin_dbu + s_width_dbu, s_margin_dbu + s_width_dbu,
                                   w_dbu - s_margin_dbu - s_width_dbu, h_dbu - s_margin_dbu - s_width_dbu)
                merged_top.shapes(seal_idx).insert(db.Region(outer_box) - db.Region(inner_box))

            # --- 4. 几何形状 (Box, Polygon, Path, Via Array) ---
            if self.user_shapes:
                self.status_var.set("Generating Custom Shapes & Vias...")
                self.root.update()
                for s in self.user_shapes:
                    lyr_idx = target_layout.layer(db.LayerInfo(s['layer'], s['dt']))
                    pts = s['points']
                    if s['type'] == 'box':
                        d_box = db.DBox(min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1]), max(pts[0][0], pts[1][0]),
                                        max(pts[0][1], pts[1][1]))
                        merged_top.shapes(lyr_idx).insert(d_box)
                    elif s['type'] == 'polygon':
                        d_poly = db.DPolygon([db.DPoint(x, y) for x, y in pts])
                        merged_top.shapes(lyr_idx).insert(d_poly)
                    elif s['type'] == 'path':
                        d_path = db.DPath([db.DPoint(x, y) for x, y in pts], s['width'])
                        merged_top.shapes(lyr_idx).insert(d_path)
                    elif s['type'] == 'via_array':
                        # 完全重写：使用平铺 Region 写入真实的过孔阵列，100% 避免被 Clip 忽略
                        vw_dbu, vh_dbu = int(s['via_w'] / dbu), int(s['via_h'] / dbu)
                        px_dbu, py_dbu = int(s['pitch_x'] / dbu), int(s['pitch_y'] / dbu)
                        if px_dbu > 0 and py_dbu > 0 and vw_dbu > 0 and vh_dbu > 0:
                            min_x, min_y = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
                            max_x, max_y = max(pts[0][0], pts[1][0]), max(pts[0][1], pts[1][1])
                            min_x_dbu, min_y_dbu = int(min_x / dbu), int(min_y / dbu)
                            max_x_dbu, max_y_dbu = int(max_x / dbu), int(max_y / dbu)

                            via_region = db.Region()
                            curr_x = min_x_dbu
                            while curr_x + vw_dbu <= max_x_dbu:
                                curr_y = min_y_dbu
                                while curr_y + vh_dbu <= max_y_dbu:
                                    via_region.insert(db.Box(curr_x, curr_y, curr_x + vw_dbu, curr_y + vh_dbu))
                                    curr_y += py_dbu
                                curr_x += px_dbu
                            merged_top.shapes(lyr_idx).insert(via_region)

            # --- 5. 文字 ---
            if self.user_texts:
                self.status_var.set("Generating Text Polygons...")
                self.root.update()
                gen = db.TextGenerator.default_generator()
                for ut in self.user_texts:
                    lbl_idx = target_layout.layer(db.LayerInfo(ut['layer'], ut['dt']))
                    text_region = gen.text(ut['text'], dbu)
                    if text_region.bbox().height() > 0:
                        text_cell = target_layout.create_cell(f"TEXT_{ut['text']}")
                        text_cell.shapes(lbl_idx).insert(text_region)
                        _ = text_cell.bbox()  # <--- 强制刷新，防止文本 Cell 被 Clip 引擎丢弃
                        current_h_um = text_region.bbox().height() * dbu
                        scale_factor = ut['size'] / current_h_um if current_h_um > 0 else 1.0
                        t = db.DCplxTrans(scale_factor, 0, False, ut['x'], ut['y'])
                        merged_top.insert(db.DCellInstArray(text_cell.cell_index(), t))

            # --- 6. Dummy Fill ---
            if self.enable_dummy_var.get():
                self.status_var.set("Calculating Dummy Fill...")
                self.root.update()
                layer_num, datatype_num = int(self.dummy_layer_var.get()), int(self.dummy_datatype_var.get())
                size_um, space_um = float(self.dummy_size_var.get()), float(self.dummy_spacing_var.get())
                margin_um = float(self.dummy_margin_var.get())
                layer_idx = target_layout.layer(db.LayerInfo(layer_num, datatype_num))

                keep_out_region = db.Region(merged_top.begin_shapes_rec(layer_idx))
                keep_out_region.size(int(margin_um / dbu));
                keep_out_region.merge()

                dummy_region = db.Region()
                box_size_dbu, box_pitch_dbu = int(size_um / dbu), int((size_um + space_um) / dbu)
                fill_area = db.Box(0, 0, int(self.block_width / dbu), int(self.block_height / dbu))

                y, row_index = fill_area.bottom, 0
                while y + box_size_dbu <= fill_area.top:
                    x_offset = int(box_pitch_dbu / 2) if self.staggered_var.get() and (row_index % 2 != 0) else 0
                    x = fill_area.left + x_offset
                    while x + box_size_dbu <= fill_area.right:
                        dummy_region.insert(db.Box(x, y, x + box_size_dbu, y + box_size_dbu))
                        x += box_pitch_dbu
                    y += box_pitch_dbu
                    row_index += 1

                final_dummy_region = dummy_region - dummy_region.interacting(keep_out_region)
                merged_top.shapes(layer_idx).insert(final_dummy_region)

            # --- 7. Crop 裁剪 ---
            if self.crop_box:
                self.status_var.set("Clipping to Crop Box...")
                self.root.update()

                # 强制刷新顶层 BBox 缓存，防止 KLayout Clip 引擎遗漏最新插入的多边形
                _ = merged_top.bbox()

                pts = self.crop_box
                min_x, min_y = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
                max_x, max_y = max(pts[0][0], pts[1][0]), max(pts[0][1], pts[1][1])

                clip_box_dbu = db.Box(int(min_x / dbu), int(min_y / dbu), int(max_x / dbu), int(max_y / dbu))
                clipped_cell_idx = target_layout.clip(merged_top.cell_index(), clip_box_dbu)

                merged_top = target_layout.cell(clipped_cell_idx)
                merged_top.name = self.top_cell_name_var.get() or "MERGED"

            self.status_var.set("Writing to disk...")
            self.root.update()

            # 使用 SaveLayoutOptions 确保只导出最终的 Top Cell
            save_opt = db.SaveLayoutOptions()
            save_opt.add_cell(merged_top.cell_index())
            target_layout.write(out_p, save_opt)

            self.status_var.set("Ready")
            messagebox.showinfo("OK", "Merged Success!\n导出合并成功！")

        except Exception as e:
            self.status_var.set("Ready")
            messagebox.showerror("Error", f"Failed to export merged GDS:\n{str(e)}")


if __name__ == "__main__":
    app = GDSMultiStitcherApp()
    app.root.mainloop()