import aiomysql
from datetime import date, timedelta, datetime
from typing import Optional, Dict, Any, List, Union
from config import settings

class Database:
    def __init__(self):
        self.pool = None

    async def create_pool(self):
        self.pool = await aiomysql.create_pool(
            host=settings.db_host,
            user=settings.db_user,
            password=settings.db_password,
            db=settings.db_name,
            autocommit=True,
            minsize=1,
            maxsize=5
        )

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT u.*, ac.coef_value 
                    FROM `User` u
                    LEFT JOIN `active_coefficient` ac ON u.coefficient_ID = ac.coefficient_ID
                    WHERE u.user_ID = %s
                """, (user_id,))
                return await cur.fetchone()

    async def add_user(self, user_id: str, data: Dict[str, Any]):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                trial_start = date.today()
                trial_end = trial_start + timedelta(days=30)

                await cur.execute("""
                    INSERT INTO `User` 
                    (user_ID, gender, date_of_birth, height, weight, goal_ID, coefficient_ID, 
                     trial_period_start, trial_period_end, trial_period_user_flag)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                    gender = %s,
                    date_of_birth = %s,
                    height = %s,
                    weight = %s,
                    goal_ID = %s,
                    coefficient_ID = %s
                """, (
                    user_id, data.get('gender'), data.get('birthdate'), data.get('height'),
                    data.get('weight'), data.get('goal_ID'), data.get('coefficient_ID'),
                    trial_start, trial_end, 1,
                    data.get('gender'), data.get('birthdate'), data.get('height'),
                    data.get('weight'), data.get('goal_ID'), data.get('coefficient_ID')
                ))

    async def get_latest_calories(self, user_id: str) -> Optional[int]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT daily_calorie_target 
                    FROM Macros_Calculation 
                    WHERE user_ID = %s 
                    ORDER BY macros_calculation_ID DESC 
                    LIMIT 1
                """, (user_id,))
                result = await cur.fetchone()
                return result['daily_calorie_target'] if result else None

    async def get_latest_macros(self, user_id: str) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT daily_calorie_target, protein, fat, carbs 
                    FROM Macros_Calculation 
                    WHERE user_ID = %s 
                    ORDER BY macros_calculation_ID DESC 
                    LIMIT 1
                """, (user_id,))
                return await cur.fetchone()

    async def get_active_subscription(self, user_id: str) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT s.*, sp.name as period_name, sp.num_sub_interval
                    FROM Subscription s
                    JOIN Subscription_Period sp ON s.sub_period_ID = sp.sub_period_ID
                    WHERE s.user_ID = %s AND s.is_active = 1
                    ORDER BY s.end_date DESC
                    LIMIT 1
                """, (user_id,))
                return await cur.fetchone()

    async def get_recipes_by_goal(self, goal_id: int) -> list:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                query = """
                    SELECT 
                        r.recipe_ID, r.category_ID, r.name as recipe_name, r.description, 
                        r.image, r.calories, r.protein, r.fat, r.carbs,
                        rc.quantity, i.name as ingredient_name, u.name as unit_name
                    FROM recipe r
                    LEFT JOIN recipe_composition rc ON r.recipe_ID = rc.recipe_ID
                    LEFT JOIN ingredient i ON rc.ingredient_ID = i.ingredient_ID
                    LEFT JOIN unit_ofmeasure u ON i.unit_ID = u.unit_ID
                    WHERE r.goal_ID = %s
                """
                await cur.execute(query, (goal_id,))
                rows = await cur.fetchall()

                recipes_dict = {}
                for row in rows:
                    rid = row['recipe_ID']
                    if rid not in recipes_dict:
                        recipes_dict[rid] = {
                            "id": rid,
                            "category_ID": row['category_ID'],
                            "title": row['recipe_name'],
                            "instructions": row['description'],
                            "image": row['image'],
                            "calories": row['calories'],
                            "protein": float(row['protein']) if row['protein'] else 0,
                            "fat": float(row['fat']) if row['fat'] else 0,      
                            "carbs": float(row['carbs']) if row['carbs'] else 0, 
                            "ingredients": []
                        }
                    
                    if row['ingredient_name']:
                        qty = float(row['quantity']) if row['quantity'] else 0
                        qty_str = f"{qty:g}" 
                        ingredient_str = f"{row['ingredient_name']} - {qty_str} {row['unit_name']}"
                        recipes_dict[rid]["ingredients"].append(ingredient_str)

                return list(recipes_dict.values())

    async def get_or_create_daily_exercise(self, user_id: str) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # 1. Ищем упражнение на СЕГОДНЯ. 
                # Обернули de.date в DATE(), чтобы отбросить часы и минуты, если колонка DATETIME
                await cur.execute("""
                    SELECT e.name, e.description, e.visual_representation 
                    FROM Daily_Exercise de
                    JOIN Exercise e ON de.exercise_ID = e.exercise_ID
                    WHERE de.user_ID = %s AND DATE(de.date) = CURDATE()
                    ORDER BY de.date DESC
                    LIMIT 1
                """, (user_id,))
                
                existing_exercise = await cur.fetchone()
                
                # Если упражнение на сегодня уже есть — просто отдаем его
                if existing_exercise:
                    return existing_exercise

                # 2. Если на сегодня упражнения НЕТ — выбираем случайное из базы
                await cur.execute("SELECT exercise_ID, name, description, visual_representation FROM Exercise ORDER BY RAND() LIMIT 1")
                random_exercise = await cur.fetchone()

                if not random_exercise:
                    return None 
                
                # 3. Сохраняем новое упражнение для пользователя с датой "сегодня"
                await cur.execute("INSERT INTO Daily_Exercise (user_ID, exercise_ID, date) VALUES (%s, %s, CURDATE())", 
                                  (user_id, random_exercise['exercise_ID']))
                
                await conn.commit() 
                
                return {
                    "name": random_exercise['name'], 
                    "description": random_exercise['description'],
                    "visual_representation": random_exercise['visual_representation']
                }
    async def save_kbju(self, user_id: str, result: dict, user_data):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. Переводим пол в формат базы данных
                db_gender = "Мужской" if user_data.gender == "male" else "Женский"
                
                # 2. Маппинг цели в ID (lose=1, gain=2, maintain=3)
                goal_map = {"lose": 1, "gain": 2, "maintain": 3}
                db_goal_id = goal_map.get(user_data.goal, 3)
                
                # 3. Маппинг активности в ID
                db_coef_id = 3 # по умолчанию средняя
                if user_data.activity == 1.2: db_coef_id = 1
                elif user_data.activity == 1.375: db_coef_id = 2
                elif user_data.activity == 1.55: db_coef_id = 3
                elif user_data.activity == 1.725: db_coef_id = 4
                elif user_data.activity == 1.9: db_coef_id = 5

                # 4. Вычисляем примерную дату рождения из возраста
                from datetime import date
                today = date.today()
                try:
                    # Вычитаем возраст из текущего года
                    approx_dob = today.replace(year=today.year - user_data.age)
                except ValueError:
                    # Защита от високосного года (если сегодня 29 февраля, а в году рождения его нет)
                    approx_dob = today.replace(year=today.year - user_data.age, day=28)

                # 5. Обновляем ВСЕ измененные данные пользователя (включая дату рождения)
                await cur.execute("""
                    UPDATE `User` 
                    SET weight = %s, 
                        height = %s, 
                        gender = %s,
                        goal_ID = %s,
                        coefficient_ID = %s,
                        date_of_birth = %s
                    WHERE user_ID = %s
                """, (
                    user_data.weight, 
                    user_data.height, 
                    db_gender, 
                    db_goal_id, 
                    db_coef_id, 
                    approx_dob,
                    user_id
                ))
                
                # 6. Сохраняем результаты расчета макросов
                await cur.execute("""
                    INSERT INTO Macros_Calculation (user_ID, daily_calorie_target, protein, fat, carbs)
                    VALUES (%s, %s, %s, %s, %s)
                """, (user_id, result['tdee'], result['protein'], result['fat'], result['carbs']))

    async def get_trial_end_date(self, user_id: str) -> Optional[date]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT trial_period_end FROM `User` WHERE user_ID = %s", (user_id,))
                result = await cur.fetchone()
                return result['trial_period_end'] if result else None

    async def create_or_extend_subscription(self, user_id: str, sub_period_id: int, days: int) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT name FROM Subscription_Period WHERE sub_period_ID = %s", (sub_period_id,))
                period_result = await cur.fetchone()
                period_name = period_result['name'] if period_result else f"{days} дней"

                active_sub = await self.get_active_subscription(user_id)

                if active_sub:
                    new_start = active_sub['end_date']
                    new_end = new_start + timedelta(days=days)
                    await cur.execute("UPDATE Subscription SET end_date = %s WHERE sub_ID = %s", 
                                      (new_end, active_sub['sub_ID']))
                    return {'start_date': new_start, 'end_date': new_end, 'period_name': period_name, 'action': 'extended'}
                else:
                    trial_end = await self.get_trial_end_date(user_id)
                    today = date.today()
                    new_start = trial_end if (trial_end and trial_end > today) else today
                    new_end = new_start + timedelta(days=days)

                    await cur.execute("""
                        INSERT INTO Subscription (user_ID, sub_period_ID, start_date, end_date, is_active)
                        VALUES (%s, %s, %s, %s, 1)
                    """, (user_id, sub_period_id, new_start, new_end))
                    return {'start_date': new_start, 'end_date': new_end, 'period_name': period_name, 'action': 'created'}

    async def save_schedule(self, user_id: str, category_id: Optional[int],
                            program_id: Optional[int], planned_date: date):
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        INSERT INTO schedule (user_ID, program_ID, category_ID, planned_date)
                        VALUES (%s, %s, %s, %s)
                    """, (user_id, program_id, category_id, planned_date))
                    
                    await conn.commit()
                    return cur.lastrowid
        except Exception as e:
            print(f"Ошибка в save_schedule: {e}")
            return None

    async def save_notification(self, user_id: str, schedule_id: int, notification_time: datetime):
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        INSERT INTO notifications (user_ID, schedule_ID, date_and_time_ending, is_sent)
                        VALUES (%s, %s, %s, 0)
                    """, (user_id, schedule_id, notification_time))
                    
                    await conn.commit()
                    return cur.lastrowid
        except Exception as e:
            print(f"Ошибка в save_notification: {e}")
            return None

    async def get_user_schedule_for_dates(self, user_id: str, dates: List[str]):
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT schedule_ID, program_ID, category_ID, planned_date
                    FROM schedule
                    WHERE user_ID = %s AND planned_date IN %s
                    ORDER BY planned_date, schedule_ID
                """, (user_id, tuple(dates)))
                return await cur.fetchall()

    async def delete_schedule_entry(self, schedule_id: int, user_id: str) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM notifications WHERE schedule_ID = %s", (schedule_id,))
                await cur.execute("DELETE FROM schedule WHERE schedule_ID = %s AND user_ID = %s", (schedule_id, user_id))
                return cur.rowcount > 0
            
    async def generate_individual_program(self, user_id: str, gender: str, age_group: str, goal_id: int, goal_name: str):
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    db_gender = "Мужской" if gender == "male" else "Женский"
                    db_gender_alt = "male" if gender == "male" else "female"
                    short_gender = "М" if gender == "male" else "Ж"
                    
                    await cur.execute("""
                        SELECT exercise_ID, name, description, visual_representation, repetitions, execution
                        FROM exercise
                        WHERE (gender = %s OR gender = %s OR gender LIKE %s)
                          AND (age_group = %s OR age_group LIKE %s)
                          AND goal_ID = %s
                        ORDER BY RAND() 
                        LIMIT 12
                    """, (db_gender, db_gender_alt, f"%{short_gender}%", age_group, f"%{age_group}%", goal_id))

                    exercises = await cur.fetchall()

                    if not exercises:
                        return None

                    beginner, medium, advanced = [], [], []

                    for i, ex in enumerate(exercises):
                        exercise_data = {
                            "id": ex["exercise_ID"],
                            "name": ex["name"],
                            "description": ex["description"] or ex["execution"] or "",
                            "sets": 2 if i % 3 == 0 else 3,
                            "reps": ex["execution"] or str(ex["repetitions"]),
                        }
                        if i % 3 == 0: beginner.append(exercise_data)
                        elif i % 3 == 1: medium.append(exercise_data)
                        else: advanced.append(exercise_data)

                    title = f"Индивидуальная: {goal_name}"
                    first_exercise_id = exercises[0]["exercise_ID"]

                    await cur.execute("""
                        INSERT INTO program (user_ID, exercise_ID, name, creation_date, Users_programcol)
                        VALUES (%s, %s, %s, CURDATE(), %s)
                    """, (user_id, first_exercise_id, title, f"{db_gender}-{age_group}-{goal_name}"))

                    program_id = cur.lastrowid

                    values = [(program_id, ex["exercise_ID"]) for ex in exercises]
                    await cur.executemany("""
                        INSERT INTO ex_prog (program_ID, exercise_ID)
                        VALUES (%s, %s)
                    """, values)

                    await conn.commit()

                    return {
                        "id": program_id,
                        "title": title,
                        "difficulty": "Средний",
                        "duration": "30 мин" if goal_name == "Похудение" else "25 мин",
                        "calories": 320 if goal_name == "Похудение" else 220,
                        "focus": goal_name,
                        "exercises": beginner,
                        "availableExercisesByDifficulty": {
                            "beginner": beginner,
                            "medium": medium,
                            "advanced": advanced
                        }
                    }
        except Exception as e:
            print(f"Ошибка generate_individual_program: {e}")
            return None

    async def get_user_program_exercises(self, user_id: str):
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT program_ID, name 
                    FROM program
                    WHERE user_ID = %s
                """, (user_id,))
                result = await cur.fetchall()
                
                if not result:
                    return []
                return [{'id': row['program_ID'], 'name': row['name']} for row in result]

    async def get_pending_notifications(self, current_time: datetime):
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT 
                        n.*, 
                        s.planned_date, 
                        s.user_ID, 
                        s.category_ID, 
                        s.program_ID,
                        p.name AS program_name 
                    FROM notifications n
                    JOIN schedule s ON n.schedule_ID = s.schedule_ID
                    LEFT JOIN program p ON s.program_ID = p.program_ID
                    WHERE n.date_and_time_ending <= %s 
                    AND n.is_sent = 0
                """, (current_time,))
                return await cur.fetchall()

    async def mark_notification_sent(self, notification_id: int):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE notifications 
                    SET is_sent = 1, sent_at = NOW() 
                    WHERE notification_ID = %s
                """, (notification_id,))

    async def close(self):
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()

    async def save_user_program(self, user_id: str, title: str, exercise_ids: list) -> Optional[int]:
        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        INSERT INTO program (user_ID, name, creation_date, exercise_ID)
                        VALUES (%s, %s, CURDATE(), NULL)
                    """, (user_id, title))
                    
                    await conn.commit()
                    program_id = cur.lastrowid
                    
                    if exercise_ids and program_id:
                        values = [(program_id, ex_id) for ex_id in exercise_ids]
                        await cur.executemany("""
                            INSERT INTO ex_prog (program_ID, exercise_ID)
                            VALUES (%s, %s)
                        """, values)
                        await conn.commit()
                        
                    return program_id
        except Exception as e:
            print(f"Ошибка при сохранении программы: {e}")
            return None

    async def get_user_programs_with_exercises(self, user_id: str) -> list:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT program_ID, name, creation_date, Users_programcol
                    FROM program
                    WHERE user_ID = %s
                    ORDER BY program_ID DESC
                """, (user_id,))
                programs = await cur.fetchall()
                
                if not programs:
                    return []
                
                program_ids = [p['program_ID'] for p in programs]
                format_strings = ','.join(['%s'] * len(program_ids))
                
                await cur.execute(f"""
                    SELECT ep.program_ID, e.exercise_ID, e.name, e.description, e.visual_representation, e.repetitions, e.execution
                    FROM ex_prog ep
                    JOIN exercise e ON ep.exercise_ID = e.exercise_ID
                    WHERE ep.program_ID IN ({format_strings})
                """, tuple(program_ids))
                
                exercises = await cur.fetchall()
                
                prog_dict = {}
                for p in programs:
                    prog_dict[p['program_ID']] = {
                        "id": p['program_ID'],
                        "title": p['name'],
                        "difficulty": "Средний",
                        "duration": "30 мин",
                        "calories": 300,
                        "focus": "Индивидуальная",
                        "exercises": []
                    }
                
                for ex in exercises:
                    prog_dict[ex['program_ID']]['exercises'].append({
                        "id": ex['exercise_ID'],
                        "name": ex['name'],
                        "description": ex['description'] or ex['execution'] or "",
                        "image": ex['visual_representation'],
                        "sets": 3,
                        "reps": ex['execution'] or str(ex['repetitions'])
                    })
                    
                return list(prog_dict.values())

    async def update_user_program(self, user_id: str, program_id: int, difficulty: str, exercises: list) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT program_ID FROM program WHERE program_ID=%s AND user_ID=%s", (program_id, user_id))
                    if not await cur.fetchone():
                        return False

                    await cur.execute("DELETE FROM ex_prog WHERE program_ID = %s", (program_id,))

                    if exercises:
                        values = [(program_id, ex["id"]) for ex in exercises]
                        await cur.executemany("INSERT INTO ex_prog (program_ID, exercise_ID) VALUES (%s, %s)", values)

                    await conn.commit()
                    return True
                except Exception as e:
                    print(f"Ошибка при обновлении программы: {e}")
                    return False
                
    async def delete_user_program(self, user_id: str, program_id: int) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("SELECT program_ID FROM program WHERE program_ID = %s AND user_ID = %s", (program_id, user_id))
                    if not await cur.fetchone():
                        return False

                    await cur.execute("""
                        DELETE FROM notifications 
                        WHERE schedule_ID IN (
                            SELECT schedule_ID FROM schedule WHERE program_ID = %s
                        )
                    """, (program_id,))

                    await cur.execute("DELETE FROM schedule WHERE program_ID = %s", (program_id,))
                    await cur.execute("DELETE FROM ex_prog WHERE program_ID = %s", (program_id,))
                    await cur.execute("DELETE FROM program WHERE program_ID = %s AND user_ID = %s", (program_id, user_id))
                    
                    await conn.commit()
                    return True
                    
                except Exception as e:
                    print(f"Ошибка при удалении программы: {e}")
                    await conn.rollback()
                    return False

    # === ФИЛЬТР ТРЕНИРОВОК ПО ВОЗРАСТУ ===
    async def get_workouts(self, user_gender: str, user_age_group: str) -> Dict[str, List[Dict[str, Any]]]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                
                db_gender_alt = "male" if user_gender == "Мужской" else "female"
                short_gender = "М" if user_gender == "Мужской" else "Ж"

                query = """
                    SELECT 
                        t.training_ID as id, 
                        t.name as title, 
                        c.name as section_title, 
                        t.difficulty, 
                        t.duration,
                        g.name as goal_name
                    FROM training t
                    JOIN goal g ON t.goal_ID = g.goal_ID
                    JOIN exercise_category c ON t.category_ID = c.category_ID
                    WHERE EXISTS (
                        SELECT 1
                        FROM training_composition tc
                        JOIN exercise e ON tc.exercise_ID = e.exercise_ID
                        WHERE tc.training_ID = t.training_ID
                          AND (e.gender = %s OR e.gender = %s OR e.gender LIKE %s OR e.gender IS NULL OR e.gender = '')
                          AND (e.age_group = %s OR e.age_group LIKE %s OR e.age_group IS NULL OR e.age_group = '')
                    )
                    ORDER BY g.name, c.name, t.training_ID
                """
                await cur.execute(query, (user_gender, db_gender_alt, f"%{short_gender}%", user_age_group, f"%{user_age_group}%"))
                rows = await cur.fetchall()

                workouts_by_goal = {}
                for row in rows:
                    goal = row['goal_name']
                    section = row['section_title']

                    if goal not in workouts_by_goal:
                        workouts_by_goal[goal] = {}
                    if section not in workouts_by_goal[goal]:
                        workouts_by_goal[goal][section] = []

                    workouts_by_goal[goal][section].append({
                        "id": row['id'],
                        "title": row['title'],
                        "difficulty": row['difficulty'],
                        "duration": row['duration']
                    })

                result = {}
                for goal, sections in workouts_by_goal.items():
                    result[goal] = [
                        {"sectionTitle": sec_title, "workouts": works}
                        for sec_title, works in sections.items()
                    ]

                return result

    # === СБОРКА УПРАЖНЕНИЙ ВНУТРИ ТРЕНИРОВКИ ===
    async def get_workout_exercises(self, training_id: int, gender: str, age_group: str) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                final_exercises = []
                used_ids = set()
                
                db_gender_alt = "male" if gender == "Мужской" else "female"
                short_gender = "М" if gender == "Мужской" else "Ж"

                await cur.execute("SELECT goal_ID FROM training WHERE training_ID = %s", (training_id,))
                goal_row = await cur.fetchone()
                if not goal_row:
                    return []
                training_goal_id = goal_row['goal_ID']

                # ИСПРАВЛЕНИЕ: Убрал вызов несуществующего tc.duration
                await cur.execute("""
                    SELECT e.exercise_ID as id, e.name, e.description, e.visual_representation, 
                           e.execution, e.repetitions, 
                           tc.number as composition_number
                    FROM exercise e
                    JOIN training_composition tc ON e.exercise_ID = tc.exercise_ID
                    WHERE tc.training_ID = %s 
                      AND (e.gender = %s OR e.gender = %s OR e.gender LIKE %s OR e.gender IS NULL OR e.gender = '')
                      AND (e.age_group = %s OR e.age_group LIKE %s OR e.age_group IS NULL OR e.age_group = '')
                    ORDER BY tc.number ASC
                """, (training_id, gender, db_gender_alt, f"%{short_gender}%", age_group, f"%{age_group}%"))
                
                for ex in await cur.fetchall():
                    ex['composition_duration'] = '45 сек' # Добавляем длительность вручную
                    final_exercises.append(ex)
                    used_ids.add(ex['id'])

                if len(final_exercises) < 6:
                    needed = 6 - len(final_exercises)
                    
                    if used_ids:
                        placeholders = ','.join(['%s'] * len(used_ids))
                        query = f"""
                            SELECT exercise_ID as id, name, description, visual_representation, 
                                   execution, repetitions
                            FROM exercise
                            WHERE goal_ID = %s 
                              AND (gender = %s OR gender = %s OR gender LIKE %s OR gender IS NULL)
                              AND (age_group = %s OR age_group LIKE %s OR age_group IS NULL)
                              AND exercise_ID NOT IN ({placeholders})
                            ORDER BY RAND() LIMIT %s
                        """
                        params = [training_goal_id, gender, db_gender_alt, f"%{short_gender}%", age_group, f"%{age_group}%"] + list(used_ids) + [needed]
                    else:
                        query = """
                            SELECT exercise_ID as id, name, description, visual_representation, 
                                   execution, repetitions
                            FROM exercise
                            WHERE goal_ID = %s 
                              AND (gender = %s OR gender = %s OR gender LIKE %s OR gender IS NULL)
                              AND (age_group = %s OR age_group LIKE %s OR age_group IS NULL)
                            ORDER BY RAND() LIMIT %s
                        """
                        params = [training_goal_id, gender, db_gender_alt, f"%{short_gender}%", age_group, f"%{age_group}%", needed]

                    await cur.execute(query, tuple(params))
                    
                    next_number = len(final_exercises) + 1
                    for ex in await cur.fetchall():
                        ex['composition_number'] = next_number
                        ex['composition_duration'] = '45 сек' # Добавляем длительность вручную
                        final_exercises.append(ex)
                        next_number += 1
                        used_ids.add(ex['id'])

                for ex in final_exercises:
                    ex['description'] = ex['description'] or ""
                    ex['execution'] = ex['execution'] or "Выполнение"
                    if not ex.get('repetitions'):
                        ex['repetitions'] = 15 
                    
                return final_exercises

db = Database()