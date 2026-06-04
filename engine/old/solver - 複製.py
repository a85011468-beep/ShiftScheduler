import pandas as pd
from ortools.sat.python import cp_model

class ScheduleEngine:
    def __init__(self, db_manager):
        self.db = db_manager
        self.ALL_STATES = ['01早M', '01午M', '01夜B1', 'L', 'Train', 'r']
        
        # 註：移除了 56 天的 Anchor Date，引擎現在完全動態

    def _ensure_blank_grid(self, employees, dates):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        blank_grid = []
        for emp in employees:
            for d in dates:
                blank_grid.append((emp['emp_id'], d, None, 0))
                
        cursor.executemany('''
            INSERT OR IGNORE INTO schedule (emp_id, date, shift_code, is_locked)
            VALUES (?, ?, ?, ?)
        ''', blank_grid)
        conn.commit()
        conn.close()

    def run_scheduler(self, start_date, end_date):
        """執行彈性排班：UI 傳什麼區間，就只算該區間"""
        employees = self.db.get_all_active_employees()
        if not employees:
            return False, "❌ 找不到員工資料，請先匯入名單。"

        emp_ids = [e['emp_id'] for e in employees]
        
        # 直接使用 UI 傳進來的精確日期
        dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        
        print(f"🧠 引擎啟動：精準鎖定運算區間 {dates[0]} ~ {dates[-1]}")

        # 鋪設空白網格
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

        # [限制 1] 每人每天只能有一種狀態
        for emp_id in emp_ids:
            for date in dates:
                model.AddExactlyOne([works[(emp_id, date, state)] for state in self.ALL_STATES])

        # [限制 2] 鎖定手動排定的假別與歷史資料
        for emp_id in emp_ids:
            for date in dates:
                record = dict_sched.get((emp_id, date))
                if record and record['is_locked'] == 1 and record['shift_code'] in self.ALL_STATES:
                    model.Add(works[(emp_id, date, record['shift_code'])] == 1)

        # [限制 3] 每日人力需求 (目前暫定為：每天至少 1 人上早班)
        # 後續的各班別人數上下限，都會寫在這裡
        for date in dates:
            model.Add(sum([works[(emp_id, date, '01早M')] for emp_id in emp_ids]) >= 1)

        # 求解與寫回
        solver = cp_model.CpSolver()
        solver.parameters.log_search_progress = False
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = 10.0 

        status = solver.Solve(model)

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
            
            return True, "✅ 引擎排班成功！請預覽下方班表。"
        else:
            return False, f"❌ 求解失敗 (狀態碼: {status})。請檢查排班條件是否與請假衝突。"