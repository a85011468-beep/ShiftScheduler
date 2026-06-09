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

    def run_scheduler(self, start_date, end_date, leave_quotas=None, split_date=None):
        if leave_quotas is None: leave_quotas = {}
        
        employees = self.db.get_all_active_employees()
        if not employees:
            return False, "❌ 找不到員工資料，請先匯入名單。"

        emp_ids = [e['emp_id'] for e in employees]
        job_levels = {str(e['emp_id']).strip(): str(e['job_level']).strip() for e in employees}
        shift_prefs = {str(e['emp_id']).strip(): str(e.get('shift_pref', 'MIX')).strip() for e in employees}
        block_prefs = {str(e['emp_id']).strip(): str(e.get('block_pref', 'ANY')).strip() for e in employees}

        target_dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        eval_dates = target_dates 
        
        self._ensure_blank_grid(employees, eval_dates)
        schedules = self.db.get_schedule_by_date_range(eval_dates[0], eval_dates[-1])
        dict_sched = {(s['emp_id'], s['date']): s for s in schedules}

        eval_start_dt = datetime.strptime(eval_dates[0], '%Y-%m-%d')
        history_start = (eval_start_dt - timedelta(days=7)).strftime('%Y-%m-%d')
        history_end = (eval_start_dt - timedelta(days=1)).strftime('%Y-%m-%d')
        history_schedules = self.db.get_schedule_by_date_range(history_start, history_end)
        dict_history = {(s['emp_id'], s['date']): s['shift_code'] for s in history_schedules}

        def attempt_solve(strict_time_rules=True, strict_quotas=True):
            model = cp_model.CpModel()
            works = {}

            for emp_id in emp_ids:
                for date in eval_dates:
                    for state in ALL_STATES:
                        works[(emp_id, date, state)] = model.NewBoolVar(f'w_{emp_id}_{date}_{state}')

            # 1. 職級、圖釘與意願鎖死
            for emp_id in emp_ids:
                is_manager = job_levels.get(str(emp_id).strip(), 'Normal') in ('M', 'Chief')
                s_pref = shift_prefs.get(str(emp_id).strip(), 'MIX')
                
                for date in eval_dates:
                    model.AddExactlyOne([works[(emp_id, date, state)] for state in ALL_STATES])
                    record = dict_sched.get((emp_id, date))
                    is_locked = (record and record['is_locked'] == 1 and record['shift_code'] in ALL_STATES)
                    
                    if is_locked:
                        model.Add(works[(emp_id, date, record['shift_code'])] == 1)
                    else:
                        model.Add(works[(emp_id, date, 'Train')] == 0)
                        model.Add(works[(emp_id, date, '日')] == 0)
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
                    
                    # 💡 導入虛擬人力：真實人力 + 虛擬人力 >= 最低需求
                    slack_min = model.NewIntVar(0, min_req, f'slack_min_{date}_{shift}')
                    model.Add(sum(shift_vars) + slack_min >= min_req)
                    model.Add(sum(shift_vars) <= max_req)
                    
                    virtual_penalties.append(slack_min * 1000000)
                    virtual_vars_dict[(date, shift)] = slack_min

                early_combo_vars = [works[(emp_id, date, '01早B2')] for emp_id in emp_ids] + \
                                   [works[(emp_id, date, '01早m')] for emp_id in emp_ids]
                slack_early = model.NewIntVar(0, 2, f'slack_early_{date}')
                model.Add(sum(early_combo_vars) + slack_early >= 2)
                virtual_penalties.append(slack_early * 1000000)
                virtual_vars_dict[(date, '早班複合戰力')] = slack_early

                noon_combo_vars = [works[(emp_id, date, '01午B2')] for emp_id in emp_ids] + \
                                  [works[(emp_id, date, '01午m')] for emp_id in emp_ids]
                slack_noon = model.NewIntVar(0, 2, f'slack_noon_{date}')
                model.Add(sum(noon_combo_vars) + slack_noon >= 2)
                virtual_penalties.append(slack_noon * 1000000)
                virtual_vars_dict[(date, '午班複合戰力')] = slack_noon

            # 3. 測試變因宇宙 A：QSpinBox 絕對鎖死 (硬限制開關)
            if strict_quotas:
                for emp_id in emp_ids:
                    eid = str(emp_id).strip()
                    q = leave_quotas.get(eid, {})
                    
                    # 💡 如果有跨越八週邊界，分別對兩個區間進行配額約束
                    if split_date and eval_dates[0] <= split_date < eval_dates[-1]:
                        p1_dates = [d for d in eval_dates if d <= split_date]
                        p2_dates = [d for d in eval_dates if d > split_date]
                        
                        # 區間 1 (期內) 限制
                        model.Add(sum(works[(emp_id, d, 'L')] for d in p1_dates) == q.get('L', 0))
                        model.Add(sum(works[(emp_id, d, 'P')] for d in p1_dates) == q.get('P', 0))
                        model.Add(sum(works[(emp_id, d, 'r')] for d in p1_dates) == q.get('r', 0))
                        model.Add(sum(works[(emp_id, d, 'R')] for d in p1_dates) == q.get('R', 0))
                        
                        # 區間 2 (跨出) 限制
                        model.Add(sum(works[(emp_id, d, 'L')] for d in p2_dates) == q.get('L2', 0))
                        model.Add(sum(works[(emp_id, d, 'P')] for d in p2_dates) == q.get('P2', 0))
                        model.Add(sum(works[(emp_id, d, 'r')] for d in p2_dates) == q.get('r2', 0))
                        model.Add(sum(works[(emp_id, d, 'R')] for d in p2_dates) == q.get('R2', 0))
                    else:
                        # 💡 未跨界的常規單一約束
                        model.Add(sum(works[(emp_id, d, 'L')] for d in eval_dates) == q.get('L', 0))
                        model.Add(sum(works[(emp_id, d, 'P')] for d in eval_dates) == q.get('P', 0))
                        model.Add(sum(works[(emp_id, d, 'r')] for d in eval_dates) == q.get('r', 0))
                        model.Add(sum(works[(emp_id, d, 'R')] for d in eval_dates) == q.get('R', 0))
                    
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

            # =========================================================================
            # 🛡️ 連班與例假防線 (取代原有的單純 is_working 判斷)
            # =========================================================================
            # 準備「週日到週六」的日期分組字典，用來限制例假 (R)
            weeks_dict = {}
            for d_str in all_eval_dates:
                d_obj = datetime.strptime(d_str, '%Y-%m-%d')
                # weekday(): 0 是週一, 6 是週日。計算距離本週日的差值以求出週日日期
                days_since_sun = (d_obj.weekday() + 1) % 7 
                sun_date = d_obj - timedelta(days=days_since_sun)
                sun_str = sun_date.strftime('%Y-%m-%d')
                if sun_str not in weeks_dict:
                    weeks_dict[sun_str] = []
                weeks_dict[sun_str].append(d_str)

            for emp_id in emp_ids:
                # 條件 1：任何連續 7 天內，(上班 + P + L) 最多只能 6 天
                # (等同於強迫任何滾動 7 天區間，至少要有一天的 r 或是 R)
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
                
                # 條件 2：每個「週日到週六」的區間內，一定要出現至少一個 'R'
                for sun_str, week_dates in weeks_dict.items():
                    # 防呆保護：只約束「完整 7 天都落在資料範圍內」且「包含未來需排班日期」的週
                    # 避免因為月初/月底的殘缺週，強迫壓縮排 R 導致引擎無解
                    if len(week_dates) == 7 and any(d >= eval_dates[0] for d in week_dates):
                        window_R = []
                        for d in week_dates:
                            if d < eval_dates[0]:
                                shift = dict_history.get((emp_id, d))
                                val = 1 if shift == 'R' else 0
                                window_R.append(val)
                            else:
                                window_R.append(works[(emp_id, d, 'R')])
                        model.Add(sum(window_R) == 1)

            # 5. 交接班時序防線
            if strict_time_rules:
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
                        
                        if s_pref != 'NIGHT_ONLY':
                            just_started_off_after_night = model.NewBoolVar(f'night_to_off_{emp_id}_{today}')
                            model.Add(just_started_off_after_night == 1).OnlyEnforceIf([is_night[(emp_id, ytd)], is_off[(emp_id, today)]])
                            model.AddImplication(just_started_off_after_night, is_off[(emp_id, tmr)])

            # =========================================================================
            # 🎯 權重優化核心 (軟限制打分)
            # =========================================================================
            WEIGHT_SHIFT_PREF = 10       
            WEIGHT_LOCATION_MIX = 5      # 💡 中等權重：避免連兩天同地點
            WEIGHT_BLOCK_PREF = 1        

            shift_pref_score = []
            block_penalty_score = []
            location_penalty_score = []  # 💡 儲存地點扣分變數
            m_level_penalty_score = []   # 💡 職級 M 軟限制扣分

            for emp_id in emp_ids:
                s_pref = shift_prefs.get(str(emp_id).strip(), 'MIX')
                b_pref = block_prefs.get(str(emp_id).strip(), 'ANY')
                is_m_level = job_levels.get(str(emp_id).strip(), 'Normal') == 'M'
                
                # 🤡 [新增] 職級 M 的專屬軟限制
                if is_m_level:
                    for d in target_dates:
                        m_level_penalty_score.append(10 * works[(emp_id, d, '01早B1')])
                        m_level_penalty_score.append(10 * works[(emp_id, d, '01午B1')])
                        m_level_penalty_score.append(20 * works[(emp_id, d, '01早B2')])
                        m_level_penalty_score.append(20 * works[(emp_id, d, '01午B2')])

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

            # ⚖️ 執行優化打分 (總分 = 加分 - 連班扣分 - 地點連莊扣分)
            model.Maximize(
                WEIGHT_SHIFT_PREF * sum(shift_pref_score) 
                - WEIGHT_BLOCK_PREF * sum(block_penalty_score)
                - WEIGHT_LOCATION_MIX * sum(location_penalty_score)
                - sum(m_level_penalty_score)
                - sum(virtual_penalties)
            )

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 15.0 
            return solver.Solve(model), solver, works, virtual_vars_dict
            # 💡 回傳時多帶上虛擬人力的變數字典

        # =========================================================================
        # 🚀 執行主引擎與死結診斷 (Relaxation Analysis)
        # =========================================================================
        print("🔍 啟動排班主引擎 (含虛擬人力避震器)...")
        status, solver, works, virtual_vars = attempt_solve(strict_time_rules=True, strict_quotas=True)
        
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
                v_used_nt = sum(solver_nt.Value(v) for v in v_vars_nt.values()) if status_nt in (cp_model.OPTIMAL, cp_model.FEASIBLE) else float('inf')
                
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