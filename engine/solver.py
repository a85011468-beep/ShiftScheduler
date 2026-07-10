import pandas as pd
from ortools.sat.python import cp_model
from datetime import datetime, timedelta
from PySide6.QtWidgets import QMessageBox

from config.settings import (ALL_STATES, WORK_SHIFTS, OFF_SHIFTS, SHIFT_DEMANDS, MANAGER_ONLY_SHIFTS, 
                             EARLY_SHIFTS, NOON_SHIFTS, NIGHT_SHIFTS,
                             FORBIDDEN_AFTER_NOON, FORBIDDEN_AFTER_NIGHT,
                             LOCATION_G1, LOCATION_G2) # 💡 匯入地點分組

class ScheduleEngine:
    def __init__(self, db_manager):
        self.db = db_manager
        self.current_solver = None # 用於綁定目前的求解器
        self.is_cancelled = False  # 中斷訊號標記

    def cancel(self):
        """觸發中斷訊號，立刻停止 C++ 底層運算"""
        self.is_cancelled = True
        if self.current_solver:
            self.current_solver.StopSearch()

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

    def _run_pre_flight_diagnostics(self, emp_ids, eval_dates, dict_sched, dict_history, job_levels, shift_prefs, leave_quotas,night_dates, dict_sched_overall, split_date=None):
        diagnostics = []
        
        # 1. 建立全局鎖定看板 (合併歷史紀錄與未來的圖釘)
        locked_board = {}
        for (emp_id, d), shift in dict_history.items():
            locked_board[(emp_id, d)] = shift
        for (emp_id, d), record in dict_sched.items():
            if record['is_locked'] == 1:
                locked_board[(emp_id, d)] = record['shift_code']
                
        all_eval_dates = sorted(list(set([d for _, d in locked_board.keys()] + eval_dates)))

        # ==========================================
        # 🛡️ 檢核 1：最大人數上限 (max_req) 衝突
        # ==========================================
        for date in eval_dates:
            daily_counts = {}
            for emp_id in emp_ids:
                shift = locked_board.get((emp_id, date))
                if shift:
                    daily_counts[shift] = daily_counts.get(shift, 0) + 1
            for shift, count in daily_counts.items():
                if shift in SHIFT_DEMANDS:
                    max_req = SHIFT_DEMANDS[shift][1]
                    if count > max_req:
                        diagnostics.append(f"[{date}] 「{shift}」被人工圖釘鎖定 {count} 人，已超過每日需求上限 {max_req} 人。")

        # ==========================================
        # 🛡️ 檢核 2：交接班時序衝突 (午接早、夜接早)
        # ==========================================
        for emp_id in emp_ids:
            eid = str(emp_id).strip()
            for i in range(len(all_eval_dates) - 1):
                today = all_eval_dates[i]
                tmr = all_eval_dates[i+1]
                if tmr not in eval_dates: continue # 只檢核會影響未來排班的邊界
                
                shift_today = locked_board.get((emp_id, today))
                shift_tmr = locked_board.get((emp_id, tmr))
                if shift_today and shift_tmr:
                    if shift_today in NOON_SHIFTS and shift_tmr in FORBIDDEN_AFTER_NOON:
                        diagnostics.append(f"[{today}跨{tmr}] {eid} 圖釘違規：午班不可接早班。")
                    if shift_today in NIGHT_SHIFTS and shift_tmr in FORBIDDEN_AFTER_NIGHT:
                        diagnostics.append(f"[{today}跨{tmr}] {eid} 圖釘違規：夜班下莊不可接早午班。")

        # ==========================================
        # 🛡️ 檢核 3：七休一連班違規
        # ==========================================
        for emp_id in emp_ids:
            eid = str(emp_id).strip()
            for i in range(len(all_eval_dates) - 6):
                window = [all_eval_dates[i+j] for j in range(7)]
                if not any(d in eval_dates for d in window): continue
                
                locked_work = sum(1 for d in window if locked_board.get((emp_id, d)) in WORK_SHIFTS or locked_board.get((emp_id, d)) in ['L', 'P'])
                if locked_work == 7:
                    diagnostics.append(f"[{window[0]}至{window[-1]}] {eid} 被連續圖釘鎖死 7 天無休息日，違反七休一。")

        # ==========================================
        # 🛡️ 檢核 4 & 5：自訂週期 R 假防線與配額死結 (取代自然週)
        # ==========================================
        for emp_id in emp_ids:
            eid = str(emp_id).strip()
            required_Rs = 0
            
            # 💡 依使用者需求：從排班第一天起，每 7 天切一個固定區塊
            for i in range(0, len(eval_dates), 7):
                chunk_dates = eval_dates[i:i+7]
                
                # 檢核 4：單一區塊被人工釘了超過 1 個 R
                locked_Rs = sum(1 for d in chunk_dates if locked_board.get((emp_id, d)) == 'R')
                if locked_Rs > 1:
                    diagnostics.append(f"[{chunk_dates[0]} 至 {chunk_dates[-1]}] {eid} 該區間被鎖定了 {locked_Rs} 天例假(R)，違反「每區間僅能1例」規定。")
                
                # 計算引擎在這區塊「被迫」一定要排幾個 R
                if len(chunk_dates) == 7:
                    required_Rs += 1

            # 檢核 5：QSpinBox 總配額不足以應付法律底線
            #q = leave_quotas.get(eid, {})
            #r1 = int(q.get('R', 0))
            #r2 = int(q.get('R2', 0))
            #user_R_quota = r1 + r2
            
            #if user_R_quota < required_Rs:
            #    diagnostics.append(f"[配額死結] {eid} 依 7 天區塊劃分，必須排 {required_Rs} 天例假(R)。但系統讀到的設定配額僅 {user_R_quota} 天。請確認 QSpinBox 總和。")
        # ==========================================
        # 🛡️ 檢核 6：圖釘數量是否大於 QSpinBox 額度 (回歸單一區間)
        # ==========================================
        check_shifts = ['L', 'P', 'r', 'O']
        for emp_id in emp_ids:
            eid = str(emp_id).strip()
            q = leave_quotas.get(eid, {})
            
            for shift in check_shifts:
                locked_all = sum(1 for d in eval_dates if locked_board.get((emp_id, d)) == shift)
                quota_all = int(q.get(shift, 0))
                if locked_all > quota_all:
                    diagnostics.append(f"[額度死結] {eid} 期間內被圖釘釘了 {locked_all} 天 {shift}，但 QSpinBox 額度僅給予 {quota_all}。")

        # 👇 💡 [新增] 檢核 7：夜班 QSpinBox 總量防呆
        night_eval_dates = [d for d in eval_dates if d in night_dates]
        night_outside_dates = [d for d in night_dates if d not in eval_dates]
        
        total_night_quota = sum(int(leave_quotas.get(str(eid).strip(), {}).get('夜', 0)) for eid in emp_ids)
        
        # 計算夜班參照區間內，落在「非運算區」且已經是夜班的人數
        already_night_total = 0
        for d in night_outside_dates:
            for emp_id in emp_ids:
                record = dict_sched_overall.get((emp_id, d))
                if record and record['shift_code'] in ('01夜B1', '01夜B2'):
                    already_night_total += 1

        from config.settings import SHIFT_DEMANDS
        req_night_per_day = SHIFT_DEMANDS.get('01夜B1', [0,0])[0] + SHIFT_DEMANDS.get('01夜B2', [0,0])[0]
        # 只算主運算區間且涵蓋在參照區間內的日子需求
        min_night_needed = req_night_per_day * len(night_eval_dates)
        
        if total_night_quota - already_night_total < min_night_needed:
            diagnostics.append(f"[配額死結] 扣除參照區間內已定案之夜班後，剩餘夜班配額 ({total_night_quota - already_night_total}) 不足以填滿運算區間所需夜班數 ({min_night_needed})！")

        return list(set(diagnostics))


    def run_scheduler(self, start_date, end_date, night_start_date, night_end_date, leave_quotas=None, debug_mode=False, rule_config=None):
        if leave_quotas is None: leave_quotas = {}
        if rule_config is None: rule_config = {}  # 確保預設為空字典防呆
        
        employees = self.db.get_all_active_employees()
        if not employees:
            return False, "❌ 找不到員工資料，請先匯入名單。"

        emp_ids = [e['emp_id'] for e in employees]
        job_levels = {str(e['emp_id']).strip(): str(e['job_level']).strip() for e in employees}
        shift_prefs = {str(e['emp_id']).strip(): str(e.get('shift_pref', 'MIX')).strip() for e in employees}
        block_prefs = {str(e['emp_id']).strip(): str(e.get('block_pref', 'ANY')).strip() for e in employees}
        # 💡 [新增] 讀取夜班分段意願 (設定值可為 '1', '2', '3', 或 'ANY')
        night_seg_prefs = {str(e['emp_id']).strip(): str(e.get('night_seg_pref', 'ANY')).strip() for e in employees}
    
        target_dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        eval_dates = target_dates 
        night_dates = pd.date_range(start=night_start_date, end=night_end_date).strftime('%Y-%m-%d').tolist()

        # 求出涵蓋主運算與夜班的整體範圍，以利調閱全域排班
        overall_start = min(start_date, night_start_date)
        overall_end = max(end_date, night_end_date) 
        
        self._ensure_blank_grid(employees, eval_dates)

        schedules = self.db.get_schedule_by_date_range(eval_dates[0], eval_dates[-1])
        dict_sched = {(s['emp_id'], s['date']): s for s in schedules}

        # 💡 調閱涵蓋兩者的整體班表
        overall_schedules = self.db.get_schedule_by_date_range(overall_start, overall_end)
        dict_sched_overall = {(s['emp_id'], s['date']): s for s in overall_schedules}

        eval_start_dt = datetime.strptime(eval_dates[0], '%Y-%m-%d')
        history_start = (eval_start_dt - timedelta(days=7)).strftime('%Y-%m-%d')
        history_end = (eval_start_dt - timedelta(days=1)).strftime('%Y-%m-%d')
        history_schedules = self.db.get_schedule_by_date_range(history_start, history_end)
        dict_history = {(s['emp_id'], s['date']): s['shift_code'] for s in history_schedules}

        # =========================================================================
        # 🚀 啟動硬規則飛行前安檢 (Pre-flight Diagnostics)
        # =========================================================================
        print("🔍 啟動硬規則飛行前安檢...")
        hard_conflicts = self._run_pre_flight_diagnostics(
            emp_ids, eval_dates, dict_sched, dict_history, job_levels, shift_prefs, leave_quotas, night_dates, dict_sched_overall
            )
        
        if hard_conflicts:
            error_msg = "❌ 引擎安檢未通過！偵測到無法解開的「硬規則死結」：\n\n"
            error_msg += "👉 " + "\n👉 ".join(hard_conflicts[:8])
            if len(hard_conflicts) > 8:
                error_msg += f"\n\n...等共 {len(hard_conflicts)} 項衝突。"
            error_msg += "\n\n💡 引擎已被攔截保護。請至排班面板調整「特休配額」或解除衝突的「圖釘鎖定」。"
            return False, error_msg

        def attempt_solve(strict_time_rules=True, strict_quotas=rule_config.get('strict_quotas', True)):
            model = cp_model.CpModel()
            works = {}

            for emp_id in emp_ids:
                for date in eval_dates:
                    for state in ALL_STATES:
                        works[(emp_id, date, state)] = model.NewBoolVar(f'w_{emp_id}_{date}_{state}')

            # 1. 職級、圖釘與意願鎖死
            for emp_id in emp_ids:
                is_manager = job_levels.get(str(emp_id).strip(), 'Normal') in ('M', 'Chief', 't')
                s_pref = shift_prefs.get(str(emp_id).strip(), 'MIX')
                
                for date in eval_dates:
                    model.AddExactlyOne([works[(emp_id, date, state)] for state in ALL_STATES])
                    record = dict_sched.get((emp_id, date))
                    is_locked = (record and record['is_locked'] == 1 and record['shift_code'] in ALL_STATES)
                    
                    if is_locked:
                        model.Add(works[(emp_id, date, record['shift_code'])] == 1)
                    else:
                        model.Add(works[(emp_id, date, 'Train')] == 0)
                        # 💡 [解鎖] 將 '日' 班開放，作為吸收過剩人力的「溢流待命椅」
                        # model.Add(works[(emp_id, date, '日')] == 0)
                        model.Add(works[(emp_id, date, '01早')] == 0)
                        model.Add(works[(emp_id, date, '01午')] == 0)
                        model.Add(works[(emp_id, date, '01夜')] == 0)
                        
                        if not is_manager:
                            for m_shift in MANAGER_ONLY_SHIFTS:
                                model.Add(works[(emp_id, date, m_shift)] == 0)
                                
                        if s_pref == 'NIGHT_ONLY':
                            for w_shift in WORK_SHIFTS:
                                if w_shift not in NIGHT_SHIFTS:
                                    model.Add(works[(emp_id, date, w_shift)] == 0)

            # =========================================================================
            # 2. 每日需求、複合戰力 與 🚑 虛擬人力 (Slack Variables)
            # =========================================================================
            virtual_penalties = []
            virtual_vars_dict = {} # 記錄虛擬人力使用了多少
            
            for date in eval_dates:
                for shift, (min_req, max_req) in SHIFT_DEMANDS.items():
                    shift_vars = [works[(emp_id, date, shift)] for emp_id in emp_ids]
                    
                    if rule_config.get('hard_demands', True):
                        slack_min = model.NewIntVar(0, min_req, f'slack_min_{date}_{shift}')
                        model.Add(sum(shift_vars) + slack_min >= min_req)
                        model.Add(sum(shift_vars) <= max_req)
                        
                        virtual_penalties.append(slack_min * 1000000)
                        virtual_vars_dict[(date, shift)] = slack_min

                early_combo_vars = [works[(emp_id, date, '01早B2')] for emp_id in emp_ids] + \
                                   [works[(emp_id, date, '01早B2c')] for emp_id in emp_ids] + \
                                   [works[(emp_id, date, '01早m')] for emp_id in emp_ids]
                if rule_config.get('hard_demands', True):
                    slack_early = model.NewIntVar(0, 2, f'slack_early_{date}')
                    model.Add(sum(early_combo_vars) + slack_early >= 2)
                    virtual_penalties.append(slack_early * 1000000)
                    virtual_vars_dict[(date, '早班複合戰力')] = slack_early

                noon_combo_vars = [works[(emp_id, date, '01午B2')] for emp_id in emp_ids] + \
                                  [works[(emp_id, date, '01午B2c')] for emp_id in emp_ids] + \
                                  [works[(emp_id, date, '01午m')] for emp_id in emp_ids]
                if rule_config.get('hard_demands', True):
                    slack_noon = model.NewIntVar(0, 2, f'slack_noon_{date}')
                    model.Add(sum(noon_combo_vars) + slack_noon >= 2)
                    virtual_penalties.append(slack_noon * 1000000)
                    virtual_vars_dict[(date, '午班複合戰力')] = slack_noon

            # =========================================================================
            # 2.5 🚑 加班 (O) 轉換邏輯：將指定的加班額度強制指派為特定工作班別
            # =========================================================================
            is_overtime_vars = {}
            for emp_id in emp_ids:
                eid = str(emp_id).strip()
                s_pref = shift_prefs.get(eid, 'MIX')
                
                # 💡 判斷加班允許的班別 (Night_only 特例處理)
                if s_pref == 'NIGHT_ONLY':
                    allowed_ot_shifts = ['01夜B1', '01夜B2']
                else:
                    allowed_ot_shifts = ['01早B1', '01午B1', '01早B2', '01午B2', '01中A', '01早m', '01午m']
                
                for d in eval_dates:
                    is_ot = model.NewBoolVar(f'is_ot_{eid}_{d}')
                    is_overtime_vars[(emp_id, d)] = is_ot
                    
                    # 篩選出該員工確實能上的允許班別
                    allowed_vars = [works[(emp_id, d, s)] for s in allowed_ot_shifts if (emp_id, d, s) in works]
                    
                    if allowed_vars:
                        # 💡 如果這天被選為「加班日」(is_ot == 1)，那當天班別必須是上述允許的其中之一
                        model.Add(sum(allowed_vars) == 1).OnlyEnforceIf(is_ot)
                    else:
                        model.Add(is_ot == 0)

            # =========================================================================
            # 3. 測試變因宇宙 A：QSpinBox 絕對鎖死 (硬限制開關)
            # =========================================================================
            if strict_quotas:
                for emp_id in emp_ids:
                    eid = str(emp_id).strip()
                    q = leave_quotas.get(eid, {})
                    
                    # 💡 徹底刪除所有 split_date 判斷，回歸最單純的單一區間加總
                    model.Add(sum(works[(emp_id, d, 'L')] for d in eval_dates) == q.get('L', 0))
                    model.Add(sum(works[(emp_id, d, 'P')] for d in eval_dates) == q.get('P', 0))
                    model.Add(sum(works[(emp_id, d, 'r')] for d in eval_dates) == q.get('r', 0))
                   #model.Add(sum(works[(emp_id, d, 'R')] for d in eval_dates) == q.get('R', 0))
                    model.Add(sum(is_overtime_vars[(emp_id, d)] for d in eval_dates) == q.get('O', 0))
                    # 👇 💡 [修改] 夜班總數鎖死：從「參照區間 (night_dates)」計算
                    night_eval_dates = [d for d in eval_dates if d in night_dates]
                    night_outside_dates = [d for d in night_dates if d not in eval_dates]
                    
                    # 結算該員工在「參照區間」但「非運算區間」已經確定的夜班
                    already_night = 0
                    for d in night_outside_dates:
                        record = dict_sched_overall.get((emp_id, d))
                        if record and record['shift_code'] in ('01夜B1', '01夜B2'):
                            already_night += 1
                            
                    # 本次引擎針對重疊天數應該排出的夜班數 = 總配額 - 既有夜班
                    target_night = max(0, q.get('夜', 0) - already_night)
                    
                    # 限制引擎只能在重疊區間 (night_eval_dates) 受 QSpinBox 限制
                    if night_eval_dates:
                        model.Add(sum(works[(emp_id, d, '01夜B1')] + works[(emp_id, d, '01夜B2')] for d in night_eval_dates) == target_night)
                    
                    # 🚨 不在 night_eval_dates 裡的日子 (如：在主運算內，但不在參照區內)，
                    # 將直接不綁定 QSpinBox，讓引擎視人力需求自由指派。
                    
                    # 💡 [移除] 把 01中A 和 01泛用 的強迫配額刪除！
                    # 引擎現在可以為了大局，自由地把中A和泛用發放給合適的員工。

            # 4. 時序代理變數 (💡 新增 G1/G2 地點變數)
            all_eval_dates = pd.date_range(start=history_start, end=eval_dates[-1]).strftime('%Y-%m-%d').tolist()
            is_working = {}
            is_off = {}
            is_noon = {}
            is_night = {}
            is_g1 = {}
            is_g2 = {}
            
            for emp_id in emp_ids:
                for d in all_eval_dates:
                    is_working[(emp_id, d)] = model.NewBoolVar(f'work_{emp_id}_{d}')
                    is_off[(emp_id, d)] = model.NewBoolVar(f'off_{emp_id}_{d}')
                    is_noon[(emp_id, d)] = model.NewBoolVar(f'noon_{emp_id}_{d}')
                    is_night[(emp_id, d)] = model.NewBoolVar(f'night_{emp_id}_{d}')
                    is_g1[(emp_id, d)] = model.NewBoolVar(f'g1_{emp_id}_{d}')
                    is_g2[(emp_id, d)] = model.NewBoolVar(f'g2_{emp_id}_{d}')
                    
                    if d < eval_dates[0]:
                        shift = dict_history.get((emp_id, d))
                        model.Add(is_working[(emp_id, d)] == (1 if shift in WORK_SHIFTS else 0))
                        model.Add(is_off[(emp_id, d)] == (1 if shift in OFF_SHIFTS else 0))
                        model.Add(is_noon[(emp_id, d)] == (1 if shift in NOON_SHIFTS else 0))
                        model.Add(is_night[(emp_id, d)] == (1 if shift in NIGHT_SHIFTS else 0))
                        model.Add(is_g1[(emp_id, d)] == (1 if shift in LOCATION_G1 else 0))
                        model.Add(is_g2[(emp_id, d)] == (1 if shift in LOCATION_G2 else 0))
                    else:
                        model.Add(is_working[(emp_id, d)] == sum(works[(emp_id, d, s)] for s in WORK_SHIFTS))
                        model.Add(is_off[(emp_id, d)] == sum(works[(emp_id, d, s)] for s in OFF_SHIFTS))
                        model.Add(is_noon[(emp_id, d)] == sum(works[(emp_id, d, s)] for s in NOON_SHIFTS))
                        model.Add(is_night[(emp_id, d)] == sum(works[(emp_id, d, s)] for s in NIGHT_SHIFTS))
                        model.Add(is_g1[(emp_id, d)] == sum(works[(emp_id, d, s)] for s in LOCATION_G1))
                        model.Add(is_g2[(emp_id, d)] == sum(works[(emp_id, d, s)] for s in LOCATION_G2))
            # 👇 請在這裡新增這個獨立區塊 👇
            # =========================================================================
            # 4.5 動態硬限制：單雙數決定中A班 (依賴 is_working)
            # =========================================================================
            if rule_config.get('hard_mid_a', True):
                for date in eval_dates:
                # 1. 取得該日「總上班人數」(使用剛剛步驟4定義好的 is_working)
                    total_working_d = sum(is_working[(emp_id, date)] for emp_id in emp_ids)
                
                # 2. 取得該日被排入 '01中A' 的總人數
                    mid_a_count = sum(works[(emp_id, date, '01中A')] for emp_id in emp_ids if (emp_id, date, '01中A') in works)
                
                # 3. 單雙數商數與餘數判定
                    max_q = len(emp_ids) // 2 + 1
                    q_var = model.NewIntVar(0, max_q, f'q_working_{date}')
                    model.Add(total_working_d == 2 * q_var + mid_a_count)
                
                # 4. 防呆機制：確保中A人數絕對不超過 1
                    model.Add(mid_a_count <= 1)

            # =========================================================================
            # 🛡️ 連班與例假防線
            # =========================================================================
            # 💡 [修改] 廢棄容易產生歷史邊界漏洞的自然週，改用使用者定義的絕對 7 天區塊
            
            for emp_id in emp_ids:
                # 條件 1：任何連續 7 天內，(上班 + P + L) 最多只能 6 天
                if rule_config.get('hard_law_7_in_1', True):
                    for i in range(len(all_eval_dates) - 6):
                        window_wpl = []
                        for j in range(7):
                            d = all_eval_dates[i+j]
                            if d < eval_dates[0]:
                                shift = dict_history.get((emp_id, d))
                                val = 1 if (shift in WORK_SHIFTS or shift in ['P', 'L']) else 0
                                window_wpl.append(val)
                            else:
                                val = is_working[(emp_id, d)] + works[(emp_id, d, 'P')] + works[(emp_id, d, 'L')]
                                window_wpl.append(val)
                        model.Add(sum(window_wpl) <= 6)
                
                # 條件 2：從排班第一天起，每 7 天一個絕對區間，每個區間只能有 1 個 R
                if not debug_mode:
                    for i in range(0, len(eval_dates), 7):
                        chunk_dates = eval_dates[i:i+7]
                        window_R = [works[(emp_id, d, 'R')] for d in chunk_dates]
                        
                        if len(chunk_dates) == 7:
                            model.Add(sum(window_R) == 1) # 💡 完整 7 天區塊，引擎會自己挑 1 天放 R
                        else:
                            model.Add(sum(window_R) <= 1) # 💡 不足 7 天的尾端，最多放 1 個 R
            
                # 💡 [修改] 加入控制台判斷
                if rule_config.get('hard_no_4_off', True):            
                    # 👇 💡 [新增] 條件 3：拒絕引擎連續四天休假 (預排圖釘四天以上除外)
                    for i in range(len(all_eval_dates) - 3):
                        window_dates = [all_eval_dates[i+j] for j in range(4)]
                    
                        # 計算這 4 天內，有幾天是「歷史紀錄的休假」或「未來被人工鎖定的休假」
                        locked_off_count = 0
                        for d in window_dates:
                            if d < eval_dates[0]: # 過去的歷史紀錄
                                shift = dict_history.get((emp_id, d))
                                if shift in OFF_SHIFTS:
                                    locked_off_count += 1
                            else:                 # 未來需排班的日子
                                record = dict_sched.get((emp_id, d))
                                if record and record['is_locked'] == 1 and record['shift_code'] in OFF_SHIFTS:
                                    locked_off_count += 1
                    
                    # 💡 判斷：若這 4 天「沒有全部被人工鎖死為休假」
                    # 代表引擎有權限排定其中至少 1 天，我們就強制限制這 4 天的休假總和不得超過 3 天
                        if locked_off_count < 4:
                            model.Add(sum(is_off[(emp_id, d)] for d in window_dates) <= 3)

            # 5. 交接班時序防線
            if strict_time_rules and not debug_mode and rule_config.get('hard_clash', True):
                for emp_id in emp_ids:
                    s_pref = shift_prefs.get(str(emp_id).strip(), 'MIX')
                    for i in range(len(all_eval_dates) - 1):
                        today = all_eval_dates[i]
                        tmr = all_eval_dates[i+1]
                        if tmr < eval_dates[0]: continue
                        for f_shift in FORBIDDEN_AFTER_NOON:
                            model.AddImplication(is_noon[(emp_id, today)], works[(emp_id, tmr, f_shift)].Not())
                        for f_shift in FORBIDDEN_AFTER_NIGHT:
                            model.AddImplication(is_night[(emp_id, today)], works[(emp_id, tmr, f_shift)].Not())

                    for i in range(len(all_eval_dates) - 2):
                        ytd = all_eval_dates[i]
                        today = all_eval_dates[i+1]
                        tmr = all_eval_dates[i+2]
                        if tmr < eval_dates[0]: continue
                        
                        # 加入 rule_config 判斷，預設為 True
                        if s_pref != 'NIGHT_ONLY' and rule_config.get('hard_night_2_off', True):
                            just_started_off_after_night = model.NewBoolVar(f'night_to_off_{emp_id}_{today}')
                            model.Add(just_started_off_after_night == 1).OnlyEnforceIf([is_night[(emp_id, ytd)], is_off[(emp_id, today)]])
                            model.AddImplication(just_started_off_after_night, is_off[(emp_id, tmr)])

            # =========================================================================
            # 🎯 權重優化核心 (軟限制打分)
            # =========================================================================
            WEIGHT_SHIFT_PREF = 10 if rule_config.get('soft_shift_pref', True) else 0       
            WEIGHT_BLOCK_PREF = 5  if rule_config.get('soft_block_pref', True) else 0
            WEIGHT_LOCATION_MIX = 5 if rule_config.get('soft_loc_mix', True) else 0      # 💡 中等權重：避免連兩天同地點
            WEIGHT_NIGHT_SEGMENT = 50 if rule_config.get('soft_night_seg', True) else 0     # 💡 [新增] 夜班分段偏好的扣分權重


            WEIGHT_BALANCE_EARLY_NOON = 100 if rule_config.get('soft_bal_early_noon', True) else 0 # 💡 [新增] 早午班平均權重 (設定為 3，讓它具有一定影響力但不至於蓋過員工意願) 
            #WEIGHT_NIGHT_BALANCE = 300 if rule_config.get('soft_bal_night', True) else 0      # 💡 [新增] 夜班平分權重 (差距 1 天就扣 5 分)       
            WEIGHT_M_DAY_BALANCE = 5 if rule_config.get('soft_bal_m_day', True) else 0       # 💡 [新增] 日間主管班 (M/m) 平分的扣分權重
            WEIGHT_C_SHIFT_BALANCE = 5 if rule_config.get('soft_bal_c', True) else 0        # 💡 [新增] Normal 員工 C 班平分的扣分權重
 
            WEIGHT_A_SHIFT_BALANCE = 10 if rule_config.get('soft_bal_a', True) else 0    # 💡 [新增] 中A班平分的扣分權重
            WEIGHT_SUPPORT_BALANCE = 80 if rule_config.get('soft_bal_support', True) else 0 # 💡 [新增] 支援班平分權重
           
            WEIGHT_A_SHIFT_MAX_1 = 50     # 💡 [新增] 中A班超過1天的極重度防線權重
            WEIGHT_M_SHIFT_BONUS = 10      # 💡 [新增] m 班別優先權重 (+1分)

            shift_pref_score = []
            block_penalty_score = []
            location_penalty_score = []  # 💡 儲存地點扣分變數
            m_level_penalty_score = []   # 💡 職級 M 軟限制扣分
            balance_penalty_score = []   # 💡 [新增] 存放每天早午班人數差異的扣分陣列
            global_consec_6_penalties = []
            #night_balance_penalty = []
            m_shift_bonus_score = []     # 💡 [新增] 存放排入 m 班的加分陣列
            t_fallback_penalty_score = [] # 💡 [新增] 存放 t 職級退讓的扣分陣列
            night_segment_penalty_score = [] # 💡 [新增] 存放夜班段數違規的扣分陣列
            m_day_balance_penalty = []    # 💡 [新增] 存放 M/m 班平分違規的扣分陣列
            c_shift_balance_penalty = []  # 💡 [新增] 存放 C 班平分違規的扣分陣列
            a_shift_balance_penalty = []  # 💡 [新增] 存放中A班平分違規的扣分陣列
            a_shift_max_penalty = []      # 💡 [新增] 存放中A班超過1天的扣分陣列
            support_balance_penalty = []  # 💡 [新增] 存放支援班平分違規的扣分陣列

            # 💡 [修改] 補上 c 班別，讓引擎精準計算早班與午班的總人力
            early_shifts_list = ['01早B1', '01早B1c', '01早B2', '01早B2c', '01早m'] 
            noon_shifts_list = ['01午B1', '01午B1c', '01午B2', '01午B2c', '01午m']
            
            for d in eval_dates:
                early_count = sum(works[(emp_id, d, s)] for emp_id in emp_ids for s in early_shifts_list if (emp_id, d, s) in works)
                noon_count = sum(works[(emp_id, d, s)] for emp_id in emp_ids for s in noon_shifts_list if (emp_id, d, s) in works)
                
                # 宣告一個變數代表差距
                diff_var = model.NewIntVar(0, len(emp_ids), f'diff_e_n_{d}')
                
                # 數學實作絕對值：diff_var >= early - noon 且 diff_var >= noon - early
                model.Add(diff_var >= early_count - noon_count)
                model.Add(diff_var >= noon_count - early_count)
                
                balance_penalty_score.append(diff_var)
            # =========================================================================
            # 🎯 全域權重規則：非 b_pref == '6' 者，連六天上班扣 5 分
            # =========================================================================
            for emp_id in emp_ids:
                eid = str(emp_id).strip()
                b_pref = block_prefs.get(eid, 'ANY')
                if b_pref == '6':
                    continue  # 跳過做六休一偏好的人
                # 掃描滾動 6 天區間
                for i in range(len(all_eval_dates) - 5):
                    window_w = []
                    for j in range(6):
                        d = all_eval_dates[i+j]
                        if d < eval_dates[0]:
                            shift = dict_history.get((emp_id, d))
                            val = 1 if (shift in WORK_SHIFTS) else 0
                            window_w.append(val)
                        else:
                            window_w.append(is_working[(emp_id, d)])
                    
                    is_c6 = model.NewBoolVar(f'global_c6_{eid}_{i}')
                    model.Add(sum(window_w) == 6).OnlyEnforceIf(is_c6)
                    model.Add(sum(window_w) < 6).OnlyEnforceIf(is_c6.Not())
                    global_consec_6_penalties.append(10 * is_c6)
            # =========================================================================
            # ⚖️ 日間主管班 (早M 與 午M) 天數平分軟限制 + 負債制
            # =========================================================================
            # 💡 預留外部傳入的「本期提撥償還字典」(若尚未從 UI 傳入則預設為空 {})
            # 格式範例: applied_m_debts = {"A": 3, "B": -1}
            applied_m_debts = getattr(self, 'applied_m_debts', {}) 

            # 1. 篩選名單：職級為 M/Chief 且 意願不是純夜班
            m_chief_ids_day = [
                emp_id for emp_id in emp_ids 
                if job_levels.get(str(emp_id).strip(), 'Normal') in ['M', 'Chief'] 
                and shift_prefs.get(str(emp_id).strip(), 'MIX') != 'NIGHT_ONLY'
            ]

            if len(m_chief_ids_day) > 1:
                # 💡 [修改] 僅保留 '01早M' 與 '01午M'，將 m 班排除在平分與負債機制之外
                day_m_shifts_list = ['01早M', '01午M']
                emp_day_m_counts_vars = []

                for emp_id in m_chief_ids_day:
                    eid_str = str(emp_id).strip()
                    
                    # 2. 計算該主管本月【實際】被排入的 早M 與 午M 班總數
                    actual_m_count_expr = sum(
                        works[(emp_id, d, s)] 
                        for d in eval_dates 
                        for s in day_m_shifts_list 
                        if (emp_id, d, s) in works
                    )
                    
                    # 3. 讀取人工設定的「本期提撥數」 (欠債為正，公司補償為負)
                    applied_val = applied_m_debts.get(eid_str, 0)
                    
                    # 4. 宣告「虛擬承載量」 = 實際排班數 - 提撥數
                    # (範圍放寬以容納極端負債，例如 -30 到 100)
                    eff_m_count_var = model.NewIntVar(-len(eval_dates), len(eval_dates) * 2, f'eff_m_day_{emp_id}')
                    model.Add(eff_m_count_var == actual_m_count_expr - applied_val)
                    emp_day_m_counts_vars.append(eff_m_count_var)

                # 5. 引擎計算總虛擬承載量，並除以人數求「平均基準 (Fair Share)」
                total_eff_m = model.NewIntVar(-len(eval_dates)*len(m_chief_ids_day), len(eval_dates)*len(m_chief_ids_day)*2, 'total_eff_m_day')
                model.Add(total_eff_m == sum(emp_day_m_counts_vars))
                
                avg_eff_m = model.NewIntVar(-len(eval_dates), len(eval_dates)*2, 'avg_eff_m_day')
                model.AddDivisionEquality(avg_eff_m, total_eff_m, len(m_chief_ids_day))

                # 6. 計算每個人與平均數的「絕對差值」，並丟進扣分陣列
                for emp_id, eff_var in zip(m_chief_ids_day, emp_day_m_counts_vars):
                    diff_m_var = model.NewIntVar(-len(eval_dates)*2, len(eval_dates)*2, f'diff_m_day_{emp_id}')
                    model.Add(diff_m_var == eff_var - avg_eff_m)
                    
                    abs_diff_m_var = model.NewIntVar(0, len(eval_dates)*2, f'abs_diff_m_day_{emp_id}')
                    model.AddAbsEquality(abs_diff_m_var, diff_m_var)
                    
                    m_day_balance_penalty.append(abs_diff_m_var)
            # =========================================================================
            # ⚖️ Normal 員工 C 班天數平分軟限制 + 負債制
            # =========================================================================
            # 💡 預留外部傳入的「本期 C 班提撥償還字典」(若尚未從 UI 傳入則預設為空 {})
            applied_c_debts = getattr(self, 'applied_c_debts', {}) 

            # 1. 篩選名單：職級為 Normal 且 意願不是純夜班
            normal_ids_c_shift = [
                emp_id for emp_id in emp_ids 
                if job_levels.get(str(emp_id).strip(), 'Normal') == 'Normal' 
                and shift_prefs.get(str(emp_id).strip(), 'MIX') != 'NIGHT_ONLY'
            ]

            if len(normal_ids_c_shift) > 1:
                # 💡 指定所有 c 班別作為平分目標
                c_shifts_list = ['01早B1c', '01早B2c', '01午B1c', '01午B2c']
                emp_c_counts_vars = []

                for emp_id in normal_ids_c_shift:
                    eid_str = str(emp_id).strip()
                    
                    # 2. 計算該員工本月【實際】被排入的 C 班總數
                    actual_c_count_expr = sum(
                        works[(emp_id, d, s)] 
                        for d in eval_dates 
                        for s in c_shifts_list 
                        if (emp_id, d, s) in works
                    )
                    
                    # 3. 讀取人工設定的 C 班「本期提撥數」 (欠債為正，公司補償為負)
                    applied_val_c = applied_c_debts.get(eid_str, 0)
                    
                    # 4. 宣告「虛擬承載量」 = 實際排班數 - 提撥數
                    eff_c_count_var = model.NewIntVar(-len(eval_dates), len(eval_dates) * 2, f'eff_c_{emp_id}')
                    model.Add(eff_c_count_var == actual_c_count_expr - applied_val_c)
                    emp_c_counts_vars.append(eff_c_count_var)

                # 5. 引擎計算總虛擬承載量，並除以人數求「平均基準 (Fair Share)」
                total_eff_c = model.NewIntVar(-len(eval_dates)*len(normal_ids_c_shift), len(eval_dates)*len(normal_ids_c_shift)*2, 'total_eff_c')
                model.Add(total_eff_c == sum(emp_c_counts_vars))
                
                avg_eff_c = model.NewIntVar(-len(eval_dates), len(eval_dates)*2, 'avg_eff_c')
                model.AddDivisionEquality(avg_eff_c, total_eff_c, len(normal_ids_c_shift))

                # 6. 計算每個人與平均數的「絕對差值」，並丟進扣分陣列
                for emp_id, eff_var in zip(normal_ids_c_shift, emp_c_counts_vars):
                    diff_c_var = model.NewIntVar(-len(eval_dates)*2, len(eval_dates)*2, f'diff_c_{emp_id}')
                    model.Add(diff_c_var == eff_var - avg_eff_c)
                    
                    abs_diff_c_var = model.NewIntVar(0, len(eval_dates)*2, f'abs_diff_c_{emp_id}')
                    model.AddAbsEquality(abs_diff_c_var, diff_c_var)
                    
                    c_shift_balance_penalty.append(abs_diff_c_var)

            # =========================================================================
            # ⚖️ 全體員工 待命支援班 (日) 天數平分軟限制 + 負債制
            # =========================================================================
            applied_support_debts = getattr(self, 'applied_support_debts', {}) 

            # 篩選名單：所有人 (不分職級)，排除純夜班
            support_ids = [
                emp_id for emp_id in emp_ids 
                if shift_prefs.get(str(emp_id).strip(), 'MIX') != 'NIGHT_ONLY, chief'
            ]

            if len(support_ids) > 1:
                support_shifts_list = ['日']
                emp_support_counts_vars = []

                for emp_id in support_ids:
                    eid_str = str(emp_id).strip()
                    
                    actual_support_count_expr = sum(
                        works[(emp_id, d, s)] 
                        for d in eval_dates 
                        for s in support_shifts_list 
                        if (emp_id, d, s) in works
                    )
                    
                    applied_val_sup = applied_support_debts.get(eid_str, 0)
                    
                    eff_sup_count_var = model.NewIntVar(-999, 999, f'eff_sup_{emp_id}')
                    model.Add(eff_sup_count_var == actual_support_count_expr - applied_val_sup)
                    emp_support_counts_vars.append(eff_sup_count_var)

                total_eff_sup = model.NewIntVar(-9999, 9999, 'total_eff_sup')
                model.Add(total_eff_sup == sum(emp_support_counts_vars))
                
                avg_eff_sup = model.NewIntVar(-999, 999, 'avg_eff_sup')
                model.AddDivisionEquality(avg_eff_sup, total_eff_sup, len(support_ids))

                for emp_id, eff_var in zip(support_ids, emp_support_counts_vars):
                    diff_sup_var = model.NewIntVar(-999, 999, f'diff_sup_{emp_id}')
                    model.Add(diff_sup_var == eff_var - avg_eff_sup)
                    
                    abs_diff_sup_var = model.NewIntVar(0, 999, f'abs_diff_sup_{emp_id}')
                    model.AddAbsEquality(abs_diff_sup_var, diff_sup_var)
                    
                    support_balance_penalty.append(abs_diff_sup_var)

            # =========================================================================
            # ⚖️ Normal 員工 中A 班：(1) 天數平分 + 負債制 (2) 至多一天限制
            # =========================================================================
            # 💡 預留外部傳入的「本期 中A 班提撥償還字典」(若尚未從 UI 傳入則預設為空 {})
            applied_a_debts = getattr(self, 'applied_a_debts', {}) 

            # 篩選名單：職級為 Normal 且 意願不是純夜班
            normal_ids_a_shift = [
                emp_id for emp_id in emp_ids 
                if job_levels.get(str(emp_id).strip(), 'Normal') == 'Normal' 
                and shift_prefs.get(str(emp_id).strip(), 'MIX') != 'NIGHT_ONLY'
            ]

            if len(normal_ids_a_shift) > 0:
                a_shifts_list = ['01中A']
                emp_a_counts_vars = []

                for emp_id in normal_ids_a_shift:
                    eid_str = str(emp_id).strip()
                    
                    # 1. 計算該員工本月【實際】被排入的 中A 班總數
                    actual_a_count_expr = sum(
                        works[(emp_id, d, s)] 
                        for d in eval_dates 
                        for s in a_shifts_list 
                        if (emp_id, d, s) in works
                    )
                    
                    # 📜 規則二：每月至多一天限制 (超過1天即產生物理扣分)
                    over_1_var = model.NewIntVar(0, len(eval_dates), f'over_1_a_{emp_id}')
                    model.AddMaxEquality(over_1_var, [0, actual_a_count_expr - 1])
                    a_shift_max_penalty.append(over_1_var)
                    
                    # 📜 規則一：平分與負債制
                    applied_val_a = applied_a_debts.get(eid_str, 0)
                    
                    # 宣告「虛擬承載量」 = 實際排班數 - 提撥數
                    eff_a_count_var = model.NewIntVar(-len(eval_dates), len(eval_dates) * 2, f'eff_a_{emp_id}')
                    model.Add(eff_a_count_var == actual_a_count_expr - applied_val_a)
                    emp_a_counts_vars.append(eff_a_count_var)

                # 必須有 2 人以上才具備互相「平分」的意義
                if len(normal_ids_a_shift) > 1:
                    total_eff_a = model.NewIntVar(-len(eval_dates)*len(normal_ids_a_shift), len(eval_dates)*len(normal_ids_a_shift)*2, 'total_eff_a')
                    model.Add(total_eff_a == sum(emp_a_counts_vars))
                    
                    avg_eff_a = model.NewIntVar(-len(eval_dates), len(eval_dates)*2, 'avg_eff_a')
                    model.AddDivisionEquality(avg_eff_a, total_eff_a, len(normal_ids_a_shift))

                    for emp_id, eff_var in zip(normal_ids_a_shift, emp_a_counts_vars):
                        diff_a_var = model.NewIntVar(-len(eval_dates)*2, len(eval_dates)*2, f'diff_a_{emp_id}')
                        model.Add(diff_a_var == eff_var - avg_eff_a)
                        
                        abs_diff_a_var = model.NewIntVar(0, len(eval_dates)*2, f'abs_diff_a_{emp_id}')
                        model.AddAbsEquality(abs_diff_a_var, diff_a_var)
                        
                        a_shift_balance_penalty.append(abs_diff_a_var)
            # =========================================================================
            # ⚖️ 夜班天數平分軟限制 (M 與 Chief 夜班差距越小越好，避免硬限制死結)
            # =========================================================================
        #    m_chief_ids = [
        #        emp_id for emp_id in emp_ids 
        #        if job_levels.get(str(emp_id).strip(), 'Normal') in ['M', 'Chief', 't'] 
        #        and shift_prefs.get(str(emp_id).strip(), 'MIX') != 'NIGHT_ONLY'
        #    ]
            
            
            # 必須有 2 人以上才能計算差距
        #    if len(m_chief_ids) > 1:
        #        night_shifts_list = ['01夜B1', '01夜B2']
        #        emp_night_counts_vars = []
        #        
                # 1. 計算每位 M/Chief 員工的夜班總數，並宣告為 IntVar 變數
        #        for emp_id in m_chief_ids:
        #            count_expr = sum(
        #                works[(emp_id, d, s)] 
        #                for d in eval_dates 
        #                for s in night_shifts_list 
        #                if (emp_id, d, s) in works
        #            )
        #            count_var = model.NewIntVar(0, len(eval_dates), f'night_count_{emp_id}')
        #            model.Add(count_var == count_expr)
        #            emp_night_counts_vars.append(count_var)
                
                # 2. 計算夜班總數與平均數
        #        total_night_shifts = model.NewIntVar(0, len(eval_dates) * len(m_chief_ids), 'total_m_chief_night')
        #        model.Add(total_night_shifts == sum(emp_night_counts_vars))
                
        #        avg_night = model.NewIntVar(0, len(eval_dates), 'avg_m_chief_night')
                # 引擎整數除法，計算出平均夜班數
        #        model.AddDivisionEquality(avg_night, total_night_shifts, len(m_chief_ids))
                
                # 3. 計算每個人與平均數的差值 (絕對值)
        #        for emp_id, count_var in zip(m_chief_ids, emp_night_counts_vars):
                    # 宣告差值變數 (允許為負數)
        #            diff_var = model.NewIntVar(-len(eval_dates), len(eval_dates), f'diff_night_{emp_id}')
        #            model.Add(diff_var == count_var - avg_night)
                    
                    # 宣告絕對值變數
        #            abs_diff_var = model.NewIntVar(0, len(eval_dates), f'abs_diff_night_{emp_id}')
        #            model.AddAbsEquality(abs_diff_var, diff_var)
                    
                    # 將每個人的差值絕對值放入扣分陣列
        #            night_balance_penalty.append(abs_diff_var)

            for emp_id in emp_ids:
                s_pref = shift_prefs.get(str(emp_id).strip(), 'MIX')
                b_pref = block_prefs.get(str(emp_id).strip(), 'ANY')
                n_seg_pref = night_seg_prefs.get(str(emp_id).strip(), 'ANY') # 💡 取得夜班段數意願
                is_m_level = job_levels.get(str(emp_id).strip(), 'Normal') in ('M', 'Chief', 't')
                is_t_level = job_levels.get(str(emp_id).strip(), 'Normal') == 't'
                # 🤡 [新增] 職級 M 的專屬軟限制

                # 🌙 [新增] 夜班連續段數偏好打分
                if n_seg_pref in ['1', '2', '3'] and s_pref != 'NIGHT_ONLY':
                    segment_vars = []
                    # 1. 掃描整個歷史與未來區間，計算「夜班段數」
                    for i in range(1, len(all_eval_dates)):
                        curr_d = all_eval_dates[i]
                        prev_d = all_eval_dates[i-1]
                        
                        # 定義段數起點：昨天不是夜班，今天是夜班
                        is_start_night = model.NewBoolVar(f'start_night_{emp_id}_{curr_d}')
                        model.Add(is_start_night == 1).OnlyEnforceIf([is_night[(emp_id, prev_d)].Not(), is_night[(emp_id, curr_d)]])
                        model.Add(is_start_night == 0).OnlyEnforceIf(is_night[(emp_id, prev_d)])
                        model.Add(is_start_night == 0).OnlyEnforceIf(is_night[(emp_id, curr_d)].Not())
                        segment_vars.append(is_start_night)
                        
                    total_segments = model.NewIntVar(0, len(all_eval_dates), f'total_n_seg_{emp_id}')
                    model.Add(total_segments == sum(segment_vars))
                    
                    # 2. 防呆防線：如果該員工這個月根本沒上夜班，不該因為沒達到段數要求被扣分
                    has_night = model.NewBoolVar(f'has_any_night_{emp_id}')
                    total_night_days = sum(is_night[(emp_id, d)] for d in eval_dates)
                    model.Add(total_night_days > 0).OnlyEnforceIf(has_night)
                    model.Add(total_night_days == 0).OnlyEnforceIf(has_night.Not())
                    
                    # 3. 依照意願給予相對應的懲罰
                    if n_seg_pref == '1':
                        # 喜歡一次上完：段數 > 1 就扣分 (扣分 = 段數 - 1)
                        pen_var = model.NewIntVar(0, len(all_eval_dates), f'pen_n_seg1_{emp_id}')
                        model.AddMaxEquality(pen_var, [0, total_segments - 1])
                        
                        final_pen = model.NewIntVar(0, len(all_eval_dates), f'final_pen_n_seg1_{emp_id}')
                        model.Add(final_pen == pen_var).OnlyEnforceIf(has_night)
                        model.Add(final_pen == 0).OnlyEnforceIf(has_night.Not())
                        night_segment_penalty_score.append(final_pen)
                        
                    elif n_seg_pref == '2':
                        # 喜歡分兩段：取與 2 的絕對差值 (不到 2 段或超過 2 段都扣分)
                        diff_var = model.NewIntVar(-len(all_eval_dates), len(all_eval_dates), f'diff_n_seg2_{emp_id}')
                        model.Add(diff_var == total_segments - 2)
                        abs_pen_var = model.NewIntVar(0, len(all_eval_dates), f'abs_pen_n_seg2_{emp_id}')
                        model.AddAbsEquality(abs_pen_var, diff_var)
                        
                        final_pen = model.NewIntVar(0, len(all_eval_dates), f'final_pen_n_seg2_{emp_id}')
                        model.Add(final_pen == abs_pen_var).OnlyEnforceIf(has_night)
                        model.Add(final_pen == 0).OnlyEnforceIf(has_night.Not())
                        night_segment_penalty_score.append(final_pen)
                        
                    elif n_seg_pref == '3':
                        # 喜歡分三段以上：段數 < 3 就扣分 (扣分 = 3 - 段數)
                        pen_var = model.NewIntVar(0, 3, f'pen_n_seg3_{emp_id}')
                        model.AddMaxEquality(pen_var, [0, 3 - total_segments])
                        
                        final_pen = model.NewIntVar(0, 3, f'final_pen_n_seg3_{emp_id}')
                        model.Add(final_pen == pen_var).OnlyEnforceIf(has_night)
                        model.Add(final_pen == 0).OnlyEnforceIf(has_night.Not())
                        night_segment_penalty_score.append(final_pen)


                if is_m_level:
                    for d in target_dates:
                        # 💡 [修改] 加入 c 班別的扣分，維持 B1扣10分、B2扣20分 的邏輯
                        m_level_penalty_score.append(10 * works[(emp_id, d, '01早B1')])
                        m_level_penalty_score.append(10 * works[(emp_id, d, '01早B1c')])
                        m_level_penalty_score.append(10 * works[(emp_id, d, '01午B1')])
                        m_level_penalty_score.append(10 * works[(emp_id, d, '01午B1c')])
                        
                        m_level_penalty_score.append(50 * works[(emp_id, d, '01早B2')])
                        m_level_penalty_score.append(50 * works[(emp_id, d, '01早B2c')])
                        m_level_penalty_score.append(50 * works[(emp_id, d, '01午B2')])
                        m_level_penalty_score.append(50 * works[(emp_id, d, '01午B2c')])
                        m_level_penalty_score.append(20 * works[(emp_id, d, '01中A')])
                if is_t_level:
                    for d in target_dates:
                        for s in ['01早M', '01午M']:
                            if (emp_id, d, s) in works:
                                t_fallback_penalty_score.append(10 * works[(emp_id, d, s)]) #664

                # A. 班別意願加分
                if s_pref == 'EARLY':
                    for d in target_dates:
                        for shift in EARLY_SHIFTS:
                            shift_pref_score.append(works[(emp_id, d, shift)])
                elif s_pref == 'NOON':
                    for d in target_dates:
                        for shift in NOON_SHIFTS:
                            shift_pref_score.append(works[(emp_id, d, shift)])

                # B. 連班偏好精準打分
                if b_pref == '3':
                    for i in range(len(target_dates) - 3):
                        window = [is_working[(emp_id, target_dates[i+j])] for j in range(4)]
                        pen_over_3 = model.NewBoolVar(f'pen_over3_{emp_id}_{i}')
                        model.Add(sum(window) == 4).OnlyEnforceIf(pen_over_3)
                        model.Add(sum(window) < 4).OnlyEnforceIf(pen_over_3.Not())
                        block_penalty_score.append(pen_over_3)
                        
                elif b_pref == '5':
                    for i in range(1, len(target_dates)):
                        curr_d = target_dates[i]
                        prev_d = target_dates[i-1]
                        
                        start_work = model.NewBoolVar(f'start_work_{emp_id}_{curr_d}')
                        model.Add(start_work == 1).OnlyEnforceIf([is_off[(emp_id, prev_d)], is_working[(emp_id, curr_d)]])
                        model.Add(start_work == 0).OnlyEnforceIf(is_off[(emp_id, prev_d)].Not())
                        model.Add(start_work == 0).OnlyEnforceIf(is_working[(emp_id, curr_d)].Not())
                        
                        for length in [1, 2, 3, 4]:
                            end_idx = i + length
                            if end_idx < len(all_eval_dates):
                                end_d = all_eval_dates[end_idx]
                                pen_under_5 = model.NewBoolVar(f'pen_under5_{emp_id}_{curr_d}_{length}')
                                model.Add(pen_under_5 == 1).OnlyEnforceIf([start_work, is_off[(emp_id, end_d)]])
                                model.Add(pen_under_5 == 0).OnlyEnforceIf(start_work.Not())
                                model.Add(pen_under_5 == 0).OnlyEnforceIf(is_off[(emp_id, end_d)].Not())
                                block_penalty_score.append(pen_under_5)

                # 💡 [新增] b_pref == '6'：連續上班小於 6 天就扣分
                elif b_pref == '6':
                    for i in range(1, len(target_dates)):
                        curr_d = target_dates[i]
                        prev_d = target_dates[i-1]
                        
                        start_work = model.NewBoolVar(f'start_work_p6_{emp_id}_{curr_d}')
                        # 判定今天是否為「開始上班的第一天」(昨天放假，今天上班)
                        model.Add(start_work == 1).OnlyEnforceIf([is_off[(emp_id, prev_d)], is_working[(emp_id, curr_d)]])
                        model.Add(start_work == 0).OnlyEnforceIf(is_off[(emp_id, prev_d)].Not())
                        model.Add(start_work == 0).OnlyEnforceIf(is_working[(emp_id, curr_d)].Not())
                        
                        # 若開始上班，檢查接下來的 1 到 5 天內是否出現休假 (代表沒上滿 6 天)
                        for length in [1, 2, 3, 4, 5]:
                            end_idx = i + length
                            if end_idx < len(all_eval_dates):
                                end_d = all_eval_dates[end_idx]
                                pen_under_6 = model.NewBoolVar(f'pen_under6_{emp_id}_{curr_d}_{length}')
                                model.Add(pen_under_6 == 1).OnlyEnforceIf([start_work, is_off[(emp_id, end_d)]])
                                model.Add(pen_under_6 == 0).OnlyEnforceIf(start_work.Not())
                                model.Add(pen_under_6 == 0).OnlyEnforceIf(is_off[(emp_id, end_d)].Not())
                                block_penalty_score.append(pen_under_6)
                elif b_pref == '4':
                    for i in range(len(target_dates) - 4):
                        window = [is_working[(emp_id, target_dates[i+j])] for j in range(5)]
                        pen_over_4 = model.NewBoolVar(f'pen_over4_{emp_id}_{i}')
                        model.Add(sum(window) == 5).OnlyEnforceIf(pen_over_4)
                        model.Add(sum(window) < 5).OnlyEnforceIf(pen_over_4.Not())
                        block_penalty_score.append(pen_over_4)
                        
                    for i in range(1, len(target_dates)):
                        curr_d = target_dates[i]
                        prev_d = target_dates[i-1]
                        
                        start_work = model.NewBoolVar(f'start_work_p4_{emp_id}_{curr_d}')
                        model.Add(start_work == 1).OnlyEnforceIf([is_off[(emp_id, prev_d)], is_working[(emp_id, curr_d)]])
                        model.Add(start_work == 0).OnlyEnforceIf(start_work.Not())
                        
                        for length in [1, 2, 3]:
                            end_idx = i + length
                            if end_idx < len(all_eval_dates):
                                end_d = all_eval_dates[end_idx]
                                pen_under_4 = model.NewBoolVar(f'pen_under4_{emp_id}_{curr_d}_{length}')
                                model.Add(pen_under_4 == 1).OnlyEnforceIf([start_work, is_off[(emp_id, end_d)]])
                                model.Add(pen_under_4 == 0).OnlyEnforceIf(pen_under_4.Not())
                                block_penalty_score.append(pen_under_4)

                # 📍 C. 執勤地點輪調打分 (確保跨越歷史邊界也會受懲罰)
                for i in range(len(all_eval_dates) - 1):
                    today = all_eval_dates[i]
                    tmr = all_eval_dates[i+1]
                    
                    if tmr < eval_dates[0]: 
                        continue
                        
                    # G1 連班懲罰：今天 G1 且明天 G1，變數被迫大於等於 1
                    pen_g1 = model.NewBoolVar(f'pen_g1_{emp_id}_{today}')
                    model.Add(pen_g1 >= is_g1[(emp_id, today)] + is_g1[(emp_id, tmr)] - 1)
                    location_penalty_score.append(pen_g1)
                    
                    # G2 連班懲罰
                    pen_g2 = model.NewBoolVar(f'pen_g2_{emp_id}_{today}')
                    model.Add(pen_g2 >= is_g2[(emp_id, today)] + is_g2[(emp_id, tmr)] - 1)
                    location_penalty_score.append(pen_g2)
            # 💡 [新增] 若排入 01早m 或 01午m，給予加分 (鼓勵引擎優先選用 m 班別)
            for emp_id in emp_ids:
                for d in eval_dates:
                    if (emp_id, d, '01早m') in works:
                        m_shift_bonus_score.append(works[(emp_id, d, '01早m')])
                    if (emp_id, d, '01午m') in works:
                        m_shift_bonus_score.append(works[(emp_id, d, '01午m')])

            # =========================================================================
            # ⚖️ 執行優化打分 (總分 = 加分 - 連班扣分 - 地點連莊扣分)
            # =========================================================================
            if debug_mode:
                # 🐛 [Debug 模式]：無視所有員工的連班偏好、地點輪調、班別意願與公平性
                # 唯一的目標只有「盡可能不要動用到虛擬人力 (滿足每日上下限)」
                print("🐛 [Debug 模式] 啟動：無視所有軟限制，僅依勞基法與基本需求暴力求解...")
                model.Maximize(- sum(virtual_penalties))
                
            else:
                # 🌟 [正常模式]：完整的智慧權重計分板
                model.Maximize(
                    WEIGHT_SHIFT_PREF * sum(shift_pref_score) 
                    - WEIGHT_BLOCK_PREF * sum(block_penalty_score)
                    - WEIGHT_LOCATION_MIX * sum(location_penalty_score)
                    - sum(m_level_penalty_score)
                    - sum(t_fallback_penalty_score)
                    - WEIGHT_BALANCE_EARLY_NOON * sum(balance_penalty_score)
                    - sum(global_consec_6_penalties) 
                    #- WEIGHT_NIGHT_BALANCE * sum(night_balance_penalty) 
                    - WEIGHT_NIGHT_SEGMENT * sum(night_segment_penalty_score) 
                    - WEIGHT_M_DAY_BALANCE * sum(m_day_balance_penalty)  # 💡 [新增] 扣除 M/m 班分佈不均的違規分數
                    - WEIGHT_C_SHIFT_BALANCE * sum(c_shift_balance_penalty)  # 💡 [新增] 扣除 C 班分佈不均的違規分數
                    - WEIGHT_A_SHIFT_BALANCE * sum(a_shift_balance_penalty)  # 💡 [新增] 扣除 中A 班分佈不均的違規分數
                    - WEIGHT_SUPPORT_BALANCE * sum(support_balance_penalty)  # 💡 [新增] 扣除支援班分配不均
                    - WEIGHT_A_SHIFT_MAX_1 * sum(a_shift_max_penalty)        # 💡 [新增] 扣除 中A 班超過1天的極重度懲罰
                    + WEIGHT_M_SHIFT_BONUS * sum(m_shift_bonus_score) 
                    - sum(virtual_penalties)
                )

            solver = cp_model.CpSolver()
            self.current_solver = solver  # 綁定到實例，讓 cancel() 抓得到
            solver.parameters.max_time_in_seconds = 600.0 
            # 👇 開啟多核心平行運算 (例如設定為 4 或 8 核心，視你的硬體而定)
            # 預設為 1 核心，開啟後對於複雜的排班限制解題速度會有顯著提升
            solver.parameters.num_search_workers = 3  # 可依硬體調整，建議不要超過 CPU 核心數

            # 👇 加入這兩行開啟底層日誌
            solver.parameters.log_search_progress = True
            return solver.Solve(model), solver, works, virtual_vars_dict
            # 💡 回傳時多帶上虛擬人力的變數字典

        # =========================================================================
        # 🚀 執行主引擎與死結診斷 (Relaxation Analysis)
        # =========================================================================
        print("🔍 啟動排班主引擎 (含虛擬人力避震器)...")
        status, solver, works, virtual_vars = attempt_solve(strict_time_rules=True, strict_quotas=True)
        
        # 捕捉取消事件 (主引擎)
        if self.is_cancelled:
            return False, "🛑 運算已由使用者手動取消。"

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # 結算到底動用了多少虛擬人力
            total_virtual_used = sum(solver.Value(v) for v in virtual_vars.values())
            
            debug_msg = ""
            if total_virtual_used > 0:
                debug_msg += f"\n\n⚠️ 注意：系統被迫動用了 {total_virtual_used} 人次的「虛擬人力」才成功產出班表！\n(請見下方缺口清單，排班網格將會留空以利手動補人)"
                
                missing_details = []
                for (d, s), var in virtual_vars.items():
                    val = solver.Value(var)
                    if val > 0:
                        missing_details.append(f"[{d}] {s} 缺 {val} 人")
                debug_msg += "\n👉 " + "\n👉 ".join(missing_details)
                
                # 💡 在背景悄悄執行降級探針，純粹作為 Debug 診斷報告
                print("⚠️ 偵測到缺口，啟動背景死結診斷探針...")
                status_nt, solver_nt, _, v_vars_nt = attempt_solve(strict_time_rules=False, strict_quotas=True)
                if self.is_cancelled: return False, "🛑 運算已由使用者手動取消。" # 新增
                status_nt, solver_nt, _, v_vars_nt = attempt_solve(strict_time_rules=False, strict_quotas=True)
                v_used_nt = sum(solver_nt.Value(v) for v in v_vars_nt.values()) if status_nt in (cp_model.OPTIMAL, cp_model.FEASIBLE) else float('inf')
                
                status_nq, solver_nq, _, v_vars_nq = attempt_solve(strict_time_rules=True, strict_quotas=False)
                if self.is_cancelled: return False, "🛑 運算已由使用者手動取消。" # 新增
                status_nq, solver_nq, _, v_vars_nq = attempt_solve(strict_time_rules=True, strict_quotas=False)
                v_used_nq = sum(solver_nq.Value(v) for v in v_vars_nq.values()) if status_nq in (cp_model.OPTIMAL, cp_model.FEASIBLE) else float('inf')
                
                debug_msg += "\n\n💡 【死結診斷分析 (Relaxation Analysis)】："
                if v_used_nt < total_virtual_used:
                    debug_msg += f"\n- 若犧牲「交接班時序防線(如午禁早)」，缺口可降至 {v_used_nt} 人次。"
                if v_used_nq < total_virtual_used:
                    debug_msg += f"\n- 若犧牲「強制特休/排休配額」，缺口可降至 {v_used_nq} 人次。"
                if v_used_nt >= total_virtual_used and v_used_nq >= total_virtual_used:
                    debug_msg += "\n- 經測試，放寬時序或休假配額皆無法減少缺口。此為【硬性人力不足】，請手動介入補人或減少需求。"
            
            # 寫入資料庫 (虛擬人力承接的班別，因為無真實員工對應，自然不會寫入 DB，UI 會呈現缺人)
            results_to_update = []
            for emp_id in emp_ids:
                for date in eval_dates:
                    for state in ALL_STATES:
                        if solver.Value(works[(emp_id, date, state)]) == 1:
                            record = dict_sched.get((emp_id, date))
                            if not record or record['is_locked'] == 0:
                                results_to_update.append((state, emp_id, date))
                                
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.executemany('UPDATE schedule SET shift_code = ? WHERE emp_id = ? AND date = ? AND is_locked = 0', results_to_update)
            conn.commit()
            conn.close()
            
            if total_virtual_used == 0:
                return True, "✅ 智能排班完美達成！無動用虛擬人力。"
            else:
                return True, f"✅ 排班已產出 (含虛擬缺口)！{debug_msg}"

        return False, "❌ 嚴重錯誤：連動用百萬虛擬人力都無法排出！\n請檢查是否有人工鎖定(圖釘)的班別發生了嚴重的物理互斥。"