import os
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
        window_height = 680
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x_cordinate = int((screen_width / 2) - (window_width / 2))
        y_cordinate = int((screen_height / 2) - (window_height / 2))
        self.root.geometry(f"{window_width}x{window_height}+{x_cordinate}+{y_cordinate}")

        # Core data structure:
        # [{'path': str, 'name': str, 'base_bbox': db.DBox, 'trans': db.DTrans,
        #   'offset_x': float, 'offset_y': float, 'color': str, 'patch': object}]
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
        self.block_patch = None

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

        # 1b. Block Size Settings
        block_settings_frame = ttk.LabelFrame(left_frame, text="1b. Block Size Settings", padding=10)
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
        self.apply_block_size_btn.pack(fill=tk.X, pady=(10, 0))

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
        right_frame = ttk.LabelFrame(main_frame, text="Interactive Canvas (Drag to Move, Right-Click for Options)",
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

        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=2)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    # ---------------- BBox Extraction ----------------
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
            self.draw_preview()
        except ValueError as e:
            messagebox.showerror("Format Error", str(e))

    def add_gds(self):
        filepaths = filedialog.askopenfilenames(title="Select GDS Files", filetypes=[("GDS Files", "*.gds")])
        for filepath in filepaths:
            try:
                base_bbox = self.extract_base_bbox(filepath)
                gds_info = {
                    'path': filepath,
                    'name': os.path.basename(filepath),
                    'base_bbox': base_bbox,
                    'trans': db.DTrans(),
                    'offset_x': 0.0,
                    'offset_y': 0.0,
                    'color': self.color_palette[len(self.gds_list) % len(self.color_palette)],
                    'patch': None
                }
                self.gds_list.append(gds_info)
                self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {gds_info['name']}")
            except Exception as e:
                messagebox.showerror("Read Error", f"Cannot read {filepath}:\n{str(e)}")

        if filepaths:
            self.draw_preview()

    # ---------------- Context Menu (Right-Click) ----------------
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

    # ---------- Action Functions ----------
    def action_duplicate(self, idx):
        orig = self.gds_list[idx]
        shift = self.block_width * 0.05 if self.block_width > 0 else 500

        new_gds = {
            'path': orig['path'],
            'name': orig['name'] + "_copy",
            'base_bbox': orig['base_bbox'],
            'trans': orig['trans'] * db.DTrans(),
            'offset_x': orig['offset_x'] + shift,
            'offset_y': orig['offset_y'] - shift,
            'color': orig['color'],
            'patch': None
        }
        self.gds_list.append(new_gds)
        self.listbox.insert(tk.END, f"[{len(self.gds_list)}] {new_gds['name']}")
        self.status_var.set(f"Duplicated {orig['name']}")
        self.draw_preview()

    def action_rotate_ccw(self, idx):
        self.gds_list[idx]['trans'] = db.DTrans(1, False, 0, 0) * self.gds_list[idx]['trans']
        self.status_var.set(f"Rotated 90° CCW: {self.gds_list[idx]['name']}")
        self.draw_preview()

    def action_rotate_cw(self, idx):
        # 顺时针旋转90度相当于逆时针旋转270度 (rot=3)
        self.gds_list[idx]['trans'] = db.DTrans(3, False, 0, 0) * self.gds_list[idx]['trans']
        self.status_var.set(f"Rotated 90° CW: {self.gds_list[idx]['name']}")
        self.draw_preview()

    def action_flip_horizontal(self, idx):
        # 左右镜像
        self.gds_list[idx]['trans'] = db.DTrans(2, True, 0, 0) * self.gds_list[idx]['trans']
        self.status_var.set(f"Flipped Left-Right: {self.gds_list[idx]['name']}")
        self.draw_preview()

    def action_flip_vertical(self, idx):
        # 上下镜像：直接沿 X 轴翻转 (rot=0, mirror=True)
        self.gds_list[idx]['trans'] = db.DTrans(0, True, 0, 0) * self.gds_list[idx]['trans']
        self.status_var.set(f"Flipped Top-Bottom: {self.gds_list[idx]['name']}")
        self.draw_preview()

    def action_delete(self, idx):
        del self.gds_list[idx]
        self.listbox.delete(0, tk.END)
        for i, gds in enumerate(self.gds_list):
            self.listbox.insert(tk.END, f"[{i + 1}] {gds['name']}")
        self.draw_preview()

    def action_delete_selected(self):
        selection = self.listbox.curselection()
        if selection:
            self.action_delete(selection[0])

    # ---------------- Core Drawing Logic ----------------
    def draw_preview(self):
        self.ax.clear()

        rect_block = patches.Rectangle((0, 0), self.block_width, self.block_height,
                                       linewidth=5, edgecolor='black', facecolor='none',
                                       label='_nolegend_', alpha=1.0)
        self.ax.add_patch(rect_block)

        if not self.gds_list:
            self.ax.text(self.block_width / 2, self.block_height / 2,
                         'Add files and drag them here to stitch\nRight-Click a block for Copy/Rotate/Flip',
                         ha='center', va='center', color='gray', fontsize=12)
            pad_x, pad_y = self.block_width * 0.1, self.block_height * 0.1
            self.ax.set_xlim(0 - pad_x, self.block_width + pad_x)
            self.ax.set_ylim(0 - pad_y, self.block_height + pad_y)
            self.ax.set_xticks([])
            self.ax.set_yticks([])
            self.canvas.draw()
            return

        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            start_x = t_box.left + gds['offset_x']
            start_y = t_box.bottom + gds['offset_y']

            rect = patches.Rectangle((start_x, start_y), t_box.width(), t_box.height(),
                                     linewidth=2, edgecolor=gds['color'], facecolor=gds['color'],
                                     alpha=0.3, label=gds['name'])
            self.ax.add_patch(rect)
            gds['patch'] = rect

        self.ax.autoscale_view()
        self.ax.set_aspect('equal', adjustable='datalim')

        handles, labels = self.ax.get_legend_handles_labels()
        if labels:
            self.ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), frameon=True, fontsize=9, borderaxespad=0.)

        self.ax.grid(True, linestyle='--', alpha=0.6)
        self.canvas.draw()

    # ---------------- Mouse Interaction ----------------
    def on_press(self, event):
        if event.inaxes != self.ax: return

        for idx in range(len(self.gds_list) - 1, -1, -1):
            gds = self.gds_list[idx]
            if gds['patch'] and gds['patch'].contains(event)[0]:

                # Right-click (Context Menu)
                if event.button in [2, 3]:
                    self.show_context_menu(idx)
                    return

                # Left-click (Drag)
                elif event.button == 1:
                    self.dragging_idx = idx
                    self.drag_start_x = event.xdata
                    self.drag_start_y = event.ydata
                    self.rect_start_x = gds['patch'].get_x()
                    self.rect_start_y = gds['patch'].get_y()

                    gds['patch'].set_alpha(0.6)
                    self.canvas.draw_idle()

                    self.listbox.selection_clear(0, tk.END)
                    self.listbox.selection_set(idx)
                    return

    def on_motion(self, event):
        if self.dragging_idx == -1 or event.inaxes != self.ax: return

        gds = self.gds_list[self.dragging_idx]
        delta_x = event.xdata - self.drag_start_x
        delta_y = event.ydata - self.drag_start_y

        new_x = self.rect_start_x + delta_x
        new_y = self.rect_start_y + delta_y

        gds['patch'].set_x(new_x)
        gds['patch'].set_y(new_y)
        self.canvas.draw_idle()

        t_box = gds['trans'] * gds['base_bbox']
        gds['offset_x'] = new_x - t_box.left
        gds['offset_y'] = new_y - t_box.bottom

    def on_release(self, event):
        if self.dragging_idx != -1:
            if self.gds_list[self.dragging_idx]['patch']:
                self.gds_list[self.dragging_idx]['patch'].set_alpha(0.3)
            self.dragging_idx = -1
            self.ax.relim()
            self.ax.autoscale_view()
            self.canvas.draw_idle()

    # ---------------- Export with Instantiation (Fixed Copied Block Bug) ----------------
    def execute_stitch(self):
        if not self.gds_list:
            messagebox.showwarning("Notice", "No GDS files have been added yet!")
            return

        out_path = filedialog.asksaveasfilename(title="Save Stitched GDS", defaultextension=".gds",
                                                filetypes=[("GDS Files", "*.gds")])
        if not out_path: return

        top_cell_name = self.top_cell_name_var.get().strip() or "MERGED_CHIP"
        self.status_var.set("Merging and transforming, please wait...")
        self.root.update()

        try:
            layout = db.Layout()
            merged_top = layout.create_cell(top_cell_name)

            loaded_files_cache = {}

            for i, gds_info in enumerate(self.gds_list):
                file_path = gds_info['path']

                if file_path not in loaded_files_cache:
                    old_top_indices = [c.cell_index() for c in layout.top_cells()]
                    layout.read(file_path)

                    new_top_cells = [c for c in layout.top_cells()
                                     if c.cell_index() not in old_top_indices
                                     and c.cell_index() != merged_top.cell_index()]

                    if new_top_cells:
                        loaded_files_cache[file_path] = new_top_cells[0].cell_index()

                if file_path in loaded_files_cache:
                    cell_to_instantiate_idx = loaded_files_cache[file_path]

                    final_trans = db.DTrans(gds_info['offset_x'], gds_info['offset_y']) * gds_info['trans']
                    merged_top.insert(db.DCellInstArray(cell_to_instantiate_idx, final_trans))

            layout.write(out_path)
            self.status_var.set("Stitch successful!")
            messagebox.showinfo("Success",
                                f"Successfully merged {len(self.gds_list)} items with transformations!\nSaved to:\n{out_path}")
        except Exception as e:
            self.status_var.set("Export failed!")
            messagebox.showerror("Error", f"An error occurred during processing:\n{str(e)}")


if __name__ == "__main__":
    app = GDSMultiStitcherApp()
    app.root.mainloop()


