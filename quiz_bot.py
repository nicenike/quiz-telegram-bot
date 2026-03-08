import json
import random
import os
from pathlib import Path
from datetime import time, datetime


from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    PollAnswerHandler,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))

QUIET_FROM = 22
QUIET_TO = 8
POST_MINUTE = 0  # каждый час ровно в :00

BASE_DIR = Path(__file__).resolve().parent
SCORES_FILE = BASE_DIR / "scores.json"
STATE_FILE = BASE_DIR / "state.json"
QUIZZES_FILE = BASE_DIR / "quizzes.json"


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

scores = load_json(SCORES_FILE, {})
state = load_json(
    STATE_FILE,
    {
        "last_quiz_index": None,
        "polls": {}
    },
)

QUIZZES = load_json(QUIZZES_FILE, [])


if not QUIZZES:
    raise RuntimeError("Файл quizzes.json пустой или сломан.")

# на случай, если state.json старый
if "polls" not in state:
    state["polls"] = {}
if "last_quiz_index" not in state:
    state["last_quiz_index"] = None

def in_quiet_hours(hour: int) -> bool:
    return hour >= QUIET_FROM or hour < QUIET_TO

def display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return full_name or "Без имени"

def player_level(points: int) -> str:
    if points >= 100:
        return "👑 Легенда"
    elif points >= 50:
        return "🏆 Мастер"
    elif points >= 25:
        return "🥇 Знаток"
    elif points >= 10:
        return "📘 Эрудит"
    else:
        return "🌱 Новичок"

def next_level_info(points: int):
    levels = [
        (0, "🌱 Новичок"),
        (10, "📘 Эрудит"),
        (25, "🥇 Знаток"),
        (50, "🏆 Мастер"),
        (100, "👑 Легенда"),
    ]

    for required_points, level_name in levels:
        if points < required_points:
            return level_name, required_points - points

    return None, 0

def difficulty_label(level: str) -> str:
    labels = {
        "easy": "🟢 Лёгкая",
        "medium": "🟡 Средняя",
        "hard": "🔴 Сложная",
    }
    return labels.get(level, "⚪ Обычная")

def main_menu():
    keyboard = [
        ["🎮 Новая викторина"],
        ["🏆 Лидерборд"],
        ["👤 Мой профиль"]
    ]

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True
    )

async def send_quiz(context: ContextTypes.DEFAULT_TYPE):
    last_quiz_index = state.get("last_quiz_index")
    available = list(range(len(QUIZZES)))

    if last_quiz_index in available and len(available) > 1:
        available.remove(last_quiz_index)

    quiz_index = random.choice(available)
    quiz = QUIZZES[quiz_index]

    difficulty = quiz.get("difficulty", "medium")
    label = difficulty_label(difficulty)

    message = await context.bot.send_poll(
        chat_id=CHAT_ID,
        question=f"{label} викторина\n\n{quiz['question']}",
        options=quiz["options"],
        type="quiz",
        correct_option_id=quiz["correct"],
        explanation=quiz.get("explanation", ""),
        is_anonymous=False,
        allows_multiple_answers=False,
    )

    state["last_quiz_index"] = quiz_index
    state["polls"][message.poll.id] = {
        "correct": quiz["correct"],
        "difficulty": difficulty,
    }
    save_json(STATE_FILE, state)
    print("QUIZ SENT")

async def hourly_post(context: ContextTypes.DEFAULT_TYPE):
    hour = datetime.now().hour

    if in_quiet_hours(hour):
        print("QUIET HOURS - SKIP")
        return

    await send_quiz(context)

async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    option_ids = answer.option_ids

    poll_info = state.get("polls", {}).get(poll_id)
    if not poll_info:
        print("NO POLL INFO FOUND")
        return

    correct_option = poll_info["correct"]
    difficulty = poll_info.get("difficulty", "medium")

    points_map = {
        "easy": 1,
        "medium": 2,
        "hard": 3,
    }

    earned_points = points_map.get(difficulty, 1)

    user_key = str(answer.user.id)
    user_name = display_name(answer.user)

    if user_key not in scores:
        scores[user_key] = {
            "name": user_name,
            "points": 0,
            "answers": 0,
            "correct_answers": 0
        }

    if "answers" not in scores[user_key]:
        scores[user_key]["answers"] = 0
    if "correct_answers" not in scores[user_key]:
        scores[user_key]["correct_answers"] = 0

    scores[user_key]["name"] = user_name
    scores[user_key]["answers"] += 1

    if option_ids and option_ids[0] == correct_option:
        scores[user_key]["points"] += earned_points
        scores[user_key]["correct_answers"] += 1
        save_json(SCORES_FILE, scores)

        total_points = scores[user_key]["points"]
        label = difficulty_label(difficulty)

        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"✅ {user_name} отвечает правильно!\n\n"
                f"{label} вопрос\n"
                f"➕ +{earned_points} очк.\n"
                f"🏆 Всего очков: {total_points}"
            )
        )

        print(f"POINT ADDED: {user_name} +{earned_points}")
    else:
        save_json(SCORES_FILE, scores)
        print(f"WRONG ANSWER: {user_name}")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not scores:
        await update.message.reply_text("Пока никто не набрал очков 🫠")
        return

    top = sorted(scores.values(), key=lambda x: x["points"], reverse=True)[:10]

    lines = ["🏆 Таблица лидеров:\n"]
    medals = ["🥇", "🥈", "🥉"]

    for i, player in enumerate(top, start=1):
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{prefix} {player['name']} — {player['points']}")

    await update.message.reply_text("\n".join(lines))

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_key = str(user.id)
    user_name = display_name(user)

    if user_key not in scores:
        text = (
            f"👤 Профиль игрока\n\n"
            f"Имя: {user_name}\n"
            f"Уровень: 🌱 Новичок\n"
            f"Очки: 0\n"
            f"Место в топе: —\n"
            f"Всего ответов: 0\n"
            f"Правильных ответов: 0\n"
            f"Точность: 0%\n"
            f"До следующего уровня: 10 очк.\n"
        )
        await update.message.reply_text(text)
        return

    player = scores[user_key]

    points = player.get("points", 0)
    answers = player.get("answers", 0)
    correct_answers = player.get("correct_answers", 0)

    level = player_level(points)
    next_level, points_left = next_level_info(points)

    sorted_players = sorted(
        scores.items(),
        key=lambda x: x[1].get("points", 0),
        reverse=True
    )
    rank = next((i + 1 for i, (uid, _) in enumerate(sorted_players) if uid == user_key), None)

    accuracy = 0
    if answers > 0:
        accuracy = round((correct_answers / answers) * 100)

    if next_level is None:
        next_level_text = "👑 Максимальный уровень достигнут"
    else:
        next_level_text = f"{points_left} очк. до {next_level}"

    text = (
        f"👤 Профиль игрока\n\n"
        f"Имя: {player.get('name', user_name)}\n"
        f"Уровень: {level}\n"
        f"Очки: {points}\n"
        f"Место в топе: {rank if rank else '—'}\n"
        f"Всего ответов: {answers}\n"
        f"Правильных ответов: {correct_answers}\n"
        f"Точность: {accuracy}%\n"
        f"До следующего уровня: {next_level_text}\n"
    )

    await update.message.reply_text(text)

async def postnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hour = datetime.now().hour

    if in_quiet_hours(hour):
        await update.message.reply_text("Сейчас тихие часы, но я всё равно отправлю викторину для проверки.")

    await send_quiz(context)
    await update.message.reply_text("Готово ✅")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет!\n\n"
        "Я бот-викторина.\n"
        "Нажми кнопку ниже чтобы играть.",
        reply_markup=main_menu()
    )

async def menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🎮 Новая викторина":
        await send_quiz(context)

    elif text == "🏆 Лидерборд":
        await leaderboard(update, context)

    elif text == "👤 Мой профиль":
        await profile(update, context)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("postnow", postnow))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_buttons))

    for hour in range(9, 21):
        app.job_queue.run_daily(
            hourly_post,
            time=time(hour=hour, minute=POST_MINUTE),
        )

    print("BOT STARTED ✅")
    app.run_polling()

if __name__ == "__main__":
    main()

from flask import Flask
import threading

app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Bot is running"

def run_web():
    app_web.run(host="0.0.0.0", port=8000)

if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    main()