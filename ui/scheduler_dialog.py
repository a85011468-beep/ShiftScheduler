from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QLabel, QDateEdit, QMessageBox, QFileDialog, QTableWidget, 
                               QTableWidgetItem, QHeaderView, QSpinBox, QAbstractSpinBox) # 💡 新增這兩個
from PySide6.QtCore import Qt, QDate, QSettings
from PySide6.QtGui import QColor, QFont
import pandas as pd
from engine.solver import ScheduleEngine
from config.settings import OFF_SHIFTS, ALL_STATES

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

        # 💡 [新增] 儲存手動預排圖釘按鈕
        self.btn_save_pins = QPushButton("📌 釘上手動預排")
        self.btn_save_pins.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_save_pins.clicked.connect(self.on_save_pins_clicked)
        ctrl_layout.addWidget(self.btn_save_pins)

        self.btn_export = QPushButton("💾 滿意並匯出 Excel")
        self.btn_export.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_export.clicked.connect(self.on_export_clicked)
        ctrl_layout.addWidget(self.btn_export)

        # 3. 🔧 [功能二] 新增「刪除未來班表」按鈕
        self.btn_clear = QPushButton("🗑️ 清空區間未鎖定班表")
        self.btn_clear.setStyleSheet("background-color: #795548; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_clear.clicked.connect(self.on_clear_schedule_clicked)
        ctrl_layout.addWidget(self.btn_clear)

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
        start_date, end_date = self.get_selected_dates()
        
        employees = self.db.get_all_active_employees()
        if not employees:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        self.leave_widgets = {} # 💡 存放 SpinBox 或 Label
        self.remaining_labels = {} 
        
        level_dict = {str(e['emp_id']).strip(): str(e['job_level']).strip() for e in employees}
        name_dict = {str(e['emp_id']).strip(): e['name'] for e in employees}
        # 💡 [新增] 讀取員工偏好
        shift_pref_dict = {str(e['emp_id']).strip(): str(e.get('shift_pref', 'MIX')).strip() for e in employees}
        emp_ids = [str(e['emp_id']).strip() for e in employees]
        
        date_columns = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        total_days = len(date_columns) 
        
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

        # 💡 [擴充設定欄] 增加「固定(其他)」欄位
        settings_headers = ["特休(L)", "事假(P)", "中A班", "泛用01", "休息(r)", "例假(R)", "固定(其他)", "剩餘(天)"]
        all_headers = settings_headers + date_columns
        
        self.table.setRowCount(len(pivot_df) + 2)
        self.table.setColumnCount(len(all_headers))
        self.table.setHorizontalHeaderLabels(all_headers)
        
        y_labels = ["⚡ 批次套用"] + [f"{emp_id} {name_dict.get(emp_id, '')}" for emp_id in pivot_df.index] + ["📊 每日出勤總計"]
        self.table.setVerticalHeaderLabels(y_labels)

        GENERIC_01_SHIFTS = ["01早B1", "01早B2", "01午B1", "01午B2"]
        # 控制項的 KEY 列表
        control_keys = ['L', 'P', '01中A', '01泛用', 'r', 'R']

        # ==========================================
        # ⚡ [第 0 列] 批次套用工具列
        # ==========================================
        def make_batch_updater(key):
            def update_all_emps(val):
                for e_id, widgets in self.leave_widgets.items():
                    w = widgets.get(key)
                    # 只有該欄位是 QSpinBox 且沒被反灰鎖死時，才允許批次更新
                    if isinstance(w, QSpinBox):
                        if val >= w.minimum():
                            w.setValue(val)
                        else:
                            w.setValue(w.minimum())
            return update_all_emps

        for col_idx, key in enumerate(control_keys):
            spin = QSpinBox()
            spin.setRange(0, total_days)
            spin.setButtonSymbols(QAbstractSpinBox.NoButtons) 
            spin.setAlignment(Qt.AlignCenter)
            spin.setStyleSheet("background-color: #FFF9C4; color: black; font-weight: bold; border: 1px solid #ccc;")
            spin.valueChanged.connect(make_batch_updater(key))
            self.table.setCellWidget(0, col_idx, spin)

        for col_idx in range(len(control_keys), len(all_headers)):
            item = QTableWidgetItem("")
            item.setBackground(QColor("#E0E0E0"))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
            self.table.setItem(0, col_idx, item)


        # ==========================================
        # 👥 [第 1 ~ N 列] 員工配額控制台
        # ==========================================
        # 建立動態計算中心
        def make_updater(emp_id, lvl, pref, fixed_other, exist_gen01):
            def update():
                widgets = self.leave_widgets[emp_id]
                
                # 讀取手動設定的數值
                val_L = widgets['L'].value() if isinstance(widgets['L'], QSpinBox) else int(widgets['L'].text())
                val_P = widgets['P'].value() if isinstance(widgets['P'], QSpinBox) else int(widgets['P'].text())
                val_r = widgets['r'].value() if isinstance(widgets['r'], QSpinBox) else int(widgets['r'].text())
                val_R = widgets['R'].value() if isinstance(widgets['R'], QSpinBox) else int(widgets['R'].text())
                val_MidA = widgets['01中A'].value() if isinstance(widgets['01中A'], QSpinBox) else int(widgets['01中A'].text())

                consumed = val_L + val_P + val_r + val_R + val_MidA + fixed_other

                # 💡 [核心邏輯] 判斷「01泛用」該如何表現
                if pref == 'NIGHT_ONLY':
                    val_Gen01 = 0
                elif lvl == 'Normal':
                    # 一般員工：自動補滿，讓剩餘天數歸零 (不低於已鎖定天數)
                    val_Gen01 = max(exist_gen01, total_days - consumed)
                    widgets['01泛用'].setText(str(val_Gen01))
                else:
                    val_Gen01 = widgets['01泛用'].value() if isinstance(widgets['01泛用'], QSpinBox) else int(widgets['01泛用'].text())

                total_consumed = consumed + val_Gen01
                rem = total_days - total_consumed

                lbl_rem = self.remaining_labels[emp_id]
                lbl_rem.setText(str(rem))

                if rem < 0:
                    lbl_rem.setStyleSheet("background-color: #FFCDD2; color: #B71C1C; font-weight: bold; border: 1px solid #B71C1C;")
                else:
                    lbl_rem.setStyleSheet("color: #1565C0; font-weight: bold; background-color: transparent;")
            return update

        for row_idx, emp_id in enumerate(pivot_df.index):
            real_row = row_idx + 1 
            emp_data = pivot_df.loc[emp_id]
            
            job_level = level_dict.get(emp_id, 'Normal')
            s_pref = shift_pref_dict.get(emp_id, 'MIX')

            existing_L = sum(emp_data == 'L')
            existing_P = sum(emp_data == 'P')
            existing_MidA = sum(emp_data == '01中A')
            existing_Gen01 = sum(emp_data.isin(GENERIC_01_SHIFTS))
            existing_r = sum(emp_data == 'r')
            existing_R = sum(emp_data == 'R')
            
            # 💡 計算「固定(其他)」：有值，且不屬於 L,P,中A,泛用,r,R 的班別 (例如主管班、Train、日)
            managed_states = ['L', 'P', '01中A', 'r', 'R'] + GENERIC_01_SHIFTS
            fixed_other = sum((emp_data != '') & (~emp_data.isin(managed_states)))
            
            self.leave_widgets[emp_id] = {}
            updater = make_updater(emp_id, job_level, s_pref, fixed_other, existing_Gen01)

            # --- Widget 工廠函式 ---
            def create_label(col_idx, key, val, style="background-color: #EEEEEE; color: #9E9E9E; border: 1px solid #ccc;"):
                lbl = QLabel(str(val))
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet(style)
                self.table.setCellWidget(real_row, col_idx, lbl)
                self.leave_widgets[emp_id][key] = lbl

            def create_spinbox(col_idx, key, existing_val):
                spin = QSpinBox()
                spin.setRange(existing_val, total_days) 
                spin.setValue(existing_val)
                spin.setAlignment(Qt.AlignCenter)
                spin.setButtonSymbols(QAbstractSpinBox.NoButtons) 
                spin.setStyleSheet("background-color: white; color: black; border: 1px solid #ddd;")
                spin.valueChanged.connect(updater) 
                self.table.setCellWidget(real_row, col_idx, spin)
                self.leave_widgets[emp_id][key] = spin

            # --- 開始依據規則佈署格子 ---
            create_spinbox(0, 'L', existing_L)
            create_spinbox(1, 'P', existing_P)
            
            # 01中A：NIGHT_ONLY 反灰歸 0，否則 SpinBox
            if s_pref == 'NIGHT_ONLY':
                create_label(2, '01中A', 0)
            else:
                create_spinbox(2, '01中A', existing_MidA)
                
            # 01泛用：NIGHT_ONLY 歸 0，Normal 動態反灰，M/Chief 為 SpinBox
            if s_pref == 'NIGHT_ONLY':
                create_label(3, '01泛用', 0)
            elif job_level == 'Normal':
                create_label(3, '01泛用', existing_Gen01, style="background-color: #E3F2FD; color: #1565C0; font-weight: bold; border: 1px solid #ccc;")
            else:
                create_spinbox(3, '01泛用', existing_Gen01)

            create_spinbox(4, 'r', existing_r)
            create_spinbox(5, 'R', existing_R)

            # 固定(其他)
            create_label(6, '固定(其他)', fixed_other)

            # 剩餘(天)
            rem_label = QLabel()
            rem_label.setAlignment(Qt.AlignCenter)
            self.table.setCellWidget(real_row, 7, rem_label)
            self.remaining_labels[emp_id] = rem_label
            
            # 初始化觸發一次計算
            updater() 

            # 渲染後方的日期資料格
            for col_offset, date in enumerate(date_columns):
                val = pivot_df.at[emp_id, date]
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignCenter)
                
                if val: item.setBackground(Qt.lightGray)
                if val and val not in ALL_STATES:
                    item.setBackground(QColor("#FFCDD2")) 
                    item.setForeground(QColor("#B71C1C")) 
                
                self.table.setItem(real_row, col_offset + len(settings_headers), item)

        # ==========================================
        # 👑 [最後一列] 每日出勤統計
        # ==========================================
        last_row_idx = len(pivot_df) + 1
        
        for i in range(len(settings_headers)):
            item = QTableWidgetItem("")
            item.setBackground(QColor("#E0E0E0"))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
            self.table.setItem(last_row_idx, i, item)

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
            item.setFont(font)
            
            self.table.setItem(last_row_idx, col_idx + len(settings_headers), item)

        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.horizontalHeader().setDefaultSectionSize(75)
        self.table.verticalHeader().setDefaultSectionSize(40)
        
        for i in range(len(settings_headers)):
            self.table.horizontalHeader().resizeSection(i, 60)


    def on_run_engine_clicked(self):
        start_date, end_date = self.get_selected_dates()
        
        # 💡 智慧讀取：不管是 SpinBox 還是 Label，通通提煉出正確的數字
        leave_quotas = {}
        for emp_id, widgets in self.leave_widgets.items():
            def get_val(key):
                w = widgets[key]
                return w.value() if isinstance(w, QSpinBox) else int(w.text())

            leave_quotas[emp_id] = {
                'L': get_val('L'),
                'P': get_val('P'),
                '01中A': get_val('01中A'),
                '01泛用': get_val('01泛用'),
                'r': get_val('r'),
                'R': get_val('R')
            }
            
        engine = ScheduleEngine(self.db)
        success, message = engine.run_scheduler(start_date, end_date, leave_quotas)
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
    def on_save_pins_clicked(self):
        """將畫面上手動輸入的班別 (如 L, Train) 儲存並釘上圖釘"""
        start_date, end_date = self.get_selected_dates()
        
        if self.db.is_period_locked(start_date, end_date):
            QMessageBox.critical(self, "🛑 拒絕寫入", "該區間已被系統結算鎖定，無法新增圖釘。")
            return

        records_to_update = []
        
        # 取得所有員工 ID 與日期欄位 (跳過前面兩個 L/P 設定欄位)
        date_columns = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        
        for row_idx in range(self.table.rowCount()):
            # 取得垂直標籤 (emp_id 姓名) 並抽出 emp_id
            v_header = self.table.verticalHeaderItem(row_idx).text()
            emp_id = v_header.split(" ")[0]
            
            for col_idx, date in enumerate(date_columns):
                # 表格中的日期從第 3 欄 (index 2) 開始
                item = self.table.item(row_idx, col_idx + 2)
                if item:
                    val = item.text().strip()
                    # 💡 只有當格子內有您手動輸入的值 (例如 L, Train) 時，才執行寫入並鎖定
                    if val:
                        records_to_update.append((emp_id, date, val, 1))

        if not records_to_update:
            QMessageBox.information(self, "無更新", "您沒有在畫面上輸入任何新的手動班別。")
            return

        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.executemany('''
            INSERT INTO schedule (emp_id, date, shift_code, is_locked)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(emp_id, date) DO UPDATE SET 
                shift_code = excluded.shift_code,
                is_locked = excluded.is_locked
        ''', records_to_update)
        conn.commit()
        conn.close()
        
        QMessageBox.information(self, "圖釘釘入成功", f"✅ 已成功將 {len(records_to_update)} 筆手動班別寫入資料庫並鎖定。\n引擎下次排班將會絕對服從這些紀錄。")
        self.refresh_table()