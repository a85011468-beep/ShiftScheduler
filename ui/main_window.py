from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QTableWidget, QTableWidgetItem, QHeaderView, QLabel, QDateEdit, 
                               QMessageBox, QFileDialog, QDialog, QTextBrowser) # <--- 新增這兩個

from PySide6.QtCore import Qt, QDate, QSettings
from PySide6.QtGui import QColor, QFont
import pandas as pd
from datetime import datetime
from database.db_manager import DatabaseManager
from database.data_importer import DataImporter
from ui.db_dialog import DatabaseManagerDialog
from ui.scheduler_dialog import SchedulerDialog
from config.settings import ALL_STATES, OFF_SHIFTS

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("智慧排班系統 - 檢視與八週稽核面板")
        self.resize(1200, 700)
        self.db = DatabaseManager()

        self.setup_ui()
        self.refresh_table()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # 初始化 QSettings (用於記憶日期)
        self.settings = QSettings("MyUnit", "ShiftScheduler")

        # === 系統功能切換區 ===
        sys_layout = QHBoxLayout()

        # 🔧 [補回] 基礎名單匯入功能
        self.btn_import = QPushButton("📂 匯入員工名單")
        self.btn_import.clicked.connect(self.on_import_clicked)
        sys_layout.addWidget(self.btn_import)

        self.btn_db_manager = QPushButton("🗄️ 歷史班表與資料庫管理")
        self.btn_db_manager.setStyleSheet("background-color: #607D8B; color: white;")
        self.btn_db_manager.clicked.connect(self.on_db_manager_clicked)
        sys_layout.addWidget(self.btn_db_manager)

        self.btn_open_scheduler = QPushButton("🚀 開啟排班與匯出中心")
        self.btn_open_scheduler.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold;")
        self.btn_open_scheduler.clicked.connect(self.on_open_scheduler_clicked)
        sys_layout.addWidget(self.btn_open_scheduler)

        layout.addLayout(sys_layout)

        # === 八週變形工時檢核區 ===
        audit_layout = QHBoxLayout()
        audit_layout.addWidget(QLabel("📅 嚴格八週檢視區間 (56天)："))

        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)

        saved_date = self.settings.value("audit_start_date", "")
        if saved_date:
            self.date_start.setDate(QDate.fromString(saved_date, "yyyy-MM-dd"))
        else:
            self.date_start.setDate(QDate.currentDate().addDays(-QDate.currentDate().day() + 1))
            
        audit_layout.addWidget(self.date_start)
        audit_layout.addWidget(QLabel("至"))

        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        self.date_end.setReadOnly(True) 
        audit_layout.addWidget(self.date_end)

        self.date_start.dateChanged.connect(self.auto_update_end_date)
        self.auto_update_end_date()

        self.btn_refresh = QPushButton("🔄 讀取班表")
        self.btn_refresh.clicked.connect(self.refresh_table)
        audit_layout.addWidget(self.btn_refresh)

        self.btn_audit = QPushButton("⚖️ 執行勞基法紅綠燈檢核")
        self.btn_audit.setStyleSheet("background-color: #FFC107; font-weight: bold;")
        self.btn_audit.clicked.connect(self.on_audit_clicked)
        audit_layout.addWidget(self.btn_audit)

        layout.addLayout(audit_layout)

        # === 核心顯示區 (表格) ===
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

    # === 事件處理與邏輯 ===

    def auto_update_end_date(self):
        start_qdate = self.date_start.date()
        end_qdate = start_qdate.addDays(55)
        self.date_end.setDate(end_qdate)

    def get_selected_dates(self):
        return self.date_start.date().toString("yyyy-MM-dd"), self.date_end.date().toString("yyyy-MM-dd")
    
    def _get_gradient_color(self, val):
        """依據上班人數回傳對應的漸變背景色 (8=紅, 15=綠, 中間=漸變黃)"""
        if val <= 8:
            return QColor(255, 170, 170)  # 柔和紅
        elif val >= 15:
            return QColor(170, 255, 170)  # 柔和綠
        else:
            # 計算 8 到 15 之間的比例 (0.0 ~ 1.0)
            ratio = (val - 8) / 7.0
            
            # 為了讓中間色不要變成髒髒的棕色，我們採用三段式過渡：紅 -> 黃 -> 綠
            if ratio <= 0.5:
                # 前半段：紅 (255, 170, 170) 漸變到 黃 (255, 255, 170)
                r = 255
                g = int(170 + (255 - 170) * (ratio / 0.5))
                b = 170
            else:
                # 後半段：黃 (255, 255, 170) 漸變到 綠 (170, 255, 170)
                r = int(255 - (255 - 170) * ((ratio - 0.5) / 0.5))
                g = 255
                b = 170
            return QColor(r, g, b)


    # 🔧 [補回] 匯入員工名單的事件觸發
    def on_import_clicked(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "選擇員工名單 Excel", "", "Excel Files (*.xlsx)")
        if file_path:
            importer = DataImporter(self.db)
            success, msg = importer.import_employee_excel(file_path)
            if success:
                QMessageBox.information(self, "匯入成功", msg)
                self.refresh_table()
            else:
                QMessageBox.critical(self, "匯入失敗", msg)

    def on_db_manager_clicked(self):
        dialog = DatabaseManagerDialog(self.db, self)
        dialog.exec()
        self.refresh_table()

    def on_open_scheduler_clicked(self):
        dialog = SchedulerDialog(self.db, self)
        dialog.exec()
        self.refresh_table()

    def refresh_table(self):
        start_date, end_date = self.get_selected_dates()
        schedules = self.db.get_schedule_by_date_range(start_date, end_date)
        
        if not schedules:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        employees = self.db.get_all_active_employees()
        level_dict = {str(e['emp_id']).strip(): str(e['job_level']).strip() for e in employees}
        name_dict = {str(e['emp_id']).strip(): e['name'] for e in employees}

        df = pd.DataFrame(schedules)
        pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')
        pivot_df.index = pivot_df.index.astype(str).str.strip()

        # 排序邏輯
        def sort_key(emp_id):
            level = level_dict.get(str(emp_id), 'Normal')
            if level == 'Chief': return (0, str(emp_id))
            elif level == 'M': return (1, str(emp_id))
            else: return (2, str(emp_id))

        sorted_emp_ids = sorted(pivot_df.index, key=sort_key)
        pivot_df = pivot_df.reindex(sorted_emp_ids)

        self.table.setRowCount(len(pivot_df))
        self.table.setColumnCount(len(pivot_df.columns))
        self.table.setHorizontalHeaderLabels(pivot_df.columns)
        
        y_labels = [f"{emp_id} {name_dict.get(emp_id, '')}" for emp_id in pivot_df.index]
        self.table.setVerticalHeaderLabels(y_labels)

        # 💡 [解耦核心] 被動安檢白名單直接與中央處理器同步
        VALID_STATES = ALL_STATES

        self.table.setRowCount(len(pivot_df) + 1)
        self.table.setColumnCount(len(pivot_df.columns))
        # 設定欄位標題（含週日紅色高亮）

        headers = list(pivot_df.columns)
        self.table.setHorizontalHeaderLabels(headers)

        for col_idx, header_text in enumerate(headers):
            item = QTableWidgetItem(header_text)
            try:
                dt = datetime.strptime(header_text, '%Y-%m-%d')
                if dt.weekday() == 6:  # 週日
                    item.setBackground(QColor("#AD1457"))
                    item.setForeground(QColor("#FFFFFF"))
                    font = QFont()
                    font.setBold(True)
                    item.setFont(font)
            except ValueError:
                pass
            self.table.setHorizontalHeaderItem(col_idx, item)

        self.table.horizontalHeader().setStyleSheet(
            "QHeaderView::section { padding: 4px; border: 1px solid #ccc; }"
        )

        y_labels = [f"{emp_id} {name_dict.get(emp_id, '')}" for emp_id in pivot_df.index] + ["📊 每日出勤總計"]
        self.table.setVerticalHeaderLabels(y_labels)

        # ==========================================
        # 👥 [第一步] 先繪製員工班表 (從第 0 列開始)
        # ==========================================
        for row_idx, emp_id in enumerate(pivot_df.index):
            for col_idx, date in enumerate(pivot_df.columns):
                val = str(pivot_df.at[emp_id, date]).strip()
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
                
                # 🚨 被動防呆檢核漏網雷達
                if val and val not in VALID_STATES:
                    item.setBackground(QColor("#FFCDD2")) 
                    item.setForeground(QColor("#B71C1C")) 
                    item.setToolTip(f"⚠️ 系統無法辨識班別「{val}」！\n請至「資料庫管理中心」修正，否則會導致勞檢與排班引擎崩潰。")
                
                # 直接使用 row_idx，不用再 +1 了
                self.table.setItem(row_idx, col_idx, item)

        # ==========================================
        # 👑 [第二步] 繪製最後一列：每日出勤統計
        # ==========================================
        last_row_idx = len(pivot_df) # 💡 統計列的絕對位置在最底下
        
        for col_idx, date in enumerate(pivot_df.columns):
            col_data = pivot_df[date]
            # 數學邏輯：格子不是空白，且不在 OFF_SHIFTS (休假陣列) 裡面，就算出勤
            work_count = sum((col_data != '') & (~col_data.isin(OFF_SHIFTS)))
            
            item = QTableWidgetItem(f"{work_count} 人")
            item.setTextAlignment(Qt.AlignCenter)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
            
            # 套用漸變色與粗體字
            item.setBackground(self._get_gradient_color(work_count))
            item.setForeground(QColor("#000000")) # 強制黑字確保清晰
            font = QFont()
            font.setBold(True)
            font.setPointSize(14) 
            item.setFont(font)
            
            # 畫在最後一個 row
            self.table.setItem(last_row_idx, col_idx, item)

        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.horizontalHeader().setDefaultSectionSize(75)
        self.table.verticalHeader().setDefaultSectionSize(40)


        #---------------------------------
    def on_audit_clicked(self):
        # 🛡️ 防呆：檢核前強制先刷新一次畫面，確保時間軸與畫面同步
        self.refresh_table()
        
        base_start, base_end = self.get_selected_dates()
        
        start_qdate = QDate.fromString(base_start, "yyyy-MM-dd")
        end_qdate = QDate.fromString(base_end, "yyyy-MM-dd")
        scan_start = start_qdate.addDays(-6).toString("yyyy-MM-dd")
        scan_end = end_qdate.addDays(6).toString("yyyy-MM-dd")
        
        schedules = self.db.get_schedule_by_date_range(scan_start, scan_end)
        
        if not schedules:
            QMessageBox.warning(self, "檢核失敗", "區間內無班表資料可供檢核！")
            return
            
        employees = self.db.get_all_active_employees()
        name_dict = {str(e['emp_id']).strip(): e['name'] for e in employees}
        
        df = pd.DataFrame(schedules)
        pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')
        pivot_df.index = pivot_df.index.astype(str).str.strip()
        
        # 定義出勤班別 (注意：請假 L/P 與例休 R/r 皆不屬於出勤，不計入連續工作天數)
        WORK_SHIFTS = ['01早M', '01早m', '01午M', '01午m', '01早B1', '01早B2', '01午B1', '01午B2', '01中A', '01夜B1', '01夜B2', 'Train', '日']

        # 💡 HTML 報告標題
        html_report = f"<h3 style='color: #333333;'>📅 區間 {base_start} 至 {base_end}<br>八週變形工時 (含跨期邊界) 檢核報告：</h3><hr>"
        all_pass = True

        for emp_id in pivot_df.index:
            emp_data = pivot_df.loc[emp_id]
            emp_name = name_dict.get(emp_id, '')
            
            # 1. 取出核心 56 天的資料 (過濾掉邊界，只針對這 56 天算假)
            core_dates = pd.date_range(start=base_start, end=base_end).strftime('%Y-%m-%d')
            valid_core_dates = [d for d in core_dates if d in emp_data.index]
            core_data = emp_data[valid_core_dates]
            
            # 💡 [核心修正] 嚴格檢核 8R 與 8r，多一天少一天都不行，且不可用 L 或 P 混充！
            R_count = sum(core_data == 'R')
            r_count = sum(core_data == 'r')
            
            off_pass = (R_count == 8) and (r_count == 8)
            
            # 2. 檢核連續上班天數 (掃描包含前後6天的完整 68 天)
            max_consec = 0
            current_consec = 0
            for val in emp_data:
                if val in WORK_SHIFTS:
                    current_consec += 1
                    max_consec = max(max_consec, current_consec)
                else:
                    current_consec = 0
            consec_pass = max_consec <= 6
            
            if off_pass and consec_pass:
                html_report += f"<p style='color: green; font-size: 14px;'>✅ <b>{emp_id} {emp_name}</b>: 合格 (例假 8 天, 休息日 8 天，最長連班 {max_consec} 天)</p>"
            else:
                all_pass = False
                err_msg = []
                if R_count != 8 or r_count != 8: 
                    err_msg.append(f"例假 <b>{R_count}</b> 天 / 休息日 <b>{r_count}</b> 天 (依法應各為 8 天)")
                if not consec_pass: 
                    err_msg.append(f"發現跨期連班達 <b>{max_consec}</b> 天")
                html_report += f"<p style='color: red; font-size: 14px;'>❌ <b>{emp_id} {emp_name}</b>: 違規！ {', '.join(err_msg)}</p>"

        # 💡 [視覺與功能升級] 呼叫專業的獨立彈出視窗來渲染完整報告
        dialog = QDialog(self)
        dialog.setWindowTitle("⚖️ 勞基法稽核中心")
        dialog.resize(500, 600)  # 視窗加大，容納所有員工
        dialog_layout = QVBoxLayout(dialog)
        
        status_label = QLabel()
        if all_pass:
            status_label.setText("🎉 完美！區間內所有員工皆符合八週休假規範，且邊界無連班違規。")
            status_label.setStyleSheet("color: green; font-weight: bold; font-size: 16px;")
        else:
            status_label.setText("⚠️ 發現違規排班！請查看下方詳細報告：")
            status_label.setStyleSheet("color: red; font-weight: bold; font-size: 16px;")
        dialog_layout.addWidget(status_label)

        # 使用 QTextBrowser，支援完整 HTML 標籤與滑鼠滾輪
        text_browser = QTextBrowser()
        text_browser.setHtml(html_report)
        dialog_layout.addWidget(text_browser)

        btn_close = QPushButton("關閉報告")
        btn_close.setStyleSheet("height: 35px; font-weight: bold;")
        btn_close.clicked.connect(dialog.accept)
        dialog_layout.addWidget(btn_close)

        dialog.exec()
    def closeEvent(self, event):
        start_date_str = self.date_start.date().toString("yyyy-MM-dd")
        self.settings.setValue("audit_start_date", start_date_str)
        super().closeEvent(event)    