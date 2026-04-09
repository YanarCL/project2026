import os
import sys
import json
import hmac
import hashlib
from datetime import date, datetime, time, timedelta
from urllib.parse import parse_qsl
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_DIR = os.path.join(BASE_DIR, "chat")
sys.path.append(CHAT_DIR)

from db import db
from config import settings

app = FastAPI()

# Статические файлы (изображения)
IMAGES_DIR = os.path.join(BASE_DIR, "images")
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

# CORS настройки
origins = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "https://snnfitmate.ru",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === ЖИЗНЕННЫЙ ЦИКЛ ПРИЛОЖЕНИЯ ===
@app.on_event("startup")
async def startup():
    print("Подключение к БД...")
    await db.create_pool()
    print("БД подключена успешно!")

@app.on_event("shutdown")
async def shutdown():
    await db.close()

# === PYDANTIC МОДЕЛИ ===
class KBJURequest(BaseModel):
    init_data: str
    gender: str
    weight: float
    height: float
    age: int
    activity: float
    goal: str

class InitDataRequest(BaseModel):
    init_data: str

class RecipesRequest(BaseModel):
    init_data: str
    goal: str

class ScheduleRequest(BaseModel):
    init_data: str
    days: List[str]
    workoutIds: List[str]
    deleteIds: Optional[List[int]] = None

class GetScheduleRequest(BaseModel):
    init_data: str
    dates: List[str]

class ProgramRequest(BaseModel):
    init_data: str

class ProgramSaveRequest(BaseModel):
    init_data: str
    title: str
    exercise_ids: List[int]

class ProgramActionRequest(BaseModel):
    init_data: str
    program_id: int

class ProgramGenerateRequest(BaseModel):
    init_data: str
    gender: str
    age_group: str
    goal: str

class ProgramUpdateRequest(BaseModel):
    init_data: str
    difficulty: str
    exercises: List[dict]

class WorkoutExercisesRequest(BaseModel):
    training_id: int
    gender: str = "female"     
    age_group: str = "20-30"   

class WorkoutsRequest(BaseModel):
    init_data: Optional[str] = ""

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def format_date_ru(value):
    if not value:
        return None
    return value.strftime("%d.%m.%Y")

def get_age_string(age: int) -> str:
    if age % 10 == 1 and age % 100 != 11:
        return f"{age} год"
    elif 2 <= age % 10 <= 4 and (age % 100 < 10 or age % 100 >= 20):
        return f"{age} года"
    else:
        return f"{age} лет"

def translate_gender(gender: str) -> str:
    if not gender:
        return "—"
    g = gender.lower()
    if g in ["male", "мужской", "м", "m"]: return "Мужской"
    if g in ["female", "женский", "ж", "f"]: return "Женский"
    return gender.capitalize()

def validate_telegram_init_data(init_data: str, bot_token: str):
    if not init_data:
        return None
    parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed_data.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed_data.items()))
    secret_key = hmac.new(key=b"WebAppData", msg=bot_token.encode(), digestmod=hashlib.sha256).digest()
    calculated_hash = hmac.new(key=secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    user_data = parsed_data.get("user")
    if user_data:
        try:
            parsed_data["user"] = json.loads(user_data)
        except json.JSONDecodeError:
            parsed_data["user"] = None

    return parsed_data

# === ENDPOINTS ===

@app.get("/")
async def root():
    return {"status": "ok"}

@app.post("/calculate-kbju")
async def calculate_kbju(data: KBJURequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")

    tg_user = validated_data.get("user")
    user_id = str(tg_user.get("id"))
    if data.gender == "male":
        bmr = 10 * data.weight + 6.25 * data.height - 5 * data.age + 5
    else:
        bmr = 10 * data.weight + 6.25 * data.height - 5 * data.age - 161   
    bmr = max(0, bmr)
    if data.goal == "lose": 
        tdee = bmr * data.activity * (1 - 0.175)
    elif data.goal == "gain": 
        tdee = bmr * data.activity * (1 + 0.1)
    else: 
        tdee = bmr * data.activity  
    tdee = max(0, tdee)
    if data.goal == "maintain": 
        protein_p, fat_p, carbs_p = 0.2, 0.3, 0.5
    elif data.goal == "gain": 
        protein_p, fat_p, carbs_p = 0.25, 0.25, 0.5
    else: 
        protein_p, fat_p, carbs_p = 0.3, 0.22, 0.48    
    result = {
        "bmr": round(bmr),
        "tdee": round(tdee),
        "protein": round((tdee * protein_p) / 4),
        "fat": round((tdee * fat_p) / 9),
        "carbs": round((tdee * carbs_p) / 4),
    }
    await db.save_kbju(user_id, result, data)
    return result

@app.post("/telegram/user")
async def get_telegram_user(data: InitDataRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")

    tg_user = validated_data.get("user")
    if not tg_user:
        raise HTTPException(status_code=400, detail="Telegram user data not found")

    telegram_user_id = str(tg_user.get("id"))
    db_user = await db.get_user(telegram_user_id)

    tg_response = {
        "id": tg_user.get("id"),
        "first_name": tg_user.get("first_name"),
        "last_name": tg_user.get("last_name"),
        "username": tg_user.get("username"),
        "language_code": tg_user.get("language_code"),
    }

    if not db_user:
        return {"telegram": tg_response, "profile": None, "message": "Пользователь не найден в БД"}

    age_str = "—"
    if db_user.get("date_of_birth"):
        dob = db_user["date_of_birth"]
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        age_str = get_age_string(age)

    height_str = f"{db_user.get('height')} см" if db_user.get("height") else "—"
    weight_str = f"{db_user.get('weight')} кг" if db_user.get("weight") else "—"

    calories_str = "—"
    macros_dict = None

    try:
        if hasattr(db, 'get_latest_macros'):
            macros_data = await db.get_latest_macros(telegram_user_id)
            if macros_data:
                target = macros_data.get("daily_calorie_target")
                calories_str = f"{target} ккал" if target else "—"
                macros_dict = {
                    "tdee": target,
                    "protein": float(macros_data.get("protein") or 0),
                    "fat": float(macros_data.get("fat") or 0),
                    "carbs": float(macros_data.get("carbs") or 0)
                }
        else:
            target = await db.get_latest_calories(telegram_user_id)
            calories_str = f"{target} ккал" if target else "—"
    except Exception as e:
        print(f"Ошибка получения макросов: {e}")
        target = await db.get_latest_calories(telegram_user_id)
        calories_str = f"{target} ккал" if target else "—"

    active_sub = await db.get_active_subscription(telegram_user_id)
    subscription_str = "Нет"

    if active_sub:
        end_date = active_sub.get("end_date")
        if end_date:
            subscription_str = f"До {end_date.strftime('%d.%m.%Y')}"
        else:
            subscription_str = "Активна"
    else:
        trial_end = db_user.get("trial_period_end")
        if db_user.get("trial_period_user_flag") == 1 and trial_end and trial_end >= date.today():
            subscription_str = f"Пробный (до {trial_end.strftime('%d.%m.%Y')})"

    activity_val = 1.55
    coef_id = db_user.get("coefficient_ID")
    if coef_id == 1: activity_val = 1.2
    elif coef_id == 2: activity_val = 1.375
    elif coef_id == 3: activity_val = 1.55
    elif coef_id == 4: activity_val = 1.725
    elif coef_id == 5: activity_val = 1.9

    goal_str = "maintain"
    goal_id = db_user.get("goal_ID")
    if goal_id == 1: goal_str = "lose"
    elif goal_id == 2: goal_str = "gain"
    elif goal_id == 3: goal_str = "maintain"

    return {
        "telegram": tg_response,
        "profile": {
            "gender": translate_gender(db_user.get("gender")),
            "age": age_str,
            "height": height_str,
            "weight": weight_str,
            "caloriesPerDay": calories_str,
            "subscription": subscription_str,
            "macros": macros_dict,
            "activity": activity_val,
            "goal": goal_str          
        }
    }

@app.post("/exercise-of-the-day")
async def get_exercise_of_the_day(data: InitDataRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")

    tg_user = validated_data.get("user")
    user_id = str(tg_user.get("id"))

    exercise = await db.get_or_create_daily_exercise(user_id)

    if not exercise:
        return {
            "name": "Планка", 
            "description": "Отличное упражнение для поддержания тонуса кора!",
            "visual_representation": None
        }

    return exercise

@app.post("/daily-exercise")
async def get_daily_exercise(data: InitDataRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")
    
    tg_user = validated_data.get("user")
    if not tg_user:
        raise HTTPException(status_code=400, detail="Telegram user data not found")
    
    user_id = str(tg_user.get("id"))
    exercise = await db.get_or_create_daily_exercise(user_id)
    
    if not exercise:
        return {
            "name": "Отдых",
            "description": "База упражнений пуста или сегодня день отдыха!",
            "visual_representation": None
        }
        
    description = exercise.get("description") or exercise.get("execution") or "Полезное упражнение на сегодня"
    
    return {
        "name": exercise.get("name"),
        "description": description,
        "visual_representation": exercise.get("visual_representation")
    }

@app.get("/recipes/{goal_id}")
async def get_recipes(goal_id: int):
    try:
        recipes = await db.get_recipes_by_goal(goal_id)
        return recipes
    except Exception as e:
        print(f"Ошибка получения рецептов: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

WORKOUT_ID_MAP = {
    'yoga': 1, 'arms': 2, 'stretching': 3,
    'back': 4, 'legs': 5, 'fullbody': 6,
}
WORKOUT_ID_MAP_REVERSE = {v: k for k, v in WORKOUT_ID_MAP.items()}
WORKOUT_NAMES = {
    'yoga': 'Йога', 'arms': 'Руки', 'stretching': 'Стретчинг',
    'back': 'Спина', 'legs': 'Ноги', 'fullbody': 'Все тело'
}

@app.post("/user-program")
async def get_user_program(data: ProgramRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")

    tg_user = validated_data.get("user")
    if not tg_user:
        raise HTTPException(status_code=400, detail="Telegram user data not found")

    user_id = str(tg_user.get("id"))
    workouts = await db.get_user_program_exercises(user_id)

    return {"workouts": workouts if workouts else []}

@app.post("/get-schedule")
async def get_schedule(data: GetScheduleRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")

    tg_user = validated_data.get("user")
    if not tg_user:
        raise HTTPException(status_code=400, detail="Telegram user data not found")

    user_id = str(tg_user.get("id"))
    schedule = await db.get_user_schedule_for_dates(user_id, data.dates)

    formatted = []
    for item in schedule:
        workout_name = ""
        if item['category_ID']:
            key = WORKOUT_ID_MAP_REVERSE.get(item['category_ID'])
            workout_name = WORKOUT_NAMES.get(key, f"Тренировка #{item['category_ID']}")
        elif item['program_ID']:
            workout_name = f"Индивидуальная #{item['program_ID']}"

        formatted.append({
            "schedule_id": item['schedule_ID'],
            "workout_name": workout_name,
            "planned_date": str(item['planned_date']),
            "category_id": item['category_ID'],
            "program_id": item['program_ID']
        })

    return {"schedule": formatted}

@app.post("/workouts")
async def get_workouts(data: WorkoutsRequest):
    user_gender = "Женский"
    age_group = "20-30"

    if data.init_data:
        validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
        if validated_data:
            user_id = str(validated_data.get("user", {}).get("id"))
            db_user = await db.get_user(user_id)
            if db_user:
                # Определение пола
                if db_user.get("gender") in ["Мужской", "male", "м", "m"]:
                    user_gender = "Мужской"
                
                # Определение возрастной группы (до 30 -> 20-30, старше 30 -> 30-40)
                if db_user.get("date_of_birth"):
                    dob = db_user["date_of_birth"]
                    today = date.today()
                    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                    
                    if age > 30:
                        age_group = "30-40"
                    else:
                        age_group = "20-30"

    try:
        print(f"Запрос списка программ для: Пол={user_gender}, Возраст={age_group}")
        workouts = await db.get_workouts(user_gender, age_group)
        return {"workouts": workouts}
    except Exception as e:
        print(f"Ошибка получения тренировок: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сервера")

@app.post("/workout-exercises")
async def fetch_workout_exercises(data: WorkoutExercisesRequest):
    try:
        db_gender = "Мужской" if data.gender == "male" else "Женский"
        print(f"Сборка упражнений: Тренировка={data.training_id}, Пол={db_gender}, Возраст={data.age_group}")
        exercises = await db.get_workout_exercises(
            training_id=data.training_id, 
            gender=db_gender, 
            age_group=data.age_group
        )
        return {"exercises": exercises}
    except Exception as e:
        print(f"Ошибка загрузки упражнений: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при загрузке упражнений")

@app.post("/programs/save")
async def save_program(data: ProgramSaveRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")
        
    user_id = str(validated_data.get("user", {}).get("id"))
    
    prog_id = await db.save_user_program(user_id, data.title, data.exercise_ids)
    if not prog_id:
        raise HTTPException(status_code=500, detail="Ошибка при сохранении в БД")
        
    return {"success": True, "program_id": prog_id}

@app.post("/programs/my")
async def get_my_programs(data: InitDataRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")
        
    user_id = str(validated_data.get("user", {}).get("id"))
    programs = await db.get_user_programs_with_exercises(user_id)
    return {"programs": programs}

@app.post("/programs/delete")
async def delete_program(data: ProgramActionRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")
        
    user_id = str(validated_data.get("user", {}).get("id"))
    success = await db.delete_user_program(user_id, data.program_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Program not found or deletion failed")
    return {"success": True}

@app.post("/programs/generate")
async def generate_program(data: ProgramGenerateRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")

    tg_user = validated_data.get("user")
    if not tg_user:
        raise HTTPException(status_code=400, detail="Telegram user data not found")

    user_id = str(tg_user.get("id"))
    goal_id = 1 if data.goal == "Похудение" else 3

    program = await db.generate_individual_program(
        user_id=user_id,
        gender=data.gender,
        age_group=data.age_group,
        goal_id=goal_id,
        goal_name=data.goal
    )

    if not program:
        raise HTTPException(status_code=500, detail="Не удалось сформировать программу")

    return program

@app.put("/programs/{program_id}")
async def update_program(program_id: int, data: ProgramUpdateRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")

    user_id = str(validated_data.get("user", {}).get("id"))

    success = await db.update_user_program(
        user_id=user_id,
        program_id=program_id,
        difficulty=data.difficulty,
        exercises=data.exercises
    )

    if not success:
        raise HTTPException(status_code=400, detail="Не удалось обновить программу")

    return {"success": True}

@app.post("/save-schedule")
async def save_schedule_endpoint(data: ScheduleRequest):
    validated_data = validate_telegram_init_data(data.init_data, settings.bot_token)
    if not validated_data:
        raise HTTPException(status_code=401, detail="Invalid init data")

    tg_user = validated_data.get("user")
    if not tg_user:
        raise HTTPException(status_code=400, detail="Telegram user data not found")

    user_id = str(tg_user.get("id"))
    
    db_user = await db.get_user(user_id)
    active_sub = await db.get_active_subscription(user_id)
    
    has_access = False
    if active_sub:
        has_access = True
    elif db_user:
        trial_end = db_user.get("trial_period_end")
        if db_user.get("trial_period_user_flag") == 1 and trial_end and trial_end >= date.today():
            has_access = True

    if not has_access:
        raise HTTPException(status_code=403, detail="Нет активной подписки или пробный период истек")
        
    notification_time = time(9, 0, 0)  

    if data.deleteIds:
        for schedule_id in data.deleteIds:
            try:
                await db.delete_schedule_entry(schedule_id, user_id)
            except Exception as e:
                print(f"Ошибка при удалении {schedule_id}: {e}")

    for date_str in data.days:
        try:
            planned_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        for workout_id in data.workoutIds:
            schedule_id = None
            try:
                if workout_id in WORKOUT_ID_MAP:
                    category_id = WORKOUT_ID_MAP[workout_id]
                    schedule_id = await db.save_schedule(
                        user_id=user_id,
                        category_id=category_id,
                        program_id=None,
                        planned_date=planned_date
                    )
                elif workout_id.startswith('ind_'):
                    program_id = int(workout_id.replace('ind_', ''))
                    schedule_id = await db.save_schedule(
                        user_id=user_id,
                        category_id=None,
                        program_id=program_id,
                        planned_date=planned_date
                    )
                else:
                    continue
                    
                if schedule_id is not None and schedule_id > 0: 
                    notification_datetime = datetime.combine(planned_date, notification_time)
                    await db.save_notification(
                        user_id=user_id,
                        schedule_id=schedule_id,
                        notification_time=notification_datetime
                    )
            except Exception as e:
                print(f"Ошибка сохранения: {e}")

    return {"success": True, "message": "Расписание сохранено"}