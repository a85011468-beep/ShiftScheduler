import re

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QLabel, QDateEdit, QMessageBox, QFileDialog, QTableWidget, 
                               QTableWidgetItem, QHeaderView, QSpinBox, QAbstractSpinBox,
                               QComboBox) # 💡 新增 QComboBox

from PySide6.QtCore import Qt, QDate, QSettings
from PySide6.QtGui import QColor, QFont
from datetime import datetime
import pandas as pd
from engine.solver import ScheduleEngine
from config.settings import OFF_SHIFTS, ALL_STATES, SHIFT_DEMANDS
from database.data_importer import DataImporter
from ui.stats_dialog import StatsDialog
from ui.debug_dialog import DebugDialog
from ui.run_config_dialog import RunConfigDialog


class CustomSpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.NoButtons) # 隱藏上下箭頭
        # 💡 強制關閉滑鼠滾輪干擾，防止滑鼠滑過時意外滾動改變數字
        self.setFocusPolicy(Qt.StrongFocus) 

    def focusInEvent(self, event):
        """重寫聚焦事件：當滑鼠移入或點擊時，不允許它自動全選文字"""
        super().focusInEvent(event)
        # 延遲取消全選，防止 Qt 預設行為干涉
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.lineEdit().deselect)

class SchedulerDialog(QDialog):
    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🚀 排班與預覽匯出中心")
        self.resize(1100, 700) 
        self.db = db_manager
        
        # 初始化 QSettings (使用與主程式相同的命名空間，但用不同的 Key)
        self.settings = QSettings("MyUnit", "ShiftScheduler")
        
        # 存放每個員工的請假滾輪元件 {emp_id: {'L': QSpinBox, 'P': QSpinBox}}
        self.leave_spinboxes = {} 

        self.setup_ui()
        self.refresh_table()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # === 頂部控制區 ===
        ctrl_layout = QHBoxLayout()

        # 🧮 [新增] 人力配額純試算按鈕 (建議可以放在佈局的開頭，符合左上的需求)
        self.btn_check_manpower = QPushButton("🧮 試算人力配額")
        self.btn_check_manpower.setStyleSheet("background-color: #607D8B; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_check_manpower.clicked.connect(self.on_check_manpower_clicked)
        ctrl_layout.addWidget(self.btn_check_manpower) # 根據您的版面，也可能是加入到 top_layout
        
        # 1. 🔧 [功能一] 記憶起始日期
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        saved_start = self.settings.value("scheduler_start_date", "")
        if saved_start:
            self.date_start.setDate(QDate.fromString(saved_start, "yyyy-MM-dd"))
        else:
            self.date_start.setDate(QDate.currentDate().addDays(-QDate.currentDate().day() + 1))
        self.date_start.dateChanged.connect(self.refresh_table)
        ctrl_layout.addWidget(QLabel("目標運算區間："))
        ctrl_layout.addWidget(self.date_start)

        # 2. 🔧 [功能一] 記憶結束日期
        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        saved_end = self.settings.value("scheduler_end_date", "")
        if saved_end:
            self.date_end.setDate(QDate.fromString(saved_end, "yyyy-MM-dd"))
        else:
            self.date_end.setDate(QDate.currentDate().addDays(self.date_start.date().daysInMonth() - QDate.currentDate().day()))
        self.date_end.dateChanged.connect(self.refresh_table)
        ctrl_layout.addWidget(QLabel("至"))
        ctrl_layout.addWidget(self.date_end)

        # 功能按鈕群
        self.btn_run = QPushButton("⚡ 執行智能排班")
        self.btn_run.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_run.clicked.connect(self.on_run_engine_clicked)
        ctrl_layout.addWidget(self.btn_run)

        # 👇 💡 新增：Debug 排班按鈕 👇
        self.btn_debug_run = QPushButton("🐛 Debug 勞基法排班")
        self.btn_debug_run.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_debug_run.clicked.connect(self.on_debug_run_clicked)
        ctrl_layout.addWidget(self.btn_debug_run)
        # 👆 新增結束 👆

        # 🔍 [新增] 獨立的條件診斷器按鈕
        self.btn_rule_debug = QPushButton("🔍 班表規則診斷")
        self.btn_rule_debug.setStyleSheet("background-color: #00BCD4; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_rule_debug.clicked.connect(self.on_rule_debug_clicked)
        ctrl_layout.addWidget(self.btn_rule_debug)

        self.btn_import_pre = QPushButton("📥 匯入預排 (Excel)")
        self.btn_import_pre.clicked.connect(self.on_import_pre_schedule_clicked)
        ctrl_layout.addWidget(self.btn_import_pre)
        
        self.btn_clear_pre = QPushButton("🗑️ 清除區間預排")
        self.btn_clear_pre.clicked.connect(self.on_clear_pre_schedule_clicked)
        ctrl_layout.addWidget(self.btn_clear_pre)

        # 3. 🔧 [功能二] 新增「刪除未來班表」按鈕
        self.btn_clear = QPushButton("🗑️ 清空區間未鎖定班表")
        self.btn_clear.setStyleSheet("background-color: #795548; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_clear.clicked.connect(self.on_clear_schedule_clicked)
        ctrl_layout.addWidget(self.btn_clear)

        self.btn_export = QPushButton("💾 滿意並匯出 Excel")
        self.btn_export.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_export.clicked.connect(self.on_export_clicked)
        ctrl_layout.addWidget(self.btn_export)

        # 🔧 [新增] 週期統計按鈕
        self.btn_stats = QPushButton("📊 週期班別統計")
        self.btn_stats.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_stats.clicked.connect(self.on_stats_clicked)
        ctrl_layout.addWidget(self.btn_stats)

        layout.addLayout(ctrl_layout)

        # === 表格區 ===
        layout.addWidget(QLabel("💡 提示：左側可設定引擎代排的 L(特休)/P(事假) 天數總額。底限為已被鎖定的歷史請假天數。"))
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        # 💡 開啟滑鼠追蹤，讓 hover 效果能順暢觸發
        self.table.setMouseTracking(True) 
        
        # 💡 注入高對比 QSS 樣式碼
        self.table.setStyleSheet("""
            QTableWidget {
                alternate-background-color: #f2f2f2;
                font-size: 13px; /* 基礎字體大小 */
            }
            /* 1. 滑鼠懸停 (Hover) 時的樣式：亮黃底、黑字、放大加粗 */
            QTableWidget::item:hover {
                background-color: #FFD54F; 
                color: #000000;
                font-size: 15px; 
                font-weight: bold;
            }
            /* 2. 點擊選取 (Selected) 時的樣式：道奇藍底、純白字、放大加粗 */
            QTableWidget::item:selected {
                background-color: #1E90FF;
                color: #FFFFFF;
                font-size: 15px;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.table)

        # (您的 __init__ 或 setup_ui 前面的程式碼...)
        
        # 💡 將日期選擇器的數值變更信號，綁定到重繪表格函式
        self.date_start.dateChanged.connect(self.refresh_table)
        self.date_end.dateChanged.connect(self.refresh_table)
        
        # 💡 視窗一打開，立刻觸發第一次強制渲染
        self.refresh_table()

    def get_selected_dates(self):
        return self.date_start.date().toString("yyyy-MM-dd"), self.date_end.date().toString("yyyy-MM-dd")
    
    def on_rule_debug_clicked(self):
        """開啟勾選式的班表規則診斷器"""
        start_date, end_date = self.get_selected_dates()
        dialog = DebugDialog(self.db, start_date, end_date, self)
        dialog.exec()


    def _get_gradient_color(self, val):
        """依據上班人數回傳漸變背景色"""
        if val <= 8: return QColor(255, 170, 170)
        elif val >= 15: return QColor(170, 255, 170)
        else:
            ratio = (val - 8) / 7.0
            if ratio <= 0.5:
                r = 255
                g = int(170 + (255 - 170) * (ratio / 0.5))
                b = 170
            else:
                r = int(255 - (255 - 170) * ((ratio - 0.5) / 0.5))
                g = 255
                b = 170
            return QColor(r, g, b)

    def refresh_table(self):
        # 💡 [修復重疊 Bug 核心] 完全摧毀並重置舊的 CellWidgets，避免 Qt 殘留幽靈元件
        self.table.clearContents()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        
        start_date, end_date = self.get_selected_dates()
        
        employees = self.db.get_all_active_employees()
        if not employees:
            return

        self.leave_widgets = {} 
        self.remaining_labels = {} 
        
        level_dict = {str(e['emp_id']).strip(): str(e['job_level']).strip() for e in employees}
        name_dict = {str(e['emp_id']).strip(): e['name'] for e in employees}
        emp_ids = [str(e['emp_id']).strip() for e in employees]
        
        date_columns = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        
        schedules = self.db.get_schedule_by_date_range(start_date, end_date)
        
        if schedules:
            df = pd.DataFrame(schedules)
            pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')
            pivot_df.index = pivot_df.index.astype(str).str.strip()
        else:
            pivot_df = pd.DataFrame(index=emp_ids, columns=date_columns).fillna('')

        pivot_df = pivot_df.reindex(index=emp_ids, columns=date_columns, fill_value='')

        def sort_key(emp_id):
            level = level_dict.get(str(emp_id), 'Normal')
            if level == 'Chief': return (0, str(emp_id))
            elif level == 'M': return (1, str(emp_id))
            else: return (2, str(emp_id))

        sorted_emp_ids = sorted(pivot_df.index, key=sort_key)
        pivot_df = pivot_df.reindex(sorted_emp_ids)

        # ==========================================
        # 🎯 跨期邊界偵測與左右佈局拆分 (加入 加班 O 欄位)
        # ==========================================
        self.split_date = None
        saved_audit = self.settings.value("audit_start_date", "")
        if saved_audit:
            audit_start = QDate.fromString(saved_audit, "yyyy-MM-dd")
            audit_end = audit_start.addDays(55) 
            s_qdate = self.date_start.date()
            e_qdate = self.date_end.date()
            
            if s_qdate <= audit_end and e_qdate > audit_end:
                self.split_date = audit_end.toString("yyyy-MM-dd")

        # 💡 [UI 優化] 把期內放左邊，期外放右邊
        if self.split_date:
            headers_left = ["L(期內)", "P(期內)", "r(期內)", "R(期內)", "加班(O內)", "固定", "剩餘(內)"]
            headers_right = ["L(跨出)", "P(跨出)", "r(跨出)", "R(跨出)", "加班(O外)", "剩餘(外)"]
            self.control_keys_left = ['L', 'P', 'r', 'R', 'O']
            self.control_keys_right = ['L2', 'P2', 'r2', 'R2', 'O2']
            p1_dates = [d for d in date_columns if d <= self.split_date]
            p2_dates = [d for d in date_columns if d > self.split_date]
        else:
            headers_left = ["特休(L)", "事假(P)", "休息(r)", "例假(R)", "加班(O)", "固定", "剩餘(天)"]
            headers_right = []
            self.control_keys_left = ['L', 'P', 'r', 'R', 'O']
            self.control_keys_right = []
            p1_dates = date_columns
            p2_dates = []

        total_days_p1 = len(p1_dates)
        total_days_p2 = len(p2_dates)

        all_headers = headers_left + date_columns + headers_right
        # 💡 [新增] 定義底部的統計列與對應的班別代碼
        # 💡 [修改] 將 c 班別合併統計，以維持介面簡潔並正確觸發紅綠燈
        self.stats_rows = [
            ("早M", ["01早M"]), ("早m", ["01早m"]), 
            ("早B1(含c)", ["01早B1", "01早B1c"]), ("早B2(含c)", ["01早B2", "01早B2c"]),
            ("中A", ["01中A"]), 
            ("午M", ["01午M"]), ("午m", ["01午m"]), 
            ("午B1(含c)", ["01午B1", "01午B1c"]), ("午B2(含c)", ["01午B2", "01午B2c"]), 
            ("夜(B1+B2)", ["01夜B1", "01夜B2"])
        ]

        all_headers = headers_left + date_columns + headers_right
        # 💡 改為：1 (批次列) + 員工總數 + 詳細統計的列數
        self.table.setRowCount(len(pivot_df) + 1 + len(self.stats_rows))
        self.table.setColumnCount(len(all_headers))


        for col_idx, header_text in enumerate(all_headers):
            item = QTableWidgetItem(header_text)
            try:
                dt = datetime.strptime(header_text, '%Y-%m-%d')
                if dt.weekday() == 6: 
                    item.setBackground(QColor("#AD1457")) 
                    item.setForeground(QColor("#FFFFFF")) 
                    font = QFont()
                    font.setBold(True)
                    item.setFont(font)
            except ValueError:
                pass 
            self.table.setHorizontalHeaderItem(col_idx, item)
            
        self.table.horizontalHeader().setStyleSheet("QHeaderView::section { padding: 4px; border: 1px solid #ccc; }")
        
        # 💡 Y 軸標籤也展開
        y_labels = ["⚡ 批次套用"] + [f"{emp_id} {name_dict.get(emp_id, '')}" for emp_id in pivot_df.index] + [f"📊 {label}" for label, _ in self.stats_rows]
        self.table.setVerticalHeaderLabels(y_labels)

        # 批次套用工具列
        def make_batch_updater(key):
            def update_all_emps(val):
                for e_id, widgets in self.leave_widgets.items():
                    w = widgets.get(key)
                    if isinstance(w, QSpinBox):
                        w.setValue(val) if val >= w.minimum() else w.setValue(w.minimum())
            return update_all_emps

        # 繪製左側批次套用
        for col_idx, key in enumerate(self.control_keys_left):
            spin = CustomSpinBox()
            spin.setRange(0, total_days_p1)
            spin.setStyleSheet("background-color: #FFF9C4; color: black; font-weight: bold; border: 1px solid #ccc;")
            spin.valueChanged.connect(make_batch_updater(key))
            self.table.setCellWidget(0, col_idx, spin)

        # 繪製右側批次套用 (如果在最右側)
        if self.split_date:
            right_offset = len(headers_left) + len(date_columns)
            for i, key in enumerate(self.control_keys_right):
                spin = CustomSpinBox()
                spin.setRange(0, total_days_p2)
                spin.setStyleSheet("background-color: #E1BEE7; color: black; font-weight: bold; border: 1px solid #ccc;")
                spin.valueChanged.connect(make_batch_updater(key))
                self.table.setCellWidget(0, right_offset + i, spin)

        # 把未放置 SpinBox 的第 0 列格子變成灰色唯讀
        for col_idx in range(len(all_headers)):
            if not self.table.cellWidget(0, col_idx):
                item = QTableWidgetItem("")
                item.setBackground(QColor("#E0E0E0"))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
                self.table.setItem(0, col_idx, item)

        def make_updater(emp_id, fixed_p1, fixed_p2):
            def update():
                widgets = self.leave_widgets[emp_id]
                
                # 計算期內剩餘天數
                consumed_p1 = fixed_p1
                for k in self.control_keys_left:
                    w = widgets.get(k)
                    consumed_p1 += w.value() if isinstance(w, QSpinBox) else int(w.text())
                
                rem_p1 = total_days_p1 - consumed_p1
                lbl_rem_p1 = self.remaining_labels[f"{emp_id}_p1"]
                lbl_rem_p1.setText(str(rem_p1))
                lbl_rem_p1.setStyleSheet("color: #1565C0; font-weight: bold;" if rem_p1 >= 0 else "background-color: #FFCDD2; color: #B71C1C; font-weight: bold; border: 1px solid #B71C1C;")

                # 計算期外剩餘天數 (如果有分期)
                if self.split_date:
                    consumed_p2 = fixed_p2
                    for k in self.control_keys_right:
                        w = widgets.get(k)
                        consumed_p2 += w.value() if isinstance(w, QSpinBox) else int(w.text())
                    
                    rem_p2 = total_days_p2 - consumed_p2
                    lbl_rem_p2 = self.remaining_labels[f"{emp_id}_p2"]
                    lbl_rem_p2.setText(str(rem_p2))
                    lbl_rem_p2.setStyleSheet("color: #1565C0; font-weight: bold;" if rem_p2 >= 0 else "background-color: #FFCDD2; color: #B71C1C; font-weight: bold; border: 1px solid #B71C1C;")
            return update

        # 開始繪製每一列員工
        for row_idx, emp_id in enumerate(pivot_df.index):
            real_row = row_idx + 1 
            emp_data = pivot_df.loc[emp_id]
            managed_states = ['L', 'P', 'r', 'R']

            emp_p1 = emp_data[p1_dates]
            fixed_p1 = sum((emp_p1 != '') & (~emp_p1.isin(managed_states)))
            
            fixed_p2 = 0
            if self.split_date:
                emp_p2 = emp_data[p2_dates]
                fixed_p2 = sum((emp_p2 != '') & (~emp_p2.isin(managed_states)))

            self.leave_widgets[emp_id] = {}
            updater = make_updater(emp_id, fixed_p1, fixed_p2)

            def create_label(col_idx, key, val, style="background-color: #EEEEEE; color: #9E9E9E; border: 1px solid #ccc;"):
                lbl = QLabel(str(val))
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet(style)
                self.table.setCellWidget(real_row, col_idx, lbl)
                self.leave_widgets[emp_id][key] = lbl

            def create_spinbox(col_idx, key, existing_val, limit_days):
                spin = CustomSpinBox()
                spin.setRange(existing_val, limit_days) 
                spin.setValue(existing_val)
                spin.setAlignment(Qt.AlignCenter)
                spin.setStyleSheet("background-color: white; color: black; border: 1px solid #ddd;")
                spin.valueChanged.connect(updater) 
                self.table.setCellWidget(real_row, col_idx, spin)
                self.leave_widgets[emp_id][key] = spin

            # 左側元件 (期內) - 💡 加入索引 4 的 O
            create_spinbox(0, 'L', sum(emp_p1 == 'L'), total_days_p1)
            create_spinbox(1, 'P', sum(emp_p1 == 'P'), total_days_p1)
            create_spinbox(2, 'r', sum(emp_p1 == 'r'), total_days_p1)
            create_spinbox(3, 'R', sum(emp_p1 == 'R'), total_days_p1)
            create_spinbox(4, 'O', 0, total_days_p1) 
            create_label(5, '固定(其他)', fixed_p1)

            rem_label_p1 = QLabel()
            rem_label_p1.setAlignment(Qt.AlignCenter)
            self.table.setCellWidget(real_row, 6, rem_label_p1)
            self.remaining_labels[f"{emp_id}_p1"] = rem_label_p1

            # 中間網格 (日期)
            for col_offset, date in enumerate(date_columns):
                val = str(pivot_df.at[emp_id, date]).strip()
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
                
                if val: item.setBackground(QColor("#E0E0E0"))
                if val and val not in ALL_STATES:
                    item.setBackground(QColor("#FFCDD2")) 
                    item.setForeground(QColor("#B71C1C")) 
                
                table_col = col_offset + len(headers_left)
                self.table.setItem(real_row, table_col, item)

            # 右側元件 (跨出)
            if self.split_date:
                r_start = len(headers_left) + len(date_columns)
                create_spinbox(r_start + 0, 'L2', sum(emp_p2 == 'L'), total_days_p2)
                create_spinbox(r_start + 1, 'P2', sum(emp_p2 == 'P'), total_days_p2)
                create_spinbox(r_start + 2, 'r2', sum(emp_p2 == 'r'), total_days_p2)
                create_spinbox(r_start + 3, 'R2', sum(emp_p2 == 'R'), total_days_p2)
                create_spinbox(r_start + 4, 'O2', 0, total_days_p2)

                rem_label_p2 = QLabel()
                rem_label_p2.setAlignment(Qt.AlignCenter)
                self.table.setCellWidget(real_row, r_start + 5, rem_label_p2)
                self.remaining_labels[f"{emp_id}_p2"] = rem_label_p2

            # 強制第一次計算與繪製
            updater() 

        # ==========================================
        # 👑 最底下列：多列班別詳細統計與紅綠燈
        # ==========================================
        last_row_start = len(pivot_df) + 1
        
        for row_offset, (label, shift_codes) in enumerate(self.stats_rows):
            current_row = last_row_start + row_offset
            
            # 補滿底下的左側灰色方塊
            for i in range(len(headers_left)):
                item = QTableWidgetItem("")
                item.setBackground(QColor("#E0E0E0"))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
                self.table.setItem(current_row, i, item)

            # 該班別每日人數計算與上色
            for col_idx, date in enumerate(date_columns):
                col_data = pivot_df[date]
                count = sum(col_data.isin(shift_codes))
                
                # 計算這些班別在 SHIFT_DEMANDS 裡的「最大需求人數上限」 (防呆：若沒設定預設為 1)
                max_limit = sum(SHIFT_DEMANDS.get(sc, (0, 1))[1] for sc in shift_codes)
                if max_limit == 0: max_limit = 1 
                
                item = QTableWidgetItem(f"{count}")
                item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
                
                # 🚦 紅綠燈判定機制
                if count == 0:
                    item.setBackground(QColor("#FFCDD2")) # 淺紅背景
                    item.setForeground(QColor("#B71C1C")) # 深紅字
                elif count >= max_limit:
                    item.setBackground(QColor("#C8E6C9")) # 淺綠背景
                    item.setForeground(QColor("#1B5E20")) # 深綠字
                else:
                    item.setBackground(QColor("#FFF9C4")) # 淺黃背景 (提醒未滿)
                    item.setForeground(QColor("#F57F17")) # 深橘字
                    
                font = QFont()
                font.setBold(True)
                font.setPointSize(11) # 字體縮小一點，避免畫面太擁擠
                item.setFont(font)
                
                self.table.setItem(current_row, col_idx + len(headers_left), item)

            # 補滿底下的右側灰色方塊 (跨出區塊)
            if self.split_date:
                r_start = len(headers_left) + len(date_columns)
                for i in range(len(headers_right)):
                    item = QTableWidgetItem("")
                    item.setBackground(QColor("#E0E0E0"))
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
                    self.table.setItem(current_row, r_start + i, item)

        # 欄位寬度固定設定
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.horizontalHeader().setDefaultSectionSize(75)
        self.table.verticalHeader().setDefaultSectionSize(35)
        
        # 縮小特定設定區塊的寬度
        for i in range(len(headers_left)):
            self.table.horizontalHeader().resizeSection(i, 60)
        if self.split_date:
            r_start = len(headers_left) + len(date_columns)
            for i in range(len(headers_right)):
                self.table.horizontalHeader().resizeSection(r_start + i, 60)
          
    def on_run_engine_clicked(self):
        start_date, end_date = self.get_selected_dates()
        
        # 💡 [新增] 彈出發射控制台
        config_dialog = RunConfigDialog(self)
        if config_dialog.exec() != QDialog.Accepted:
            return  # 如果使用者按取消或關閉視窗，就中止排班
            
        # 取得使用者勾選的參數字典
        rule_config = config_dialog.get_config()
        
        leave_quotas = {}
        # 🛡️ 安全取得左右兩側的 keys
        all_keys = list(getattr(self, 'control_keys_left', ['L', 'P', 'r', 'R', 'O']))
        if getattr(self, 'split_date', None):
            all_keys.extend(getattr(self, 'control_keys_right', ['L2', 'P2', 'r2', 'R2', 'O2']))
            
        for emp_id, widgets in self.leave_widgets.items():
            leave_quotas[emp_id] = {}
            for k in all_keys:
                w = widgets.get(k)
                if w is not None:
                    try:
                        val = w.value() 
                    except AttributeError:
                        try:
                            val = int(w.text())
                        except (ValueError, TypeError):
                            val = 0
                    leave_quotas[emp_id][k] = val
                else:
                    leave_quotas[emp_id][k] = 0
                    
        engine = ScheduleEngine(self.db)
        
        # 💡 [修改] 將 rule_config 傳入引擎
        success, message = engine.run_scheduler(
            start_date, 
            end_date, 
            leave_quotas, 
            split_date=getattr(self, 'split_date', None),
            rule_config=rule_config  # 傳入設定
        )
        
        if success:
            QMessageBox.information(self, "排班結果", message)
            self.refresh_table() 
        else:
            QMessageBox.warning(self, "排班失敗", message)

    def on_debug_run_clicked(self):
        start_date, end_date = self.get_selected_dates()
        dates = pd.date_range(start=start_date, end=end_date).tolist()
        num_days = len(dates)

        # 1. 取得當前區間上下限參數
        from config.settings import SHIFT_DEMANDS
        daily_min = sum(req[0] for req in SHIFT_DEMANDS.values())
        daily_max = sum(req[1] for req in SHIFT_DEMANDS.values())
        cycle_min_demand = daily_min * num_days
        cycle_max_demand = daily_max * num_days

        # 2. 取得可用總人力基數
        active_employees = self.db.get_all_active_employees()
        num_employees = len(active_employees)
        total_theoretical_slots = num_employees * num_days

        # 3. 從畫面的 SpinBox 收集休假與加班資料，並計算「只休假」的天數
        total_off_days = 0
        leave_quotas = {}
        
        # 定義哪些狀態是純休假 (不包含 O 加班)
        off_keys_left = ['L', 'P', 'r', 'R']
        off_keys_right = ['L2', 'P2', 'r2', 'R2']
        all_off_keys = off_keys_left
        if getattr(self, 'split_date', None):
            all_off_keys += off_keys_right

        # 必須抓取所有的 keys (包含 O) 送給引擎
        # 💡 [修正] 使用 list() 強制複製陣列，避免污染原生的 self.control_keys_left
        all_keys = list(getattr(self, 'control_keys_left', ['L', 'P', 'r', 'R', 'O']))
        if getattr(self, 'split_date', None):
            all_keys.extend(getattr(self, 'control_keys_right', ['L2', 'P2', 'r2', 'R2', 'O2']))

        for emp_id, widgets in self.leave_widgets.items():
            leave_quotas[emp_id] = {}
            for k in all_keys:
                w = widgets.get(k)
                val = 0
                if w is not None:
                    try:
                        val = w.value() 
                    except AttributeError:
                        try:
                            val = int(w.text())
                        except (ValueError, TypeError):
                            val = 0
                leave_quotas[emp_id][k] = val
                
                # 如果這個 key 是休假，就計入總休假天數
                if k in all_off_keys:
                    total_off_days += val

        # 4. 計算最終實質可用人力數 = (總員工 * 總天數) - 總休假數
        available_manpower = total_theoretical_slots - total_off_days

        # 5. 組合分析報告並彈出診斷視窗
        msg = (f"📊 【排班數據物理診斷】\n\n"
               f"📅 區間：{start_date} 至 {end_date} (共 {num_days} 天)\n"
               f"👥 啟用人數：{num_employees} 人\n"
               f"🛏️ QSpinBox休假總數：{total_off_days} 天\n"
               f"----------------------------------------\n"
               f"🎯 本週期總需求下限：{cycle_min_demand} 人次\n"
               f"🎯 本週期總需求上限：{cycle_max_demand} 人次\n"
               f"💪 實際可用總人力：{available_manpower} 人次\n\n")

        # 附上智慧提示
        if available_manpower < cycle_min_demand:
            msg += "⚠️ 警告：可用人力低於最低需求下限！\n(引擎必定無法排出完整班表，將動用百萬罰分虛擬人力)\n"
        elif available_manpower > cycle_max_demand:
            msg += "⚠️ 警告：可用人力高於最高需求上限！\n(代表這週期有人一定排不滿，被迫休無薪假或變成待命狀態)\n"
        else:
            msg += "✅ 評估：人力落在安全容許區間內。\n"

        msg += "\n是否確認繼續執行「僅依勞基法」的 Debug 排班？"

        reply = QMessageBox.question(self, "Debug 排班診斷 (純物理限制)", msg, QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            engine = ScheduleEngine(self.db)
            try:
                # 💡 傳遞 debug_mode=True 標記給引擎
                success, message = engine.run_scheduler(start_date, end_date, leave_quotas, split_date=getattr(self, 'split_date', None), debug_mode=True)
            except TypeError:
                # 容錯處理：如果 solver.py 尚未實作接受 debug_mode 參數，就先呼叫原本的引擎
                QMessageBox.warning(self, "系統提示", "引擎端 (solver.py) 尚未開啟 debug_mode 支援，本次將以正常智能排班執行。")
                success, message = engine.run_scheduler(start_date, end_date, leave_quotas, split_date=getattr(self, 'split_date', None))

            if success:
                QMessageBox.information(self, "排班結果", message)
                self.refresh_table() 
            else:
                QMessageBox.warning(self, "排班失敗", message)

    def on_check_manpower_clicked(self):
        """僅顯示當前 QSpinBox 休假與人力統計資訊，不執行排班"""
        start_date, end_date = self.get_selected_dates()
        dates = pd.date_range(start=start_date, end=end_date).tolist()
        num_days = len(dates)

        # 1. 取得當前區間上下限參數
        from config.settings import SHIFT_DEMANDS
        daily_min = sum(req[0] for req in SHIFT_DEMANDS.values())
        daily_max = sum(req[1] for req in SHIFT_DEMANDS.values())
        cycle_min_demand = daily_min * num_days
        cycle_max_demand = daily_max * num_days

        # 2. 取得可用總人力基數
        active_employees = self.db.get_all_active_employees()
        num_employees = len(active_employees)
        total_theoretical_slots = num_employees * num_days

        # 3. 從畫面的 SpinBox 收集休假與加班資料
        total_off_days = 0
        
        # 定義哪些狀態是純休假 (不包含 O 加班)
        off_keys_left = ['L', 'P', 'r', 'R']
        off_keys_right = ['L2', 'P2', 'r2', 'R2']
        all_off_keys = off_keys_left
        if getattr(self, 'split_date', None):
            all_off_keys += off_keys_right

        # 💡 [防護] 使用 list() 強制複製陣列，避免污染原生的 self.control_keys_left
        all_keys = list(getattr(self, 'control_keys_left', ['L', 'P', 'r', 'R', 'O']))
        if getattr(self, 'split_date', None):
            all_keys.extend(getattr(self, 'control_keys_right', ['L2', 'P2', 'r2', 'R2', 'O2']))

        for emp_id, widgets in self.leave_widgets.items():
            for k in all_keys:
                w = widgets.get(k)
                if w is not None and k in all_off_keys:
                    try:
                        val = w.value() 
                    except AttributeError:
                        try:
                            val = int(w.text())
                        except (ValueError, TypeError):
                            val = 0
                    total_off_days += val

        # 4. 計算最終實質可用人力數 = (總員工 * 總天數) - 總休假數
        available_manpower = total_theoretical_slots - total_off_days

        # 5. 組合分析報告並彈出診斷視窗
        msg = (f"📊 【人力配額物理試算】\n\n"
               f"📅 區間：{start_date} 至 {end_date} (共 {num_days} 天)\n"
               f"👥 啟用人數：{num_employees} 人\n"
               f"🛏️ QSpinBox休假總數：{total_off_days} 天\n"
               f"----------------------------------------\n"
               f"🎯 本週期總需求下限：{cycle_min_demand} 人次\n"
               f"🎯 本週期總需求上限：{cycle_max_demand} 人次\n"
               f"💪 實際可用總人力：{available_manpower} 人次\n\n")

        # 附上智慧提示
        if available_manpower < cycle_min_demand:
            msg += "⚠️ 警告：可用人力低於最低需求下限！\n(此配額若送出排班，必定會動用百萬罰分虛擬人力)\n"
        elif available_manpower > cycle_max_demand:
            msg += "⚠️ 警告：可用人力高於最高需求上限！\n(代表這週期有人一定排不滿，被迫休無薪假或待命)\n"
        else:
            msg += "✅ 評估：人力落在安全容許區間內。\n"

        # 僅顯示 Information，沒有 Yes/No 的確認動作，不觸發引擎
        QMessageBox.information(self, "人力試算 (不排班)", msg)

    # 🔧 [功能二] 實作安全刪除邏輯
    def on_clear_schedule_clicked(self):
        """清空選定區間內所有未鎖定的排班資料"""
        start_date, end_date = self.get_selected_dates()
        
        # 1. 🛡️ 宏觀防禦：如果整個月被關帳鎖定了，直接拒絕
        if self.db.is_period_locked(start_date, end_date):
            QMessageBox.critical(self, "🛑 操作被拒", f"指定區間 ({start_date} 至 {end_date}) 已被系統結算鎖定！\n請先至資料庫管理中心解鎖。")
            return

        # 2. 詢問確認
        reply = QMessageBox.question(self, "確認清空班表", 
                                     f"您確定要清空 {start_date} 至 {end_date} 之間的所有自動排班嗎？\n\n💡 注意：這只會抹除引擎排出的班別，您匯入的歷史班表與鎖定的請假資料（📌圖釘）將會受到保護、維持原狀。",
                                     QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            try:
                # 3. 執行 SQL 刪除 (僅針對 is_locked = 0)
                conn = self.db.get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    DELETE FROM schedule 
                    WHERE date >= ? AND date <= ? AND is_locked = 0
                ''', (start_date, end_date))
                conn.commit()
                conn.close()
                
                QMessageBox.information(self, "清空完成", "✅ 未鎖定之班表已成功清除，回復為空白網格。")
                self.refresh_table()
                
            except Exception as e:
                QMessageBox.critical(self, "錯誤", f"刪除過程中發生異常：\n{str(e)}")

    def on_stats_clicked(self):
        """開啟班別統計與負債檢核視窗"""
        start_date, end_date = self.get_selected_dates()
        
        # 實例化並開啟統計視窗 (傳入 db_manager 以及目前的日期區間)
        dialog = StatsDialog(self.db, start_date, end_date, self)
        dialog.exec()

    def on_export_clicked(self):
        start_date, end_date = self.get_selected_dates()
        schedules = self.db.get_schedule_by_date_range(start_date, end_date)
        if not schedules:
            QMessageBox.warning(self, "匯出失敗", "選定區間內沒有排班資料可匯出！")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "匯出班表", f"排班表_{start_date}_至_{end_date}.xlsx", "Excel Files (*.xlsx)")
        if file_path:
            try:
                # 1. 取得員工的職級資料字典，用於後續排序
                employees = self.db.get_all_active_employees()
                level_dict = {str(e['emp_id']).strip(): str(e['job_level']).strip() for e in employees}

                # 2. 將排班表轉為 DataFrame 並建立透視表
                df = pd.DataFrame(schedules)
                pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')
                pivot_df.index = pivot_df.index.astype(str).str.strip()

                # 3. 依照職級排序 (Chief > M > Normal) -> 相同職級則依照員編排序
                def sort_key(emp_id):
                    level = level_dict.get(emp_id, 'Normal')
                    if level == 'Chief': 
                        return (0, emp_id)
                    elif level == 'M': 
                        return (1, emp_id)
                    else: 
                        return (2, emp_id)

                sorted_emp_ids = sorted(pivot_df.index, key=sort_key)
                pivot_df = pivot_df.reindex(sorted_emp_ids)

                # 4. 匯出基礎 Excel 檔案
                pivot_df.to_excel(file_path)

                # 5. 💡 使用 openpyxl 進行後期加工：標記 c 班底色
                import openpyxl
                from openpyxl.styles import PatternFill
                
                wb = openpyxl.load_workbook(file_path)
                ws = wb.active
                
                # 將 RGB(204, 192, 218) 轉為 Hex 色碼為 CCC0DA
                c_shift_fill = PatternFill(start_color="CCC0DA", end_color="CCC0DA", fill_type="solid")
                
                # 掃描所有的儲存格，尋找結尾為 'c' 的班別
                for row in ws.iter_rows(min_row=2, min_col=2): # 跳過標題列(row=1)與員編欄(col=1)
                    for cell in row:
                        if isinstance(cell.value, str) and cell.value.endswith('c'):
                            cell.fill = c_shift_fill
                            
                # 存檔覆蓋
                wb.save(file_path)

                QMessageBox.information(self, "匯出成功", f"檔案已儲存至：\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "匯出失敗", f"寫入錯誤：\n{str(e)}")

    # 🔧 [功能一] 關閉視窗時，釘死排班中心的自選日期
    def closeEvent(self, event):
        start_date_str = self.date_start.date().toString("yyyy-MM-dd")
        end_date_str = self.date_end.date().toString("yyyy-MM-dd")
        self.settings.setValue("scheduler_start_date", start_date_str)
        self.settings.setValue("scheduler_end_date", end_date_str)
        super().closeEvent(event)

    #呼叫DataImporter的預排班表匯入功能
    def on_import_pre_schedule_clicked(self):
        """從 Excel 匯入預排班表 (呼叫 data_importer 統一處理)"""
        file_path, _ = QFileDialog.getOpenFileName(self, "選擇預排 Excel 檔案", "", "Excel Files (*.xlsx *.xls)")
        if not file_path:
            return
            
        # 💡 使用 UI 面板上設定的「目標運算區間」起始日，作為 Excel 表格第 1 天的對應日期
        start_date = self.date_start.date().toString("yyyy-MM-dd")
        
        # 實例化 DataImporter 並傳入共用的 db_manager
        importer = DataImporter(self.db)
        success, message = importer.import_pre_schedule(file_path, start_date)
        
        if success:
            self.refresh_table()
            QMessageBox.information(self, "匯入成功", message)
        else:
            QMessageBox.critical(self, "匯入失敗", message)


    def on_clear_pre_schedule_clicked(self):
        """清除當前選定日期區間內，所有的鎖定預排班表"""
        start_date, end_date = self.get_selected_dates()
        
        reply = QMessageBox.question(
            self, '確認清除', 
            f'⚠️ 確定要清除 {start_date} 到 {end_date} 之間的所有【鎖定預排】嗎？\n(這將把格子還原為空白，交由引擎重新排班)',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            # 只解鎖該區間內 is_locked = 1 的格子，清空其內容
            cursor.execute('''
                UPDATE schedule 
                SET is_locked = 0, shift_code = NULL 
                WHERE date >= ? AND date <= ? AND is_locked = 1
            ''', (start_date, end_date))
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            self.refresh_table()
            QMessageBox.information(self, "清除成功", f"已解除區間內 {deleted_count} 個預排圖釘！")