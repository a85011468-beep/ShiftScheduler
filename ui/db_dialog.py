from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFileDialog, QLabel, QDateEdit)
from PySide6.QtCore import Qt, QDate
import pandas as pd
import os
from database.data_importer import DataImporter

class DatabaseManagerDialog(QDialog):
    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 資料庫與歷史班表管理中心")
        self.resize(1000, 600)
        self.db = db_manager

        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 頂部控制區

        ctrl_layout = QHBoxLayout()

        # === [新增] 重置資料庫按鈕 ===
        self.btn_reset = QPushButton("⚠️ 重置資料庫")
        self.btn_reset.setStyleSheet("background-color: #9E9E9E; color: white; font-weight: bold;")
        self.btn_reset.clicked.connect(self.on_reset_clicked)
        ctrl_layout.addWidget(self.btn_reset)
        
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        self.date_start.setDate(QDate.currentDate().addDays(-QDate.currentDate().day() + 1))
        ctrl_layout.addWidget(QLabel("管理區間："))
        ctrl_layout.addWidget(self.date_start)
        
        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        self.date_end.setDate(QDate.currentDate().addDays(self.date_start.date().daysInMonth() - QDate.currentDate().day()))
        ctrl_layout.addWidget(QLabel("至"))
        ctrl_layout.addWidget(self.date_end)

        btn_load = QPushButton("🔄 讀取資料")
        btn_load.clicked.connect(self.load_data)
        ctrl_layout.addWidget(btn_load)

        self.btn_import = QPushButton("📥 匯入歷史班表")
        self.btn_import.clicked.connect(self.on_import_clicked)
        ctrl_layout.addWidget(self.btn_import)
        
        self.btn_save = QPushButton("💾 儲存手動修改")
        self.btn_save.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        self.btn_save.clicked.connect(self.on_save_clicked)
        ctrl_layout.addWidget(self.btn_save)

        self.btn_lock = QPushButton("🔒 鎖定此區間 (禁止匯入)")
        self.btn_lock.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
        self.btn_lock.clicked.connect(self.on_lock_clicked)
        ctrl_layout.addWidget(self.btn_lock)

        # === [新增] 解除鎖定按鈕 ===
        self.btn_unlock = QPushButton("🔓 解除鎖定")
        self.btn_unlock.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
        self.btn_unlock.clicked.connect(self.on_unlock_clicked)
        ctrl_layout.addWidget(self.btn_unlock)

        layout.addLayout(ctrl_layout)

        # 警告提示
        layout.addWidget(QLabel("💡 提示：雙擊下方表格儲存格可直接修改班別。修改完畢請點擊「儲存手動修改」。"))

        # 資料表格
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

    def load_data(self):
        """讀取資料並顯示，邏輯與主畫面類似，但這裡允許編輯"""
        start_date = self.date_start.date().toString("yyyy-MM-dd")
        end_date = self.date_end.date().toString("yyyy-MM-dd")
        
        schedules = self.db.get_schedule_by_date_range(start_date, end_date)
        if not schedules:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        df = pd.DataFrame(schedules)
        self.pivot_df = df.pivot(index='emp_id', columns='date', values='shift_code').fillna('')

        self.table.setRowCount(len(self.pivot_df))
        self.table.setColumnCount(len(self.pivot_df.columns))
        self.table.setHorizontalHeaderLabels(self.pivot_df.columns)
        self.table.setVerticalHeaderLabels(self.pivot_df.index)

        for row_idx, emp_id in enumerate(self.pivot_df.index):
            for col_idx, date in enumerate(self.pivot_df.columns):
                val = self.pivot_df.at[emp_id, date]
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignCenter)
                # 在這裡我們不設定唯讀，讓使用者可以直接雙擊編輯
                self.table.setItem(row_idx, col_idx, item)

        self.table.horizontalHeader().setDefaultSectionSize(75)

    def on_import_clicked(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "選擇歷史班表 (Excel)", "", "Excel Files (*.xlsx)")
        if file_path:
            start_date = self.date_start.date().toString("yyyy-MM-dd")
            importer = DataImporter(self.db)
            success, msg = importer.import_historical_schedule(file_path, start_date)
            if success:
                QMessageBox.information(self, "匯入成功", msg)
                self.load_data()
            else:
                QMessageBox.critical(self, "匯入失敗", msg)

    def on_save_clicked(self):
        """走訪整個表格，將畫面上的修改寫回資料庫"""
        start_date = self.date_start.date().toString("yyyy-MM-dd")
        end_date = self.date_end.date().toString("yyyy-MM-dd")
        
        # 🛡️ 絕對阻斷機制：在進入任何迴圈前，第一時間攔截
        if self.db.is_period_locked(start_date, end_date):
            QMessageBox.critical(self, "🛑 拒絕寫入", 
                                 f"區間 ({start_date} 至 {end_date}) 已被鎖定！\n請先解除鎖定再進行手動修改。",
                                 QMessageBox.Ok)
            return  # 強制打斷，結束函式

        # 以下為正常的儲存迴圈 (僅在未鎖定時執行)
        records_to_update = []
        for row_idx, emp_id in enumerate(self.pivot_df.index):
            for col_idx, date in enumerate(self.pivot_df.columns):
                item = self.table.item(row_idx, col_idx)
                if item:
                    new_val = item.text().strip()
                    shift_code = new_val if new_val else None
                    records_to_update.append((emp_id, date, shift_code, 1))

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
        
        QMessageBox.information(self, "儲存成功", "✅ 您的手動修改已寫入資料庫！")
    def on_lock_clicked(self):
        start_date = self.date_start.date().toString("yyyy-MM-dd")
        end_date = self.date_end.date().toString("yyyy-MM-dd")
        
        reply = QMessageBox.question(self, "確認鎖定", 
                                     f"確定要鎖定 {start_date} 至 {end_date} 嗎？\n鎖定後將無法再匯入與覆蓋此區間之班表。", 
                                     QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            self.db.lock_period(start_date, end_date)
            QMessageBox.information(self, "鎖定成功", "區間已鎖定。")

    def on_unlock_clicked(self):
        start_date = self.date_start.date().toString("yyyy-MM-dd")
        end_date = self.date_end.date().toString("yyyy-MM-dd")
        
        reply = QMessageBox.question(self, "確認解除", 
                                     f"確定要解除 {start_date} 至 {end_date} 的鎖定嗎？\n解除後，該區間將允許重新匯入歷史班表，或被排班引擎覆蓋。", 
                                     QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            self.db.unlock_period(start_date, end_date)
            QMessageBox.information(self, "解除成功", f"{start_date} 至 {end_date} 已解除鎖定！")

    def on_reset_clicked(self):
        reply = QMessageBox.question(self, "危險操作確認", 
                                     "您確定要刪除並重置整個資料庫嗎？\n這將清空所有員工名單、歷史班表與鎖定紀錄，且無法復原！", 
                                     QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            try:
                # 1. 取得資料庫路徑並刪除實體檔案
                db_path = self.db.db_path
                if os.path.exists(db_path):
                    os.remove(db_path)
                
                # 2. 呼叫初始化，原地蓋回全新的空白資料表
                self.db.initialize_database()
                
                # 3. 清空畫面
                self.load_data()
                
                QMessageBox.information(self, "重置完成", "資料庫已徹底清空並重建。\n請記得回主畫面重新匯入「員工名單」。")
            
            except PermissionError:
                QMessageBox.critical(self, "權限錯誤", "資料庫檔案目前被系統佔用。\n請關閉程式後手動刪除，或稍後再試。")