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
        start_date, end_date = self.get_selected_dates()
        
        employees = self.db.get_all_active_employees()
        if not employees:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        self.leave_widgets = {} 
        self.remaining_labels = {} 
        self.date_comboboxes = {} 
        
        level_dict = {str(e['emp_id']).strip(): str(e['job_level']).strip() for e in employees}
        name_dict = {str(e['emp_id']).strip(): e['name'] for e in employees}
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

        # 💡 [修改] 拔除中A與泛用，留下純粹的休假與總量控制
        settings_headers = ["特休(L)", "事假(P)", "休息(r)", "例假(R)", "固定(其他)", "剩餘(天)"]
        all_headers = settings_headers + date_columns
        
        self.table.setRowCount(len(pivot_df) + 2)
        self.table.setColumnCount(len(all_headers))
        # ==========================================
        # 🎨 表頭獨立渲染：週日高亮系統
        # ==========================================
        for col_idx, header_text in enumerate(all_headers):
            item = QTableWidgetItem(header_text)
            
            try:
                # 嘗試將文字解析為日期 (YYYY-MM-DD)
                dt = datetime.strptime(header_text, '%Y-%m-%d')
                
                # dt.weekday() 中，0 是週一，6 是週日
                if dt.weekday() == 6: 
                    item.setBackground(QColor("#AD1457")) # 暗粉紅色
                    item.setForeground(QColor("#FFFFFF")) # 搭配白色字體確保辨識度
                    font = QFont()
                    font.setBold(True)
                    item.setFont(font)
            except ValueError:
                # 若解析失敗 (例如遇到 "特休(L)", "剩餘(天)" 等設定欄位)，則維持預設樣式
                pass 
                
            self.table.setHorizontalHeaderItem(col_idx, item)
            
        # ⚠️ (防呆處理) 若您的作業系統原生主題會強制覆蓋表頭顏色，加上這行可強制啟用自訂顏色：
        self.table.horizontalHeader().setStyleSheet("QHeaderView::section { padding: 4px; border: 1px solid #ccc; }")
        
        y_labels = ["⚡ 批次套用"] + [f"{emp_id} {name_dict.get(emp_id, '')}" for emp_id in pivot_df.index] + ["📊 每日出勤總計"]
        self.table.setVerticalHeaderLabels(y_labels)

        # 💡 [修改] 控制項 KEY 更新
        control_keys = ['L', 'P', 'r', 'R']

        # ==========================================
        # ⚡ [第 0 列] 批次套用工具列
        # ==========================================
        def make_batch_updater(key):
            def update_all_emps(val):
                for e_id, widgets in self.leave_widgets.items():
                    w = widgets.get(key)
                    if isinstance(w, QSpinBox):
                        w.setValue(val) if val >= w.minimum() else w.setValue(w.minimum())
            return update_all_emps

        for col_idx, key in enumerate(control_keys):
            spin = CustomSpinBox()
            spin.setRange(0, total_days)
            spin.setStyleSheet("background-color: #FFF9C4; color: black; font-weight: bold; border: 1px solid #ccc;")
            spin.valueChanged.connect(make_batch_updater(key))
            self.table.setCellWidget(0, col_idx, spin)

        for col_idx in range(len(control_keys), len(all_headers)):
            item = QTableWidgetItem("")
            item.setBackground(QColor("#E0E0E0"))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
            self.table.setItem(0, col_idx, item)

        # ==========================================
        # 👥 [第 1 ~ N 列] 員工配額控制台與下拉選單班表
        # ==========================================
        def make_updater(emp_id, fixed_other):
            def update():
                widgets = self.leave_widgets[emp_id]
                val_L = widgets['L'].value() if isinstance(widgets['L'], QSpinBox) else int(widgets['L'].text())
                val_P = widgets['P'].value() if isinstance(widgets['P'], QSpinBox) else int(widgets['P'].text())
                val_r = widgets['r'].value() if isinstance(widgets['r'], QSpinBox) else int(widgets['r'].text())
                val_R = widgets['R'].value() if isinstance(widgets['R'], QSpinBox) else int(widgets['R'].text())

                # 💡 [修改] 消耗掉的天數只算休假與手動釘上的其他班別
                consumed = val_L + val_P + val_r + val_R + fixed_other
                rem = total_days - consumed

                lbl_rem = self.remaining_labels[emp_id]
                lbl_rem.setText(str(rem))
                lbl_rem.setStyleSheet("color: #1565C0; font-weight: bold;" if rem >= 0 else "background-color: #FFCDD2; color: #B71C1C; font-weight: bold; border: 1px solid #B71C1C;")
            return update

        for row_idx, emp_id in enumerate(pivot_df.index):
            real_row = row_idx + 1 
            emp_data = pivot_df.loc[emp_id]

            existing_L = sum(emp_data == 'L')
            existing_P = sum(emp_data == 'P')
            existing_r = sum(emp_data == 'r')
            existing_R = sum(emp_data == 'R')
            
            # 💡 [修改] 計算「固定(其他)」：除了這四種假，其餘被釘上的都算作已消耗配額 (包含主管手釘的中A與泛用)
            managed_states = ['L', 'P', 'r', 'R']
            fixed_other = sum((emp_data != '') & (~emp_data.isin(managed_states)))
            
            self.leave_widgets[emp_id] = {}
            updater = make_updater(emp_id, fixed_other)

            def create_label(col_idx, key, val, style="background-color: #EEEEEE; color: #9E9E9E; border: 1px solid #ccc;"):
                lbl = QLabel(str(val))
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet(style)
                self.table.setCellWidget(real_row, col_idx, lbl)
                self.leave_widgets[emp_id][key] = lbl

            def create_spinbox(col_idx, key, existing_val):
                spin = CustomSpinBox()
                spin.setRange(existing_val, total_days) 
                spin.setValue(existing_val)
                spin.setAlignment(Qt.AlignCenter)
                spin.setStyleSheet("background-color: white; color: black; border: 1px solid #ddd;")
                spin.valueChanged.connect(updater) 
                self.table.setCellWidget(real_row, col_idx, spin)
                self.leave_widgets[emp_id][key] = spin

            # 依序放回格子
            create_spinbox(0, 'L', existing_L)
            create_spinbox(1, 'P', existing_P)
            create_spinbox(2, 'r', existing_r)
            create_spinbox(3, 'R', existing_R)
            create_label(4, '固定(其他)', fixed_other)

            rem_label = QLabel()
            rem_label.setAlignment(Qt.AlignCenter)
            self.table.setCellWidget(real_row, 5, rem_label)
            self.remaining_labels[emp_id] = rem_label
            updater() 

            combo_items = [""] + ALL_STATES

            for col_offset, date in enumerate(date_columns):
                val = str(pivot_df.at[emp_id, date]).strip()
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                
                # 💡 強制設為唯讀，不允許在畫面上亂點亂改
                item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
                
                if val: 
                    item.setBackground(QColor("#E0E0E0"))
                if val and val not in ALL_STATES:
                    item.setBackground(QColor("#FFCDD2")) 
                    item.setForeground(QColor("#B71C1C")) 
                
                table_col = col_offset + len(settings_headers)
                self.table.setItem(real_row, table_col, item)

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
        
        leave_quotas = {}
        for emp_id, widgets in self.leave_widgets.items():
            def get_val(key):
                w = widgets.get(key)
                if w is None: return 0
                return w.value() if isinstance(w, QSpinBox) else int(w.text())

            # 💡 [修改] 只抽取休假資料傳給引擎
            leave_quotas[emp_id] = {
                'L': get_val('L'),
                'P': get_val('P'),
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
    def on_import_pre_schedule_clicked(self):
        """從 Excel 匯入預排班表，無視空格，強制鎖定有值的格子"""
        file_path, _ = QFileDialog.getOpenFileName(self, "選擇預排 Excel 檔案", "", "Excel Files (*.xlsx *.xls)")
        if not file_path:
            return
            
        try:
            import re
            # 強制以字串格式讀取，避免 Pandas 自作聰明把員工編號或日期轉成小數點
            df = pd.read_excel(file_path, dtype=str)
            
            # 1. 尋找員工編號欄位 (假設欄位名叫 emp_id，若無則抓取第一欄)
            emp_col = 'emp_id' if 'emp_id' in df.columns else df.columns[0]
                
            # 2. 自動識別日期欄位 (尋找格式如 2026-06-01 的欄位名)
            date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}')
            date_columns = [col for col in df.columns if date_pattern.match(str(col).strip())]
            
            if not date_columns:
                QMessageBox.warning(self, "格式錯誤", "找不到符合 YYYY-MM-DD 格式的日期欄位，請檢查 Excel 標題列。")
                return
                
            conn = self.db.get_connection()
            cursor = conn.cursor()
            records_to_upsert = []
            
            # 3. 遍歷 Excel 抓取資料
            for index, row in df.iterrows():
                raw_emp = str(row[emp_col]).strip()
                # 防呆：如果第一欄是 "001 小明"，自動切出 "001"
                emp_id = raw_emp.split()[0] if raw_emp != 'nan' else None
                if not emp_id:
                    continue
                    
                for date_col in date_columns:
                    val = str(row[date_col]).strip()
                    
                    # 💡 核心條件：無視空格與 nan
                    if val and val.lower() != 'nan':
                        # 💡 核心條件：取代 r' 為 r
                        if val == "r'":
                            val = "r"
                            
                        # 擷取純日期部分 YYYY-MM-DD
                        clean_date = str(date_col).strip()[:10]
                        
                        # 準備寫入資料 (emp_id, date, shift_code, is_locked)
                        records_to_upsert.append((emp_id, clean_date, val, 1))
                        
            # 4. 執行資料庫 Upsert (存在則更新，不存在則新增)
            if records_to_upsert:
                cursor.executemany('''
                    INSERT INTO schedule (emp_id, date, shift_code, is_locked)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(emp_id, date) DO UPDATE SET
                        shift_code = excluded.shift_code,
                        is_locked = excluded.is_locked
                ''', records_to_upsert)
            
            conn.commit()
            conn.close()
            
            self.refresh_table()
            QMessageBox.information(self, "匯入成功", f"成功匯入並鎖定 {len(records_to_upsert)} 筆預排班別！")
            
        except Exception as e:
            QMessageBox.critical(self, "匯入失敗", f"讀取 Excel 時發生錯誤：\n{str(e)}")

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