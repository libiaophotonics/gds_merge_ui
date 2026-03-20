import os, math, json, sys
from PyQt5 import QtWidgets, QtCore, QtGui
import matplotlib

matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.patches as patches
import matplotlib.colors as mcolors
from matplotlib.textpath import TextPath
from matplotlib.patches import PathPatch
from matplotlib.collections import PolyCollection
import matplotlib.transforms as mtransforms
import klayout.db as db

plt.rcParams['font.family'] = ['sans-serif']
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans', 'Arial', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False


class GDSMergerProQt(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GDS MERGER Pro - Advanced Mask Prep Tool (PyQt5 Edition)")
        self.resize(1300, 800)
        self.setAcceptDrops(True)

        self.gds_list, self.measurements, self.undo_stack, self.guide_lines, self.overlap_patches = [], [], [], [], []
        self.user_texts, self.user_shapes = [], []
        self.crop_box = None
        self.layer_mapping = {}

        self.dragging_type, self.dragging_idx = None, -1
        self.drag_start_x = self.drag_start_y = self.rect_start_x = self.rect_start_y = 0
        self.drag_start_offsets = {}
        self.drag_snapshot_taken = False

        self.measure_start_pt = self.measure_line = self.measure_text = self.snap_indicator = None
        self.measure_state = 0

        self.draw_mode, self.draw_points, self.draw_current_props, self.temp_draw_preview = None, [], {}, None
        self.ctrl_pressed, self.last_mouse_event = False, None

        self.block_w, self.block_h = 5000.0, 5000.0
        self.top_cell_name = "MERGED_CHIP"
        self.color_palette = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22',
                              '#17becf']

        self.setup_ui()
        self.draw_preview(reset_view=True)

    def on_key_press(self, event):
        if event.key in ['control', 'ctrl']:
            self.ctrl_pressed = True
            if self.last_mouse_event and getattr(self.last_mouse_event, 'inaxes', False):
                self.on_motion(self.last_mouse_event)
        # --- 新增：画布选中后使用 Delete 键删除器件 ---
        elif event.key in ['delete', 'backspace']:
            self.action_delete_selected()

    def on_key_release(self, event):
        if event.key in ['control', 'ctrl']:
            self.ctrl_pressed = False
            if self.last_mouse_event and getattr(self.last_mouse_event, 'inaxes', False):
                self.on_motion(self.last_mouse_event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        added = False
        for url in event.mimeData().urls():
            filepath = url.toLocalFile()
            if filepath.lower().endswith('.gds'):
                self.process_single_gds(filepath);
                added = True
        if added: self.draw_preview(reset_view=True)

    def setup_ui(self):
        self.main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.setCentralWidget(self.main_splitter)

        # --- Left Panel ---
        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)

        proj_layout = QtWidgets.QHBoxLayout()
        btn_load = QtWidgets.QPushButton("📂 Load Project");
        btn_load.clicked.connect(self.action_load_project)
        btn_save = QtWidgets.QPushButton("💾 Save Project");
        btn_save.clicked.connect(self.action_save_project)
        proj_layout.addWidget(btn_load);
        proj_layout.addWidget(btn_save)
        left_layout.addLayout(proj_layout)

        grp_list = QtWidgets.QGroupBox("1a. GDS Files (Drag & Drop)")
        vbox_list = QtWidgets.QVBoxLayout(grp_list)
        btn_gds_layout = QtWidgets.QHBoxLayout()
        btn_add = QtWidgets.QPushButton("➕ Add");
        btn_add.clicked.connect(self.add_gds)
        btn_del = QtWidgets.QPushButton("➖ Del");
        btn_del.clicked.connect(self.action_delete_selected)
        btn_gds_layout.addWidget(btn_add);
        btn_gds_layout.addWidget(btn_del)
        vbox_list.addLayout(btn_gds_layout)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list_widget.itemSelectionChanged.connect(self.on_listbox_select)
        vbox_list.addWidget(self.list_widget)
        left_layout.addWidget(grp_list, 1)

        grp_size = QtWidgets.QGroupBox("1b. Total Block Size (um)")
        size_layout = QtWidgets.QHBoxLayout(grp_size)
        size_layout.addWidget(QtWidgets.QLabel("W x H:"))
        self.inp_block_w = QtWidgets.QLineEdit(str(self.block_w));
        self.inp_block_h = QtWidgets.QLineEdit(str(self.block_h))
        btn_apply_size = QtWidgets.QPushButton("Apply");
        btn_apply_size.clicked.connect(self.update_block_size)
        size_layout.addWidget(self.inp_block_w);
        size_layout.addWidget(self.inp_block_h);
        size_layout.addWidget(btn_apply_size)
        left_layout.addWidget(grp_size)

        self.tabs = QtWidgets.QTabWidget()
        tab_pos = QtWidgets.QWidget()
        pos_layout = QtWidgets.QFormLayout(tab_pos)
        self.cb_anchor = QtWidgets.QComboBox()
        self.cb_anchor.addItems(["Bottom-Left", "Bottom-Right", "Top-Left", "Top-Right", "Center"])
        self.cb_anchor.currentIndexChanged.connect(self.on_anchor_change)
        pos_layout.addRow("Anchor:", self.cb_anchor)

        h_pos = QtWidgets.QHBoxLayout()
        self.inp_x = QtWidgets.QLineEdit("0.0");
        self.inp_y = QtWidgets.QLineEdit("0.0")
        btn_apply_pos = QtWidgets.QPushButton("Apply");
        btn_apply_pos.clicked.connect(self.apply_manual_position)
        h_pos.addWidget(QtWidgets.QLabel("X:"));
        h_pos.addWidget(self.inp_x)
        h_pos.addWidget(QtWidgets.QLabel("Y:"));
        h_pos.addWidget(self.inp_y)
        h_pos.addWidget(btn_apply_pos)
        pos_layout.addRow("Pos:", h_pos)
        self.tabs.addTab(tab_pos, "🎯 Pos")

        tab_finish = QtWidgets.QWidget()
        finish_layout = QtWidgets.QVBoxLayout(tab_finish)
        grp_dummy = QtWidgets.QGroupBox("1. Dummy Fill")
        f_dummy = QtWidgets.QFormLayout(grp_dummy)
        self.chk_dummy = QtWidgets.QCheckBox("Enable");
        self.chk_stagger = QtWidgets.QCheckBox("Staggered");
        self.chk_stagger.setChecked(True)
        h_d1 = QtWidgets.QHBoxLayout();
        h_d1.addWidget(self.chk_dummy);
        h_d1.addWidget(self.chk_stagger);
        f_dummy.addRow(h_d1)
        h_d2 = QtWidgets.QHBoxLayout();
        self.inp_dlyr = QtWidgets.QLineEdit("1");
        self.inp_ddt = QtWidgets.QLineEdit("0")
        h_d2.addWidget(QtWidgets.QLabel("Lyr:"));
        h_d2.addWidget(self.inp_dlyr);
        h_d2.addWidget(QtWidgets.QLabel("DT:"));
        h_d2.addWidget(self.inp_ddt);
        f_dummy.addRow(h_d2)
        h_d3 = QtWidgets.QHBoxLayout();
        self.inp_dsize = QtWidgets.QLineEdit("5.0");
        self.inp_dspc = QtWidgets.QLineEdit("5.0")
        h_d3.addWidget(QtWidgets.QLabel("Size:"));
        h_d3.addWidget(self.inp_dsize);
        h_d3.addWidget(QtWidgets.QLabel("Spc:"));
        h_d3.addWidget(self.inp_dspc);
        f_dummy.addRow(h_d3)
        self.inp_dmargin = QtWidgets.QLineEdit("3.0");
        f_dummy.addRow("Margin:", self.inp_dmargin)
        finish_layout.addWidget(grp_dummy)

        grp_seal = QtWidgets.QGroupBox("2. Seal Ring")
        f_seal = QtWidgets.QFormLayout(grp_seal)
        self.chk_seal = QtWidgets.QCheckBox("Enable Seal Ring");
        f_seal.addRow(self.chk_seal)
        h_s1 = QtWidgets.QHBoxLayout();
        self.inp_slyr = QtWidgets.QLineEdit("10");
        self.inp_sdt = QtWidgets.QLineEdit("0")
        h_s1.addWidget(QtWidgets.QLabel("Lyr:"));
        h_s1.addWidget(self.inp_slyr);
        h_s1.addWidget(QtWidgets.QLabel("DT:"));
        h_s1.addWidget(self.inp_sdt);
        f_seal.addRow(h_s1)
        h_s2 = QtWidgets.QHBoxLayout();
        self.inp_sw = QtWidgets.QLineEdit("20.0");
        self.inp_sdist = QtWidgets.QLineEdit("0.0")
        h_s2.addWidget(QtWidgets.QLabel("Width:"));
        h_s2.addWidget(self.inp_sw);
        h_s2.addWidget(QtWidgets.QLabel("Dist:"));
        h_s2.addWidget(self.inp_sdist);
        f_seal.addRow(h_s2)
        finish_layout.addWidget(grp_seal)
        self.tabs.addTab(tab_finish, "✨ Finish")

        tab_export = QtWidgets.QWidget()
        export_layout = QtWidgets.QVBoxLayout(tab_export)
        export_layout.addWidget(QtWidgets.QLabel("Merged Cell Name:"))
        self.inp_topname = QtWidgets.QLineEdit(self.top_cell_name);
        export_layout.addWidget(self.inp_topname)
        btn_map = QtWidgets.QPushButton("🛠️ Layer Mapping");
        btn_map.clicked.connect(self.open_layer_mapping_dialog);
        export_layout.addWidget(btn_map)
        btn_exp = QtWidgets.QPushButton("💾 EXPORT GDS");
        btn_exp.setMinimumHeight(40);
        btn_exp.setStyleSheet("background-color: #0078D7; color: white; font-weight: bold; border-radius: 4px;")
        btn_exp.clicked.connect(self.execute_stitch);
        export_layout.addWidget(btn_exp)
        export_layout.addStretch()
        self.tabs.addTab(tab_export, "💾 Export")
        left_layout.addWidget(self.tabs)

        # --- Center Panel ---
        center_panel = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 5, 0, 5)

        tb1 = QtWidgets.QHBoxLayout()
        btn_undo = QtWidgets.QPushButton("↩️ Undo");
        btn_undo.clicked.connect(self.action_undo);
        tb1.addWidget(btn_undo)
        btn_fit = QtWidgets.QPushButton("🔍 Fit");
        btn_fit.clicked.connect(lambda: self.draw_preview(reset_view=True));
        tb1.addWidget(btn_fit)
        self.btn_measure = QtWidgets.QPushButton("📏 Measure");
        self.btn_measure.setCheckable(True);
        self.btn_measure.toggled.connect(self.on_measure_toggle);
        tb1.addWidget(self.btn_measure)
        self.btn_overlap = QtWidgets.QPushButton("🔴 Overlap (ON)");
        self.btn_overlap.setCheckable(True);
        self.btn_overlap.setChecked(True);
        self.btn_overlap.toggled.connect(self.on_overlap_toggle);
        tb1.addWidget(self.btn_overlap)
        self.btn_bbox = QtWidgets.QPushButton("✅ BBox Only");
        self.btn_bbox.setCheckable(True);
        self.btn_bbox.setChecked(True);
        self.btn_bbox.toggled.connect(self.on_bbox_toggle);
        tb1.addWidget(self.btn_bbox)
        self.chk_snap = QtWidgets.QCheckBox("🌐 Snap");
        self.inp_snap = QtWidgets.QLineEdit("10.0");
        self.inp_snap.setFixedWidth(50)
        tb1.addWidget(self.chk_snap);
        tb1.addWidget(self.inp_snap);
        tb1.addStretch()
        center_layout.addLayout(tb1)

        tb2 = QtWidgets.QHBoxLayout()
        btn_text = QtWidgets.QPushButton("📝 Text");
        btn_text.clicked.connect(self.action_add_text_dialog);
        tb2.addWidget(btn_text)
        btn_box = QtWidgets.QPushButton("🔲 Box");
        btn_box.clicked.connect(lambda: self.action_add_shape_dialog('box'));
        tb2.addWidget(btn_box)
        btn_poly = QtWidgets.QPushButton("🔶 Poly");
        btn_poly.clicked.connect(lambda: self.action_add_shape_dialog('polygon'));
        tb2.addWidget(btn_poly)
        btn_path = QtWidgets.QPushButton("〰️ Path");
        btn_path.clicked.connect(lambda: self.action_add_shape_dialog('path'));
        tb2.addWidget(btn_path)
        btn_via = QtWidgets.QPushButton("⚄ ViaArray");
        btn_via.clicked.connect(lambda: self.action_add_shape_dialog('via_array'));
        tb2.addWidget(btn_via)
        btn_crop = QtWidgets.QPushButton("✂️ Crop");
        btn_crop.clicked.connect(self.action_draw_crop_box);
        tb2.addWidget(btn_crop)
        btn_clear = QtWidgets.QPushButton("🗑️ Clear");
        btn_clear.clicked.connect(self.action_clear_annotations);
        tb2.addWidget(btn_clear)
        self.cb_align_go = QtWidgets.QComboBox();
        self.cb_align_go.addItems(
            ["Align Left", "Align Center X", "Align Right", "Align Top", "Align Center Y", "Align Bottom",
             "Distribute H", "Distribute V"])
        btn_align = QtWidgets.QPushButton("▶ Align");
        btn_align.clicked.connect(self.execute_align)
        tb2.addWidget(self.cb_align_go);
        tb2.addWidget(btn_align);
        tb2.addStretch()
        center_layout.addLayout(tb2)

        self.figure = plt.Figure(figsize=(6, 5), dpi=100);
        self.figure.patch.set_facecolor('#2b2b2b')
        self.ax = self.figure.add_subplot(111);
        self.ax.set_facecolor('#1e1e1e')
        self.canvas = FigureCanvas(self.figure);
        self.canvas.setFocusPolicy(QtCore.Qt.StrongFocus)
        center_layout.addWidget(self.canvas)

        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('button_release_event', self.on_release)
        self.canvas.mpl_connect('scroll_event', self.on_scroll)
        self.canvas.mpl_connect('key_press_event', self.on_key_press)
        self.canvas.mpl_connect('key_release_event', self.on_key_release)

        self.status_label = QtWidgets.QLabel("Ready: You can drag and drop GDS files here.");
        center_layout.addWidget(self.status_label)

        # --- Right Panel ---
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)
        grp_layers = QtWidgets.QGroupBox("Layers")
        l_layout = QtWidgets.QVBoxLayout(grp_layers)
        btn_refresh = QtWidgets.QPushButton("🔄 Refresh");
        btn_refresh.clicked.connect(self.refresh_layer_list);
        l_layout.addWidget(btn_refresh)
        self.layer_list = QtWidgets.QListWidget();
        l_layout.addWidget(self.layer_list)
        right_layout.addWidget(grp_layers)

        self.main_splitter.addWidget(left_panel);
        self.main_splitter.addWidget(center_panel);
        self.main_splitter.addWidget(right_panel)
        self.main_splitter.setSizes([350, 800, 150])

    def refresh_layer_list(self):
        self.layer_list.clear();
        global_layers = set()
        for gds in self.gds_list: global_layers.update(gds.get('layers', []))
        for l, d in sorted(list(global_layers)): self.layer_list.addItem(f"{l}/{d}")

    # ================= 核心业务 =================
    def process_single_gds(self, filepath):
        try:
            self.save_snapshot()
            base_name = os.path.splitext(os.path.basename(filepath))[0]
            base_bbox, true_polygons, layers = self.parse_gds_info(filepath)
            cx_block, cy_block = self.block_w / 2.0, self.block_h / 2.0
            cx_gds, cy_gds = (base_bbox.left + base_bbox.right) / 2.0, (base_bbox.bottom + base_bbox.top) / 2.0
            gds_info = {'path': filepath, 'name': base_name, 'base_bbox': base_bbox, 'trans': db.DTrans(),
                        'offset_x': cx_block - cx_gds, 'offset_y': cy_block - cy_gds,
                        'color': self.color_palette[len(self.gds_list) % len(self.color_palette)],
                        'patch': None, 'shadow_patch': None, 'collection': None, 'center_text': None,
                        'true_polygons': true_polygons, 'layers': layers}
            self.gds_list.append(gds_info)
            self.list_widget.addItem(f"[{len(self.gds_list)}] {base_name}")
            self.refresh_layer_list()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to load {filepath}:\n{str(e)}")

    def parse_gds_info(self, filepath):
        layout = db.Layout();
        layout.read(filepath)
        top_cell = layout.top_cells()[0]
        base_bbox = top_cell.dbbox()
        layers = [(layout.get_info(li).layer, layout.get_info(li).datatype) for li in layout.layer_indexes()]
        region = db.Region()
        for li in layout.layer_indexes(): region.insert(top_cell.begin_shapes_rec(li))
        region.merge();
        region = region.hulls();
        trans = db.DCplxTrans(layout.dbu)
        true_polygons = [[(pt.x, pt.y) for pt in db.DPolygon(poly).transformed(trans).each_point_hull()] for poly in
                         region.each() if list(db.DPolygon(poly).transformed(trans).each_point_hull())]
        return base_bbox, true_polygons, layers

    def add_gds(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Open GDS", "", "GDS Files (*.gds)")
        added = False
        for p in paths: self.process_single_gds(p); added = True
        if added: self.draw_preview(reset_view=True)

    def on_bbox_toggle(self):
        self.btn_bbox.setText("✅ BBox Only" if self.btn_bbox.isChecked() else "🔲 Full Detail")
        self.draw_preview(reset_view=False)

    def on_overlap_toggle(self):
        self.btn_overlap.setText("🔴 Overlaps (ON)" if self.btn_overlap.isChecked() else "⭕ Overlaps (OFF)")
        self.draw_overlaps();
        self.canvas.draw_idle()

    def draw_overlaps(self):
        for p in getattr(self, 'overlap_patches', []):
            try:
                p.remove()
            except:
                pass
        self.overlap_patches.clear()
        if not self.btn_overlap.isChecked(): return
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
        self.gds_list.clear();
        self.list_widget.clear()
        for i, item in enumerate(snapshot['gds_list']):
            gds_info = {'path': item['path'], 'name': item['name'], 'base_bbox': item['base_bbox'],
                        'trans': item['trans'], 'offset_x': item['offset_x'], 'offset_y': item['offset_y'],
                        'color': item['color'], 'patch': None, 'shadow_patch': None, 'collection': None,
                        'center_text': None, 'true_polygons': item['true_polygons'], 'layers': item.get('layers', [])}
            self.gds_list.append(gds_info);
            self.list_widget.addItem(f"[{i + 1}] {item['name']}")
        self.refresh_layer_list();
        self.measurements = snapshot.get('measurements', [])
        self.user_texts = snapshot.get('user_texts', []);
        self.user_shapes = snapshot.get('user_shapes', []);
        self.crop_box = snapshot.get('crop_box')
        self.clear_active_measurement();
        self.draw_preview(reset_view=False)

    def action_save_project(self):
        if not self.gds_list: return
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Project", "", "GDS Project (*.gdsprj)")
        if not filepath: return
        try:
            serializable_mapping = {str(k): v for k, v in self.layer_mapping.items()}
            clean_texts = [{k: v for k, v in t.items() if k != 'text_obj'} for t in self.user_texts]
            clean_shapes = [{k: v for k, v in s.items() if k != 'patch'} for s in self.user_shapes]
            project_data = {"block_width": self.block_w, "block_height": self.block_h,
                            "top_cell_name": self.inp_topname.text(), "measurements": self.measurements,
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
            QtWidgets.QMessageBox.information(self, "Success", "Project saved!")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def action_load_project(self):
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load Project", "", "GDS Project (*.gdsprj)")
        if not filepath: return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                project_data = json.load(f)
            self.gds_list.clear();
            self.list_widget.clear()
            self.measurements = project_data.get("measurements", []);
            self.user_texts = project_data.get("user_texts", [])
            self.user_shapes = project_data.get("user_shapes", []);
            self.crop_box = project_data.get("crop_box")
            saved_mapping = project_data.get("layer_mapping", {})
            self.layer_mapping = {eval(k_str): tuple(v) for k_str, v in saved_mapping.items()}
            self.clear_active_measurement();
            self.undo_stack.clear()
            self.block_w = project_data.get("block_width", 5000.0);
            self.block_h = project_data.get("block_height", 5000.0)
            self.inp_block_w.setText(str(self.block_w));
            self.inp_block_h.setText(str(self.block_h))
            self.inp_topname.setText(project_data.get("top_cell_name", "MERGED_CHIP"))

            for i, item in enumerate(project_data.get("gds_items", [])):
                path, name = item.get("path"), item.get("name")
                if not os.path.exists(path): continue
                base_bbox, true_polygons, layers = self.parse_gds_info(path)
                trans = db.DTrans(item["trans_rot"], item["trans_mirror"], item["trans_dx"], item["trans_dy"])
                gds_info = {'path': path, 'name': name, 'base_bbox': base_bbox, 'trans': trans,
                            'offset_x': item["offset_x"], 'offset_y': item["offset_y"], 'color': item["color"],
                            'patch': None, 'shadow_patch': None, 'collection': None, 'center_text': None,
                            'true_polygons': true_polygons, 'layers': layers}
                self.gds_list.append(gds_info);
                self.list_widget.addItem(f"[{i + 1}] {name}")
            self.refresh_layer_list();
            self.draw_preview(reset_view=True)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def edit_text_dialog(self, idx):
        ut = self.user_texts[idx]
        dlg = QtWidgets.QDialog(self);
        dlg.setWindowTitle("Edit Text")
        layout = QtWidgets.QFormLayout(dlg)
        t_var = QtWidgets.QLineEdit(ut['text']);
        s_var = QtWidgets.QLineEdit(str(ut['size']))
        l_var = QtWidgets.QLineEdit(str(ut['layer']));
        dt_var = QtWidgets.QLineEdit(str(ut['dt']))
        layout.addRow("Text:", t_var);
        layout.addRow("Size:", s_var);
        layout.addRow("Layer:", l_var);
        layout.addRow("DT:", dt_var)
        btn = QtWidgets.QPushButton("Update");
        btn.clicked.connect(dlg.accept);
        layout.addRow(btn)
        if dlg.exec_():
            try:
                self.save_snapshot()
                ut['text'], ut['size'], ut['layer'], ut['dt'] = t_var.text(), float(s_var.text()), int(
                    l_var.text()), int(dt_var.text())
                tp = TextPath((ut['x'], ut['y']), ut['text'], size=ut['size']);
                ut['text_obj'].set_path(tp);
                self.canvas.draw_idle()
            except ValueError:
                pass

    def edit_shape_dialog(self, idx):
        s = self.user_shapes[idx]
        dlg = QtWidgets.QDialog(self);
        dlg.setWindowTitle(f"Edit {s['type'].capitalize()}")
        layout = QtWidgets.QFormLayout(dlg)
        l_var = QtWidgets.QLineEdit(str(s['layer']));
        dt_var = QtWidgets.QLineEdit(str(s['dt']))
        layout.addRow("Layer:", l_var);
        layout.addRow("DT:", dt_var)

        w_var = QtWidgets.QLineEdit();
        box_w_var = QtWidgets.QLineEdit();
        box_h_var = QtWidgets.QLineEdit()
        px_var = QtWidgets.QLineEdit();
        py_var = QtWidgets.QLineEdit();
        min_x, min_y = 0, 0

        if s['type'] == 'path':
            w_var.setText(str(s.get('width', 20.0)));
            layout.addRow("Width:", w_var)
        elif s['type'] == 'box':
            pts = s['points'];
            min_x, min_y = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
            box_w_var.setText(str(abs(pts[1][0] - pts[0][0])));
            box_h_var.setText(str(abs(pts[1][1] - pts[0][1])))
            layout.addRow("Width:", box_w_var);
            layout.addRow("Height:", box_h_var)
        elif s['type'] == 'via_array':
            w_var.setText(str(s.get('via_w', 1.0)));
            box_w_var.setText(str(s.get('via_h', 1.0)))
            px_var.setText(str(s.get('pitch_x', 2.0)));
            py_var.setText(str(s.get('pitch_y', 2.0)))
            h1 = QtWidgets.QHBoxLayout();
            h1.addWidget(w_var);
            h1.addWidget(box_w_var);
            layout.addRow("Via WxH:", h1)
            h2 = QtWidgets.QHBoxLayout();
            h2.addWidget(px_var);
            h2.addWidget(py_var);
            layout.addRow("Pitch XxY:", h2)

        btn = QtWidgets.QPushButton("Update");
        btn.clicked.connect(dlg.accept);
        layout.addRow(btn)
        if dlg.exec_():
            try:
                self.save_snapshot();
                s['layer'], s['dt'] = int(l_var.text()), int(dt_var.text())
                if s['type'] == 'path':
                    s['width'] = float(w_var.text())
                elif s['type'] == 'box':
                    nw, nh = float(box_w_var.text()), float(box_h_var.text())
                    s['points'] = [(min_x, min_y), (min_x + nw, min_y + nh)]
                elif s['type'] == 'via_array':
                    s['via_w'], s['via_h'] = float(w_var.text()), float(box_w_var.text())
                    s['pitch_x'], s['pitch_y'] = float(px_var.text()), float(py_var.text())
                self.draw_preview(reset_view=False)
            except ValueError:
                pass

    def action_add_text_dialog(self):
        self.cancel_draw_mode()
        dlg = QtWidgets.QDialog(self);
        dlg.setWindowTitle("Add Text")
        layout = QtWidgets.QFormLayout(dlg)
        t_var = QtWidgets.QLineEdit("CHIP_LABEL");
        s_var = QtWidgets.QLineEdit("100.0")
        l_var = QtWidgets.QLineEdit("10");
        dt_var = QtWidgets.QLineEdit("0")
        layout.addRow("Text:", t_var);
        layout.addRow("Size:", s_var);
        layout.addRow("Layer:", l_var);
        layout.addRow("DT:", dt_var)
        btn = QtWidgets.QPushButton("Place on Canvas");
        btn.clicked.connect(dlg.accept);
        layout.addRow(btn)
        if dlg.exec_():
            try:
                self.draw_current_props = {'text': t_var.text(), 'size': float(s_var.text()),
                                           'layer': int(l_var.text()), 'dt': int(dt_var.text())}
                self.draw_mode = 'text';
                self.btn_measure.setChecked(False)
                self.status_label.setText("Text Mode: Click on Canvas to place.");
                self.canvas.setFocus()
            except ValueError:
                pass

    def action_add_shape_dialog(self, shape_type):
        self.cancel_draw_mode()
        dlg = QtWidgets.QDialog(self);
        dlg.setWindowTitle(f"Add {shape_type.capitalize()}")
        layout = QtWidgets.QFormLayout(dlg)
        l_var = QtWidgets.QLineEdit("10");
        dt_var = QtWidgets.QLineEdit("0")
        layout.addRow("Layer:", l_var);
        layout.addRow("DT:", dt_var)

        w_var = QtWidgets.QLineEdit("20.0");
        vw_var = QtWidgets.QLineEdit("1.0");
        vh_var = QtWidgets.QLineEdit("1.0")
        px_var = QtWidgets.QLineEdit("2.0");
        py_var = QtWidgets.QLineEdit("2.0")

        if shape_type == 'path':
            layout.addRow("Width:", w_var)
        elif shape_type == 'via_array':
            h1 = QtWidgets.QHBoxLayout();
            h1.addWidget(vw_var);
            h1.addWidget(vh_var);
            layout.addRow("Via WxH:", h1)
            h2 = QtWidgets.QHBoxLayout();
            h2.addWidget(px_var);
            h2.addWidget(py_var);
            layout.addRow("Pitch XxY:", h2)

        btn = QtWidgets.QPushButton("Start Drawing");
        btn.clicked.connect(dlg.accept);
        layout.addRow(btn)
        if dlg.exec_():
            try:
                self.draw_current_props = {'type': shape_type, 'layer': int(l_var.text()), 'dt': int(dt_var.text())}
                if shape_type == 'path':
                    self.draw_current_props['width'] = float(w_var.text())
                elif shape_type == 'via_array':
                    self.draw_current_props.update(
                        {'via_w': float(vw_var.text()), 'via_h': float(vh_var.text()), 'pitch_x': float(px_var.text()),
                         'pitch_y': float(py_var.text())})

                self.draw_mode = shape_type;
                self.draw_points = [];
                self.btn_measure.setChecked(False)
                self.status_label.setText(f"{shape_type.capitalize()} Mode active. (Hold Ctrl for Ortho)");
                self.canvas.setFocus()
            except ValueError:
                pass

    def action_draw_crop_box(self):
        self.cancel_draw_mode()
        self.draw_mode = 'crop';
        self.draw_points = [];
        self.btn_measure.setChecked(False)
        self.status_label.setText("Crop Mode: Click top-left, click bottom-right.");
        self.canvas.setFocus()

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
        self.status_label.setText("Ready")

    # ================= 修改 1：使用双层色块实现安全阴影，避免渲染引擎崩溃 =================
    def update_canvas_selection(self):
        selected_indices = [item.row() for item in self.list_widget.selectedItems()]
        for i, gds in enumerate(self.gds_list):
            if gds.get('patch'):
                if i in selected_indices:
                    # 显示阴影图层，设置器件高亮和斜纹
                    if 'shadow_patch' in gds and gds['shadow_patch']:
                        gds['shadow_patch'].set_alpha(0.6)  # 激活阴影块

                    gds['patch'].set_alpha(0.8)
                    gds['patch'].set_linewidth(3.0)
                    gds['patch'].set_edgecolor('#00FFFF')  # 青色高亮边框
                    gds['patch'].set_hatch('///')  # 斜纹标记

                    if gds.get('collection'):
                        gds['collection'].set_edgecolor('#00FFFF')
                        gds['collection'].set_linewidth(1.5)
                else:
                    # 隐藏阴影图层，器件恢复原状
                    if 'shadow_patch' in gds and gds['shadow_patch']:
                        gds['shadow_patch'].set_alpha(0.0)  # 隐藏阴影块

                    gds['patch'].set_alpha(0.3)
                    gds['patch'].set_linewidth(1.0)
                    gds['patch'].set_edgecolor(gds['color'])
                    gds['patch'].set_hatch(None)

                    if gds.get('collection'):
                        gds['collection'].set_edgecolor(mcolors.to_rgba(gds['color'], alpha=0.9))
                        gds['collection'].set_linewidth(0.5)

        self.canvas.draw_idle()

    def open_layer_mapping_dialog(self):
        dlg = QtWidgets.QDialog(self);
        dlg.setWindowTitle("Layer Mapping");
        dlg.resize(400, 300)
        layout = QtWidgets.QVBoxLayout(dlg)
        all_src = set()
        for gds in self.gds_list: all_src.update(gds.get('layers', []))
        for sl in sorted(list(all_src)):
            if sl not in self.layer_mapping: self.layer_mapping[sl] = sl

        table = QtWidgets.QTableWidget(len(self.layer_mapping), 4)
        table.setHorizontalHeaderLabels(["Src Layer", "Src DT", "New Layer", "New DT"])
        for i, ((sl, sdt), (tl, tdt)) in enumerate(sorted(self.layer_mapping.items())):
            table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(sl)));
            table.setItem(i, 1, QtWidgets.QTableWidgetItem(str(sdt)))
            table.setItem(i, 2, QtWidgets.QTableWidgetItem(str(tl)));
            table.setItem(i, 3, QtWidgets.QTableWidgetItem(str(tdt)))
        layout.addWidget(table)

        def save_mapping():
            for i in range(table.rowCount()):
                try:
                    sl, sdt = int(table.item(i, 0).text()), int(table.item(i, 1).text())
                    tl, tdt = int(table.item(i, 2).text()), int(table.item(i, 3).text())
                    self.layer_mapping[(sl, sdt)] = (tl, tdt)
                except:
                    pass
            dlg.accept()

        btn = QtWidgets.QPushButton("Save Mapping");
        btn.clicked.connect(save_mapping);
        layout.addWidget(btn)
        dlg.exec_()

    def get_bbox(self, gds):
        t_box = gds['trans'] * gds['base_bbox']
        return t_box.left + gds['offset_x'], t_box.right + gds['offset_x'], t_box.bottom + gds['offset_y'], t_box.top + \
               gds['offset_y']

    def execute_align(self):
        mode_map = {"Align Left": "left", "Align Center X": "center_x", "Align Right": "right", "Align Top": "top",
                    "Align Center Y": "center_y", "Align Bottom": "bottom", "Distribute H": "dist_h",
                    "Distribute V": "dist_v"}
        mode = mode_map.get(self.cb_align_go.currentText())
        if mode in ['left', 'right', 'center_x', 'bottom', 'top', 'center_y']:
            self.align_selected(mode)
        elif mode in ['dist_h', 'dist_v']:
            self.distribute_selected(mode[-1])

    def align_selected(self, mode):
        selection = [item.row() for item in self.list_widget.selectedItems()]
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
        selection = [item.row() for item in self.list_widget.selectedItems()]
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
        if self.btn_measure.isChecked():
            self.cancel_draw_mode()
            self.status_label.setText("Measure Mode ON: Click once to start, click again to finish.")
            # 使用阻塞信号来防止状态死循环
            self.list_widget.blockSignals(True)
            self.list_widget.clearSelection()
            self.list_widget.blockSignals(False)
            self.update_canvas_selection()
            self.canvas.setFocus()
        else:
            self.status_label.setText("Measure Mode OFF.")
            self.clear_active_measurement();
            self.canvas.draw_idle()

    def action_clear_annotations(self):
        self.save_snapshot()
        self.measurements.clear();
        self.user_texts.clear();
        self.user_shapes.clear();
        self.crop_box = None
        self.clear_active_measurement();
        self.draw_preview(reset_view=False)

    def clear_active_measurement(self):
        for item in [self.measure_line, self.measure_text, self.snap_indicator, self.temp_draw_preview]:
            if item:
                try:
                    item.remove()
                except:
                    pass
        self.measure_line = self.measure_text = self.snap_indicator = self.temp_draw_preview = None
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

    def on_listbox_select(self):
        selection = [item.row() for item in self.list_widget.selectedItems()]
        if selection:
            x, y = self.get_anchor_coords(self.gds_list[selection[0]], self.cb_anchor.currentText())
            self.inp_x.setText(f"{x:.3f}");
            self.inp_y.setText(f"{y:.3f}")
            if self.btn_measure.isChecked(): self.btn_measure.setChecked(False)
        self.update_canvas_selection()

    def on_anchor_change(self):
        self.on_listbox_select()

    def apply_manual_position(self):
        selection = [item.row() for item in self.list_widget.selectedItems()]
        if not selection: return
        self.save_snapshot()
        try:
            self.set_anchor_coords(self.gds_list[selection[0]], self.cb_anchor.currentText(), float(self.inp_x.text()),
                                   float(self.inp_y.text()))
            self.draw_preview(reset_view=False)
        except ValueError:
            pass

    def update_block_size(self):
        try:
            self.block_w, self.block_h = float(self.inp_block_w.text()), float(self.inp_block_h.text())
            self.draw_preview(reset_view=True)
        except:
            pass

    # ================= 修改 2：在执行删除时屏蔽 UI 信号更新，避免由于下标错位导致的闪退 =================
    def action_delete_selected(self):
        selection = sorted([item.row() for item in self.list_widget.selectedItems()], reverse=True)
        if selection:
            self.save_snapshot()

            # --- 阻止信号避免 Qt 产生递归错误 ---
            self.list_widget.blockSignals(True)
            for idx in selection:
                del self.gds_list[idx];
                self.list_widget.takeItem(idx)
            self.list_widget.clearSelection()  # 必须清理残余的选择状态
            self.list_widget.blockSignals(False)

            self.refresh_layer_list()
            self.inp_x.setText("0.0");
            self.inp_y.setText("0.0")
            self.draw_preview(reset_view=False)

    def draw_preview(self, reset_view=False):
        cur_xlim, cur_ylim = self.ax.get_xlim(), self.ax.get_ylim()
        self.ax.clear();
        self.clear_active_measurement()

        for spine in self.ax.spines.values(): spine.set_visible(False)
        self.ax.tick_params(axis='both', which='both', length=4, width=0.8, direction='out', colors='#555555',
                            labelcolor='#aaaaaa')
        self.ax.set_axisbelow(True)

        self.ax.add_patch(patches.Rectangle((0, 0), self.block_w, self.block_h, linewidth=1.5, edgecolor='#555555',
                                            facecolor='#252525', linestyle='-.', zorder=0))
        self.ax.plot(0, 0, marker='+', color='#ffffff', markersize=15, markeredgewidth=1.5, zorder=1)

        if not self.gds_list: self.ax.text(self.block_w / 2, self.block_h / 2, 'No GDS Loaded', ha='center',
                                           va='center', color='#bbbbbb', fontsize=12)

        shadow_offset = min(self.block_w, self.block_h) * 0.015  # 阴影偏移量，视块尺寸而定

        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            sx, sy = t_box.left + gds['offset_x'], t_box.bottom + gds['offset_y']
            w, h = t_box.width(), t_box.height()

            # --- 增加原生阴影图层，垫在主图形下方（zorder=8），默认透明度0 ---
            shadow_rect = patches.Rectangle((sx + shadow_offset, sy - shadow_offset), w, h,
                                            linewidth=0, facecolor='black', alpha=0.0, zorder=8)
            self.ax.add_patch(shadow_rect)
            gds['shadow_patch'] = shadow_rect

            # 主图层
            rect = patches.Rectangle((sx, sy), w, h, linewidth=0.5, edgecolor=gds['color'], facecolor=gds['color'],
                                     alpha=0.6, zorder=10)
            self.ax.add_patch(rect)
            gds['patch'] = rect;
            gds['collection'] = None

            if not self.btn_bbox.isChecked() and gds['true_polygons']:
                fc, ec = mcolors.to_rgba('black', alpha=0.5), mcolors.to_rgba(gds['color'], alpha=0.9)
                coll = PolyCollection(gds['true_polygons'], facecolors=fc, edgecolors=ec, linewidths=0.5, zorder=15)
                tr = mtransforms.Affine2D()
                if gds['trans'].is_mirror(): tr.scale(1.0, -1.0)
                tr.rotate_deg(gds['trans'].rot * 90.0)
                tr.translate(gds['trans'].disp.x + gds['offset_x'], gds['trans'].disp.y + gds['offset_y'])
                coll.set_transform(tr + self.ax.transData)
                self.ax.add_collection(coll)
                gds['collection'] = coll

            ratio = min(w, h) / min(self.block_w, self.block_h) if min(self.block_w, self.block_h) > 0 else 1.0
            gds['center_text'] = self.ax.text(sx + w / 2, sy + h / 2, gds['name'], ha='center', va='center',
                                              fontsize=max(6, min(35, int(6 + 18 * ratio))), color='white',
                                              fontweight='bold', alpha=0.7, zorder=90)

        for m in self.measurements:
            self.ax.plot([m['x0'], m['x1']], [m['y0'], m['y1']], color='#FF1493', linestyle='--', linewidth=1,
                         zorder=300)
            self.ax.text(m['x1'], m['y1'],
                         f" L: {math.hypot(m['x1'] - m['x0'], m['y1'] - m['y0']):.2f}\n dx: {abs(m['x1'] - m['x0']):.2f}\n dy: {abs(m['y1'] - m['y0']):.2f}",
                         color='#FF1493', fontsize=10, fontweight='bold', zorder=301,
                         bbox=dict(facecolor='black', alpha=0.8, edgecolor='none', pad=2))

        for ut in self.user_texts:
            tp = TextPath((ut['x'], ut['y']), ut['text'], size=ut['size'])
            text_patch = PathPatch(tp, facecolor='#00CED1', edgecolor='none', zorder=250, alpha=0.8)
            self.ax.add_patch(text_patch)
            ut['text_obj'] = text_patch

        for s in self.user_shapes:
            if s['type'] in ['box', 'via_array']:
                pts = s['points'];
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
            pts = self.crop_box;
            x0, y0 = pts[0];
            x1, y1 = pts[1]
            rect = patches.Rectangle((min(x0, x1), min(y0, y1)), abs(x1 - x0), abs(y1 - y0), fill=False,
                                     edgecolor='red', linestyle='--', linewidth=3, zorder=400)
            self.ax.add_patch(rect)

        self.update_canvas_selection()

        if reset_view:
            self.ax.set_xlim(-self.block_w * 0.1, self.block_w * 1.1)
            self.ax.set_ylim(-self.block_h * 0.1, self.block_h * 1.1)
        else:
            self.ax.set_xlim(cur_xlim);
            self.ax.set_ylim(cur_ylim)

        self.ax.set_aspect('equal', adjustable='datalim')
        self.ax.grid(True, linestyle='-', color='#444444', alpha=0.4)
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

        if (self.btn_measure.isChecked() and self.measure_state == 1 and self.measure_start_pt) or \
                (self.draw_mode in ['polygon', 'path'] and self.draw_points):
            if self.ctrl_pressed:
                if self.btn_measure.isChecked():
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
                    self.status_label.setText("Text added.")
                    self.draw_preview(reset_view=False)
                return

            elif self.draw_mode in ['box', 'via_array', 'crop']:
                if event.button == 1:
                    if not self.draw_points:
                        self.draw_points.append((snap_x, snap_y))
                    else:
                        self.draw_points.append((snap_x, snap_y));
                        self.finalize_shape()
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

        if self.btn_measure.isChecked() and event.button == 1:
            if self.measure_state == 0:
                self.clear_active_measurement()
                self.measure_start_pt = (snap_x, snap_y)
                self.measure_line, = self.ax.plot([snap_x, snap_x], [snap_y, snap_y], color='#FF1493', linestyle='--',
                                                  linewidth=1, zorder=300)
                self.measure_text = self.ax.text(snap_x, snap_y, '', color='#FF1493', fontsize=10, fontweight='bold',
                                                 zorder=301,
                                                 bbox=dict(facecolor='black', alpha=0.8, edgecolor='none', pad=2))
                self.snap_indicator, = self.ax.plot([snap_x], [snap_y], marker='+', color='red', markersize=12,
                                                    markeredgewidth=2, zorder=305)
                self.measure_state = 1
            elif self.measure_state == 1:
                self.save_snapshot()
                self.measurements.append(
                    {'x0': self.measure_start_pt[0], 'y0': self.measure_start_pt[1], 'x1': snap_x, 'y1': snap_y})
                self.measure_state = 0
                if self.snap_indicator: self.snap_indicator.set_data([], [])
                self.measure_line = self.measure_text = None
            self.canvas.draw_idle();
            return

        for line in self.guide_lines: line.remove()
        self.guide_lines.clear()

        for i in range(len(self.user_texts) - 1, -1, -1):
            if 'text_obj' in self.user_texts[i] and self.user_texts[i]['text_obj']:
                cont, _ = self.user_texts[i]['text_obj'].contains(event)
                if cont and event.button == 1:
                    self.dragging_type = 'text';
                    self.dragging_idx = i
                    self.drag_start_x, self.drag_start_y = event.xdata, event.ydata
                    self.rect_start_x, self.rect_start_y = self.user_texts[i]['x'], self.user_texts[i]['y']
                    return

        for i in range(len(self.user_shapes) - 1, -1, -1):
            if 'patch' in self.user_shapes[i] and self.user_shapes[i]['patch']:
                cont, _ = self.user_shapes[i]['patch'].contains(event)
                if cont and event.button == 1:
                    self.dragging_type = 'shape';
                    self.dragging_idx = i
                    self.drag_start_x, self.drag_start_y = event.xdata, event.ydata
                    self.drag_start_offsets = list(self.user_shapes[i]['points'])
                    return

        clicked_idx = next(
            (i for i in range(len(self.gds_list) - 1, -1, -1) if self.gds_list[i]['patch'].contains(event)[0]), -1)
        if clicked_idx != -1:
            if event.button in [2, 3]:
                self.show_context_menu(clicked_idx);
                return
            elif event.button == 1:
                self.dragging_type = 'gds';
                self.dragging_idx = clicked_idx
                self.drag_start_x, self.drag_start_y = event.xdata, event.ydata
                self.rect_start_x, self.rect_start_y = self.gds_list[clicked_idx]['patch'].get_x(), \
                    self.gds_list[clicked_idx]['patch'].get_y()

                current_selection = [item.row() for item in self.list_widget.selectedItems()]

                # ================= 修改 3：在这里使用阻塞信号，从根源掐断引起崩溃的 Qt 信号环路 =================
                self.list_widget.blockSignals(True)
                try:
                    if self.ctrl_pressed:
                        if clicked_idx in current_selection:
                            self.list_widget.item(clicked_idx).setSelected(False)
                        else:
                            self.list_widget.item(clicked_idx).setSelected(True)
                    elif clicked_idx not in current_selection:
                        self.list_widget.clearSelection()
                        self.list_widget.item(clicked_idx).setSelected(True)
                finally:
                    self.list_widget.blockSignals(False)

                self.drag_start_offsets = {idx: (self.gds_list[idx]['offset_x'], self.gds_list[idx]['offset_y']) for idx
                                           in [item.row() for item in self.list_widget.selectedItems()]}
                # 只有这里统一安全地调用一次更新
                self.on_listbox_select()
                return

        elif event.button == 1 and not self.ctrl_pressed:
            self.list_widget.blockSignals(True)
            self.list_widget.clearSelection()
            self.list_widget.blockSignals(False)
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

        if (self.btn_measure.isChecked() and self.measure_state == 1 and self.measure_start_pt) or \
                (self.draw_mode in ['polygon', 'path'] and self.draw_points):
            if self.ctrl_pressed:
                if self.btn_measure.isChecked():
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
                    self.temp_draw_preview = PathPatch(tp, facecolor='#00CED1', edgecolor='none', zorder=350, alpha=0.5)
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

        if self.btn_measure.isChecked():
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

        dx_raw = event.xdata - self.drag_start_x;
        dy_raw = event.ydata - self.drag_start_y

        if self.dragging_type == 'text':
            nx, ny = self.rect_start_x + dx_raw, self.rect_start_y + dy_raw
            if self.chk_snap.isChecked():
                try:
                    g_size = float(self.inp_snap.text())
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
            if self.chk_snap.isChecked():
                try:
                    g_size = float(self.inp_snap.text())
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
        if self.chk_snap.isChecked():
            try:
                g_size = float(self.inp_snap.text())
                if g_size > 0:
                    anchor = self.cb_anchor.currentText()
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

            # --- 拖拽时同步更新底层阴影方块的位置 ---
            if 'shadow_patch' in gds and gds['shadow_patch']:
                shadow_offset = min(self.block_w, self.block_h) * 0.015
                gds['shadow_patch'].set_x(nx_final + shadow_offset)
                gds['shadow_patch'].set_y(ny_final - shadow_offset)

            if gds['center_text']: gds['center_text'].set_position(
                (nx_final + (gds['trans'] * gds['base_bbox']).width() / 2,
                 ny_final + (gds['trans'] * gds['base_bbox']).height() / 2))

            if gds.get('collection'):
                tr = mtransforms.Affine2D()
                if gds['trans'].is_mirror(): tr.scale(1.0, -1.0)
                tr.rotate_deg(gds['trans'].rot * 90.0)
                tr.translate(gds['trans'].disp.x + new_ox, gds['trans'].disp.y + new_oy)
                gds['collection'].set_transform(tr + self.ax.transData)

        if not is_grid_snapped:
            if best_snap_x is not None: self.guide_lines.append(
                self.ax.axvline(x=best_snap_x, color='#FF8C00', linestyle='--', linewidth=1.5, zorder=200))
            if best_snap_y is not None: self.guide_lines.append(
                self.ax.axhline(y=best_snap_y, color='#FF8C00', linestyle='--', linewidth=1.5, zorder=200))

        if [item.row() for item in self.list_widget.selectedItems()] and \
                [item.row() for item in self.list_widget.selectedItems()][0] == self.dragging_idx:
            x, y = self.get_anchor_coords(handle_gds, self.cb_anchor.currentText())
            self.inp_x.setText(f"{x:.3f}");
            self.inp_y.setText(f"{y:.3f}")

        self.draw_overlaps();
        self.canvas.draw_idle()

    def on_release(self, event):
        if self.btn_measure.isChecked() and event.button == 1: return
        if self.dragging_type is not None:
            self.dragging_type = None;
            self.dragging_idx = -1;
            self.drag_snapshot_taken = False;
            self.drag_start_offsets.clear()
            for line in self.guide_lines: line.remove()
            self.guide_lines.clear();
            self.update_canvas_selection();
            self.canvas.draw_idle()

    def get_pois(self, gds, temp_ox=None, temp_oy=None):
        t_box = gds['trans'] * gds['base_bbox']
        ox, oy = (gds['offset_x'] if temp_ox is None else temp_ox), (gds['offset_y'] if temp_oy is None else temp_oy)
        return [t_box.left + ox, t_box.right + ox, (t_box.left + t_box.right) / 2 + ox], \
            [t_box.bottom + oy, t_box.top + oy, (t_box.bottom + t_box.top) / 2 + oy]

    def show_context_menu(self, idx):
        menu = QtWidgets.QMenu(self)
        a_dup = menu.addAction(f"Duplicate {self.gds_list[idx]['name']}")
        a_arr = menu.addAction("Create Array (Step & Repeat)...")
        menu.addSeparator()
        a_ccw = menu.addAction("Rotate 90 CCW");
        a_cw = menu.addAction("Rotate 90 CW")
        a_fh = menu.addAction("Flip H");
        a_fv = menu.addAction("Flip V")

        action = menu.exec_(QtGui.QCursor.pos())
        if action == a_dup:
            self.action_duplicate(idx)
        elif action == a_arr:
            self.action_create_array(idx)
        elif action == a_ccw:
            self.action_rotate_ccw(idx)
        elif action == a_cw:
            self.action_rotate_cw(idx)
        elif action == a_fh:
            self.action_flip_horizontal(idx)
        elif action == a_fv:
            self.action_flip_vertical(idx)

    def action_duplicate(self, idx):
        self.save_snapshot();
        o = self.gds_list[idx]
        self.gds_list.append(
            {'path': o['path'], 'name': o['name'], 'base_bbox': o['base_bbox'], 'trans': o['trans'] * db.DTrans(),
             'offset_x': o['offset_x'] + 200, 'offset_y': o['offset_y'] - 200, 'color': o['color'], 'patch': None,
             'shadow_patch': None, 'collection': None, 'center_text': None, 'true_polygons': o['true_polygons'],
             'layers': o.get('layers', [])})
        self.list_widget.addItem(f"[{len(self.gds_list)}] {o['name']}")
        self.draw_preview()

    def action_create_array(self, idx):
        dlg = QtWidgets.QDialog(self);
        dlg.setWindowTitle("Create Array")
        layout = QtWidgets.QFormLayout(dlg)
        rows_var = QtWidgets.QLineEdit("2");
        cols_var = QtWidgets.QLineEdit("2")
        spc_x_var = QtWidgets.QLineEdit("1000");
        spc_y_var = QtWidgets.QLineEdit("1000")
        layout.addRow("Rows (Y):", rows_var);
        layout.addRow("Cols (X):", cols_var)
        layout.addRow("Space X (um):", spc_x_var);
        layout.addRow("Space Y (um):", spc_y_var)
        btn = QtWidgets.QPushButton("Generate Array");
        btn.clicked.connect(dlg.accept);
        layout.addRow(btn)

        if dlg.exec_():
            try:
                r, c, sx, sy = int(rows_var.text()), int(cols_var.text()), float(spc_x_var.text()), float(
                    spc_y_var.text())
                if r < 1 or c < 1: return
                self.save_snapshot();
                o = self.gds_list[idx]
                for i in range(r):
                    for j in range(c):
                        if i == 0 and j == 0: continue
                        self.gds_list.append(
                            {'path': o['path'], 'name': f"{o['name']}_R{i}C{j}", 'base_bbox': o['base_bbox'],
                             'trans': o['trans'] * db.DTrans(), 'offset_x': o['offset_x'] + j * sx,
                             'offset_y': o['offset_y'] + i * sy, 'color': o['color'], 'patch': None,
                             'shadow_patch': None, 'collection': None,
                             'center_text': None, 'true_polygons': o['true_polygons'], 'layers': o.get('layers', [])})
                        self.list_widget.addItem(f"[{len(self.gds_list)}] {self.gds_list[-1]['name']}")
                self.draw_preview()
            except ValueError:
                pass

    def action_rotate_ccw(self, i):
        self.save_snapshot();
        self.gds_list[i]['trans'] = db.DTrans(1, False, 0, 0) * self.gds_list[i][
            'trans'];
        self.draw_preview();
        self.on_listbox_select()

    def action_rotate_cw(self, i):
        self.save_snapshot();
        self.gds_list[i]['trans'] = db.DTrans(3, False, 0, 0) * self.gds_list[i][
            'trans'];
        self.draw_preview();
        self.on_listbox_select()

    def action_flip_horizontal(self, i):
        self.save_snapshot();
        self.gds_list[i]['trans'] = db.DTrans(2, True, 0, 0) * self.gds_list[i][
            'trans'];
        self.draw_preview();
        self.on_listbox_select()

    def action_flip_vertical(self, i):
        self.save_snapshot();
        self.gds_list[i]['trans'] = db.DTrans(0, True, 0, 0) * self.gds_list[i][
            'trans'];
        self.draw_preview();
        self.on_listbox_select()

    def execute_stitch(self):
        if not self.gds_list: return
        out_p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save GDS", "", "GDS Files (*.gds)")
        if not out_p: return
        try:
            self.status_label.setText("Processing and merging GDS files...")
            QtWidgets.QApplication.processEvents()

            target_layout = db.Layout()
            merged_top = target_layout.create_cell(self.inp_topname.text() or "MERGED")
            cache = {}

            for idx, g in enumerate(self.gds_list):
                file_path = g['path']
                if file_path not in cache:
                    if not os.path.exists(file_path):
                        QtWidgets.QMessageBox.warning(self, "Missing", f"Missing GDS:\n{file_path}")
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

            if getattr(self, 'layer_mapping', None):
                for (sl, sdt), (tl, tdt) in self.layer_mapping.items():
                    if (sl, sdt) == (tl, tdt): continue
                    src_idx = target_layout.find_layer(db.LayerInfo(sl, sdt))
                    if src_idx is not None:
                        tgt_idx = target_layout.layer(db.LayerInfo(tl, tdt))
                        if src_idx != tgt_idx:
                            for cell in target_layout.each_cell():
                                cell.shapes(tgt_idx).insert(cell.shapes(src_idx))
                                cell.shapes(src_idx).clear()
                            try:
                                target_layout.delete_layer(src_idx)
                            except:
                                pass

            dbu = target_layout.dbu

            if self.chk_seal.isChecked():
                seal_idx = target_layout.layer(db.LayerInfo(int(self.inp_slyr.text()), int(self.inp_sdt.text())))
                s_width_dbu, s_margin_dbu = int(float(self.inp_sw.text()) / dbu), int(
                    float(self.inp_sdist.text()) / dbu)
                w_dbu, h_dbu = int(self.block_w / dbu), int(self.block_h / dbu)
                outer_box = db.Box(s_margin_dbu, s_margin_dbu, w_dbu - s_margin_dbu, h_dbu - s_margin_dbu)
                inner_box = db.Box(s_margin_dbu + s_width_dbu, s_margin_dbu + s_width_dbu,
                                   w_dbu - s_margin_dbu - s_width_dbu, h_dbu - s_margin_dbu - s_width_dbu)
                merged_top.shapes(seal_idx).insert(db.Region(outer_box) - db.Region(inner_box))

            if self.user_shapes:
                for s in self.user_shapes:
                    lyr_idx = target_layout.layer(db.LayerInfo(int(s['layer']), int(s['dt'])))
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

            if self.user_texts:
                gen = db.TextGenerator.default_generator()
                for ut in self.user_texts:
                    lbl_idx = target_layout.layer(db.LayerInfo(int(ut['layer']), int(ut['dt'])))
                    text_region = gen.text(ut['text'], dbu)
                    if text_region.bbox().height() > 0:
                        text_cell = target_layout.create_cell(f"TEXT_{ut['text']}")
                        text_cell.shapes(lbl_idx).insert(text_region)
                        _ = text_cell.bbox()
                        current_h_um = text_region.bbox().height() * dbu
                        scale_factor = ut['size'] / current_h_um if current_h_um > 0 else 1.0
                        t = db.DCplxTrans(scale_factor, 0, False, ut['x'], ut['y'])
                        merged_top.insert(db.DCellInstArray(text_cell.cell_index(), t))

            if self.chk_dummy.isChecked():
                layer_idx = target_layout.layer(db.LayerInfo(int(self.inp_dlyr.text()), int(self.inp_ddt.text())))
                size_um, space_um, margin_um = float(self.inp_dsize.text()), float(self.inp_dspc.text()), float(
                    self.inp_dmargin.text())
                keep_out = db.Region(merged_top.begin_shapes_rec(layer_idx))
                keep_out.size(int(margin_um / dbu));
                keep_out.merge()

                dummy_region = db.Region()
                box_size_dbu, box_pitch_dbu = int(size_um / dbu), int((size_um + space_um) / dbu)
                w_dbu, h_dbu = int(self.block_w / dbu), int(self.block_h / dbu)

                y, row_index = 0, 0
                stagger = self.chk_stagger.isChecked()
                while y + box_size_dbu <= h_dbu:
                    x_offset = int(box_pitch_dbu / 2) if stagger and (row_index % 2 != 0) else 0
                    x = x_offset
                    while x + box_size_dbu <= w_dbu:
                        dummy_region.insert(db.Box(x, y, x + box_size_dbu, y + box_size_dbu))
                        x += box_pitch_dbu
                    y += box_pitch_dbu
                    row_index += 1

                merged_top.shapes(layer_idx).insert(dummy_region - dummy_region.interacting(keep_out))

            if self.crop_box:
                _ = merged_top.bbox()
                pts = self.crop_box
                min_x, min_y = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
                max_x, max_y = max(pts[0][0], pts[1][0]), max(pts[0][1], pts[1][1])
                clip_box_dbu = db.Box(int(min_x / dbu), int(min_y / dbu), int(max_x / dbu), int(max_y / dbu))
                clipped_cell_idx = target_layout.clip(merged_top.cell_index(), clip_box_dbu)
                merged_top = target_layout.cell(clipped_cell_idx)
                merged_top.name = self.inp_topname.text() or "MERGED"

            self.status_label.setText("Writing to disk...")
            QtWidgets.QApplication.processEvents()

            save_opt = db.SaveLayoutOptions()
            save_opt.add_cell(merged_top.cell_index())
            target_layout.write(out_p, save_opt)

            self.status_label.setText("Ready")
            QtWidgets.QMessageBox.information(self, "OK", "导出合并成功！")

        except Exception as e:
            self.status_label.setText("Ready")
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to export:\n{str(e)}")


if __name__ == "__main__":
    if hasattr(QtCore.Qt, 'AA_EnableHighDpiScaling'):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    if hasattr(QtCore.Qt, 'AA_UseHighDpiPixmaps'):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.Window, QtGui.QColor(43, 43, 43))
    palette.setColor(QtGui.QPalette.WindowText, QtCore.Qt.white)
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor(25, 25, 25))
    palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(43, 43, 43))
    palette.setColor(QtGui.QPalette.ToolTipBase, QtCore.Qt.white)
    palette.setColor(QtGui.QPalette.ToolTipText, QtCore.Qt.white)
    palette.setColor(QtGui.QPalette.Text, QtCore.Qt.white)
    palette.setColor(QtGui.QPalette.Button, QtGui.QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.ButtonText, QtCore.Qt.white)
    palette.setColor(QtGui.QPalette.BrightText, QtCore.Qt.red)
    palette.setColor(QtGui.QPalette.Link, QtGui.QColor(42, 130, 218))
    palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(42, 130, 218))
    palette.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.black)
    app.setPalette(palette)

    window = GDSMergerProQt()
    window.show()
    sys.exit(app.exec_())