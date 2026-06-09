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
from config.settings import OFF_SHIFTS, ALL_STATES
from database.data_importer import DataImporter
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
        self.table.setRowCount(len(pivot_df) + 2)
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
        
        y_labels = ["⚡ 批次套用"] + [f"{emp_id} {name_dict.get(emp_id, '')}" for emp_id in pivot_df.index] + ["📊 每日出勤總計"]
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
        # 👑 最底下列：每日出勤統計
        # ==========================================
        last_row_idx = len(pivot_df) + 1
        
        # 補滿底下的左側灰色方塊
        for i in range(len(headers_left)):
            item = QTableWidgetItem("")
            item.setBackground(QColor("#E0E0E0"))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
            self.table.setItem(last_row_idx, i, item)

        # 日期出勤加總
        for col_idx, date in enumerate(date_columns):
            col_data = pivot_df[date]
            work_count = sum((col_data != '') & (~col_data.isin(OFF_SHIFTS)))
            item = QTableWidgetItem(f"{work_count} 人")
            item.setTextAlignment(Qt.AlignCenter)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
            item.setBackground(self._get_gradient_color(work_count))
            item.setForeground(QColor("#000000"))
            font = QFont()
            font.setBold(True)
            font.setPointSize(14)
            self.table.setItem(last_row_idx, col_idx + len(headers_left), item)

        # 補滿底下的右側灰色方塊
        if self.split_date:
            r_start = len(headers_left) + len(date_columns)
            for i in range(len(headers_right)):
                item = QTableWidgetItem("")
                item.setBackground(QColor("#E0E0E0"))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
                self.table.setItem(last_row_idx, r_start + i, item)

        # 欄位寬度固定設定
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.horizontalHeader().setDefaultSectionSize(75)
        self.table.verticalHeader().setDefaultSectionSize(40)
        
        # 縮小特定設定區塊的寬度
        for i in range(len(headers_left)):
            self.table.horizontalHeader().resizeSection(i, 60)
        if self.split_date:
            r_start = len(headers_left) + len(date_columns)
            for i in range(len(headers_right)):
                self.table.horizontalHeader().resizeSection(r_start + i, 60)

    def on_run_engine_clicked(self):
        start_date, end_date = self.get_selected_dates()
        leave_quotas = {}
        
        # 取得所有啟用的鍵值 (L, P, r, R + 可能有的 L2, P2, r2, R2)
        all_keys = self.control_keys_left + self.control_keys_right
        
        for emp_id, widgets in self.leave_widgets.items():
            leave_quotas[emp_id] = {}
            for k in all_keys:
                w = widgets.get(k)
                leave_quotas[emp_id][k] = w.value() if isinstance(w, QSpinBox) else int(w.text()) if w else 0
            
        engine = ScheduleEngine(self.db)
        success, message = engine.run_scheduler(start_date, end_date, leave_quotas, split_date=self.split_date)
        if success:
            QMessageBox.information(self, "排班結果", message)
            self.refresh_table() 
        else:
            QMessageBox.warning(self, "排班失敗", message)
    def on_run_engine_clicked(self):
        start_date, end_date = self.get_selected_dates()
        
        leave_quotas = {}
        for emp_id, widgets in self.leave_widgets.items():
            leave_quotas[emp_id] = {}
            for k in self.control_keys:
                w = widgets.get(k)
                leave_quotas[emp_id][k] = w.value() if isinstance(w, QSpinBox) else int(w.text()) if w else 0
            
        engine = ScheduleEngine(self.db)
        # 💡 將分裂日期傳入引擎
        success, message = engine.run_scheduler(start_date, end_date, leave_quotas, split_date=self.split_date)
        if success:
            QMessageBox.information(self, "排班結果", message)
            self.refresh_table() 
        else:
            QMessageBox.warning(self, "排班失敗", message)

            
    def on_run_engine_clicked(self):
        start_date, end_date = self.get_selected_dates()
        leave_quotas = {}
        
        # 🛡️ 安全取得左右兩側的 keys，確保不會因為重新整理狀態丟失而落空
        all_keys = getattr(self, 'control_keys_left', ['L', 'P', 'r', 'R'])
        if getattr(self, 'split_date', None):
            all_keys += getattr(self, 'control_keys_right', ['L2', 'P2', 'r2', 'R2'])
            
        for emp_id, widgets in self.leave_widgets.items():
            leave_quotas[emp_id] = {}
            for k in all_keys:
                w = widgets.get(k)
                if w is not None:
                    try:
                        # 💡 迴避 PyQt 的繼承陷阱，直接暴力取值
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
        # 將捕捉到的精準配額交給引擎
        success, message = engine.run_scheduler(start_date, end_date, leave_quotas, split_date=getattr(self, 'split_date', None))
        
        if success:
            QMessageBox.information(self, "排班結果", message)
            self.refresh_table() 
        else:
            QMessageBox.warning(self, "排班失敗", message)


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

    def on_export_clicked(self):
        start_date, end_date = self.get_selected_dates()
        schedules = self.db.get_schedule_by_date_range(start_date, end_date)
        if not schedules:
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "匯出班表", f"排班表_{start_date}_至_{end_date}.xlsx", "Excel Files (*.xlsx)")
        if file_path:
            try:
                df = pd.DataFrame(schedules)
                pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')
                pivot_df.to_excel(file_path)
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