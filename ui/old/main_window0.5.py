from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QTableWidget, QTableWidgetItem, QHeaderView, QLabel, QDateEdit, QMessageBox)
from PySide6.QtCore import Qt, QDate, QSettings  # <--- 確保補上 QSettings
import pandas as pd
from database.db_manager import DatabaseManager
from ui.db_dialog import DatabaseManagerDialog
from ui.scheduler_dialog import SchedulerDialog

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("智慧排班系統 - 檢視與八週稽核面板")
        self.resize(1900, 900)
        self.db = DatabaseManager()

        self.setup_ui()
        self.refresh_table()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # === 1. 系統功能切換區 ===
        sys_layout = QHBoxLayout()

        self.btn_db_manager = QPushButton("🗄️ 歷史班表與資料庫管理")
        self.btn_db_manager.setStyleSheet("background-color: #607D8B; color: white;")
        self.btn_db_manager.clicked.connect(self.on_db_manager_clicked)
        sys_layout.addWidget(self.btn_db_manager)

        self.btn_open_scheduler = QPushButton("🚀 開啟排班與匯出中心")
        self.btn_open_scheduler.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold;")
        self.btn_open_scheduler.clicked.connect(self.on_open_scheduler_clicked)
        sys_layout.addWidget(self.btn_open_scheduler)

        layout.addLayout(sys_layout)

        # === 2. 八週變形工時檢核區 ===
        self.settings = QSettings("MyUnit", "ShiftScheduler")

        audit_layout = QHBoxLayout()
        audit_layout.addWidget(QLabel("📅 嚴格八週檢視區間 (56天)："))

        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)

        # 💡 [關鍵修改] 嘗試讀取上次儲存的日期，若無，則預設為當月 1 號
        saved_date = self.settings.value("audit_start_date", "")
        if saved_date:
            self.date_start.setDate(QDate.fromString(saved_date, "yyyy-MM-dd"))
        else:
            self.date_start.setDate(QDate.currentDate().addDays(-QDate.currentDate().day() + 1))
            
        self.date_start.dateChanged.connect(self.auto_update_end_date)
        audit_layout.addWidget(self.date_start)

        self.date_start.setDate(QDate.currentDate().addDays(-QDate.currentDate().day() + 1))
        # 綁定事件：只要起始日改變，結束日自動 +55 天
        self.date_start.dateChanged.connect(self.auto_update_end_date)
        audit_layout.addWidget(self.date_start)

        audit_layout.addWidget(QLabel("至"))

        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        self.date_end.setReadOnly(True) # 強制唯讀，防止人為亂調破壞八週定義
        self.auto_update_end_date()     # 啟動時先算一次
        audit_layout.addWidget(self.date_end)

        self.btn_refresh = QPushButton("🔄 讀取班表")
        self.btn_refresh.clicked.connect(self.refresh_table)
        audit_layout.addWidget(self.btn_refresh)

        # (預留給未來實作的檢核按鈕)
        self.btn_audit = QPushButton("⚖️ 執行勞基法紅綠燈檢核")
        self.btn_audit.setStyleSheet("background-color: #FFC107; font-weight: bold;")
        audit_layout.addWidget(self.btn_audit)

        layout.addLayout(audit_layout)

        # === 3. 核心顯示區 (表格) ===
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("alternate-background-color: #f2f2f2;")
        layout.addWidget(self.table)

    # 💡 [關鍵新增] 當使用者關閉主視窗時，自動將當前設定的日期釘死在系統記憶體中
    def closeEvent(self, event):
        start_date_str = self.date_start.date().toString("yyyy-MM-dd")
        self.settings.setValue("audit_start_date", start_date_str)
        super().closeEvent(event)
    # === 事件處理邏輯 ===

    def auto_update_end_date(self):
        """核心防呆：強制將結束日期鎖定為起算日 + 55 天"""
        start_qdate = self.date_start.date()
        end_qdate = start_qdate.addDays(55)
        self.date_end.setDate(end_qdate)

    def get_selected_dates(self):
        return self.date_start.date().toString("yyyy-MM-dd"), self.date_end.date().toString("yyyy-MM-dd")

    def on_db_manager_clicked(self):
        dialog = DatabaseManagerDialog(self.db, self)
        dialog.exec()
        self.refresh_table()

    def on_open_scheduler_clicked(self):
        dialog = SchedulerDialog(self.db, self)
        dialog.exec()
        # 排班視窗關閉後，自動重繪主畫面的班表，確保看到最新結果
        self.refresh_table()

    def refresh_table(self):
        """讀取資料並依職級渲染表格"""
        start_date, end_date = self.get_selected_dates()
        schedules = self.db.get_schedule_by_date_range(start_date, end_date)
        
        if not schedules:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        # 1. 取得職級
        employees = self.db.get_all_active_employees()
        level_dict = {e['emp_id']: e['job_level'] for e in employees}
        name_dict = {e['emp_id']: e['name'] for e in employees}

        # 2. DataFrame 轉換
        df = pd.DataFrame(schedules)
        pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')

        # 3. 職級排序 (Chief > M > Normal)
        def sort_key(emp_id):
            level = level_dict.get(emp_id, 'Normal')
            if level == 'Chief':
                weight = 0
            elif level == 'M':
                weight = 1
            else:
                weight = 2
            return (weight, emp_id)

        sorted_emp_ids = sorted(pivot_df.index, key=sort_key)
        pivot_df = pivot_df.reindex(sorted_emp_ids)

        # 4. 表格渲染
        self.table.setRowCount(len(pivot_df))
        self.table.setColumnCount(len(pivot_df.columns))
        self.table.setHorizontalHeaderLabels(pivot_df.columns)
        
        y_labels = [f"{emp_id} {name_dict.get(emp_id, '')}" for emp_id in pivot_df.index]
        self.table.setVerticalHeaderLabels(y_labels)

        for row_idx, emp_id in enumerate(pivot_df.index):
            for col_idx, date in enumerate(pivot_df.columns):
                val = pivot_df.at[emp_id, date]
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignCenter)
                # 主畫面設定為唯讀，禁止直接在畫面上修改
                item.setFlags(item.flags() & ~Qt.ItemIsEditable) 
                self.table.setItem(row_idx, col_idx, item)

        # 5. 滾動條與固定窗格大小設定
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.horizontalHeader().setDefaultSectionSize(75)
        self.table.verticalHeader().setDefaultSectionSize(40)