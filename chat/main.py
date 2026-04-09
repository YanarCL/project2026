import asyncio
import logging
import re
from datetime import datetime, date, timedelta
from itertools import groupby

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    ReplyKeyboardRemove, 
    WebAppInfo,
    MenuButtonWebApp, 
    MenuButtonCommands
)

from config import settings
from db import db

bot = Bot(token=settings.bot_token)
dp = Dispatcher()

GENDER_MAP_DB = {"Мужчина 👨": "male", "Женщина 👩": "female"}
GOAL_MAP_DB = {"Похудеть": 1, "Коррекция": 3}
ACTIVITY_MAP_DB = {"Минимальная": 1, "Низкая": 2, "Средняя": 3, "Высокая": 4, "Очень высокая": 5}
SUBSCRIPTION_MAP = {
    "1 месяц": {"sub_period_ID": 1, "days": 30},
    "3 месяца": {"sub_period_ID": 2, "days": 90},
    "1 год": {"sub_period_ID": 3, "days": 365}
}

class Survey(StatesGroup):
    waiting_for_start = State()
    waiting_for_gender = State()
    waiting_for_birthdate = State()
    waiting_for_height = State()
    waiting_for_weight = State()
    waiting_for_activity = State()
    waiting_for_goal = State()
    choosing_subscription = State()
    confirm_payment = State()

def get_start_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Начать 🏃‍♀️")]], resize_keyboard=True, one_time_keyboard=True)

def get_gender_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Мужчина 👨"), KeyboardButton(text="Женщина 👩")]], resize_keyboard=True, one_time_keyboard=True)

def get_activity_keyboard():
    kb = [
        [KeyboardButton(text="Минимальная"), KeyboardButton(text="Низкая")],
        [KeyboardButton(text="Средняя"), KeyboardButton(text="Высокая")],
        [KeyboardButton(text="Очень высокая")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True)

def get_goal_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Похудеть")], [KeyboardButton(text="Коррекция")]], resize_keyboard=True, one_time_keyboard=True)

def get_main_menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Продлить/купить подписку")]], resize_keyboard=True)

def get_subscription_keyboard():
    kb = [
        [KeyboardButton(text="1 месяц")],
        [KeyboardButton(text="3 месяца")],
        [KeyboardButton(text="1 год")],
        [KeyboardButton(text="❌ Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_confirm_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Да"), KeyboardButton(text="Нет")]], resize_keyboard=True)

async def update_user_menu_button(bot_instance: Bot, chat_id: int, has_access: bool):
    if has_access:
        await bot_instance.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(
                text="Mini-App", 
                web_app=WebAppInfo(url=settings.web_app_url)
            )
        )
    else:
        await bot_instance.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonCommands()
        )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "🤖 <b>Справка по боту:</b>\n\n"
        "Этот бот — ваш персональный фитнес-помощник 💪.\n\n"
        "🔸 /start — проверить подписку и открыть главное меню.\n"
        "🔸 /restart — сбросить анкету и заполнить параметры заново (рост, вес и т.д.).\n"
        "🔸 /help — показать это сообщение.\n\n"
        "Кнопка <b>«Открыть App 📱»</b> слева от поля ввода доступна только после прохождения опроса "
        "и при наличии активной подписки или пробного периода."
    )
    await message.answer(help_text, parse_mode="HTML")

@dp.message(Command("restart"))
async def cmd_restart(message: types.Message, state: FSMContext):
    await state.clear()
    chat_id = message.chat.id

    await update_user_menu_button(bot, chat_id, False)

    await message.answer(
        "🔄 Вы решили начать всё с чистого листа!\n\n"
        "Давайте заново заполним вашу анкету, чтобы обновить параметры "
        "для идеальной программы тренировок и плана питания.",
        reply_markup=get_start_keyboard()
    )
    await state.set_state(Survey.waiting_for_start)

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = str(message.from_user.id)
    chat_id = message.chat.id

    existing_user = await db.get_user(user_id)

    if existing_user:
        today = date.today()
        active_sub = await db.get_active_subscription(user_id)
        
        has_access = False 

        if active_sub and active_sub['end_date'] >= today:
            has_access = True
            subscription_status = (
                f"💎 Платная подписка активна до {active_sub['end_date'].strftime('%d.%m.%Y')}.\n"
                f"Тариф: {active_sub['period_name']}"
            )
        else:
            trial_end = existing_user.get('trial_period_end')
            trial_flag = existing_user.get('trial_period_user_flag')

            if trial_flag == 1 and trial_end and trial_end >= today:
                has_access = True
                subscription_status = (
                    f"🎁 Пробная подписка активна до {trial_end.strftime('%d.%m.%Y')}.\n"
                    f"У вас есть полный доступ ко всем функциям!"
                )
            elif trial_flag == 1 and trial_end and trial_end < today:
                subscription_status = (
                    f"⚠️ Пробная подписка истекла {trial_end.strftime('%d.%m.%Y')}.\n"
                    f"Оформите платную подписку для продолжения работы."
                )
            else:
                subscription_status = "❌ У вас нет активной подписки."

        await update_user_menu_button(bot, chat_id, has_access)

        await message.answer(
            f"С возвращением! 👋\n\n"
            f"Я помню тебя! Твои данные уже сохранены в системе.\n\n"
            f"{subscription_status}\n\n"
            f"Если хочешь обновить рост и вес, нажми /restart.",
            reply_markup=get_main_menu()
        )
    else:
        await update_user_menu_button(bot, chat_id, False)

        await message.answer(
            "Привет! Я твой персональный фитнес-помощник 💪.\n"
            "Чтобы я мог составить идеальную программу тренировок и план питания, "
            "мне нужно немного узнать о тебе. Это займет всего 1 минуту.",
            reply_markup=get_start_keyboard()
        )
        await state.set_state(Survey.waiting_for_start)


@dp.message(Survey.waiting_for_start, F.text == "Начать 🏃‍♀️")
async def process_start(message: types.Message, state: FSMContext):
    await message.answer("Отлично! Для начала, кто ты? 🧍‍♂️🧍‍♀️", reply_markup=get_gender_keyboard())
    await state.set_state(Survey.waiting_for_gender)

@dp.message(Survey.waiting_for_gender, F.text.in_(GENDER_MAP_DB.keys()))
async def process_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=GENDER_MAP_DB[message.text])
    await message.answer(
        "Принято! 🎯\nТеперь введите дату рождения в формате ДД.ММ.ГГГГ (например: 15.05.1995)",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(Survey.waiting_for_birthdate)

@dp.message(Survey.waiting_for_gender)
async def invalid_gender(message: types.Message):
    await message.answer("⚠️ Пожалуйста, выберите пол из кнопок ниже:", reply_markup=get_gender_keyboard())

@dp.message(Survey.waiting_for_birthdate)
async def process_birthdate(message: types.Message, state: FSMContext):
    date_text = message.text.strip()
    if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", date_text):
        return await message.answer("❌ Неверный формат. Используйте ДД.ММ.ГГГГ (например: 15.05.1995)")

    try:
        birth_date = datetime.strptime(date_text, "%d.%m.%Y").date()
    except ValueError:
        return await message.answer("❌ Такой даты не существует. Проверьте ввод.")

    today = date.today()
    age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))

    if age < 16 or age > 60:
        return await message.answer(f"❌ Возраст {age} не подходит нашему боту 😓\nВам должно быть от 16 до 60 лет.")
    await state.update_data(birthdate=birth_date, age=age)
    await message.answer("Супер! 🎂 Какой у тебя рост в сантиметрах? (например: 175)")
    await state.set_state(Survey.waiting_for_height)

@dp.message(Survey.waiting_for_height)
async def process_height(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or len(text) > 3:
        return await message.answer("❌ Введите число от 140 до 250 (максимум 3 цифры, например: 175)")
    height = int(text)
    if height < 140 or height > 250:
        return await message.answer("❌ Рост должен быть от 140 до 250 см. Попробуйте ещё раз.")

    await state.update_data(height=height)
    await message.answer("А текущий вес в килограммах? (например: 70.5) 🤸‍♀️")
    await state.set_state(Survey.waiting_for_weight)

@dp.message(Survey.waiting_for_weight)
async def process_weight(message: types.Message, state: FSMContext):
    text = message.text.strip().replace(',', '.')
    if not re.match(r"^\d+(\.\d{1,2})?$", text):
        return await message.answer("❌ Введите число с максимум 2 знаками после запятой (например: 70.5)")
    weight = float(text)
    if weight < 35 or weight > 300:
        return await message.answer("❌ Вес должен быть от 35 до 300 кг. Попробуйте ещё раз.")

    await state.update_data(weight=weight)
    activity_description = (
        "Почти готово! 🏃 Как бы ты описал свою обычную активность?\n\n"
        "🛀 Минимальная — сидячая работа, отсутствие спорта.\n"
        "🚶 Низкая — лёгкие тренировки 1–3 раза в неделю.\n"
        "🚴 Средняя — занятия спортом 3–5 раз в неделю.\n"
        "🚵 Высокая — интенсивные тренировки 6–7 раз в неделю.\n"
        "🏋 Очень высокая — проф. спорт, экстремальные нагрузки."
    )
    await message.answer(activity_description, reply_markup=get_activity_keyboard())
    await state.set_state(Survey.waiting_for_activity)

@dp.message(Survey.waiting_for_activity, F.text.in_(ACTIVITY_MAP_DB.keys()))
async def process_activity(message: types.Message, state: FSMContext):
    await state.update_data(coefficient_ID=ACTIVITY_MAP_DB[message.text])
    goal_description = (
        "И последний вопрос. 🎯 Какова твоя главная цель сейчас? \n"
        "Наш бот предоставляет упражнения для похудения или коррекции фигуры 😉\n"
        "Набор массы можно указать в расчете КБЖУ внутри приложения."
    )
    await message.answer(goal_description, reply_markup=get_goal_keyboard())
    await state.set_state(Survey.waiting_for_goal)

@dp.message(Survey.waiting_for_activity)
async def invalid_activity(message: types.Message):
    await message.answer("⚠️ Пожалуйста, выберите активность из кнопок:", reply_markup=get_activity_keyboard())

@dp.message(Survey.waiting_for_goal, F.text.in_(GOAL_MAP_DB.keys()))
async def process_goal(message: types.Message, state: FSMContext):
    data = await state.get_data()
    data['goal_ID'] = GOAL_MAP_DB[message.text]
    user_id = str(message.from_user.id)
    chat_id = message.chat.id

    existing_user = await db.get_user(user_id)
    await db.add_user(user_id, data)
    await update_user_menu_button(bot, chat_id, True)

    if not existing_user:
        trial_end = date.today() + timedelta(days=30)
        await message.answer(
            f"✅ Всё готово! Я сохранил твои данные.\n\n"
            f"🎁 Вам выдана пробная подписка на 30 дней!\n"
            f"📅 Действует до: {trial_end.strftime('%d.%m.%Y')}\n\n"
            f"Теперь слева внизу появилась кнопка «Mini-App». Нажмите на неё для работы с приложением 😉",
            reply_markup=get_main_menu()
        )
    else:
        await message.answer(
            f"✅ Анкета успешно обновлена!\n\n"
            f"Ваши новые параметры сохранены и будут учитываться в приложении.\n"
            f"Кнопка меню снова активна!",
            reply_markup=get_main_menu()
        )
        
    await state.clear()

@dp.message(Survey.waiting_for_goal)
async def invalid_goal(message: types.Message):
    await message.answer("⚠️ Пожалуйста, выберите цель из кнопок ниже:", reply_markup=get_goal_keyboard())


@dp.message(F.text == "Продлить/купить подписку")
async def subscription_menu(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    active_sub = await db.get_active_subscription(user_id)

    if active_sub:
        end_date = active_sub['end_date'].strftime('%d.%m.%Y')
        await message.answer(
            f"📋 Ваша текущая подписка: {active_sub['period_name']}\n"
            f"📅 Действует до: {end_date}\n\n"
            "Выберите срок новой подписки:",
            reply_markup=get_subscription_keyboard()
        )
    else:
        await message.answer("Выберите срок подписки:", reply_markup=get_subscription_keyboard())
    
    await state.set_state(Survey.choosing_subscription)

@dp.message(Survey.choosing_subscription, F.text == "❌ Назад")
async def subscription_back(message: types.Message, state: FSMContext):
    await message.answer("Возвращаемся в главное меню 😎", reply_markup=get_main_menu())
    await state.clear()

@dp.message(Survey.choosing_subscription, F.text.in_(SUBSCRIPTION_MAP.keys()))
async def choose_plan(message: types.Message, state: FSMContext):
    plan_text = message.text
    plan_info = SUBSCRIPTION_MAP[plan_text]

    await state.update_data(
        plan=plan_text,
        sub_period_ID=plan_info['sub_period_ID'],
        days=plan_info['days']
    )
    await message.answer(f"Вы выбрали подписку: {plan_text}.\nПроводим оплату?", reply_markup=get_confirm_keyboard())
    await state.set_state(Survey.confirm_payment)

@dp.message(Survey.confirm_payment, F.text == "Да")
async def confirm_yes(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = str(message.from_user.id)
    chat_id = message.chat.id

    sub_info = await db.create_or_extend_subscription(
        user_id=user_id,
        sub_period_id=data['sub_period_ID'],
        days=data['days']
    )

    end_date = sub_info['end_date'].strftime('%d.%m.%Y')
    status_text = "✅ Подписка продлена!\n" if sub_info['action'] == 'extended' else "✅ Подписка оформлена!\n"

    await update_user_menu_button(bot, chat_id, True)

    await message.answer(
        f"{status_text}"
        f"📋 Тариф: {sub_info['period_name']}\n"
        f"📅 Действует до: {end_date}\n\n"
        f"Доступ к мини-приложению открыт! 🎉",
        reply_markup=get_main_menu()
    )
    await state.clear()

@dp.message(Survey.confirm_payment, F.text == "Нет")
async def confirm_no_payment(message: types.Message, state: FSMContext):
    await message.answer("Покупка отменена. Выберите срок подписки или вернитесь назад:", reply_markup=get_subscription_keyboard())
    await state.set_state(Survey.choosing_subscription)

def get_plural_workout(n):
    if 11 <= n % 100 <= 19:
        return "тренировок"
    rem = n % 10
    if rem == 1:
        return "тренировка"
    if 2 <= rem <= 4:
        return "тренировки"
    return "тренировок"

async def check_and_send_notifications():
    try:
        now = datetime.now()
        notifications = await db.get_pending_notifications(now)
        if not notifications:
            return            
        notifications_sorted = sorted(notifications, key=lambda x: x['user_ID'])        
        for user_id, user_notifs in groupby(notifications_sorted, key=lambda x: x['user_ID']):
            user_notifs_list = list(user_notifs)
            workout_names = []            
            for notif in user_notifs_list:
                if notif.get('program_ID'):
                    name = notif.get('program_name') or f"Индивидуальная программа"
                elif notif.get('category_ID'):
                    name = {1: 'Йога', 2: 'Руки', 3: 'Стретчинг',
                            4: 'Спина', 5: 'Ноги', 6: 'Все тело'}.get(notif['category_ID'], 'Тренировка')
                else:
                    name = 'Тренировка'
                
                workout_names.append(name)
            unique_names = list(dict.fromkeys(workout_names))
            count = len(unique_names)
            if count == 1:
                text = f"💪 Пора тренироваться!\n\nСегодня у вас: {unique_names[0]}."
            else:
                word = get_plural_workout(count)
                text = f"💪 Пора тренироваться!\n\nСегодня у вас {count} {word}:\n"
                text += "\n".join(f"• {name}" for name in unique_names)
            try:
                await bot.send_message(chat_id=user_id, text=text)
                for notif in user_notifs_list:
                    await db.mark_notification_sent(notif['notification_ID'])
                logging.info(f"Отправлено уведомление пользователю {user_id}: {count} тренировок")
            except Exception as e:
                logging.error(f"Ошибка отправки пользователю {user_id}: {e}")            
            await asyncio.sleep(0.5)  
    except Exception as e:
        logging.error(f"Ошибка в check_and_send_notifications: {e}")

async def start_notification_scheduler():
    while True:
        await check_and_send_notifications()
        await asyncio.sleep(60)


async def main():
    await db.create_pool()
    logging.info("✅ БД подключена")

    asyncio.create_task(start_notification_scheduler())
    logging.info("✅ Сервис уведомлений запущен")

    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    asyncio.run(main())