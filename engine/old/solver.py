import pandas as pd
from ortools.sat.python import cp_model

class ScheduleEngine:
    def __init__(self, db_manager):
        self.db = db_manager
        self.ALL_STATES = ['01早M', '01午M', '01夜B1', 'L', 'Train', 'r']
        
        # 定義班別屬性 (非常重要！這是計算勞基法的基礎)
        self.WORK_SHIFTS = ['01早M', '01午M', '01夜B1', 'Train']  # 凡是有出勤都算
        self.OFF_SHIFTS = ['L', 'r']                             # 特休與排休
        
        # 八週變形工時的基準日 (用來推算 56 天的週期)
        self.ANCHOR_DATE = pd.to_datetime('2026-01-05') # 假設某個八週的起點 (週一)
    def _ensure_blank_grid(self, employees, dates):
        """內部方法：確保指定區間內，所有員工在資料庫裡都有格子存在"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        blank_grid = []
        for emp in employees:
            for d in dates:
                blank_grid.append((emp['emp_id'], d, None, 0))
                
        # 使用 INSERT OR IGNORE 確保不覆蓋已存在的歷史資料或請假鎖定
        cursor.executemany('''
            INSERT OR IGNORE INTO schedule (emp_id, date, shift_code, is_locked)
            VALUES (?, ?, ?, ?)
        ''', blank_grid)
        conn.commit()
        conn.close()

    def run_scheduler(self, ui_start_date, ui_end_date):
        employees = self.db.get_all_active_employees()
        if not employees:
            return False, "❌ 找不到員工資料，請先匯入名單。"

        emp_ids = [e['emp_id'] for e in employees]

        # ==========================================
        # 🗓️ 核心邏輯 1：推算真正的「八週運算區間 (56天)」
        # ==========================================
        target_date = pd.to_datetime(ui_start_date)
        delta_days = (target_date - self.ANCHOR_DATE).days
        cycle_index = delta_days // 56
        
        cycle_start = self.ANCHOR_DATE + pd.Timedelta(days=cycle_index * 56)
        cycle_end = cycle_start + pd.Timedelta(days=55)
        
        # 引擎強制覆蓋日期為這 56 天
        dates = pd.date_range(start=cycle_start, end=cycle_end).strftime('%Y-%m-%d').tolist()
        
        print(f"🧠 引擎啟動：UI 請求區間 {ui_start_date} ~ {ui_end_date}")
        print(f"🧠 引擎擴張：實際鎖定八週週期 {dates[0]} ~ {dates[-1]}")

        # 鋪設空白網格 (針對這 56 天)
        self._ensure_blank_grid(employees, dates)

        schedules = self.db.get_schedule_by_date_range(dates[0], dates[-1])
        dict_sched = {(s['emp_id'], s['date']): s for s in schedules}

        model = cp_model.CpModel()
        works = {}

        # [宣告變數]
        for emp_id in emp_ids:
            for date in dates:
                for state in self.ALL_STATES:
                    works[(emp_id, date, state)] = model.NewBoolVar(f'w_{emp_id}_{date}_{state}')

        # [基礎限制 1] 每人每天只能有一種狀態
        for emp_id in emp_ids:
            for date in dates:
                model.AddExactlyOne([works[(emp_id, date, state)] for state in self.ALL_STATES])

        # [基礎限制 2] 鎖定手動排定的假別
        for emp_id in emp_ids:
            for date in dates:
                record = dict_sched.get((emp_id, date))
                if record and record['is_locked'] == 1 and record['shift_code'] in self.ALL_STATES:
                    model.Add(works[(emp_id, date, record['shift_code'])] == 1)

        # ==========================================
        # ⚖️ 勞基法鐵律 1：八週內休假總天數必須達標
        # ==========================================
        # 依據台灣勞基法八週變形：8個例假 + 8個休息日 = 16 天休假 (若有國定假日需另加)
        TARGET_OFF_DAYS = 16 
        
        for emp_id in emp_ids:
            off_vars = []
            for date in dates:
                for state in self.OFF_SHIFTS:
                    off_vars.append(works[(emp_id, date, state)])
            
            # 限制這 56 天內的休假總數「必須等於」(或大於等於) 16 天
            model.Add(sum(off_vars) >= TARGET_OFF_DAYS)

        # ==========================================
        # ⚖️ 勞基法鐵律 2：滑動視窗 (Sliding Window) 嚴禁連上七天
        # ==========================================
        # 邏輯：在任意連續的 7 天內，上班的天數總和絕對不可以超過 6 天。
        for emp_id in emp_ids:
            for i in range(len(dates) - 6):  # 走訪每一個「起點」
                window_dates = dates[i : i + 7]  # 抓出連續 7 天
                
                work_vars = []
                for d in window_dates:
                    for s in self.WORK_SHIFTS:
                        work_vars.append(works[(emp_id, d, s)])
                
                # 這 7 天內的上班變數總和 <= 6
                model.Add(sum(work_vars) <= 6)
        
        # [基礎限制 3] 每日需求：至少 1 人上早班
        for date in dates:
            model.Add(sum([works[(emp_id, date, '01早M')] for emp_id in emp_ids]) >= 1)

        # 3. 設定求解器並運算
        solver = cp_model.CpSolver()
        solver.parameters.log_search_progress = False
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = 10.0 

        status = solver.Solve(model)

        # 4. 處理結果並寫回資料庫
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            results_to_update = []
            for emp_id in emp_ids:
                for date in dates:
                    for state in self.ALL_STATES:
                        if solver.Value(works[(emp_id, date, state)]) == 1:
                            record = dict_sched.get((emp_id, date))
                            # 雙重保險：只覆蓋未被鎖定的資料
                            if not record or record['is_locked'] == 0:
                                results_to_update.append((state, emp_id, date))

            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.executemany('''
                UPDATE schedule 
                SET shift_code = ? 
                WHERE emp_id = ? AND date = ? AND is_locked = 0
            ''', results_to_update)
            conn.commit()
            conn.close()
            
            return True, "✅ 引擎排班成功！已更新至資料庫。"
        else:
            return False, f"❌ 求解失敗 (狀態碼: {status})。請檢查排班條件是否與請假衝突。"