import os, math, json, sys, tempfile, uuid
from PyQt5 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg
import klayout.db as db

# 开启 PyQtGraph 抗锯齿以获得极致的图形渲染质量
pg.setConfigOptions(antialias=True)


# ================= 后台多线程加载 Worker =================
class GDSLoadWorker(QtCore.QThread):
    result_ready = QtCore.pyqtSignal(dict)
    error_occurred = QtCore.pyqtSignal(str, str)

    def __init__(self, filepath):
        super().__init__()
        self.filepath = filepath

    def run(self):
        try:
            layout = db.Layout()
            layout.read(self.filepath)
            top_cell = layout.top_cells()[0]
            base_bbox = top_cell.dbbox()

            layers = [(layout.get_info(li).layer, layout.get_info(li).datatype) for li in layout.layer_indexes()]

            # 1. 获取外轮廓 (用于外轮廓模式)
            region = db.Region()
            for li in layout.layer_indexes():
                region.insert(top_cell.begin_shapes_rec(li))
            region.merge()
            region = region.hulls()

            trans = db.DCplxTrans(layout.dbu)
            true_polygons = [[(pt.x, pt.y) for pt in db.DPolygon(poly).transformed(trans).each_point_hull()]
                             for poly in region.each() if list(db.DPolygon(poly).transformed(trans).each_point_hull())]

            # 2. 获取按层区分的完整几何多边形 (含孔洞，用于全图层模式)
            full_polygons_by_layer = {}
            for li in layout.layer_indexes():
                layer_info = layout.get_info(li)
                lyr_key = (layer_info.layer, layer_info.datatype)
                shapes_region = db.Region(top_cell.begin_shapes_rec(li))
                shapes_region.merge()  # 融合图层内的多边形以优化渲染性能

                layer_polys = []
                for poly in shapes_region.each():
                    dpoly = db.DPolygon(poly).transformed(trans)
                    # 提取外轮廓
                    hull = [(pt.x, pt.y) for pt in dpoly.each_point_hull()]
                    if not hull: continue
                    # 提取内部的所有孔洞 (Holes)
                    holes = [[(pt.x, pt.y) for pt in dpoly.each_point_hole(h)] for h in range(dpoly.holes())]
                    layer_polys.append({'hull': hull, 'holes': holes})

                if layer_polys:
                    full_polygons_by_layer[lyr_key] = layer_polys

            result = {
                'filepath': self.filepath,
                'bbox_left': base_bbox.left, 'bbox_bottom': base_bbox.bottom,
                'bbox_right': base_bbox.right, 'bbox_top': base_bbox.top,
                'true_polygons': true_polygons,
                'full_polygons': full_polygons_by_layer,
                'layers': layers
            }
            self.result_ready.emit(result)
        except Exception as e:
            import traceback
            self.error_occurred.emit(self.filepath, traceback.format_exc())


# ================= 自定义高交互 ViewBox =================
class GDSViewBox(pg.ViewBox):
    def __init__(self, main_ui, *args, **kw):
        super().__init__(*args, **kw)
        self.main_ui = main_ui

    def mouseClickEvent(self, ev):
        pt = self.mapSceneToView(ev.scenePos())
        if ev.button() == QtCore.Qt.RightButton:
            if self.main_ui.draw_mode in ['polygon', 'path']:
                if len(self.main_ui.draw_points) >= (3 if self.main_ui.draw_mode == 'polygon' else 2):
                    self.main_ui.finalize_shape()
                else:
                    self.main_ui.cancel_draw_mode()
                ev.accept()
            else:
                self.main_ui.handle_mouse_click(pt.x(), pt.y(), is_double=False, button=ev.button())
                ev.accept()
            return
        if ev.button() == QtCore.Qt.LeftButton:
            if not ev.double():
                self.main_ui.handle_mouse_click(pt.x(), pt.y(), is_double=False, button=ev.button())
            ev.accept()
        else:
            super().mouseClickEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            ev.accept()
            pt = self.mapSceneToView(ev.scenePos())
            self.main_ui.handle_mouse_doubleclick(pt.x(), pt.y())
        else:
            super().mouseDoubleClickEvent(ev)

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == QtCore.Qt.LeftButton:
            if self.main_ui.draw_mode in ['polygon', 'path']:
                ev.ignore()
                return

            pt_curr = self.mapSceneToView(ev.scenePos())
            if ev.isStart():
                pt_start = self.mapSceneToView(ev.buttonDownScenePos())
                self.main_ui.handle_drag_start(pt_start.x(), pt_start.y())
            if not ev.isStart() and not ev.isFinish():
                self.main_ui.handle_mouse_move(pt_curr.x(), pt_curr.y())
            if ev.isFinish():
                self.main_ui.handle_drag_finish(pt_curr.x(), pt_curr.y())
            ev.accept()
        else:
            super().mouseDragEvent(ev, axis)

    def hoverEvent(self, ev):
        if ev.isExit(): return
        pt = self.mapSceneToView(ev.scenePos())
        self.main_ui.handle_mouse_move(pt.x(), pt.y())


# ================= 主程序 =================
class GDSMergerProQt(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GDS Assembler Pro")
        self.resize(1400, 850)
        self.setAcceptDrops(True)

        self.default_palette = QtWidgets.QApplication.instance().palette()
        self.is_dark_mode = False

        self.gds_list, self.measurements = [], []
        self.user_texts, self.user_shapes = [], []
        self.undo_stack, self.redo_stack = [], []
        self.guide_lines, self.overlap_patches = [], []

        self.crop_box = None
        self.slot_box = None

        self.layer_mapping = {}
        self.workers = []

        self.dragging_type, self.dragging_idx = None, -1
        self.active_shape_type, self.active_shape_idx = None, -1
        self.dragging_edge = None

        self.drag_start_x = self.drag_start_y = self.rect_start_x = self.rect_start_y = 0
        self.drag_start_offsets = {}
        self.drag_snapshot_taken = False

        self.measure_start_pt = self.measure_line = self.measure_text = self.snap_indicator = None
        self.measure_state = 0
        self.draw_mode, self.draw_points, self.draw_current_props, self.temp_draw_preview = None, [], {}, None
        self.ctrl_pressed = False

        self.block_w, self.block_h = 5000.0, 5000.0
        self.top_cell_name = "MERGED_CHIP"
        self.color_palette = ['#FF0000', '#009900', '#0000FF', '#00FFFF', '#FF00FF', '#999900', '#FF8000', '#00FF80',
                              '#8000FF']

        self.setup_ui()
        self.draw_preview(reset_view=True)
        self.save_snapshot()

    # --- 辅助颜色生成器 ---
    def get_layer_color(self, layer, datatype):
        colors = ['#FF3333', '#33FF33', '#3333FF', '#FFFF33', '#FF33FF', '#33FFFF', '#FFAA00', '#AA00FF', '#00AAFF',
                  '#00FFAA']
        idx = (layer * 3 + datatype * 7) % len(colors)
        return colors[idx]

    def keyPressEvent(self, event):
        if event.key() in [QtCore.Qt.Key_Control, QtCore.Qt.Key_Meta]:
            self.ctrl_pressed = True
        elif event.key() in [QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace]:
            self.action_delete_selected()
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() in [QtCore.Qt.Key_Control, QtCore.Qt.Key_Meta]:
            self.ctrl_pressed = False
        super().keyReleaseEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            filepath = url.toLocalFile()
            if filepath.lower().endswith('.gds'):
                self.process_single_gds(filepath)

    def setup_ui(self):
        self.main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.setCentralWidget(self.main_splitter)

        # ================== 左侧面板 ==================
        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)

        proj_layout = QtWidgets.QHBoxLayout()
        btn_load = QtWidgets.QPushButton("📂 Load")
        btn_load.clicked.connect(self.action_load_project)
        btn_save = QtWidgets.QPushButton("💾 Save")
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

        grp_size = QtWidgets.QGroupBox("1b. Block Size (um)")
        size_layout = QtWidgets.QHBoxLayout(grp_size)
        self.inp_block_w = QtWidgets.QLineEdit(str(self.block_w))
        self.inp_block_h = QtWidgets.QLineEdit(str(self.block_h))
        btn_apply_size = QtWidgets.QPushButton("Apply")
        btn_apply_size.clicked.connect(self.update_block_size)
        size_layout.addWidget(QtWidgets.QLabel("W:"));
        size_layout.addWidget(self.inp_block_w)
        size_layout.addWidget(QtWidgets.QLabel("H:"));
        size_layout.addWidget(self.inp_block_h)
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
        h_d1.addWidget(self.chk_stagger)
        f_dummy.addRow(h_d1)
        h_d2 = QtWidgets.QHBoxLayout();
        self.inp_dlyr = QtWidgets.QLineEdit("1");
        self.inp_ddt = QtWidgets.QLineEdit("0")
        h_d2.addWidget(QtWidgets.QLabel("Lyr:"));
        h_d2.addWidget(self.inp_dlyr);
        h_d2.addWidget(QtWidgets.QLabel("DT:"));
        h_d2.addWidget(self.inp_ddt)
        f_dummy.addRow(h_d2)
        h_d3 = QtWidgets.QHBoxLayout();
        self.inp_dsize = QtWidgets.QLineEdit("5.0");
        self.inp_dspc = QtWidgets.QLineEdit("5.0")
        h_d3.addWidget(QtWidgets.QLabel("Size:"));
        h_d3.addWidget(self.inp_dsize);
        h_d3.addWidget(QtWidgets.QLabel("Spc:"));
        h_d3.addWidget(self.inp_dspc)
        f_dummy.addRow(h_d3)
        self.inp_dmargin = QtWidgets.QLineEdit("3.0");
        f_dummy.addRow("Margin:", self.inp_dmargin)
        finish_layout.addWidget(grp_dummy)

        grp_seal = QtWidgets.QGroupBox("2. Seal Ring")
        f_seal = QtWidgets.QFormLayout(grp_seal)
        self.chk_seal = QtWidgets.QCheckBox("Enable Seal Ring")
        f_seal.addRow(self.chk_seal)
        h_s1 = QtWidgets.QHBoxLayout();
        self.inp_slyr = QtWidgets.QLineEdit("10");
        self.inp_sdt = QtWidgets.QLineEdit("0")
        h_s1.addWidget(QtWidgets.QLabel("Lyr:"));
        h_s1.addWidget(self.inp_slyr);
        h_s1.addWidget(QtWidgets.QLabel("DT:"));
        h_s1.addWidget(self.inp_sdt)
        f_seal.addRow(h_s1)
        h_s2 = QtWidgets.QHBoxLayout();
        self.inp_sw = QtWidgets.QLineEdit("20.0");
        self.inp_sdist = QtWidgets.QLineEdit("0.0")
        h_s2.addWidget(QtWidgets.QLabel("Width:"));
        h_s2.addWidget(self.inp_sw);
        h_s2.addWidget(QtWidgets.QLabel("Dist:"));
        h_s2.addWidget(self.inp_sdist)
        f_seal.addRow(h_s2)
        finish_layout.addWidget(grp_seal)
        self.tabs.addTab(tab_finish, "✨ Finish")

        tab_export = QtWidgets.QWidget()
        export_layout = QtWidgets.QVBoxLayout(tab_export)
        export_layout.addWidget(QtWidgets.QLabel("Merged Cell Name:"))
        self.inp_topname = QtWidgets.QLineEdit(self.top_cell_name);
        export_layout.addWidget(self.inp_topname)
        btn_map = QtWidgets.QPushButton("🛠️ Layer Mapping");
        btn_map.clicked.connect(self.open_layer_mapping_dialog)
        export_layout.addWidget(btn_map)
        btn_exp = QtWidgets.QPushButton("💾 EXPORT GDS");
        btn_exp.setMinimumHeight(40)
        btn_exp.setStyleSheet("background-color: #0078D7; color: white; font-weight: bold; border-radius: 4px;")
        btn_exp.clicked.connect(self.execute_stitch)
        export_layout.addWidget(btn_exp);
        export_layout.addStretch()
        self.tabs.addTab(tab_export, "💾 Export")
        left_layout.addWidget(self.tabs)

        # ================== 中央面板 ==================
        center_panel = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 5, 0, 5)

        tb1 = QtWidgets.QHBoxLayout()
        self.btn_theme = QtWidgets.QPushButton("🌙 Dark Theme");
        self.btn_theme.setCheckable(True)
        self.btn_theme.toggled.connect(self.on_theme_toggle);
        tb1.addWidget(self.btn_theme)

        btn_undo = QtWidgets.QPushButton("↩️ Undo");
        btn_undo.clicked.connect(self.action_undo)
        btn_redo = QtWidgets.QPushButton("↪️ Redo");
        btn_redo.clicked.connect(self.action_redo)
        tb1.addWidget(btn_undo);
        tb1.addWidget(btn_redo)

        btn_fit = QtWidgets.QPushButton("🔍 Fit");
        btn_fit.clicked.connect(lambda: self.canvas.autoRange())
        tb1.addWidget(btn_fit)

        self.btn_measure = QtWidgets.QPushButton("📏 Measure");
        self.btn_measure.setCheckable(True)
        self.btn_measure.toggled.connect(self.on_measure_toggle);
        tb1.addWidget(self.btn_measure)

        self.btn_overlap = QtWidgets.QPushButton("🔴 Overlap (ON)");
        self.btn_overlap.setCheckable(True)
        self.btn_overlap.setChecked(True);
        self.btn_overlap.toggled.connect(self.on_overlap_toggle)
        tb1.addWidget(self.btn_overlap)

        # ======== 三种显示模式的下拉切换 ========
        self.cb_display = QtWidgets.QComboBox()
        self.cb_display.addItems(["⬛ BBox", "🔲 Outline", "🎨 Full Layers"])
        self.cb_display.currentIndexChanged.connect(self.on_display_mode_change)
        tb1.addWidget(self.cb_display)

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
        btn_slot_tool = QtWidgets.QPushButton("🕳️ Slot");
        btn_slot_tool.clicked.connect(self.action_draw_slot_box);
        tb2.addWidget(btn_slot_tool)

        btn_clear = QtWidgets.QPushButton("🗑️ Clear");
        btn_clear.clicked.connect(self.action_clear_annotations);
        tb2.addWidget(btn_clear)

        btn_bool = QtWidgets.QPushButton("🔣 Boolean")
        btn_bool.setStyleSheet("color: #d35400; font-weight: bold;");
        btn_bool.clicked.connect(self.action_boolean_dialog)
        tb2.addWidget(btn_bool)

        self.cb_align_go = QtWidgets.QComboBox()
        self.cb_align_go.addItems(
            ["Align Left", "Align Center X", "Align Right", "Align Top", "Align Center Y", "Align Bottom",
             "Distribute H", "Distribute V"])
        btn_align = QtWidgets.QPushButton("▶ Align");
        btn_align.clicked.connect(self.execute_align)
        tb2.addWidget(self.cb_align_go);
        tb2.addWidget(btn_align);
        tb2.addStretch()
        center_layout.addLayout(tb2)

        view_box = GDSViewBox(self)
        self.canvas = pg.PlotWidget(viewBox=view_box)
        self.grid = pg.GridItem()
        self.canvas.addItem(self.grid)
        self.canvas.setAspectLocked(True)
        self.canvas.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        center_layout.addWidget(self.canvas, 1)

        self.status_label = QtWidgets.QLabel("Ready: You can drag and drop GDS files here.")
        center_layout.addWidget(self.status_label)

        # ================== 右侧面板 ==================
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)

        grp_layers = QtWidgets.QGroupBox("Layers (Check to display)")
        l_layout = QtWidgets.QVBoxLayout(grp_layers)
        btn_refresh = QtWidgets.QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self.refresh_layer_list)
        l_layout.addWidget(btn_refresh)

        self.layer_list = QtWidgets.QListWidget()
        self.layer_list.itemChanged.connect(self.on_layer_visibility_changed)
        l_layout.addWidget(self.layer_list)
        right_layout.addWidget(grp_layers, 1)

        grp_props = QtWidgets.QGroupBox("📌 Property Inspector")
        props_layout = QtWidgets.QFormLayout(grp_props)
        self.prop_type_lbl = QtWidgets.QLabel("-");
        self.prop_type_lbl.setStyleSheet("font-weight: bold; color: #0078D7;")
        self.prop_name_inp = QtWidgets.QLineEdit()
        self.prop_x_inp = QtWidgets.QLineEdit();
        self.prop_y_inp = QtWidgets.QLineEdit()
        self.prop_w_inp = QtWidgets.QLineEdit();
        self.prop_h_inp = QtWidgets.QLineEdit()
        self.prop_l_inp = QtWidgets.QLineEdit();
        self.prop_d_inp = QtWidgets.QLineEdit()

        props_layout.addRow("Type:", self.prop_type_lbl)
        props_layout.addRow("Name/Text:", self.prop_name_inp)
        props_layout.addRow("X:", self.prop_x_inp)
        props_layout.addRow("Y:", self.prop_y_inp)
        props_layout.addRow("Width/Size:", self.prop_w_inp)
        props_layout.addRow("Height:", self.prop_h_inp)
        props_layout.addRow("Layer:", self.prop_l_inp)
        props_layout.addRow("Datatype:", self.prop_d_inp)

        btn_prop_apply = QtWidgets.QPushButton("✅ Apply Changes")
        btn_prop_apply.setStyleSheet("background-color: #2ca02c; color: white; font-weight: bold;")
        btn_prop_apply.clicked.connect(self.apply_inspector_properties)
        props_layout.addRow(btn_prop_apply)
        right_layout.addWidget(grp_props, 0)

        grp_slot = QtWidgets.QGroupBox("🧀 Advanced Slotting")
        slot_layout = QtWidgets.QFormLayout(grp_slot)

        self.slot_layer_inp = QtWidgets.QLineEdit("10/0")
        h1 = QtWidgets.QHBoxLayout()
        self.slot_w_inp = QtWidgets.QLineEdit("5.0");
        self.slot_h_inp = QtWidgets.QLineEdit("10.0")
        h1.addWidget(self.slot_w_inp);
        h1.addWidget(QtWidgets.QLabel("x"));
        h1.addWidget(self.slot_h_inp)

        h2 = QtWidgets.QHBoxLayout()
        self.slot_px_inp = QtWidgets.QLineEdit("8.0");
        self.slot_py_inp = QtWidgets.QLineEdit("15.0")
        h2.addWidget(self.slot_px_inp);
        h2.addWidget(QtWidgets.QLabel("x"));
        h2.addWidget(self.slot_py_inp)

        self.slot_margin_inp = QtWidgets.QLineEdit("3.0")

        slot_layout.addRow("Target Layer:", self.slot_layer_inp)
        slot_layout.addRow("Slot W x H:", h1)
        slot_layout.addRow("Pitch X x Y:", h2)
        slot_layout.addRow("Safe Margin:", self.slot_margin_inp)

        btn_slot = QtWidgets.QPushButton("⚡ Execute Slotting")
        btn_slot.setStyleSheet("background-color: #800080; color: white; font-weight: bold;")
        btn_slot.clicked.connect(self.action_execute_slotting)
        slot_layout.addRow(btn_slot)

        right_layout.addWidget(grp_slot, 0)

        self.main_splitter.addWidget(left_panel)
        self.main_splitter.addWidget(center_panel)
        self.main_splitter.addWidget(right_panel)
        self.main_splitter.setSizes([320, 800, 280])

        self.apply_light_theme()
        self.populate_inspector()

    def on_display_mode_change(self):
        self.draw_preview(reset_view=False)

    def on_layer_visibility_changed(self, item):
        if self.cb_display.currentIndex() == 2:
            self.draw_preview(reset_view=False)

    # ================= 动态开槽算法 =================
    def action_execute_slotting(self):
        if not self.slot_box:
            QtWidgets.QMessageBox.warning(self, "Warning", "请先使用 '🕳️ Slot' 工具框选开槽区域！")
            return

        try:
            target_lyr_text = self.slot_layer_inp.text()
            tl, tdt = map(int, target_lyr_text.split('/')) if '/' in target_lyr_text else (int(target_lyr_text), 0)
            sw, sh = float(self.slot_w_inp.text()), float(self.slot_h_inp.text())
            px, py = float(self.slot_px_inp.text()), float(self.slot_py_inp.text())
            margin = float(self.slot_margin_inp.text())

            if sw <= 0 or sh <= 0 or px <= 0 or py <= 0:
                raise ValueError("尺寸或间距必须大于0！")

            self.status_label.setText("正在执行原位内缩开槽算法...")
            QtWidgets.QApplication.processEvents()

            self.save_snapshot()

            dbu = 0.001
            if self.gds_list:
                layout = db.Layout();
                layout.read(self.gds_list[0]['path']);
                dbu = layout.dbu

            pts = self.slot_box
            min_x, min_y = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
            max_x, max_y = max(pts[0][0], pts[1][0]), max(pts[0][1], pts[1][1])
            slot_region_global = db.Region(
                db.Box(int(min_x / dbu), int(min_y / dbu), int(max_x / dbu), int(max_y / dbu)))

            modified_any = False

            for i, gds in enumerate(self.gds_list):
                layout = db.Layout()
                layout.read(gds['path'])
                li = layout.find_layer(tl, tdt)
                if li is None: continue

                top_cell = layout.top_cells()[0]

                dx_dbu = round((gds['trans'].disp.x + gds['offset_x']) / dbu)
                dy_dbu = round((gds['trans'].disp.y + gds['offset_y']) / dbu)
                gds_trans = db.Trans(gds['trans'].rot, gds['trans'].is_mirror(), dx_dbu, dy_dbu)
                inv_trans = gds_trans.inverted()

                slot_region_local = slot_region_global.dup()
                slot_region_local.transform(inv_trans)

                metal_region = db.Region(top_cell.begin_shapes_rec(li))
                metal_region.merge()

                target_metal_in_slot = metal_region & slot_region_local
                if target_metal_in_slot.is_empty(): continue

                safe_region = target_metal_in_slot.dup()
                safe_region.size(-int(margin / dbu))
                if safe_region.is_empty(): continue

                slots_region = db.Region()
                bbox = safe_region.bbox()
                sw_dbu, sh_dbu = int(sw / dbu), int(sh / dbu)
                px_dbu, py_dbu = int(px / dbu), int(py / dbu)

                curr_x = bbox.left
                while curr_x + sw_dbu <= bbox.right:
                    curr_y = bbox.bottom
                    while curr_y + sh_dbu <= bbox.top:
                        slots_region.insert(db.Box(curr_x, curr_y, curr_x + sw_dbu, curr_y + sh_dbu))
                        curr_y += py_dbu
                    curr_x += px_dbu

                valid_slots = slots_region & safe_region
                if valid_slots.is_empty(): continue

                final_metal = metal_region - valid_slots

                for cell in layout.each_cell():
                    cell.shapes(li).clear()

                top_cell.shapes(li).insert(final_metal)

                temp_path = os.path.join(tempfile.gettempdir(),
                                         f"SLOTTED_{uuid.uuid4().hex[:8]}_{os.path.basename(gds['path'])}")
                layout.write(temp_path)

                gds['path'] = temp_path

                # === 同步重新计算全图层和轮廓数据 ===
                trans_complex = db.DCplxTrans(layout.dbu)
                all_layers_region = db.Region()
                full_polygons_by_layer = {}

                for layer_index in layout.layer_indexes():
                    shapes_reg = db.Region(top_cell.begin_shapes_rec(layer_index))
                    all_layers_region.insert(shapes_reg)

                    shapes_reg.merge()
                    layer_polys = []
                    for poly in shapes_reg.each():
                        dpoly = db.DPolygon(poly).transformed(trans_complex)
                        hull = [(pt.x, pt.y) for pt in dpoly.each_point_hull()]
                        if not hull: continue
                        holes = [[(pt.x, pt.y) for pt in dpoly.each_point_hole(h)] for h in range(dpoly.holes())]
                        layer_polys.append({'hull': hull, 'holes': holes})

                    if layer_polys:
                        layer_info = layout.get_info(layer_index)
                        full_polygons_by_layer[(layer_info.layer, layer_info.datatype)] = layer_polys

                all_layers_region.merge()
                all_layers_region = all_layers_region.hulls()

                true_polygons = [[(pt.x, pt.y) for pt in db.DPolygon(poly).transformed(trans_complex).each_point_hull()]
                                 for poly in all_layers_region.each() if
                                 list(db.DPolygon(poly).transformed(trans_complex).each_point_hull())]
                gds['true_polygons'] = true_polygons

                path = QtGui.QPainterPath()
                if true_polygons:
                    for poly in true_polygons:
                        qpoly = QtGui.QPolygonF([QtCore.QPointF(pt[0], pt[1]) for pt in poly])
                        path.addPolygon(qpoly)
                gds['qpath'] = path

                qpaths_full = {}
                for lyr_key, polys in full_polygons_by_layer.items():
                    l_path = QtGui.QPainterPath()
                    l_path.setFillRule(QtCore.Qt.OddEvenFill)
                    for poly_data in polys:
                        hull_poly = QtGui.QPolygonF([QtCore.QPointF(pt[0], pt[1]) for pt in poly_data['hull']])
                        l_path.addPolygon(hull_poly)
                        for hole in poly_data['holes']:
                            hole_poly = QtGui.QPolygonF([QtCore.QPointF(pt[0], pt[1]) for pt in hole])
                            l_path.addPolygon(hole_poly)
                    qpaths_full[lyr_key] = l_path
                gds['qpaths_full'] = qpaths_full

                modified_any = True

            shapes_to_remove = []
            shapes_to_add_as_gds = []

            for i, s in enumerate(self.user_shapes):
                if s['layer'] == tl and s['dt'] == tdt:
                    reg = db.Region()
                    pts = s['points']
                    if s['type'] == 'box':
                        smx, smy = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
                        sxx, sxy = max(pts[0][0], pts[1][0]), max(pts[0][1], pts[1][1])
                        reg.insert(db.Box(int(smx / dbu), int(smy / dbu), int(sxx / dbu), int(sxy / dbu)))
                    elif s['type'] == 'polygon':
                        pts_dbu = [db.Point(int(x / dbu), int(y / dbu)) for x, y in pts]
                        reg.insert(db.Polygon(pts_dbu))
                    elif s['type'] == 'path':
                        pts_dbu = [db.Point(int(x / dbu), int(y / dbu)) for x, y in pts]
                        w_dbu = int(s['width'] / dbu)
                        reg.insert(db.Path(pts_dbu, w_dbu).polygon())
                    elif s['type'] == 'via_array':
                        vw_dbu, vh_dbu = int(s.get('via_w', 1.0) / dbu), int(s.get('via_h', 1.0) / dbu)
                        px_dbu, py_dbu = int(s.get('pitch_x', 2.0) / dbu), int(s.get('pitch_y', 2.0) / dbu)
                        if px_dbu > 0 and py_dbu > 0 and vw_dbu > 0 and vh_dbu > 0:
                            smx, smy = int(min(pts[0][0], pts[1][0]) / dbu), int(min(pts[0][1], pts[1][1]) / dbu)
                            sxx, sxy = int(max(pts[0][0], pts[1][0]) / dbu), int(max(pts[0][1], pts[1][1]) / dbu)
                            curr_x = smx
                            while curr_x + vw_dbu <= sxx:
                                curr_y = smy
                                while curr_y + vh_dbu <= sxy:
                                    reg.insert(db.Box(curr_x, curr_y, curr_x + vw_dbu, curr_y + vh_dbu))
                                    curr_y += py_dbu
                                curr_x += px_dbu
                    reg.merge()

                    target_metal_in_slot = reg & slot_region_global
                    if target_metal_in_slot.is_empty(): continue

                    safe_region = target_metal_in_slot.dup()
                    safe_region.size(-int(margin / dbu))
                    if safe_region.is_empty(): continue

                    slots_region = db.Region()
                    bbox = safe_region.bbox()
                    sw_dbu, sh_dbu = int(sw / dbu), int(sh / dbu)
                    px_dbu, py_dbu = int(px / dbu), int(py / dbu)

                    curr_x = bbox.left
                    while curr_x + sw_dbu <= bbox.right:
                        curr_y = bbox.bottom
                        while curr_y + sh_dbu <= bbox.top:
                            slots_region.insert(db.Box(curr_x, curr_y, curr_x + sw_dbu, curr_y + sh_dbu))
                            curr_y += py_dbu
                        curr_x += px_dbu

                    valid_slots = slots_region & safe_region
                    if valid_slots.is_empty(): continue

                    final_metal = reg - valid_slots
                    shapes_to_remove.append(i)
                    shapes_to_add_as_gds.append(final_metal)
                    modified_any = True

            for i in sorted(shapes_to_remove, reverse=True):
                del self.user_shapes[i]

            for final_metal in shapes_to_add_as_gds:
                temp_path = os.path.join(tempfile.gettempdir(), f"SLOTTED_SHAPE_{uuid.uuid4().hex[:8]}.gds")
                out_layout = db.Layout()
                out_layout.dbu = dbu
                out_top = out_layout.create_cell("SLOTTED_SHAPE")
                out_li = out_layout.layer(tl, tdt)
                out_top.shapes(out_li).insert(final_metal)
                out_layout.write(temp_path)
                self.process_single_gds(temp_path)

            if modified_any:
                self.slot_box = None
                self.active_shape_type = None;
                self.active_shape_idx = -1
                self.status_label.setText("原位开槽完成！图形已更新。")
                self.draw_preview()
            else:
                self.undo_stack.pop()
                self.status_label.setText("Ready")
                QtWidgets.QMessageBox.information(self, "Info",
                                                  "未在选定区域找到目标金属，或其宽度无法满足安全边距 (Margin)，开槽终止。")

        except Exception as e:
            self.status_label.setText("Ready")
            QtWidgets.QMessageBox.critical(self, "Error", f"开槽失败:\n{str(e)}")

    # ================= 一键切换主题 =================
    def on_theme_toggle(self, checked):
        self.is_dark_mode = checked
        if checked:
            self.btn_theme.setText("☀️ Light Theme")
            self.apply_dark_theme()
        else:
            self.btn_theme.setText("🌙 Dark Theme")
            self.apply_light_theme()
        self.draw_preview(reset_view=False)

    def apply_dark_theme(self):
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
        QtWidgets.QApplication.instance().setPalette(palette)

        self.canvas.setBackground('#000000')
        ax_left = self.canvas.getAxis('left');
        ax_bottom = self.canvas.getAxis('bottom')
        ax_left.setPen(pg.mkPen('#888888'));
        ax_left.setTextPen(pg.mkPen('#CCCCCC'))
        ax_bottom.setPen(pg.mkPen('#888888'));
        ax_bottom.setTextPen(pg.mkPen('#CCCCCC'))
        self.grid.setTextPen(pg.mkPen('#888888'))
        self.grid.setPen(pg.mkPen(color=(100, 100, 100, 100), width=1))

    def apply_light_theme(self):
        QtWidgets.QApplication.instance().setPalette(self.default_palette)
        self.canvas.setBackground('#FFFFFF')
        ax_left = self.canvas.getAxis('left');
        ax_bottom = self.canvas.getAxis('bottom')
        ax_left.setPen(pg.mkPen('#555555'));
        ax_left.setTextPen(pg.mkPen('#555555'))
        ax_bottom.setPen(pg.mkPen('#555555'));
        ax_bottom.setTextPen(pg.mkPen('#555555'))
        self.grid.setTextPen(pg.mkPen('#555555'))
        self.grid.setPen(pg.mkPen(color=(180, 180, 180, 100), width=1))

    # ================= 动态属性检查器 =================
    def populate_inspector(self):
        self.prop_name_inp.setEnabled(False);
        self.prop_x_inp.setEnabled(False);
        self.prop_y_inp.setEnabled(False)
        self.prop_w_inp.setEnabled(False);
        self.prop_h_inp.setEnabled(False)
        self.prop_l_inp.setEnabled(False);
        self.prop_d_inp.setEnabled(False)

        if self.active_shape_type == 'gds' and self.active_shape_idx != -1:
            gds = self.gds_list[self.active_shape_idx]
            self.prop_type_lbl.setText("GDS Cell")
            self.prop_name_inp.setEnabled(True);
            self.prop_name_inp.setText(gds['name'])
            self.prop_x_inp.setEnabled(True);
            self.prop_y_inp.setEnabled(True)
            bl_x, bl_y = self.get_anchor_coords(gds, "Bottom-Left")
            self.prop_x_inp.setText(f"{bl_x:.3f}");
            self.prop_y_inp.setText(f"{bl_y:.3f}")

        elif self.active_shape_type == 'shape' and self.active_shape_idx != -1:
            s = self.user_shapes[self.active_shape_idx]
            self.prop_type_lbl.setText(s['type'].capitalize())
            self.prop_x_inp.setEnabled(True);
            self.prop_y_inp.setEnabled(True)
            self.prop_l_inp.setEnabled(True);
            self.prop_d_inp.setEnabled(True)
            self.prop_l_inp.setText(str(s['layer']));
            self.prop_d_inp.setText(str(s['dt']))

            if s['type'] in ['box', 'via_array']:
                self.prop_w_inp.setEnabled(True);
                self.prop_h_inp.setEnabled(True)
                pts = s['points']
                x0, y0 = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
                w, h = abs(pts[1][0] - pts[0][0]), abs(pts[1][1] - pts[0][1])
                self.prop_x_inp.setText(f"{x0:.3f}");
                self.prop_y_inp.setText(f"{y0:.3f}")
                self.prop_w_inp.setText(f"{w:.3f}");
                self.prop_h_inp.setText(f"{h:.3f}")
            elif s['type'] == 'path':
                self.prop_w_inp.setEnabled(True)
                self.prop_x_inp.setText(f"{s['points'][0][0]:.3f}");
                self.prop_y_inp.setText(f"{s['points'][0][1]:.3f}")
                self.prop_w_inp.setText(f"{s['width']:.3f}")
            elif s['type'] == 'polygon':
                self.prop_x_inp.setText(f"{s['points'][0][0]:.3f}");
                self.prop_y_inp.setText(f"{s['points'][0][1]:.3f}")

        elif self.active_shape_type == 'text' and self.active_shape_idx != -1:
            t = self.user_texts[self.active_shape_idx]
            self.prop_type_lbl.setText("Text Label")
            self.prop_name_inp.setEnabled(True);
            self.prop_name_inp.setText(t['text'])
            self.prop_x_inp.setEnabled(True);
            self.prop_y_inp.setEnabled(True)
            self.prop_x_inp.setText(f"{t['x']:.3f}");
            self.prop_y_inp.setText(f"{t['y']:.3f}")
            self.prop_w_inp.setEnabled(True);
            self.prop_w_inp.setText(f"{t['size']:.3f}")
            self.prop_l_inp.setEnabled(True);
            self.prop_d_inp.setEnabled(True)
            self.prop_l_inp.setText(str(t['layer']));
            self.prop_d_inp.setText(str(t['dt']))

        elif self.active_shape_type == 'crop' and self.crop_box:
            self.prop_type_lbl.setText("Crop Area")
            self.prop_x_inp.setEnabled(True);
            self.prop_y_inp.setEnabled(True)
            self.prop_w_inp.setEnabled(True);
            self.prop_h_inp.setEnabled(True)
            pts = self.crop_box
            x0, y0 = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
            w, h = abs(pts[1][0] - pts[0][0]), abs(pts[1][1] - pts[0][1])
            self.prop_x_inp.setText(f"{x0:.3f}");
            self.prop_y_inp.setText(f"{y0:.3f}")
            self.prop_w_inp.setText(f"{w:.3f}");
            self.prop_h_inp.setText(f"{h:.3f}")

        elif self.active_shape_type == 'slot' and self.slot_box:
            self.prop_type_lbl.setText("Slot Target Area")
            self.prop_x_inp.setEnabled(True);
            self.prop_y_inp.setEnabled(True)
            self.prop_w_inp.setEnabled(True);
            self.prop_h_inp.setEnabled(True)
            pts = self.slot_box
            x0, y0 = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
            w, h = abs(pts[1][0] - pts[0][0]), abs(pts[1][1] - pts[0][1])
            self.prop_x_inp.setText(f"{x0:.3f}");
            self.prop_y_inp.setText(f"{y0:.3f}")
            self.prop_w_inp.setText(f"{w:.3f}");
            self.prop_h_inp.setText(f"{h:.3f}")

        else:
            self.prop_type_lbl.setText("-")
            self.prop_name_inp.clear();
            self.prop_x_inp.clear();
            self.prop_y_inp.clear()
            self.prop_w_inp.clear();
            self.prop_h_inp.clear();
            self.prop_l_inp.clear();
            self.prop_d_inp.clear()

    def apply_inspector_properties(self):
        if self.active_shape_type == 'gds' and self.active_shape_idx != -1:
            self.save_snapshot()
            gds = self.gds_list[self.active_shape_idx]
            try:
                nx, ny = float(self.prop_x_inp.text()), float(self.prop_y_inp.text())
                self.set_anchor_coords(gds, "Bottom-Left", nx, ny)
                gds['name'] = self.prop_name_inp.text()
                self.list_widget.item(self.active_shape_idx).setText(f"[{self.active_shape_idx + 1}] {gds['name']}")
            except ValueError:
                pass
            self.draw_preview()

        elif self.active_shape_type == 'shape' and self.active_shape_idx != -1:
            self.save_snapshot()
            s = self.user_shapes[self.active_shape_idx]
            try:
                s['layer'], s['dt'] = int(self.prop_l_inp.text()), int(self.prop_d_inp.text())
                nx, ny = float(self.prop_x_inp.text()), float(self.prop_y_inp.text())
                if s['type'] in ['box', 'via_array']:
                    nw, nh = float(self.prop_w_inp.text()), float(self.prop_h_inp.text())
                    s['points'] = [(nx, ny), (nx + nw, ny + nh)]
                elif s['type'] == 'path':
                    s['width'] = float(self.prop_w_inp.text())
                    dx, dy = nx - s['points'][0][0], ny - s['points'][0][1]
                    s['points'] = [(px + dx, py + dy) for px, py in s['points']]
                elif s['type'] == 'polygon':
                    dx, dy = nx - s['points'][0][0], ny - s['points'][0][1]
                    s['points'] = [(px + dx, py + dy) for px, py in s['points']]
            except ValueError:
                pass
            self.draw_preview()

        elif self.active_shape_type == 'text' and self.active_shape_idx != -1:
            self.save_snapshot()
            t = self.user_texts[self.active_shape_idx]
            try:
                t['x'], t['y'] = float(self.prop_x_inp.text()), float(self.prop_y_inp.text())
                t['text'] = self.prop_name_inp.text()
                t['size'] = float(self.prop_w_inp.text())
                t['layer'], t['dt'] = int(self.prop_l_inp.text()), int(self.prop_d_inp.text())
            except ValueError:
                pass
            self.draw_preview()

        elif self.active_shape_type == 'crop' and self.crop_box:
            self.save_snapshot()
            try:
                nx, ny = float(self.prop_x_inp.text()), float(self.prop_y_inp.text())
                nw, nh = float(self.prop_w_inp.text()), float(self.prop_h_inp.text())
                self.crop_box = [(nx, ny), (nx + nw, ny + nh)]
            except ValueError:
                pass
            self.draw_preview()

        elif self.active_shape_type == 'slot' and self.slot_box:
            self.save_snapshot()
            try:
                nx, ny = float(self.prop_x_inp.text()), float(self.prop_y_inp.text())
                nw, nh = float(self.prop_w_inp.text()), float(self.prop_h_inp.text())
                self.slot_box = [(nx, ny), (nx + nw, ny + nh)]
            except ValueError:
                pass
            self.draw_preview()

    # ================= 极致内存优化的 Undo/Redo 核心 =================
    def create_snapshot_dict(self):
        clean_texts = [{k: v for k, v in t.items() if k != 'text_obj'} for t in self.user_texts]
        clean_shapes = [{k: v for k, v in s.items() if k != 'patch'} for s in self.user_shapes]
        snapshot = {
            'gds_list': [],
            'measurements': [dict(m) for m in self.measurements],
            'user_texts': clean_texts,
            'user_shapes': clean_shapes,
            'crop_box': self.crop_box.copy() if self.crop_box else None,
            'slot_box': self.slot_box.copy() if self.slot_box else None
        }
        for gds in self.gds_list:
            snap_gds = {
                'path': gds['path'], 'name': gds['name'], 'base_bbox': gds['base_bbox'],
                'trans': gds['trans'] * db.DTrans(), 'offset_x': gds['offset_x'], 'offset_y': gds['offset_y'],
                'color': gds['color'], 'true_polygons': gds['true_polygons'], 'layers': gds.get('layers', []),
                'qpath': gds.get('qpath'), 'qpaths_full': gds.get('qpaths_full')
            }
            snapshot['gds_list'].append(snap_gds)
        return snapshot

    def save_snapshot(self):
        self.undo_stack.append(self.create_snapshot_dict())
        self.redo_stack.clear()
        if len(self.undo_stack) > 50: self.undo_stack.pop(0)

    def restore_snapshot(self, snapshot):
        self.gds_list.clear();
        self.list_widget.clear()
        self.active_shape_type = None;
        self.active_shape_idx = -1

        for i, item in enumerate(snapshot['gds_list']):
            gds_info = {
                'path': item['path'], 'name': item['name'], 'base_bbox': item['base_bbox'],
                'trans': item['trans'], 'offset_x': item['offset_x'], 'offset_y': item['offset_y'],
                'color': item['color'], 'patch': None, 'shadow_patch': None, 'collection': [],
                'center_text': None, 'true_polygons': item['true_polygons'], 'layers': item.get('layers', []),
                'qpath': item.get('qpath'), 'qpaths_full': item.get('qpaths_full')
            }
            self.gds_list.append(gds_info)
            self.list_widget.addItem(f"[{i + 1}] {item['name']}")

        self.measurements = snapshot.get('measurements', [])
        self.user_texts = snapshot.get('user_texts', [])
        self.user_shapes = snapshot.get('user_shapes', [])
        self.crop_box = snapshot.get('crop_box')
        self.slot_box = snapshot.get('slot_box')
        self.clear_active_measurement()
        self.draw_preview(reset_view=False)

    def action_undo(self):
        if not self.undo_stack: return
        self.redo_stack.append(self.create_snapshot_dict())
        self.restore_snapshot(self.undo_stack.pop())

    def action_redo(self):
        if not self.redo_stack: return
        self.undo_stack.append(self.create_snapshot_dict())
        self.restore_snapshot(self.redo_stack.pop())

    # ================= 业务方法 =================
    def refresh_layer_list(self):
        state_map = {}
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            state_map[item.text()] = item.checkState()

        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        global_layers = set()
        for gds in self.gds_list: global_layers.update(gds.get('layers', []))

        for l, d in sorted(list(global_layers)):
            item_text = f"{l}/{d}"
            item = QtWidgets.QListWidgetItem(item_text)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(state_map.get(item_text, QtCore.Qt.Checked))
            self.layer_list.addItem(item)

        self.layer_list.blockSignals(False)

    def process_single_gds(self, filepath):
        self.status_label.setText(f"⏳ 正在后台极速解析: {os.path.basename(filepath)} ...")
        QtWidgets.QApplication.processEvents()
        worker = GDSLoadWorker(filepath)
        worker.result_ready.connect(self.on_gds_loaded)
        worker.error_occurred.connect(self.on_gds_load_error)
        self.workers.append(worker);
        worker.start()

    def on_gds_loaded(self, result):
        worker = self.sender()
        if worker in self.workers: self.workers.remove(worker)

        self.save_snapshot()
        filepath = result['filepath']
        base_name = os.path.splitext(os.path.basename(filepath))[0]
        base_bbox = db.DBox(result['bbox_left'], result['bbox_bottom'], result['bbox_right'], result['bbox_top'])
        true_polygons = result['true_polygons']
        full_polygons_by_layer = result['full_polygons']
        layers = result['layers']

        path_outline = QtGui.QPainterPath()
        if true_polygons:
            for poly in true_polygons:
                qpoly = QtGui.QPolygonF([QtCore.QPointF(pt[0], pt[1]) for pt in poly])
                path_outline.addPolygon(qpoly)

        qpaths_full = {}
        for lyr_key, polys in full_polygons_by_layer.items():
            l_path = QtGui.QPainterPath()
            l_path.setFillRule(QtCore.Qt.OddEvenFill)
            for poly_data in polys:
                hull_poly = QtGui.QPolygonF([QtCore.QPointF(pt[0], pt[1]) for pt in poly_data['hull']])
                l_path.addPolygon(hull_poly)
                for hole in poly_data['holes']:
                    hole_poly = QtGui.QPolygonF([QtCore.QPointF(pt[0], pt[1]) for pt in hole])
                    l_path.addPolygon(hole_poly)
            qpaths_full[lyr_key] = l_path

        cx_block, cy_block = self.block_w / 2.0, self.block_h / 2.0
        cx_gds, cy_gds = (base_bbox.left + base_bbox.right) / 2.0, (base_bbox.bottom + base_bbox.top) / 2.0

        gds_info = {'path': filepath, 'name': base_name, 'base_bbox': base_bbox, 'trans': db.DTrans(),
                    'offset_x': cx_block - cx_gds, 'offset_y': cy_block - cy_gds,
                    'color': self.color_palette[len(self.gds_list) % len(self.color_palette)],
                    'patch': None, 'shadow_patch': None, 'collection': [], 'center_text': None,
                    'true_polygons': true_polygons, 'layers': layers,
                    'qpath': path_outline, 'qpaths_full': qpaths_full}

        self.gds_list.append(gds_info)
        self.list_widget.addItem(f"[{len(self.gds_list)}] {base_name}")
        self.refresh_layer_list()
        self.status_label.setText("Ready (后台解析完成)")
        self.draw_preview(reset_view=True)

    def on_gds_load_error(self, filepath, error_msg):
        worker = self.sender()
        if worker in self.workers: self.workers.remove(worker)
        self.status_label.setText("Ready (加载失败)")
        QtWidgets.QMessageBox.critical(self, "Error", f"Failed to load {filepath}:\n{error_msg}")

    def add_gds(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Open GDS", "", "GDS Files (*.gds)")
        for p in paths: self.process_single_gds(p)

    def on_overlap_toggle(self):
        self.btn_overlap.setText("🔴 Overlaps (ON)" if self.btn_overlap.isChecked() else "⭕ Overlaps (OFF)")
        self.draw_overlaps()

    def draw_overlaps(self):
        for p in getattr(self, 'overlap_patches', []):
            try:
                self.canvas.removeItem(p)
            except:
                pass
        self.overlap_patches.clear()
        if not self.btn_overlap.isChecked(): return
        n = len(self.gds_list)
        tol = 1e-5
        for i in range(n):
            for j in range(i + 1, n):
                l1, r1, b1, t1 = self.get_bbox(self.gds_list[i])
                l2, r2, b2, t2 = self.get_bbox(self.gds_list[j])
                if (l1 < r2 - tol) and (r1 > l2 + tol) and (b1 < t2 - tol) and (t1 > b2 + tol):
                    il, ir, ib, it = max(l1, l2), min(r1, r2), max(b1, b2), min(t1, t2)
                    rect_item = QtWidgets.QGraphicsRectItem(il, ib, ir - il, it - ib)
                    brush = QtGui.QBrush(QtGui.QColor(255, 0, 0, 150), QtCore.Qt.FDiagPattern)
                    rect_item.setBrush(brush)
                    rect_item.setPen(pg.mkPen('r', width=2))
                    rect_item.setZValue(250)
                    self.canvas.addItem(rect_item)
                    self.overlap_patches.append(rect_item)

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
                            "slot_box": self.slot_box,
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
            self.measurements = project_data.get("measurements", [])
            self.user_texts = project_data.get("user_texts", [])
            self.user_shapes = project_data.get("user_shapes", [])
            self.crop_box = project_data.get("crop_box")
            self.slot_box = project_data.get("slot_box")
            saved_mapping = project_data.get("layer_mapping", {})
            self.layer_mapping = {eval(k_str): tuple(v) for k_str, v in saved_mapping.items()}
            self.clear_active_measurement()
            self.undo_stack.clear();
            self.redo_stack.clear()
            self.block_w = project_data.get("block_width", 5000.0)
            self.block_h = project_data.get("block_height", 5000.0)
            self.inp_block_w.setText(str(self.block_w));
            self.inp_block_h.setText(str(self.block_h))
            self.inp_topname.setText(project_data.get("top_cell_name", "MERGED_CHIP"))

            for i, item in enumerate(project_data.get("gds_items", [])):
                path = item.get("path")
                if not os.path.exists(path): continue
                self.process_single_gds(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def edit_text_dialog(self, idx):
        self.active_shape_type = 'text';
        self.active_shape_idx = idx
        self.update_canvas_selection()

    def edit_shape_dialog(self, idx):
        self.active_shape_type = 'shape';
        self.active_shape_idx = idx
        self.update_canvas_selection()

    def edit_crop_dialog(self):
        self.active_shape_type = 'crop';
        self.active_shape_idx = -1
        self.update_canvas_selection()

    def action_add_text_dialog(self):
        self.cancel_draw_mode()
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Add Text")
        layout = QtWidgets.QFormLayout(dlg)
        t_var = QtWidgets.QLineEdit("CHIP_LABEL");
        s_var = QtWidgets.QLineEdit("100.0")
        l_var = QtWidgets.QLineEdit("10");
        dt_var = QtWidgets.QLineEdit("0")
        layout.addRow("Text:", t_var);
        layout.addRow("Size (um):", s_var)
        layout.addRow("Layer:", l_var);
        layout.addRow("DT:", dt_var)
        btn = QtWidgets.QPushButton("Place on Canvas");
        btn.clicked.connect(dlg.accept)
        layout.addRow(btn)
        if dlg.exec_():
            try:
                self.draw_current_props = {'text': t_var.text(), 'size': float(s_var.text()),
                                           'layer': int(l_var.text()), 'dt': int(dt_var.text()),
                                           'rot': 0, 'mirror_x': False}  # Added default text transformations
                self.draw_mode = 'text';
                self.btn_measure.setChecked(False)
                self.status_label.setText("Text Mode: Click on Canvas to place.")
            except ValueError:
                pass

    def action_add_shape_dialog(self, shape_type):
        self.cancel_draw_mode()
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Add {shape_type.capitalize()}")
        layout = QtWidgets.QFormLayout(dlg)
        l_var = QtWidgets.QLineEdit("10");
        dt_var = QtWidgets.QLineEdit("0")
        layout.addRow("Layer:", l_var);
        layout.addRow("DT:", dt_var)

        w_var = QtWidgets.QLineEdit("20.0")
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
        btn.clicked.connect(dlg.accept)
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
                self.draw_points = []
                self.btn_measure.setChecked(False)
                self.status_label.setText(
                    f"{shape_type.capitalize()} Mode active. (拖拽 或 点击 两下绘制Box；单击连线，右键完成Polygon/Path)")
            except ValueError:
                pass

    def action_draw_crop_box(self):
        self.cancel_draw_mode()
        self.draw_mode = 'crop';
        self.draw_points = []
        self.btn_measure.setChecked(False)
        self.status_label.setText("Crop Mode: Drag to define crop area.")

    def action_draw_slot_box(self):
        self.cancel_draw_mode()
        self.draw_mode = 'slot';
        self.draw_points = []
        self.btn_measure.setChecked(False)
        self.status_label.setText("Slot Mode: Drag to define the slotting target area.")

    def cancel_draw_mode(self):
        self.draw_mode = None;
        self.draw_points = []
        if self.temp_draw_preview:
            try:
                self.canvas.removeItem(self.temp_draw_preview)
            except:
                pass
            self.temp_draw_preview = None
        self.status_label.setText("Ready")

    def update_canvas_selection(self):
        selected_indices = [self.list_widget.row(item) for item in self.list_widget.selectedItems()]
        display_mode = self.cb_display.currentIndex()

        for i, gds in enumerate(self.gds_list):
            if gds.get('patch'):
                if i in selected_indices:
                    if 'shadow_patch' in gds and gds['shadow_patch']:
                        gds['shadow_patch'].setOpacity(0.6 if display_mode == 0 else 0.0)

                    shadow = QtWidgets.QGraphicsDropShadowEffect()
                    shadow.setBlurRadius(15);
                    shadow.setColor(QtGui.QColor('#00FFFF'));
                    shadow.setOffset(0, 0)
                    gds['patch'].setGraphicsEffect(shadow if display_mode == 0 else None)

                    if display_mode == 0:
                        gds['patch'].setBrush(pg.mkBrush(QtGui.QColor(gds['color']).lighter(130).name() + '80'))
                        gds['patch'].setPen(pg.mkPen('#00FFFF', width=3))
                    else:
                        gds['patch'].setBrush(pg.mkBrush(None))
                        gds['patch'].setPen(pg.mkPen('#00FFFF', width=2, style=QtCore.Qt.DashLine))

                    if gds.get('collection'):
                        for item in gds['collection']:
                            item.setPen(pg.mkPen('#00FFFF', width=2))
                else:
                    if 'shadow_patch' in gds and gds['shadow_patch']: gds['shadow_patch'].setOpacity(0.0)
                    gds['patch'].setGraphicsEffect(None)

                    if display_mode == 0:
                        gds['patch'].setBrush(pg.mkBrush(gds['color'] + '60'))
                        gds['patch'].setPen(pg.mkPen(gds['color'], width=1))
                    else:
                        gds['patch'].setBrush(pg.mkBrush(None))
                        gds['patch'].setPen(pg.mkPen(None))

                    if gds.get('collection'):
                        for item in gds['collection']:
                            if hasattr(item, 'orig_pen'):
                                item.setPen(item.orig_pen)

        for i, ut in enumerate(self.user_texts):
            if 'text_obj' in ut and ut['text_obj']:
                if getattr(self, 'active_shape_type', None) == 'text' and getattr(self, 'active_shape_idx', -1) == i:
                    ut['text_obj'].setBrush(pg.mkBrush('#00FFFF'))
                else:
                    ut['text_obj'].setBrush(pg.mkBrush('#007788'))

        for i, s in enumerate(self.user_shapes):
            if 'patch' in s and s['patch']:
                if getattr(self, 'active_shape_type', None) == 'shape' and getattr(self, 'active_shape_idx', -1) == i:
                    s['patch'].setPen(pg.mkPen('#00FFFF', width=3, style=QtCore.Qt.DashLine))
                else:
                    ec = '#FF8C00' if s['type'] == 'box' else '#00CED1'
                    if s['type'] == 'polygon': ec = '#32CD32'
                    if s['type'] == 'path': ec = '#9370DB'
                    s['patch'].setPen(pg.mkPen(ec, width=2 if s['type'] in ['box', 'via_array'] else 1))

        if getattr(self, 'crop_rect_item', None):
            if self.active_shape_type == 'crop':
                self.crop_rect_item.setPen(pg.mkPen('#00FFFF', width=3, style=QtCore.Qt.DashLine))
            else:
                self.crop_rect_item.setPen(pg.mkPen('r', width=3, style=QtCore.Qt.DashLine))

        if getattr(self, 'slot_rect_item', None):
            if self.active_shape_type == 'slot':
                self.slot_rect_item.setPen(pg.mkPen('#FFFFFF', width=3, style=QtCore.Qt.DashLine))
            else:
                self.slot_rect_item.setPen(pg.mkPen('#00FFFF', width=3, style=QtCore.Qt.DashLine))

        self.populate_inspector()

    def open_layer_mapping_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Layer Mapping")
        dlg.resize(400, 300)
        layout = QtWidgets.QVBoxLayout(dlg)
        all_src = set()
        for gds in self.gds_list: all_src.update(gds.get('layers', []))
        for sl in sorted(list(all_src)):
            if sl not in self.layer_mapping: self.layer_mapping[sl] = sl

        table = QtWidgets.QTableWidget(len(self.layer_mapping), 4)
        table.setHorizontalHeaderLabels(["Src Layer", "Src DT", "New Layer", "New DT"])
        for i, ((sl, sdt), (tl, tdt)) in enumerate(sorted(self.layer_mapping.items())):
            table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(sl)))
            table.setItem(i, 1, QtWidgets.QTableWidgetItem(str(sdt)))
            table.setItem(i, 2, QtWidgets.QTableWidgetItem(str(tl)))
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
        selection = [self.list_widget.row(item) for item in self.list_widget.selectedItems()]
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
        selection = [self.list_widget.row(item) for item in self.list_widget.selectedItems()]
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
        self.draw_preview(reset_view=False)
        self.on_listbox_select()

    def on_measure_toggle(self):
        if self.btn_measure.isChecked():
            self.cancel_draw_mode()
            self.status_label.setText("Measure Mode ON: Click once to start, click again to finish.")
            self.list_widget.blockSignals(True)
            self.list_widget.clearSelection()
            self.list_widget.blockSignals(False)
            self.update_canvas_selection()
        else:
            self.status_label.setText("Measure Mode OFF.")
            self.clear_active_measurement()

    def action_clear_annotations(self):
        self.save_snapshot()
        self.measurements.clear()
        self.clear_active_measurement()
        self.active_shape_type = None
        self.active_shape_idx = -1
        self.btn_measure.setChecked(False)
        self.draw_preview(reset_view=False)

    def clear_active_measurement(self):
        for item in [self.measure_line, self.measure_text, self.snap_indicator, self.temp_draw_preview]:
            if item:
                try:
                    self.canvas.removeItem(item)
                except:
                    pass
        self.measure_line = self.measure_text = self.snap_indicator = self.temp_draw_preview = None
        for line in self.guide_lines:
            try:
                self.canvas.removeItem(line)
            except:
                pass
        self.guide_lines.clear()
        self.measure_state = 0
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
        selection = [self.list_widget.row(item) for item in self.list_widget.selectedItems()]
        if selection:
            x, y = self.get_anchor_coords(self.gds_list[selection[0]], self.cb_anchor.currentText())
            self.inp_x.setText(f"{x:.3f}");
            self.inp_y.setText(f"{y:.3f}")
            if self.btn_measure.isChecked(): self.btn_measure.setChecked(False)
        self.update_canvas_selection()

    def on_anchor_change(self):
        self.on_listbox_select()

    def apply_manual_position(self):
        selection = [self.list_widget.row(item) for item in self.list_widget.selectedItems()]
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

    def action_delete_selected(self):
        if getattr(self, 'active_shape_type', None) == 'text' and self.active_shape_idx != -1:
            self.save_snapshot()
            del self.user_texts[self.active_shape_idx]
            self.active_shape_type = None;
            self.active_shape_idx = -1
            self.draw_preview(reset_view=False)
            return

        if getattr(self, 'active_shape_type', None) == 'shape' and self.active_shape_idx != -1:
            self.save_snapshot()
            del self.user_shapes[self.active_shape_idx]
            self.active_shape_type = None;
            self.active_shape_idx = -1
            self.draw_preview(reset_view=False)
            return

        if getattr(self, 'active_shape_type', None) == 'crop':
            self.save_snapshot()
            self.crop_box = None
            self.active_shape_type = None;
            self.active_shape_idx = -1
            self.draw_preview(reset_view=False)
            return

        if getattr(self, 'active_shape_type', None) == 'slot':
            self.save_snapshot()
            self.slot_box = None
            self.active_shape_type = None;
            self.active_shape_idx = -1
            self.draw_preview(reset_view=False)
            return

        selection = sorted([self.list_widget.row(item) for item in self.list_widget.selectedItems()], reverse=True)
        if selection:
            self.save_snapshot()
            self.list_widget.blockSignals(True)
            for idx in selection:
                del self.gds_list[idx]
                self.list_widget.takeItem(idx)
            self.list_widget.clearSelection()
            self.list_widget.blockSignals(False)
            self.refresh_layer_list()
            self.inp_x.setText("0.0");
            self.inp_y.setText("0.0")
            self.draw_preview(reset_view=False)

    def create_text_path(self, text_str, size, x, y, rot=0, mirror_x=False):
        path = QtGui.QPainterPath()
        font = QtGui.QFont("Arial")
        font.setPixelSize(100)
        path.addText(0, 0, font, text_str)
        br = path.boundingRect()
        scale = size / br.height() if br.height() > 0 else 1.0

        tr = QtGui.QTransform()
        tr.translate(x, y)
        tr.rotate(-rot)  # pyqt的旋转方向和KLayout相反，补个负号
        if mirror_x: tr.scale(-1, 1)
        tr.scale(scale, -scale)
        tr.translate(-br.left(), -br.bottom())
        return tr.map(path)

    # ================== 核心：根据不同主题及显示模式适配颜色的渲染 ==================
    def draw_preview(self, reset_view=False):
        self.canvas.clear()
        self.clear_active_measurement()

        if self.is_dark_mode:
            bg_pen_color = '#555555';
            bg_brush_color = (25, 25, 25, 150)
            origin_pen = 'w';
            origin_brush = 'w'
            no_gds_color = '#bbbbbb';
            shadow_color = (0, 0, 0, 150)
            text_fill_color = '#FFFFFF'
        else:
            bg_pen_color = '#888888';
            bg_brush_color = (240, 240, 240, 200)
            origin_pen = '#555555';
            origin_brush = '#888888'
            no_gds_color = '#888888';
            shadow_color = (100, 100, 100, 100)
            text_fill_color = '#111111'

        bg_rect = QtWidgets.QGraphicsRectItem(0, 0, self.block_w, self.block_h)
        bg_rect.setPen(pg.mkPen(bg_pen_color, width=2, style=QtCore.Qt.DashLine))
        bg_rect.setBrush(pg.mkBrush(*bg_brush_color))
        self.canvas.addItem(bg_rect)

        origin = pg.ScatterPlotItem([0], [0], size=15, pen=pg.mkPen(origin_pen, width=2), brush=origin_brush,
                                    symbol='+')
        self.canvas.addItem(origin)

        if not self.gds_list:
            text = pg.TextItem('No GDS Loaded', color=no_gds_color, anchor=(0.5, 0.5))
            text.setPos(self.block_w / 2, self.block_h / 2)
            self.canvas.addItem(text)

        shadow_offset = min(self.block_w, self.block_h) * 0.01
        display_mode = self.cb_display.currentIndex()

        visible_layers = set()
        if display_mode == 2:
            for i in range(self.layer_list.count()):
                item = self.layer_list.item(i)
                if item.checkState() == QtCore.Qt.Checked:
                    try:
                        l_str, d_str = item.text().split('/')
                        visible_layers.add((int(l_str), int(d_str)))
                    except:
                        pass

        for gds in self.gds_list:
            t_box = gds['trans'] * gds['base_bbox']
            sx, sy = t_box.left + gds['offset_x'], t_box.bottom + gds['offset_y']
            w, h = t_box.width(), t_box.height()

            shadow_rect = QtWidgets.QGraphicsRectItem(sx + shadow_offset, sy - shadow_offset, w, h)
            shadow_rect.setBrush(pg.mkBrush(*shadow_color))
            shadow_rect.setPen(pg.mkPen(None));
            shadow_rect.setOpacity(0.0)
            shadow_rect.setZValue(8)
            self.canvas.addItem(shadow_rect)
            gds['shadow_patch'] = shadow_rect

            rect = QtWidgets.QGraphicsRectItem(sx, sy, w, h)
            if display_mode == 0:
                rect.setBrush(pg.mkBrush(gds['color'] + '40'))
                rect.setPen(pg.mkPen(gds['color'], width=1))
            else:
                rect.setBrush(pg.mkBrush(None))
                rect.setPen(pg.mkPen(None))

            rect.setZValue(10)
            self.canvas.addItem(rect)
            gds['patch'] = rect

            gds['collection'] = []

            tr = QtGui.QTransform()
            tr.translate(gds['offset_x'], gds['offset_y'])
            tr.translate(gds['trans'].disp.x, gds['trans'].disp.y)
            tr.rotate(gds['trans'].rot * 90.0)
            if gds['trans'].is_mirror(): tr.scale(1.0, -1.0)

            if display_mode == 1 and gds.get('qpath'):
                path_item = QtWidgets.QGraphicsPathItem(gds['qpath'])
                path_item.setBrush(pg.mkBrush(0, 0, 0, 150) if self.is_dark_mode else pg.mkBrush(200, 200, 200, 150))
                orig_pen = pg.mkPen(gds['color'], width=1)
                path_item.setPen(orig_pen)
                path_item.orig_pen = orig_pen
                path_item.setTransform(tr)
                path_item.setZValue(15)
                self.canvas.addItem(path_item)
                gds['collection'].append(path_item)

            elif display_mode == 2 and gds.get('qpaths_full'):
                for lyr_key, l_path in gds['qpaths_full'].items():
                    if lyr_key not in visible_layers:
                        continue

                    layer_color = self.get_layer_color(lyr_key[0], lyr_key[1])
                    path_item = QtWidgets.QGraphicsPathItem(l_path)
                    path_item.setBrush(pg.mkBrush(layer_color + '90'))
                    orig_pen = pg.mkPen(layer_color, width=1)
                    path_item.setPen(orig_pen)
                    path_item.orig_pen = orig_pen
                    path_item.setTransform(tr)
                    path_item.setZValue(15)
                    self.canvas.addItem(path_item)
                    gds['collection'].append(path_item)

            text_path = QtGui.QPainterPath()
            font = QtGui.QFont("Arial")
            font.setPixelSize(100)
            text_path.addText(0, 0, font, gds['name'])
            br = text_path.boundingRect()

            scale_w = (w * 0.5) / br.width() if br.width() > 0 else 1.0
            scale_h = (h * 0.1) / br.height() if br.height() > 0 else 1.0
            scale = min(scale_w, scale_h)

            t_text = QtGui.QTransform()
            t_text.scale(scale, -scale)
            t_text.translate(-br.center().x(), -br.center().y())
            base_text_path = t_text.map(text_path)

            text_item = QtWidgets.QGraphicsPathItem(base_text_path)
            text_item.setBrush(pg.mkBrush(text_fill_color))
            text_item.setPen(pg.mkPen(None))
            text_item.setPos(sx + w / 2, sy + h / 2)
            text_item.setZValue(90)
            self.canvas.addItem(text_item)
            gds['center_text'] = text_item

        for m in self.measurements:
            line = QtWidgets.QGraphicsLineItem(m['x0'], m['y0'], m['x1'], m['y1'])
            line.setPen(pg.mkPen('#FF8800', width=2, style=QtCore.Qt.DashLine))
            self.canvas.addItem(line)
            info = f"L: {math.hypot(m['x1'] - m['x0'], m['y1'] - m['y0']):.2f}\ndx: {abs(m['x1'] - m['x0']):.2f}\ndy: {abs(m['y1'] - m['y0']):.2f}"
            t = pg.TextItem(info, color='#FFFF00', anchor=(0, 1), fill=pg.mkBrush(0, 0, 0, 200))
            t.setPos(m['x1'], m['y1'])
            self.canvas.addItem(t)

        for ut in self.user_texts:
            path = self.create_text_path(ut['text'], ut['size'], ut['x'], ut['y'], ut.get('rot', 0),
                                         ut.get('mirror_x', False))
            text_item = QtWidgets.QGraphicsPathItem(path)
            text_item.setBrush(pg.mkBrush('#007788'))
            text_item.setPen(pg.mkPen(None))
            text_item.setZValue(250)
            self.canvas.addItem(text_item)
            ut['text_obj'] = text_item

        for s in self.user_shapes:
            if s['type'] in ['box', 'via_array']:
                pts = s['points']
                x0, y0 = pts[0];
                x1, y1 = pts[1]
                rect = QtWidgets.QGraphicsRectItem(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
                if s['type'] == 'box':
                    rect.setBrush(pg.mkBrush(220, 110, 0, 80))
                    rect.setPen(pg.mkPen('#CC6600', width=2))
                else:
                    rect.setBrush(pg.mkBrush(0, 0, 0, 0))
                    rect.setPen(pg.mkPen('#007788', width=2, style=QtCore.Qt.DotLine))
                rect.setZValue(240)
                self.canvas.addItem(rect)
                s['patch'] = rect

            elif s['type'] == 'polygon':
                qpoly = QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in s['points']])
                poly = QtWidgets.QGraphicsPolygonItem(qpoly)
                poly.setBrush(pg.mkBrush(0, 150, 0, 80))
                poly.setPen(pg.mkPen('#007700', width=1))
                poly.setZValue(240)
                self.canvas.addItem(poly)
                s['patch'] = poly

            elif s['type'] == 'path':
                pts = [db.DPoint(x, y) for x, y in s['points']]
                if len(pts) >= 2:
                    hull_pts = [(pt.x, pt.y) for pt in db.DPath(pts, s['width']).polygon().each_point_hull()]
                    qpoly = QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in hull_pts])
                    poly = QtWidgets.QGraphicsPolygonItem(qpoly)
                    poly.setBrush(pg.mkBrush(100, 0, 150, 80))
                    poly.setPen(pg.mkPen('#550088', width=1))
                    poly.setZValue(240)
                    self.canvas.addItem(poly)
                    s['patch'] = poly

        if self.crop_box:
            pts = self.crop_box
            x0, y0 = pts[0];
            x1, y1 = pts[1]
            rect = QtWidgets.QGraphicsRectItem(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            rect.setPen(pg.mkPen('r', width=3, style=QtCore.Qt.DashLine))
            rect.setBrush(pg.mkBrush(255, 0, 0, 15))
            rect.setZValue(400)
            self.canvas.addItem(rect)
            self.crop_rect_item = rect
        else:
            self.crop_rect_item = None

        if self.slot_box:
            pts = self.slot_box
            x0, y0 = pts[0];
            x1, y1 = pts[1]
            rect = QtWidgets.QGraphicsRectItem(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            rect.setPen(pg.mkPen('#00FFFF', width=3, style=QtCore.Qt.DashLine))
            rect.setBrush(pg.mkBrush(0, 255, 255, 20))
            rect.setZValue(400)
            self.canvas.addItem(rect)
            self.slot_rect_item = rect
        else:
            self.slot_rect_item = None

        self.update_canvas_selection()
        self.draw_overlaps()

        if reset_view:
            self.canvas.setXRange(-self.block_w * 0.1, self.block_w * 1.1, padding=0)
            self.canvas.setYRange(-self.block_h * 0.1, self.block_h * 1.1, padding=0)

    def is_hit(self, x, y, item):
        if not item: return False
        pt = QtCore.QPointF(x, y)
        if isinstance(item, (QtWidgets.QGraphicsRectItem, QtWidgets.QGraphicsPolygonItem, QtWidgets.QGraphicsPathItem)):
            return item.contains(pt)
        scene_pt = self.canvas.plotItem.vb.mapViewToScene(pt)
        local_pt = item.mapFromScene(scene_pt)
        if isinstance(item, pg.TextItem): return item.boundingRect().contains(local_pt)
        return item.contains(local_pt)

    def get_snapped_coordinate(self, x, y):
        view_range = self.canvas.viewRange()
        cur_xlim, cur_ylim = view_range[0], view_range[1]
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

    def get_pois(self, gds, temp_ox=None, temp_oy=None):
        t_box = gds['trans'] * gds['base_bbox']
        ox, oy = (gds['offset_x'] if temp_ox is None else temp_ox), (gds['offset_y'] if temp_oy is None else temp_oy)
        return [t_box.left + ox, t_box.right + ox, (t_box.left + t_box.right) / 2 + ox], \
            [t_box.bottom + oy, t_box.top + oy, (t_box.bottom + t_box.top) / 2 + oy]

    def apply_ortho(self, nx, ny, ref_pt):
        if abs(nx - ref_pt[0]) > abs(ny - ref_pt[1]): return nx, ref_pt[1]
        return ref_pt[0], ny

    def finalize_shape(self):
        self.save_snapshot()
        if self.draw_mode == 'crop':
            self.crop_box = list(self.draw_points)
        elif self.draw_mode == 'slot':
            self.slot_box = list(self.draw_points)
        else:
            new_shape = self.draw_current_props.copy()
            new_shape['points'] = list(self.draw_points)
            self.user_shapes.append(new_shape)
        self.cancel_draw_mode()
        self.draw_preview(reset_view=False)

    def check_edge_hit(self, x, y):
        px_x, px_y = self.canvas.plotItem.vb.viewPixelSize()
        tol_x, tol_y = px_x * 8, px_y * 8

        if self.crop_box and getattr(self, 'active_shape_type', None) == 'crop':
            edge = self._get_edge(x, y, self.crop_box, tol_x, tol_y)
            if edge: return 'crop', -1, edge

        if self.slot_box and getattr(self, 'active_shape_type', None) == 'slot':
            edge = self._get_edge(x, y, self.slot_box, tol_x, tol_y)
            if edge: return 'slot', -1, edge

        if getattr(self, 'active_shape_type', None) == 'shape' and self.active_shape_idx != -1:
            s = self.user_shapes[self.active_shape_idx]
            if s['type'] in ['box', 'via_array']:
                edge = self._get_edge(x, y, s['points'], tol_x, tol_y)
                if edge: return 'shape', self.active_shape_idx, edge

        return None, -1, None

    def _get_edge(self, x, y, pts, tol_x, tol_y):
        x0, y0 = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
        x1, y1 = max(pts[0][0], pts[1][0]), max(pts[0][1], pts[1][1])
        if y0 - tol_y <= y <= y1 + tol_y:
            if abs(x - x0) <= tol_x: return 'left'
            if abs(x - x1) <= tol_x: return 'right'
        if x0 - tol_x <= x <= x1 + tol_x:
            if abs(y - y0) <= tol_y: return 'bottom'
            if abs(y - y1) <= tol_y: return 'top'
        return None

    def handle_mouse_doubleclick(self, x, y):
        for i in range(len(self.user_texts) - 1, -1, -1):
            if 'text_obj' in self.user_texts[i] and self.is_hit(x, y, self.user_texts[i]['text_obj']):
                self.edit_text_dialog(i);
                return
        for i in range(len(self.user_shapes) - 1, -1, -1):
            if 'patch' in self.user_shapes[i] and self.is_hit(x, y, self.user_shapes[i]['patch']):
                self.edit_shape_dialog(i);
                return

        if getattr(self, 'crop_rect_item', None) and self.is_hit(x, y, self.crop_rect_item):
            self.edit_crop_dialog();
            return

        if self.draw_mode in ['polygon', 'path']:
            if len(self.draw_points) >= (3 if self.draw_mode == 'polygon' else 2):
                self.finalize_shape()
            else:
                self.cancel_draw_mode()

    # === 全面升级的右键点击检测 ===
    def handle_mouse_click(self, x, y, is_double=False, button=QtCore.Qt.LeftButton):
        if button == QtCore.Qt.RightButton:
            # 1. 测查 GDS
            clicked_idx = next(
                (i for i in range(len(self.gds_list) - 1, -1, -1) if self.is_hit(x, y, self.gds_list[i]['patch'])), -1)
            if clicked_idx != -1:
                self.show_context_menu('gds', clicked_idx)
                return

            # 2. 测查自绘 Shape (Box, Polygon, ViaArray, Path)
            clicked_shape_idx = next((i for i in range(len(self.user_shapes) - 1, -1, -1) if
                                      'patch' in self.user_shapes[i] and self.is_hit(x, y,
                                                                                     self.user_shapes[i]['patch'])), -1)
            if clicked_shape_idx != -1:
                self.show_context_menu('shape', clicked_shape_idx)
                return

            # 3. 测查 Text 文字
            clicked_text_idx = next((i for i in range(len(self.user_texts) - 1, -1, -1) if
                                     'text_obj' in self.user_texts[i] and self.is_hit(x, y,
                                                                                      self.user_texts[i]['text_obj'])),
                                    -1)
            if clicked_text_idx != -1:
                self.show_context_menu('text', clicked_text_idx)
                return
            return

        snap_x, snap_y, _, _ = self.get_snapped_coordinate(x, y)
        if self.ctrl_pressed:
            if self.btn_measure.isChecked() and self.measure_start_pt:
                snap_x, snap_y = self.apply_ortho(snap_x, snap_y, self.measure_start_pt)
            elif self.draw_mode in ['polygon', 'path'] and self.draw_points:
                snap_x, snap_y = self.apply_ortho(snap_x, snap_y, self.draw_points[-1])

        if self.draw_mode == 'text':
            self.save_snapshot()
            new_text = self.draw_current_props.copy()
            new_text['x'], new_text['y'] = snap_x, snap_y
            self.user_texts.append(new_text)
            self.cancel_draw_mode()
            self.draw_preview(reset_view=False)
            return
        elif self.draw_mode in ['box', 'via_array', 'crop', 'slot']:
            if not self.draw_points:
                self.draw_points.append((snap_x, snap_y))
            else:
                self.draw_points.append((snap_x, snap_y));
                self.finalize_shape()
            return
        elif self.draw_mode in ['polygon', 'path']:
            self.draw_points.append((snap_x, snap_y))
            return

        if self.btn_measure.isChecked():
            if self.measure_state == 0:
                self.clear_active_measurement()
                self.measure_start_pt = (snap_x, snap_y)
                self.measure_line = QtWidgets.QGraphicsLineItem(snap_x, snap_y, snap_x, snap_y)
                self.measure_line.setPen(pg.mkPen('#FF8800', width=2, style=QtCore.Qt.DashLine))
                self.measure_line.setZValue(300)
                self.canvas.addItem(self.measure_line)
                self.measure_text = pg.TextItem('', color='#FFFF00', fill=pg.mkBrush(0, 0, 0, 200))
                self.measure_text.setPos(snap_x, snap_y)
                self.canvas.addItem(self.measure_text)
                self.measure_state = 1
            elif self.measure_state == 1:
                self.save_snapshot()
                self.measurements.append(
                    {'x0': self.measure_start_pt[0], 'y0': self.measure_start_pt[1], 'x1': snap_x, 'y1': snap_y})
                self.measure_state = 0
                self.measure_line = self.measure_text = None
            return

        self._process_selection_at(x, y, prepare_drag=False)

    def handle_mouse_press(self, x, y):
        pass

    def handle_drag_start(self, x, y):
        snap_x, snap_y, _, _ = self.get_snapped_coordinate(x, y)
        if self.draw_mode in ['box', 'via_array', 'crop', 'slot'] and not self.draw_points:
            self.draw_points.append((snap_x, snap_y))
            return
        if self.draw_mode is not None or self.btn_measure.isChecked(): return

        hit_type, idx, edge = self.check_edge_hit(x, y)
        if hit_type:
            self.dragging_edge = edge
            self.dragging_type = hit_type + "_edge"
            self.dragging_idx = idx
            self.drag_start_x, self.drag_start_y = x, y
            if hit_type == 'crop':
                self.drag_start_offsets = list(self.crop_box)
            elif hit_type == 'slot':
                self.drag_start_offsets = list(self.slot_box)
            else:
                self.drag_start_offsets = list(self.user_shapes[idx]['points'])
            return

        self._process_selection_at(x, y, prepare_drag=True)

    def _process_selection_at(self, x, y, prepare_drag=False):
        for i in range(len(self.user_texts) - 1, -1, -1):
            if 'text_obj' in self.user_texts[i] and self.is_hit(x, y, self.user_texts[i]['text_obj']):
                self.active_shape_type = 'text';
                self.active_shape_idx = i
                self.list_widget.blockSignals(True);
                self.list_widget.clearSelection();
                self.list_widget.blockSignals(False)
                self.update_canvas_selection()
                if prepare_drag:
                    self.dragging_type = 'text';
                    self.dragging_idx = i
                    self.drag_start_x, self.drag_start_y = x, y
                    self.rect_start_x, self.rect_start_y = self.user_texts[i]['x'], self.user_texts[i]['y']
                return

        for i in range(len(self.user_shapes) - 1, -1, -1):
            if 'patch' in self.user_shapes[i] and self.is_hit(x, y, self.user_shapes[i]['patch']):
                self.active_shape_type = 'shape';
                self.active_shape_idx = i
                self.list_widget.blockSignals(True);
                self.list_widget.clearSelection();
                self.list_widget.blockSignals(False)
                self.update_canvas_selection()
                if prepare_drag:
                    self.dragging_type = 'shape';
                    self.dragging_idx = i
                    self.drag_start_x, self.drag_start_y = x, y
                    self.drag_start_offsets = list(self.user_shapes[i]['points'])
                return

        if getattr(self, 'crop_rect_item', None) and self.is_hit(x, y, self.crop_rect_item):
            self.active_shape_type = 'crop';
            self.active_shape_idx = -1
            self.list_widget.blockSignals(True);
            self.list_widget.clearSelection();
            self.list_widget.blockSignals(False)
            self.update_canvas_selection()
            if prepare_drag:
                self.dragging_type = 'crop';
                self.dragging_idx = -1
                self.drag_start_x, self.drag_start_y = x, y
                self.drag_start_offsets = list(self.crop_box)
            return

        if getattr(self, 'slot_rect_item', None) and self.is_hit(x, y, self.slot_rect_item):
            self.active_shape_type = 'slot';
            self.active_shape_idx = -1
            self.list_widget.blockSignals(True);
            self.list_widget.clearSelection();
            self.list_widget.blockSignals(False)
            self.update_canvas_selection()
            if prepare_drag:
                self.dragging_type = 'slot';
                self.dragging_idx = -1
                self.drag_start_x, self.drag_start_y = x, y
                self.drag_start_offsets = list(self.slot_box)
            return

        clicked_idx = next(
            (i for i in range(len(self.gds_list) - 1, -1, -1) if self.is_hit(x, y, self.gds_list[i]['patch'])), -1)
        if clicked_idx != -1:
            self.active_shape_type = 'gds';
            self.active_shape_idx = clicked_idx
            current_selection = [self.list_widget.row(item) for item in self.list_widget.selectedItems()]
            self.list_widget.blockSignals(True)
            if self.ctrl_pressed:
                if clicked_idx in current_selection:
                    self.list_widget.item(clicked_idx).setSelected(False)
                else:
                    self.list_widget.item(clicked_idx).setSelected(True)
            elif clicked_idx not in current_selection:
                self.list_widget.clearSelection()
                self.list_widget.item(clicked_idx).setSelected(True)
            self.list_widget.blockSignals(False)
            self.on_listbox_select()

            if prepare_drag:
                self.dragging_type = 'gds';
                self.dragging_idx = clicked_idx
                self.drag_start_x, self.drag_start_y = x, y
                self.rect_start_x = self.gds_list[clicked_idx]['patch'].rect().x()
                self.rect_start_y = self.gds_list[clicked_idx]['patch'].rect().y()
                self.drag_start_offsets = {idx: (self.gds_list[idx]['offset_x'], self.gds_list[idx]['offset_y'])
                                           for idx in
                                           [self.list_widget.row(item) for item in self.list_widget.selectedItems()]}
            return

        if not self.ctrl_pressed:
            self.active_shape_type = None;
            self.active_shape_idx = -1
            self.list_widget.blockSignals(True);
            self.list_widget.clearSelection();
            self.list_widget.blockSignals(False)
            self.update_canvas_selection()

    def handle_drag_finish(self, x, y):
        snap_x, snap_y, _, _ = self.get_snapped_coordinate(x, y)
        if self.ctrl_pressed:
            if self.btn_measure.isChecked() and self.measure_start_pt:
                snap_x, snap_y = self.apply_ortho(snap_x, snap_y, self.measure_start_pt)
            elif self.draw_mode in ['polygon', 'path'] and self.draw_points:
                snap_x, snap_y = self.apply_ortho(snap_x, snap_y, self.draw_points[-1])

        if self.draw_mode in ['box', 'via_array', 'crop', 'slot'] and len(self.draw_points) == 1:
            self.draw_points.append((snap_x, snap_y))
            self.finalize_shape()
            return

        if self.dragging_type is not None:
            self.dragging_type = None;
            self.dragging_idx = -1;
            self.dragging_edge = None
            self.drag_snapshot_taken = False;
            self.drag_start_offsets.clear()
            for line in self.guide_lines: self.canvas.removeItem(line)
            self.guide_lines.clear()
            self.update_canvas_selection()
            self.canvas.setCursor(QtCore.Qt.ArrowCursor)

            self.draw_overlaps()

    def handle_mouse_move(self, x, y):
        snap_x, snap_y, sn_x, sn_y = self.get_snapped_coordinate(x, y)
        if self.ctrl_pressed:
            if self.btn_measure.isChecked() and self.measure_start_pt:
                snap_x, snap_y = self.apply_ortho(snap_x, snap_y, self.measure_start_pt)
            elif self.draw_mode in ['polygon', 'path'] and self.draw_points:
                snap_x, snap_y = self.apply_ortho(snap_x, snap_y, self.draw_points[-1])

        if self.dragging_type is None and self.draw_mode is None and not self.btn_measure.isChecked():
            hit_type, _, edge = self.check_edge_hit(x, y)
            if edge in ['left', 'right']:
                self.canvas.setCursor(QtCore.Qt.SizeHorCursor)
            elif edge in ['top', 'bottom']:
                self.canvas.setCursor(QtCore.Qt.SizeVerCursor)
            else:
                self.canvas.setCursor(QtCore.Qt.ArrowCursor)

        if self.draw_mode is not None:
            if self.draw_mode == 'text':
                path = self.create_text_path(self.draw_current_props['text'], self.draw_current_props['size'], snap_x,
                                             snap_y,
                                             self.draw_current_props.get('rot', 0),
                                             self.draw_current_props.get('mirror_x', False))
                if not self.temp_draw_preview:
                    self.temp_draw_preview = QtWidgets.QGraphicsPathItem(path)
                    self.temp_draw_preview.setBrush(pg.mkBrush('#007788'))
                    self.temp_draw_preview.setPen(pg.mkPen(None))
                    self.temp_draw_preview.setZValue(350)
                    self.canvas.addItem(self.temp_draw_preview)
                else:
                    self.temp_draw_preview.setPath(path)
            elif self.draw_mode in ['box', 'via_array', 'crop', 'slot'] and len(self.draw_points) == 1:
                x0, y0 = self.draw_points[0]
                if not self.temp_draw_preview:
                    self.temp_draw_preview = QtWidgets.QGraphicsRectItem(min(x0, snap_x), min(y0, snap_y),
                                                                         abs(snap_x - x0), abs(snap_y - y0))
                    self.temp_draw_preview.setPen(pg.mkPen('#CC6600', width=2, style=QtCore.Qt.DashLine))
                    self.canvas.addItem(self.temp_draw_preview)
                else:
                    self.temp_draw_preview.setRect(min(x0, snap_x), min(y0, snap_y), abs(snap_x - x0), abs(snap_y - y0))

            elif self.draw_mode in ['polygon', 'path'] and len(self.draw_points) > 0:
                pts = self.draw_points + [(snap_x, snap_y)]
                path = QtGui.QPainterPath()
                path.moveTo(pts[0][0], pts[0][1])
                for px, py in pts[1:]:
                    path.lineTo(px, py)
                if self.draw_mode == 'polygon':
                    path.lineTo(pts[0][0], pts[0][1])

                if not self.temp_draw_preview:
                    self.temp_draw_preview = QtWidgets.QGraphicsPathItem(path)
                    self.temp_draw_preview.setPen(pg.mkPen('#CC6600', width=2, style=QtCore.Qt.DashLine))
                    self.canvas.addItem(self.temp_draw_preview)
                else:
                    self.temp_draw_preview.setPath(path)
            return

        if self.btn_measure.isChecked():
            indicator_color = '#FF8800'
            if not self.snap_indicator:
                self.snap_indicator = pg.ScatterPlotItem([snap_x], [snap_y], size=12,
                                                         pen=pg.mkPen(indicator_color, width=2), brush=indicator_color,
                                                         symbol='+')
                self.canvas.addItem(self.snap_indicator)
            else:
                self.snap_indicator.setData([snap_x], [snap_y])

            for line in self.guide_lines: self.canvas.removeItem(line)
            self.guide_lines.clear()
            guide_pen = pg.mkPen(indicator_color, width=1.5, style=QtCore.Qt.DashLine)
            if sn_x:
                l = pg.InfiniteLine(pos=snap_x, angle=90, pen=guide_pen)
                self.canvas.addItem(l);
                self.guide_lines.append(l)
            if sn_y:
                l = pg.InfiniteLine(pos=snap_y, angle=0, pen=guide_pen)
                self.canvas.addItem(l);
                self.guide_lines.append(l)

            if self.measure_state == 1 and self.measure_start_pt is not None:
                x0, y0 = self.measure_start_pt
                self.measure_line.setLine(x0, y0, snap_x, snap_y)
                self.measure_text.setPos(snap_x, snap_y)
                self.measure_text.setText(
                    f" L: {math.hypot(snap_x - x0, snap_y - y0):.2f}\n dx: {abs(snap_x - x0):.2f}\n dy: {abs(snap_y - y0):.2f}")
            return

        if self.dragging_type is None: return
        if not self.drag_snapshot_taken: self.save_snapshot(); self.drag_snapshot_taken = True

        dx_raw = x - self.drag_start_x
        dy_raw = y - self.drag_start_y

        if self.dragging_edge:
            orig_pts = self.drag_start_offsets
            x0, x1 = min(orig_pts[0][0], orig_pts[1][0]), max(orig_pts[0][0], orig_pts[1][0])
            y0, y1 = min(orig_pts[0][1], orig_pts[1][1]), max(orig_pts[0][1], orig_pts[1][1])

            if self.dragging_edge == 'left':
                x0 = snap_x
            elif self.dragging_edge == 'right':
                x1 = snap_x
            elif self.dragging_edge == 'bottom':
                y0 = snap_y
            elif self.dragging_edge == 'top':
                y1 = snap_y

            new_pts = [(x0, y0), (x1, y1)]
            min_x, max_x = min(x0, x1), max(x0, x1)
            min_y, max_y = min(y0, y1), max(y0, y1)

            if self.dragging_type == 'crop_edge':
                self.crop_box = new_pts
                if self.crop_rect_item: self.crop_rect_item.setRect(min_x, min_y, max_x - min_x, max_y - min_y)
            elif self.dragging_type == 'slot_edge':
                self.slot_box = new_pts
                if self.slot_rect_item: self.slot_rect_item.setRect(min_x, min_y, max_x - min_x, max_y - min_y)
            elif self.dragging_type == 'shape_edge':
                s = self.user_shapes[self.dragging_idx]
                s['points'] = new_pts
                s['patch'].setRect(min_x, min_y, max_x - min_x, max_y - min_y)
                if self.active_shape_idx == self.dragging_idx:
                    self.prop_x_inp.setText(f"{min_x:.3f}");
                    self.prop_y_inp.setText(f"{min_y:.3f}")
                    self.prop_w_inp.setText(f"{max_x - min_x:.3f}");
                    self.prop_h_inp.setText(f"{max_y - min_y:.3f}")
            return

        if self.dragging_type == 'text':
            nx, ny = self.rect_start_x + dx_raw, self.rect_start_y + dy_raw
            if self.chk_snap.isChecked():
                try:
                    g_size = float(self.inp_snap.text())
                    if g_size > 0: nx, ny = round(nx / g_size) * g_size, round(ny / g_size) * g_size
                except ValueError:
                    pass
            self.user_texts[self.dragging_idx]['x'], self.user_texts[self.dragging_idx]['y'] = nx, ny

            ut = self.user_texts[self.dragging_idx]
            path = self.create_text_path(ut['text'], ut['size'], nx, ny, ut.get('rot', 0), ut.get('mirror_x', False))
            ut['text_obj'].setPath(path)

            if self.active_shape_type == 'text':
                self.prop_x_inp.setText(f"{nx:.3f}");
                self.prop_y_inp.setText(f"{ny:.3f}")
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
                s['patch'].setRect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            elif s['type'] == 'polygon':
                s['patch'].setPolygon(QtGui.QPolygonF([QtCore.QPointF(px, py) for px, py in new_pts]))
            elif s['type'] == 'path':
                pts = [db.DPoint(px, py) for px, py in new_pts]
                if len(pts) >= 2:
                    hull_pts = [(pt.x, pt.y) for pt in db.DPath(pts, s['width']).polygon().each_point_hull()]
                    s['patch'].setPolygon(QtGui.QPolygonF([QtCore.QPointF(px, py) for px, py in hull_pts]))

            if self.active_shape_type == 'shape':
                self.prop_x_inp.setText(f"{min(x0, x1) if s['type'] in ['box', 'via_array'] else new_pts[0][0]:.3f}")
                self.prop_y_inp.setText(f"{min(y0, y1) if s['type'] in ['box', 'via_array'] else new_pts[0][1]:.3f}")
            return

        if self.dragging_type == 'crop':
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
            self.crop_box = new_pts
            if self.crop_rect_item:
                x0, y0 = new_pts[0];
                x1, y1 = new_pts[1]
                self.crop_rect_item.setRect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            return

        if self.dragging_type == 'slot':
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
            self.slot_box = new_pts
            if self.slot_rect_item:
                x0, y0 = new_pts[0];
                x1, y1 = new_pts[1]
                self.slot_rect_item.setRect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            return

        for line in self.guide_lines: self.canvas.removeItem(line)
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
            min_dx, min_dy = (self.canvas.viewRange()[0][1] - self.canvas.viewRange()[0][0]) * 0.02, (
                    self.canvas.viewRange()[1][1] - self.canvas.viewRange()[1][0]) * 0.02
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

            t_rect = gds['trans'] * gds['base_bbox']
            nx_final, ny_final = t_rect.left + new_ox, t_rect.bottom + new_oy
            gds['patch'].setRect(nx_final, ny_final, t_rect.width(), t_rect.height())

            if 'shadow_patch' in gds and gds['shadow_patch']:
                shadow_offset = min(self.block_w, self.block_h) * 0.015
                gds['shadow_patch'].setRect(nx_final + shadow_offset, ny_final - shadow_offset, t_rect.width(),
                                            t_rect.height())

            if gds['center_text']:
                gds['center_text'].setPos(nx_final + t_rect.width() / 2, ny_final + t_rect.height() / 2)

            if gds.get('collection'):
                tr = QtGui.QTransform()
                tr.translate(new_ox, new_oy)
                tr.translate(gds['trans'].disp.x, gds['trans'].disp.y)
                tr.rotate(gds['trans'].rot * 90.0)
                if gds['trans'].is_mirror(): tr.scale(1.0, -1.0)
                for item in gds['collection']:
                    item.setTransform(tr)

        if not is_grid_snapped:
            guide_pen = pg.mkPen('#FF8800', width=1.5, style=QtCore.Qt.DashLine)
            if best_snap_x is not None:
                l = pg.InfiniteLine(pos=best_snap_x, angle=90, pen=guide_pen)
                self.canvas.addItem(l);
                self.guide_lines.append(l)
            if best_snap_y is not None:
                l = pg.InfiniteLine(pos=best_snap_y, angle=0, pen=guide_pen)
                self.canvas.addItem(l);
                self.guide_lines.append(l)

        if [self.list_widget.row(item) for item in self.list_widget.selectedItems()] and \
                [self.list_widget.row(item) for item in self.list_widget.selectedItems()][0] == self.dragging_idx:
            cx, cy = self.get_anchor_coords(handle_gds, self.cb_anchor.currentText())
            self.inp_x.setText(f"{cx:.3f}");
            self.inp_y.setText(f"{cy:.3f}")
            if self.active_shape_type == 'gds':
                self.prop_x_inp.setText(f"{cx:.3f}");
                self.prop_y_inp.setText(f"{cy:.3f}")

        self.draw_overlaps()

    # === 重构：通用的上下文右键菜单生成 ===
    def show_context_menu(self, item_type, idx):
        menu = QtWidgets.QMenu(self)

        name = ""
        if item_type == 'gds':
            name = self.gds_list[idx]['name']
        elif item_type == 'shape':
            name = f"{self.user_shapes[idx]['type'].capitalize()} Shape"
        elif item_type == 'text':
            name = f"Text '{self.user_texts[idx]['text']}'"

        a_dup = menu.addAction(f"Duplicate {name}")
        a_arr = menu.addAction("Create Array (Step & Repeat)...")
        menu.addSeparator()
        a_ccw = menu.addAction("Rotate 90 CCW")
        a_cw = menu.addAction("Rotate 90 CW")
        a_fh = menu.addAction("Flip H")
        a_fv = menu.addAction("Flip V")

        action = menu.exec_(QtGui.QCursor.pos())
        if action == a_dup:
            self.action_duplicate(item_type, idx)
        elif action == a_arr:
            self.action_create_array(item_type, idx)
        elif action == a_ccw:
            self.apply_transform(item_type, idx, 'ccw')
        elif action == a_cw:
            self.apply_transform(item_type, idx, 'cw')
        elif action == a_fh:
            self.apply_transform(item_type, idx, 'fh')
        elif action == a_fv:
            self.apply_transform(item_type, idx, 'fv')

    # === 重构：通用的数学阵列变换引擎 ===
    def apply_transform(self, item_type, idx, trans_mode):
        self.save_snapshot()
        if item_type == 'gds':
            # GDS 使用原生的 db.DTrans 处理
            if trans_mode == 'ccw':
                self.gds_list[idx]['trans'] = db.DTrans(1, False, 0, 0) * self.gds_list[idx]['trans']
            elif trans_mode == 'cw':
                self.gds_list[idx]['trans'] = db.DTrans(3, False, 0, 0) * self.gds_list[idx]['trans']
            elif trans_mode == 'fh':
                self.gds_list[idx]['trans'] = db.DTrans(2, True, 0, 0) * self.gds_list[idx]['trans']
            elif trans_mode == 'fv':
                self.gds_list[idx]['trans'] = db.DTrans(0, True, 0, 0) * self.gds_list[idx]['trans']
            self.on_listbox_select()

        elif item_type == 'shape':
            # 形状：对其所有关键点做绕中心的数学变换
            s = self.user_shapes[idx]
            pts = s['points']
            min_x, max_x = min(p[0] for p in pts), max(p[0] for p in pts)
            min_y, max_y = min(p[1] for p in pts), max(p[1] for p in pts)
            cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0

            new_pts = []
            for x, y in pts:
                if trans_mode == 'ccw':
                    new_pts.append((cx - (y - cy), cy + (x - cx)))
                elif trans_mode == 'cw':
                    new_pts.append((cx + (y - cy), cy - (x - cx)))
                elif trans_mode == 'fh':
                    new_pts.append((cx - (x - cx), y))
                elif trans_mode == 'fv':
                    new_pts.append((x, cy - (y - cy)))
            s['points'] = new_pts

            # 如果是打孔阵列旋转，必须连带交换间距与孔宽高
            if trans_mode in ['ccw', 'cw'] and s['type'] == 'via_array':
                s['via_w'], s['via_h'] = s.get('via_h', 1.0), s.get('via_w', 1.0)
                s['pitch_x'], s['pitch_y'] = s.get('pitch_y', 2.0), s.get('pitch_x', 2.0)

        elif item_type == 'text':
            # 文本：改变属性映射，使其由 QTransform 接管
            ut = self.user_texts[idx]
            if trans_mode == 'ccw':
                ut['rot'] = (ut.get('rot', 0) + 90) % 360
            elif trans_mode == 'cw':
                ut['rot'] = (ut.get('rot', 0) - 90) % 360
            elif trans_mode == 'fh':
                ut['mirror_x'] = not ut.get('mirror_x', False)
            elif trans_mode == 'fv':
                ut['mirror_x'] = not ut.get('mirror_x', False)
                ut['rot'] = (ut.get('rot', 0) + 180) % 360

        self.draw_preview()

    # === 重构：泛用的复制操作 ===
    def action_duplicate(self, item_type, idx):
        self.save_snapshot()
        offset = 200

        if item_type == 'gds':
            o = self.gds_list[idx]
            self.gds_list.append(
                {'path': o['path'], 'name': o['name'], 'base_bbox': o['base_bbox'], 'trans': o['trans'] * db.DTrans(),
                 'offset_x': o['offset_x'] + offset, 'offset_y': o['offset_y'] - offset, 'color': o['color'],
                 'patch': None,
                 'shadow_patch': None, 'collection': [], 'center_text': None, 'true_polygons': o['true_polygons'],
                 'layers': o.get('layers', []), 'qpath': o.get('qpath'), 'qpaths_full': o.get('qpaths_full')})
            self.list_widget.addItem(f"[{len(self.gds_list)}] {o['name']}")

        elif item_type == 'shape':
            o = self.user_shapes[idx].copy()
            o['points'] = [(x + offset, y - offset) for x, y in o['points']]
            if 'patch' in o: o['patch'] = None
            self.user_shapes.append(o)

        elif item_type == 'text':
            o = self.user_texts[idx].copy()
            o['x'] += offset
            o['y'] -= offset
            if 'text_obj' in o: o['text_obj'] = None
            self.user_texts.append(o)

        self.draw_preview()

    # === 重构：泛用的阵列生成操作 ===
    def action_create_array(self, item_type, idx):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Create Array")
        layout = QtWidgets.QFormLayout(dlg)
        rows_var = QtWidgets.QLineEdit("2")
        cols_var = QtWidgets.QLineEdit("2")
        spc_x_var = QtWidgets.QLineEdit("1000")
        spc_y_var = QtWidgets.QLineEdit("1000")
        layout.addRow("Rows (Y):", rows_var)
        layout.addRow("Cols (X):", cols_var)
        layout.addRow("Space X (um):", spc_x_var)
        layout.addRow("Space Y (um):", spc_y_var)
        btn = QtWidgets.QPushButton("Generate Array")
        btn.clicked.connect(dlg.accept)
        layout.addRow(btn)

        if dlg.exec_():
            try:
                r, c, sx, sy = int(rows_var.text()), int(cols_var.text()), float(spc_x_var.text()), float(
                    spc_y_var.text())
                if r < 1 or c < 1: return
                self.save_snapshot()

                if item_type == 'gds':
                    o = self.gds_list[idx]
                    for i in range(r):
                        for j in range(c):
                            if i == 0 and j == 0: continue
                            self.gds_list.append(
                                {'path': o['path'], 'name': f"{o['name']}_R{i}C{j}", 'base_bbox': o['base_bbox'],
                                 'trans': o['trans'] * db.DTrans(), 'offset_x': o['offset_x'] + j * sx,
                                 'offset_y': o['offset_y'] + i * sy, 'color': o['color'], 'patch': None,
                                 'shadow_patch': None, 'collection': [],
                                 'center_text': None, 'true_polygons': o['true_polygons'],
                                 'layers': o.get('layers', []),
                                 'qpath': o.get('qpath'), 'qpaths_full': o.get('qpaths_full')})
                            self.list_widget.addItem(f"[{len(self.gds_list)}] {self.gds_list[-1]['name']}")

                elif item_type == 'shape':
                    o = self.user_shapes[idx]
                    for i in range(r):
                        for j in range(c):
                            if i == 0 and j == 0: continue
                            new_o = o.copy()
                            new_o['points'] = [(x + j * sx, y + i * sy) for x, y in o['points']]
                            if 'patch' in new_o: new_o['patch'] = None
                            self.user_shapes.append(new_o)

                elif item_type == 'text':
                    o = self.user_texts[idx]
                    for i in range(r):
                        for j in range(c):
                            if i == 0 and j == 0: continue
                            new_o = o.copy()
                            new_o['x'] += j * sx
                            new_o['y'] += i * sy
                            if 'text_obj' in new_o: new_o['text_obj'] = None
                            self.user_texts.append(new_o)

                self.draw_preview()
            except ValueError:
                pass

    # ================= KLayout 核心操作与布尔运算 =================
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
                        cache[file_path] = None;
                        continue
                    src_layout = db.Layout()
                    src_layout.read(file_path)
                    if idx == 0: target_layout.dbu = src_layout.dbu
                    src_top = src_layout.top_cells()[0]
                    for cell in src_layout.each_cell(): cell.name = f"chip{idx}_" + cell.name
                    new_cell = target_layout.create_cell(src_top.name)
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
                        merged_top.shapes(lyr_idx).insert(
                            db.DBox(min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1]), max(pts[0][0], pts[1][0]),
                                    max(pts[0][1], pts[1][1])))
                    elif s['type'] == 'polygon':
                        merged_top.shapes(lyr_idx).insert(db.DPolygon([db.DPoint(x, y) for x, y in pts]))
                    elif s['type'] == 'path':
                        merged_top.shapes(lyr_idx).insert(db.DPath([db.DPoint(x, y) for x, y in pts], s['width']))
                    elif s['type'] == 'via_array':
                        vw_dbu, vh_dbu = int(s.get('via_w', 1.0) / dbu), int(s.get('via_h', 1.0) / dbu)
                        px_dbu, py_dbu = int(s.get('pitch_x', 2.0) / dbu), int(s.get('pitch_y', 2.0) / dbu)
                        if px_dbu > 0 and py_dbu > 0 and vw_dbu > 0 and vh_dbu > 0:
                            min_x_dbu, min_y_dbu = int(min(pts[0][0], pts[1][0]) / dbu), int(
                                min(pts[0][1], pts[1][1]) / dbu)
                            max_x_dbu, max_y_dbu = int(max(pts[0][0], pts[1][0]) / dbu), int(
                                max(pts[0][1], pts[1][1]) / dbu)
                            curr_x = min_x_dbu
                            while curr_x + vw_dbu <= max_x_dbu:
                                curr_y = min_y_dbu
                                while curr_y + vh_dbu <= max_y_dbu:
                                    merged_top.shapes(lyr_idx).insert(
                                        db.Box(curr_x, curr_y, curr_x + vw_dbu, curr_y + vh_dbu))
                                    curr_y += py_dbu
                                curr_x += px_dbu

            if self.user_texts:
                gen = db.TextGenerator.default_generator()
                for ut in self.user_texts:
                    lbl_idx = target_layout.layer(db.LayerInfo(int(ut['layer']), int(ut['dt'])))
                    text_region = gen.text(ut['text'], dbu)
                    if text_region.bbox().height() > 0:
                        text_cell = target_layout.create_cell(f"TEXT_{ut['text']}")
                        text_cell.shapes(lbl_idx).insert(text_region)
                        current_h_um = text_region.bbox().height() * dbu
                        scale_factor = ut['size'] / current_h_um if current_h_um > 0 else 1.0

                        # 应用用户设置的旋转和翻转
                        rot_angle = ut.get('rot', 0)
                        mirror_x = ut.get('mirror_x', False)
                        merged_top.insert(db.DCellInstArray(text_cell.cell_index(),
                                                            db.DCplxTrans(scale_factor, rot_angle, mirror_x, ut['x'],
                                                                          ut['y'])))

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
                    x = int(box_pitch_dbu / 2) if stagger and (row_index % 2 != 0) else 0
                    while x + box_size_dbu <= w_dbu:
                        dummy_region.insert(db.Box(x, y, x + box_size_dbu, y + box_size_dbu));
                        x += box_pitch_dbu
                    y += box_pitch_dbu;
                    row_index += 1
                merged_top.shapes(layer_idx).insert(dummy_region - dummy_region.interacting(keep_out))

            if self.crop_box:
                pts = self.crop_box
                min_x_dbu, min_y_dbu = int(min(pts[0][0], pts[1][0]) / dbu), int(min(pts[0][1], pts[1][1]) / dbu)
                max_x_dbu, max_y_dbu = int(max(pts[0][0], pts[1][0]) / dbu), int(max(pts[0][1], pts[1][1]) / dbu)
                clip_box_dbu = db.Box(min_x_dbu, min_y_dbu, max_x_dbu, max_y_dbu)
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

    def action_boolean_dialog(self):
        if len(self.gds_list) == 0 and len(self.user_shapes) == 0:
            QtWidgets.QMessageBox.warning(self, "Warning", "画布上没有任何内容！请先加载 GDS 或手动绘制图形。")
            return

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Boolean Operations (布尔运算)")
        dlg.resize(400, 260)
        layout = QtWidgets.QFormLayout(dlg)

        gds_names = [f"📦 [GDS {i + 1}] {g['name']}" for i, g in enumerate(self.gds_list)]
        shape_names = [f"✏️ [Shape {i + 1}] {s['type'].capitalize()} (L:{s['layer']} D:{s['dt']})" for i, s in
                       enumerate(self.user_shapes)]
        all_sources = gds_names + shape_names

        cb_a = QtWidgets.QComboBox();
        cb_a.addItems(all_sources)
        lay_a = QtWidgets.QLineEdit("10/0")
        cb_op = QtWidgets.QComboBox();
        cb_op.addItems(["OR (A ∪ B) 并集", "AND (A ∩ B) 交集", "NOT (A - B) 差集", "XOR (A ⊕ B) 异或"])
        cb_b = QtWidgets.QComboBox();
        cb_b.addItems(all_sources)
        lay_b = QtWidgets.QLineEdit("10/0")
        out_lay = QtWidgets.QLineEdit("100/0")
        out_name = QtWidgets.QLineEdit("BOOLEAN_RESULT")

        if len(all_sources) > 1: cb_a.setCurrentIndex(0); cb_b.setCurrentIndex(1)

        layout.addRow("源 A (选择器件或形状):", cb_a)
        layout.addRow("源 A 层号 (选 GDS 时生效):", lay_a)
        layout.addRow("运算逻辑:", cb_op)
        layout.addRow("源 B (选择器件或形状):", cb_b)
        layout.addRow("源 B 层号 (选 GDS 时生效):", lay_b)
        layout.addRow("---", QtWidgets.QLabel(""))
        layout.addRow("输出层号 (L/D):", out_lay)
        layout.addRow("输出模块名:", out_name)

        btn = QtWidgets.QPushButton("执行布尔运算")
        btn.setMinimumHeight(35)
        btn.setStyleSheet("background-color: #FF8C00; color: white; font-weight: bold; border-radius: 4px;")
        btn.clicked.connect(dlg.accept)
        layout.addRow(btn)

        if dlg.exec_():
            try:
                self.status_label.setText("正在执行布尔运算...")
                QtWidgets.QApplication.processEvents()

                idx_a, idx_b = cb_a.currentIndex(), cb_b.currentIndex()

                def parse_lyr(text):
                    parts = text.split('/');
                    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0

                la, dta = parse_lyr(lay_a.text());
                lb, dtb = parse_lyr(lay_b.text());
                lo, dto = parse_lyr(out_lay.text())
                op_idx = cb_op.currentIndex()

                target_dbu = 0.001
                if self.gds_list:
                    test_layout = db.Layout();
                    test_layout.read(self.gds_list[0]['path']);
                    target_dbu = test_layout.dbu

                reg_a = self.extract_region_for_boolean(idx_a, la, dta, target_dbu)
                reg_b = self.extract_region_for_boolean(idx_b, lb, dtb, target_dbu)

                if op_idx == 0:
                    res_reg = reg_a + reg_b
                elif op_idx == 1:
                    res_reg = reg_a & reg_b
                elif op_idx == 2:
                    res_reg = reg_a - reg_b
                elif op_idx == 3:
                    res_reg = reg_a ^ reg_b

                res_reg.merge()
                if res_reg.is_empty():
                    self.status_label.setText("Ready")
                    QtWidgets.QMessageBox.information(self, "Result",
                                                      "运算结果为空（两图形没有交集，或者提取不到对应层的数据）！")
                    return

                temp_path = os.path.join(tempfile.gettempdir(), f"{out_name.text()}.gds")
                out_layout = db.Layout();
                out_layout.dbu = target_dbu

                out_top = out_layout.create_cell(out_name.text())
                out_li = out_layout.layer(lo, dto)
                out_top.shapes(out_li).insert(res_reg)
                out_layout.write(temp_path)

                self.process_single_gds(temp_path)
                self.status_label.setText("布尔运算完成！已作为新器件加载。")
            except Exception as e:
                self.status_label.setText("Ready")
                QtWidgets.QMessageBox.critical(self, "Error", f"布尔运算失败:\n{str(e)}")

    def extract_region_for_boolean(self, global_idx, layer, datatype, dbu):
        reg = db.Region()
        num_gds = len(self.gds_list)

        if global_idx < num_gds:
            gds_info = self.gds_list[global_idx]
            layout = db.Layout()
            layout.read(gds_info['path'])
            top_cell = layout.top_cells()[0]
            li = layout.find_layer(layer, datatype)
            if li is not None: reg.insert(top_cell.begin_shapes_rec(li))
            reg.merge()

            dx_dbu = round((gds_info['trans'].disp.x + gds_info['offset_x']) / dbu)
            dy_dbu = round((gds_info['trans'].disp.y + gds_info['offset_y']) / dbu)
            i_trans = db.Trans(gds_info['trans'].rot, gds_info['trans'].is_mirror(), dx_dbu, dy_dbu)
            reg.transform(i_trans)
            return reg
        else:
            shape_idx = global_idx - num_gds
            s = self.user_shapes[shape_idx]
            pts = s['points']
            if s['type'] == 'box':
                min_x, min_y = min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1])
                max_x, max_y = max(pts[0][0], pts[1][0]), max(pts[0][1], pts[1][1])
                reg.insert(db.Box(int(min_x / dbu), int(min_y / dbu), int(max_x / dbu), int(max_y / dbu)))
            elif s['type'] == 'polygon':
                pts_dbu = [db.Point(int(x / dbu), int(y / dbu)) for x, y in pts]
                reg.insert(db.Polygon(pts_dbu))
            elif s['type'] == 'path':
                pts_dbu = [db.Point(int(x / dbu), int(y / dbu)) for x, y in pts]
                w_dbu = int(s['width'] / dbu)
                reg.insert(db.Path(pts_dbu, w_dbu).polygon())
            elif s['type'] == 'via_array':
                vw_dbu, vh_dbu = int(s.get('via_w', 1.0) / dbu), int(s.get('via_h', 1.0) / dbu)
                px_dbu, py_dbu = int(s.get('pitch_x', 2.0) / dbu), int(s.get('pitch_y', 2.0) / dbu)
                if px_dbu > 0 and py_dbu > 0 and vw_dbu > 0 and vh_dbu > 0:
                    min_x_dbu, min_y_dbu = int(min(pts[0][0], pts[1][0]) / dbu), int(min(pts[0][1], pts[1][1]) / dbu)
                    max_x_dbu, max_y_dbu = int(max(pts[0][0], pts[1][0]) / dbu), int(max(pts[0][1], pts[1][1]) / dbu)
                    curr_x = min_x_dbu
                    while curr_x + vw_dbu <= max_x_dbu:
                        curr_y = min_y_dbu
                        while curr_y + vh_dbu <= max_y_dbu:
                            reg.insert(db.Box(curr_x, curr_y, curr_x + vw_dbu, curr_y + vh_dbu))
                            curr_y += py_dbu
                        curr_x += px_dbu
            reg.merge()
            return reg


if __name__ == "__main__":
    if hasattr(QtCore.Qt, 'AA_EnableHighDpiScaling'):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    if hasattr(QtCore.Qt, 'AA_UseHighDpiPixmaps'):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("QPushButton { font-size: 14px; padding: 4px; }")

    font = app.font()
    font.setPointSize(10)  # 默认通常是 8 或 9，改到 11 或 12 会清晰很多
    app.setFont(font)

    window = GDSMergerProQt()
    window.show()
    sys.exit(app.exec_())