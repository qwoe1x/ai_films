import os
import re
import requests
import telebot
from telebot import types
from dotenv import load_dotenv
from g4f.client import Client

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Client()
user_data = {}

def log_action(message):
    print(f"[LOG] User {message.chat.id}: {message.text}")

def ask_ai(prompt: str) -> str:
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-4o"
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[AI ERROR] {str(e)}")
        return None

@bot.message_handler(commands=['start'])
def send_welcome(message):
    log_action(message)
    bot.reply_to(
        message,
        "🎬 Привіт! Опиши, який фільм хочеш подивитись"
    )

@bot.message_handler(func=lambda message: True)
def handle_user_message(message):
    chat_id = message.chat.id
    text = message.text.strip()

    if chat_id in user_data and text in user_data[chat_id]:
        show_film_details(message)
        return

    try:
        log_action(message)
        bot.send_message(chat_id, "⏳ Зачекайте, йде пошук фільмів...")

        prompt = (
            f"Користувач хоче подивитись фільм на основі опису: '{text}'. "
            f"Назви 10 підходящих фільмів у форматі: 1) Назва (рік); 2) Назва (рік). "
            f"Тільки список, без коментарів."
        )

        gpt_response = ask_ai(prompt)

        if not gpt_response:
            bot.reply_to(message, "Проблема з AI-сервісом. Спробуйте пізніше.")
            return

        films = [line.strip() for line in gpt_response.split('\n') if line.strip()][:10]

        if not films:
            bot.reply_to(message, "Не знайшов фільмів за таким описом. Спробуй інакше.")
            return

        user_data[chat_id] = films
        print(f"[BOT] Вибрані фільми: {films}")

        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
        for film in films:
            markup.add(types.KeyboardButton(film))

        bot.send_message(
            chat_id,
            "Ось що я знайшов:\n" + "\n".join(films) + "\n\nОберіть фільм:",
            reply_markup=markup
        )

    except Exception as e:
        print(f"[ERROR] handle_user_message: {e}")
        bot.reply_to(message, f"Виникла помилка: {str(e)}")

@bot.message_handler(func=lambda message: message.chat.id in user_data)
def show_film_details(message):
    try:
        log_action(message)
        selected_film = message.text
        films = user_data.get(message.chat.id, [])

        if selected_film not in films:
            bot.reply_to(message, "⚠ Оберіть фільм зі списку нижче.")
            return

        film_name = re.sub(r"^\d+\)\s*", "", selected_film)
        film_name = re.sub(r"\s*\(\d{4}\)", "", film_name).strip()

        print(f"[TMDb] Пошук: {film_name}")

        search_response = requests.get(
            f"https://api.themoviedb.org/3/search/movie",
            params={"query": film_name, "api_key": TMDB_API_KEY, "language": "uk"},
            timeout=10
        ).json()

        if not search_response.get("results"):
            bot.reply_to(message, "Не вдалося знайти інформацію про цей фільм.")
            return

        movie = search_response["results"][0]
        movie_id = movie["id"]

        details_response = requests.get(
            f"https://api.themoviedb.org/3/movie/{movie_id}",
            params={"api_key": TMDB_API_KEY, "language": "uk"},
            timeout=10
        ).json()

        title = details_response.get("title", "Невідомо")
        year = details_response.get("release_date", "N/A")[:4]
        rating = details_response.get("vote_average", "N/A")
        overview = details_response.get("overview", "Опис відсутній")
        genres = ", ".join([g["name"] for g in details_response.get("genres", [])])

        caption = (
            f"🎬 <b>{title}</b> ({year})\n"
            f"⭐ Рейтинг: <b>{rating}/10</b>\n"
            f"🎭 Жанр: <b>{genres or 'Невідомо'}</b>\n"
            f"📖 Сюжет: <i>{overview}</i>"
        )

        poster_path = details_response.get("poster_path")
        if poster_path:
            poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
            bot.send_photo(message.chat.id, poster_url, caption, parse_mode="HTML")
        else:
            bot.send_message(message.chat.id, caption, parse_mode="HTML")

    except Exception as e:
        print(f"[ERROR] show_film_details: {e}")
        bot.reply_to(message, f"Виникла помилка: {str(e)}")

if __name__ == "__main__":
    print("Bot started")
    bot.polling()
