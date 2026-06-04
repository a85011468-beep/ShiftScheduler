import pandas as pd
from ortools.sat.python import cp_model

class ScheduleEngine:
    def __init__(self, db_manager):
        self.db = db_manager
        
        self.ALL_STATES = [
            '01早M', '01早m', '01午M', '01午m', 
            '01早B1', '01早B2', '01午B1', '01午B2', 
            '01中A', '01夜B1', '01夜B2', 
            'L', 'P', 'Train', '日', 'r', 'R'
        ]
        
        self.WORK_SHIFTS = [
            '01早M', '01早m', '01午M', '01午m', 
            '01早B1', '01早B2', '01午B1', '01午B2', 
            '01中A', '01夜B1', '01夜B2', 
            'Train', '日'
        ]
        
        self.OFF_SHIFTS = ['L', 'P', 'r', 'R']

        self.SHIFT_DEMANDS = {
            '01早M': (1, 1), '01早m': (0, 1),
            '01午M': (1, 1), '01午m': (0, 1),
            '01早B1': (1, 2), '01早B2': (1, 2),
            '01午B1': (1, 2), '01午B2': (1, 2),
            '01中A': (0, 1),
            '01夜B1': (1, 1), '01夜B2': (1, 1)
        }

    def _ensure_blank_grid(self, employees, dates):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        blank_grid = [(emp['emp_id'], d, None, 0) for emp in employees for d in dates]
        cursor.executemany('''
            INSERT OR IGNORE INTO schedule (emp_id, date, shift_code, is_locked)
            VALUES (?, ?, ?, ?)
        ''', blank_grid)
        conn.commit()
        conn.close()

    def run_scheduler(self, start_date, end_date, leave_quotas=None):
        if leave_quotas is None: leave_quotas = {}
        
        employees = self.db.get_all_active_employees()
        if not employees:
            return False, "❌ 找不到員工資料，請先匯入名單。"

        emp_ids = [e['emp_id'] for e in employees]
        dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        
        self._ensure_blank_grid(employees, dates)

        # 1. 取得當前運算區間資料
        schedules = self.db.get_schedule_by_date_range(start_date, end_date)
        dict_sched = {(s['emp_id'], s['date']): s for s in schedules}

        # 💡 [核心升級] 2. 往前撈取 6 天的歷史紀錄，作為跨期防護罩的判斷依據
        history_start = (pd.to_datetime(start_date) - pd.Timedelta(days=6)).strftime('%Y-%m-%d')
        history_end = (pd.to_datetime(start_date) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        history_schedules = self.db.get_schedule_by_date_range(history_start, history_end)
        dict_history = {(s['emp_id'], s['date']): s['shift_code'] for s in history_schedules}

        model = cp_model.CpModel()
        works = {}

        for emp_id in emp_ids:
            for date in dates:
                for state in self.ALL_STATES:
                    works[(emp_id, date, state)] = model.NewBoolVar(f'w_{emp_id}_{date}_{state}')

        # ⚖️ 限制 1 & 2：單一狀態與圖釘鎖定防線
        for emp_id in emp_ids:
            for date in dates:
                model.AddExactlyOne([works[(emp_id, date, state)] for state in self.ALL_STATES])
                record = dict_sched.get((emp_id, date))
                if record and record['is_locked'] == 1 and record['shift_code'] in self.ALL_STATES:
                    model.Add(works[(emp_id, date, record['shift_code'])] == 1)
                else:
                    model.Add(works[(emp_id, date, 'Train')] == 0)
                    model.Add(works[(emp_id, date, '日')] == 0)

        # ⚖️ 限制 3：各班別每日人數上下限
        for date in dates:
            for shift, (min_req, max_req) in self.SHIFT_DEMANDS.items():
                shift_vars = [works[(emp_id, date, shift)] for emp_id in emp_ids]
                model.Add(sum(shift_vars) >= min_req)
                model.Add(sum(shift_vars) <= max_req)

        # ⚖️ 限制 4：【升級版】勞基法 - 結合歷史邊界的滑動視窗防呆
        # 建立一條涵蓋「過去 6 天 + 未來區間」的時間軸
        all_eval_dates = pd.date_range(start=history_start, end=end_date).strftime('%Y-%m-%d').tolist()
        
        for emp_id in emp_ids:
            for i in range(len(all_eval_dates) - 6):
                window_dates = all_eval_dates[i : i + 7] # 掃描任意連續 7 天
                window_vars = []
                
                for d in window_dates:
                    if d < start_date:
                        # [歷史區間] 直接讀取 DB，若查到是上班班別，就當作常數 1 塞進去
                        shift = dict_history.get((emp_id, d))
                        if shift in self.WORK_SHIFTS:
                            window_vars.append(1)
                    else:
                        # [未來區間] 放入 OR-Tools 的未知決策變數
                        for shift in self.WORK_SHIFTS:
                            window_vars.append(works[(emp_id, d, shift)])
                            
                # 無論這 7 天是由「純歷史」、「歷史+未來」還是「純未來」組成，上班總數都 <= 6
                model.Add(sum(window_vars) <= 6)

        # ⚖️ 限制 5：例假日 R 與 休息日 r 數量比例分配
        target_r = round(len(dates) * 8 / 56)
        target_R = round(len(dates) * 8 / 56)
        for emp_id in emp_ids:
            emp_r_vars = [works[(emp_id, d, 'r')] for d in dates]
            emp_R_vars = [works[(emp_id, d, 'R')] for d in dates]
            model.Add(sum(emp_r_vars) == target_r)
            model.Add(sum(emp_R_vars) == target_R)

        # ⚖️ 限制 6：特休 (L) 與事假 (P) 手動配額
        for emp_id in emp_ids:
            l_target = leave_quotas.get(emp_id, {}).get('L', 0)
            p_target = leave_quotas.get(emp_id, {}).get('P', 0)
            emp_L_vars = [works[(emp_id, d, 'L')] for d in dates]
            emp_P_vars = [works[(emp_id, d, 'P')] for d in dates]
            model.Add(sum(emp_L_vars) == l_target)
            model.Add(sum(emp_P_vars) == p_target)

        # 🎯 目標函數：最大化出勤總人數
        all_work_vars = [works[(emp_id, d, shift)] for emp_id in emp_ids for d in dates for shift in self.WORK_SHIFTS]
        model.Maximize(sum(all_work_vars))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 15.0 
        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            results_to_update = []
            for emp_id in emp_ids:
                for date in dates:
                    for state in self.ALL_STATES:
                        if solver.Value(works[(emp_id, date, state)]) == 1:
                            record = dict_sched.get((emp_id, date))
                            if not record or record['is_locked'] == 0:
                                results_to_update.append((state, emp_id, date))

            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.executemany('''
                UPDATE schedule SET shift_code = ? WHERE emp_id = ? AND date = ? AND is_locked = 0
            ''', results_to_update)
            conn.commit()
            conn.close()
            return True, f"✅ 智能排班完成！已掃描歷史邊界，確保跨期連班合法。"
        else:
            return False, f"❌ 求解失敗 (狀態碼: {status})。\n原因：極度可能是歷史資料月底已經連上多天，導致月初引擎無處安插班別而崩潰。"