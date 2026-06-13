import pandas as pd
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QLabel, QTableWidget, QTableWidgetItem, QHeaderView, 
                               QFileDialog, QMessageBox)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from config.settings import EARLY_SHIFTS, NOON_SHIFTS, NIGHT_SHIFTS

class StatsDialog(QDialog):
    def __init__(self, db_manager, start_date, end_date, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"📊 週期班別統計分析 ({start_date} 至 {end_date})")
        self.resize(850, 600)
        self.db = db_manager
        self.start_date = start_date
        self.end_date = end_date
        
        # 定義需要統計的目標班別
        self.c_shifts = ['01早B1c', '01早B2c', '01午B1c', '01午B2c']
        self.day_shifts = EARLY_SHIFTS + NOON_SHIFTS
        
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # 頂部資訊與控制列
        top_layout = QHBoxLayout()
        info_label = QLabel(f"<b>統計區間：</b> {self.start_date} 至 {self.end_date}<br>"
                            f"<span style='color: #757575;'>提示：非該職級之專屬統計項目將以「-」表示。早午班統計已包含 C 班。</span>")
        top_layout.addWidget(info_label)
        top_layout.addStretch()
        
        self.btn_export = QPushButton("💾 匯出統計 Excel")
        self.btn_export.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; height: 35px; padding: 0 15px;")
        self.btn_export.clicked.connect(self.export_to_excel)
        top_layout.addWidget(self.btn_export)
        
        layout.addLayout(top_layout)

        # 統計表格
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "員工編號", "姓名", "職級", "中A班總數", 
            "主管夜班 (M/Chief)", "基層 C班 (Normal)", "基層早午班 (Normal)"
        ])
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableWidget { font-size: 14px; alternate-background-color: #f9f9f9; }")
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setStyleSheet("QHeaderView::section { background-color: #E0E0E0; font-weight: bold; padding: 5px; }")

        layout.addWidget(self.table)

    def load_data(self):
        # 1. 取得員工基底資料
        employees = self.db.get_all_active_employees()
        if not employees:
            return
            
        stats = {}
        for e in employees:
            eid = str(e['emp_id']).strip()
            stats[eid] = {
                'name': e['name'],
                'level': str(e['job_level']).strip(),
                'mid_a': 0,
                'm_night': 0,
                'normal_c': 0,
                'normal_day': 0
            }

        # 2. 取得區間內所有排班紀錄
        schedules = self.db.get_schedule_by_date_range(self.start_date, self.end_date)
        
        # 3. 遍歷排班紀錄進行統計
        for row in schedules:
            eid = str(row['emp_id']).strip()
            shift = row['shift_code']
            if not shift or eid not in stats:
                continue
                
            level = stats[eid]['level']
            
            # 統計：中A班 (所有人)
            if shift == '01中A':
                stats[eid]['mid_a'] += 1
                
            # 統計：主管夜班
            if level in ['M', 'Chief'] and shift in NIGHT_SHIFTS:
                stats[eid]['m_night'] += 1
                
            # 統計：基層 C 班 與 早午班
            if level == 'Normal':
                if shift in self.c_shifts:
                    stats[eid]['normal_c'] += 1
                if shift in self.day_shifts:
                    stats[eid]['normal_day'] += 1

        # 4. 排序並寫入表格
        # 排序邏輯：職級 (Chief -> M -> Normal) -> 員編
        def sort_key(item):
            lvl = item[1]['level']
            lvl_order = 0 if lvl == 'Chief' else (1 if lvl == 'M' else 2)
            return (lvl_order, item[0])
            
        sorted_stats = sorted(stats.items(), key=sort_key)
        
        self.table.setRowCount(len(sorted_stats))
        for row_idx, (eid, data) in enumerate(sorted_stats):
            lvl = data['level']
            is_m = lvl in ['M', 'Chief']
            
            # 定義顯示文字 (非該職級顯示為 "-")
            str_mid_a = str(data['mid_a'])
            str_m_night = str(data['m_night']) if is_m else "-"
            str_norm_c = str(data['normal_c']) if not is_m else "-"
            str_norm_day = str(data['normal_day']) if not is_m else "-"
            
            row_data = [eid, data['name'], lvl, str_mid_a, str_m_night, str_norm_c, str_norm_day]
            
            for col_idx, text in enumerate(row_data):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable) # 唯讀
                
                # 簡單上色：如果是 "-" 就變灰色
                if text == "-":
                    item.setForeground(QColor("#BDBDBD"))
                elif col_idx >= 3 and int(text) > 0:
                    # 數字大於 0 加粗凸顯
                    font = QFont()
                    font.setBold(True)
                    item.setFont(font)
                    if col_idx == 3: item.setForeground(QColor("#E65100")) # 中A亮橘
                    if col_idx == 4: item.setForeground(QColor("#311B92")) # 主管夜班深紫
                    if col_idx == 5: item.setForeground(QColor("#B71C1C")) # C班深紅
                    if col_idx == 6: item.setForeground(QColor("#0D47A1")) # 早午深藍
                
                self.table.setItem(row_idx, col_idx, item)

    def export_to_excel(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "匯出統計表", f"班別統計_{self.start_date}_至_{self.end_date}.xlsx", "Excel Files (*.xlsx)")
        if not file_path:
            return
            
        try:
            # 抓取表格資料轉 pandas DataFrame
            rows = self.table.rowCount()
            cols = self.table.columnCount()
            data = []
            headers = [self.table.horizontalHeaderItem(i).text() for i in range(cols)]
            
            for r in range(rows):
                row_data = [self.table.item(r, c).text() for c in range(cols)]
                data.append(row_data)
                
            df = pd.DataFrame(data, columns=headers)
            df.to_excel(file_path, index=False)
            QMessageBox.information(self, "匯出成功", f"統計資料已成功儲存至：\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "匯出失敗", f"發生錯誤：\n{str(e)}")