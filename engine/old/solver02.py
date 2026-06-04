import pandas as pd
from ortools.sat.python import cp_model
from PySide6.QtCore import QSettings, QDate
from datetime import datetime, timedelta
from PySide6.QtWidgets import QMessageBox

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

        # ==========================================
        # 🧠 [核心升級 1] 自動對齊主畫面的 56 天絕對週期
        # ==========================================
        settings = QSettings("MyUnit", "ShiftScheduler")
        anchor_str = settings.value("audit_start_date", "")
        if not anchor_str:
            anchor_str = QDate.currentDate().addDays(-QDate.currentDate().day() + 1).toString("yyyy-MM-dd")
        
        anchor_date = datetime.strptime(anchor_str, '%Y-%m-%d')
        target_start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        target_end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        
        # 利用數學推算：當前排班日隸屬於哪個 56 天週期
        days_diff = (target_start_dt - anchor_date).days
        cycle_offset = days_diff // 56
        cycle_start_dt = anchor_date + timedelta(days=cycle_offset * 56)
        cycle_end_dt = cycle_start_dt + timedelta(days=55)
        
        cycle_start_str = cycle_start_dt.strftime('%Y-%m-%d')
        cycle_end_str = cycle_end_dt.strftime('%Y-%m-%d')

        # ==========================================
        # 🧠 [核心升級 2] 擴充引擎的「運算視野」
        # 取「目標排班區間」與「56 天法規週期」的聯集
        # ==========================================
        eval_start_dt = min(target_start_dt, cycle_start_dt)
        eval_end_dt = max(target_end_dt, cycle_end_dt)
        
        eval_dates = pd.date_range(start=eval_start_dt, end=eval_end_dt).strftime('%Y-%m-%d').tolist()
        target_dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        cycle_dates = pd.date_range(start=cycle_start_dt, end=cycle_end_dt).strftime('%Y-%m-%d').tolist()
        
        print(f"🔍 引擎啟動：目標區間 {start_date}~{end_date} | 法規週期 {cycle_start_str}~{cycle_end_str}")

        self._ensure_blank_grid(employees, eval_dates)

        # 撈取整個聯集視野的 DB 資料
        schedules = self.db.get_schedule_by_date_range(eval_dates[0], eval_dates[-1])
        dict_sched = {(s['emp_id'], s['date']): s for s in schedules}

        # ==========================================
        # 🛠️ [開發者專用] 引擎上帝視角 Debug 探針
        # ==========================================
        debug_msg = "🧠 引擎運算前參數攔截：\n\n"
        debug_msg += f"1️⃣ 從主畫面偷看的八週起始日：{anchor_str}\n"
        debug_msg += f"2️⃣ 推算出的 56 天絕對週期：\n     {cycle_start_str} 至 {cycle_end_str}\n\n"
        debug_msg += f"3️⃣ 該 56 天內，各員工已鎖定 (📌) 的 R 與 r 數量：\n"
        
        for emp_id in emp_ids:
            # 統計該 56 天週期內，已經被釘上圖釘的 R 與 r 數量
            emp_R = sum(1 for d in cycle_dates if dict_sched.get((emp_id, d)) and dict_sched.get((emp_id, d))['shift_code'] == 'R' and dict_sched.get((emp_id, d))['is_locked'] == 1)
            emp_r = sum(1 for d in cycle_dates if dict_sched.get((emp_id, d)) and dict_sched.get((emp_id, d))['shift_code'] == 'r' and dict_sched.get((emp_id, d))['is_locked'] == 1)
            
            # 視覺防呆：如果已經被手動排了超過 8 天，標記警告
            alert = " ⚠️(已破 8 天，引擎將無解!)" if emp_R > 8 or emp_r > 8 else ""
            debug_msg += f"   - {emp_id}: 例假(R) {emp_R} 天, 休息日(r) {emp_r} 天{alert}\n"
            
        debug_box = QMessageBox()
        debug_box.setWindowTitle("🛠️ 引擎 Debug 探針")
        debug_box.setText(debug_msg)
        # 使用等寬字體讓排版對齊
        debug_box.setStyleSheet("QLabel { font-size: 14px; font-family: Consolas; }") 
        debug_box.exec()
        # ==========================================

        # 往前撈 6 天歷史，供連班檢核使用
        history_start = (eval_start_dt - timedelta(days=6)).strftime('%Y-%m-%d')
        history_end = (eval_start_dt - timedelta(days=1)).strftime('%Y-%m-%d')
        history_schedules = self.db.get_schedule_by_date_range(history_start, history_end)
        dict_history = {(s['emp_id'], s['date']): s['shift_code'] for s in history_schedules}

        model = cp_model.CpModel()
        works = {}

        # 宣告變數 (涵蓋整個聯集視野)
        for emp_id in emp_ids:
            for date in eval_dates:
                for state in self.ALL_STATES:
                    works[(emp_id, date, state)] = model.NewBoolVar(f'w_{emp_id}_{date}_{state}')

        # 限制 1 & 2：狀態唯一性與動態圖釘鎖定
        for emp_id in emp_ids:
            for date in eval_dates:
                model.AddExactlyOne([works[(emp_id, date, state)] for state in self.ALL_STATES])
                record = dict_sched.get((emp_id, date))
                
                if date in target_dates:
                    # 📍 落在「真實想排班」的區間：遵從圖釘鎖定，且禁用 Train 與 日
                    if record and record['is_locked'] == 1 and record['shift_code'] in self.ALL_STATES:
                        model.Add(works[(emp_id, date, record['shift_code'])] == 1)
                    else:
                        model.Add(works[(emp_id, date, 'Train')] == 0)
                        model.Add(works[(emp_id, date, '日')] == 0)
                else:
                    # 📍 落在「模擬視野」的法規區間：
                    # 如果 DB 裡已經有值，當作常數鎖死；如果是空白，放手讓引擎「腦內模擬」排休以平衡法規
                    if record and record['shift_code'] in self.ALL_STATES:
                        model.Add(works[(emp_id, date, record['shift_code'])] == 1)

        # 限制 3：人數上下限 (只對「真實排班區間」生效，模擬區間不需管人數)
        for date in target_dates:
            for shift, (min_req, max_req) in self.SHIFT_DEMANDS.items():
                shift_vars = [works[(emp_id, date, shift)] for emp_id in emp_ids]
                model.Add(sum(shift_vars) >= min_req)
                model.Add(sum(shift_vars) <= max_req)

        # 限制 4：連班防呆 (針對所有視野日期)
        all_eval_dates = pd.date_range(start=history_start, end=eval_end_dt).strftime('%Y-%m-%d').tolist()
        for emp_id in emp_ids:
            for i in range(len(all_eval_dates) - 6):
                window_dates = all_eval_dates[i : i + 7]
                window_vars = []
                for d in window_dates:
                    if d < eval_dates[0]:
                        shift = dict_history.get((emp_id, d))
                        if shift in self.WORK_SHIFTS:
                            window_vars.append(1)
                    else:
                        for shift in self.WORK_SHIFTS:
                            window_vars.append(works[(emp_id, d, shift)])
                model.Add(sum(window_vars) <= 6)

        # ==========================================
        # 🧠 [核心升級 3] 絕對 8 週法規防線
        # ==========================================
        for emp_id in emp_ids:
            emp_r_vars = [works[(emp_id, d, 'r')] for d in cycle_dates]
            emp_R_vars = [works[(emp_id, d, 'R')] for d in cycle_dates]
            # 針對精準的 56 天週期，嚴格鎖死 8R 與 8r
            model.Add(sum(emp_r_vars) == 8)
            model.Add(sum(emp_R_vars) == 8)

        # 限制 6：L / P 額度 (只在 target_dates 內計算，因為手動配額是針對本次操作)
        for emp_id in emp_ids:
            l_target = leave_quotas.get(emp_id, {}).get('L', 0)
            p_target = leave_quotas.get(emp_id, {}).get('P', 0)
            emp_L_vars = [works[(emp_id, d, 'L')] for d in target_dates]
            emp_P_vars = [works[(emp_id, d, 'P')] for d in target_dates]
            model.Add(sum(emp_L_vars) == l_target)
            model.Add(sum(emp_P_vars) == p_target)

        # 🎯 目標函數：最大化出勤
        all_work_vars = [works[(emp_id, d, shift)] for emp_id in emp_ids for d in target_dates for shift in self.WORK_SHIFTS]
        model.Maximize(sum(all_work_vars))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 15.0 
        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            results_to_update = []
            for emp_id in emp_ids:
                # 💡 [核心升級 4] 寫回資料庫時，只擷取 target_dates，丟棄其餘的模擬紀錄
                for date in target_dates:
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
            return True, f"✅ 智能排班完成！已完美對齊您的八週基準 ({cycle_start_str} 至 {cycle_end_str}) 分配 8R 與 8r。"
        else:
            return False, f"❌ 求解失敗 (狀態碼: {status})。\n原因：您的請假配額或出勤下限，導致引擎無法在該 {cycle_start_str} 週期內湊滿合法的 8R 與 8r。"