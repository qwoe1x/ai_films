import os
import re
import requests
import sqlite3
import telebot
from telebot import types
from dotenv import load_dotenv
from g4f.client import Client
import threading
import time

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Client()
user_data = {}

DB_FILE = "user_films.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS recommendations (
        user_id INTEGER,
        film TEXT,
        genre TEXT,
        preferences TEXT
    )
    ''')
    conn.commit()
    conn.close()

def save_recommendation(user_id, film, genre, preferences):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO recommendations (user_id, film, genre, preferences) VALUES (?, ?, ?, ?)',
              (user_id, film, genre, preferences))
    conn.commit()
    conn.close()

def get_user_recommendations(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT DISTINCT film FROM recommendations WHERE user_id = ?', (user_id,))
    films = [row[0] for row in c.fetchall()]
    conn.close()
    return films

def clear_user_recommendations(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('DELETE FROM recommendations WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def cleanup_nodriver_file():
    try:
        nodriver_path = os.path.join("har_and_cookies", ".nodriver_is_open")
        if os.path.exists(nodriver_path):
            os.remove(nodriver_path)
            print(".nodriver_is_open deleted")
    except Exception as e:
        print(f".nodriver_is_open delete fail {e}")

def ask_ai(prompt: str) -> str:
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-4",
            web_search=False
        )
        content = response.choices[0].message.content

        content = re.sub(r'https?://\S+|www\.\S+|\S+\.(com|org|net|ua|ru|info|tv|ly|to|gg|ai)\b', '', content, flags=re.IGNORECASE)

        content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)

        return content.replace("*", "").strip()
    except Exception as e:
        print(f"[AI ERROR] {str(e)}")
        cleanup_nodriver_file()
        return None

def ask_ai_with_timeout(prompt: str, timeout: int = 30):
    result = {"response": None}

    def task():
        result["response"] = ask_ai(prompt)

    thread = threading.Thread(target=task)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        cleanup_nodriver_file()
        return None
    return result["response"]

def get_retry_markup(is_history=False):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if is_history:
        markup.add("🔁 Повторити пошук схожих", "⬅️ Повернутись в головне меню")
    else:
        markup.add("🔁 Повторити підбір", "⬅️ Повернутись в головне меню")
    return markup

@bot.message_handler(func=lambda msg: msg.text == "🔁 Повторити підбір")
def retry_recommendation(message):
    chat_id = message.chat.id
    generate_personal_recommendation(chat_id)

@bot.message_handler(func=lambda msg: msg.text == "🔁 Повторити пошук схожих")
def retry_history(message):
    chat_id = message.chat.id
    selected = user_data.get(chat_id, {}).get("last_selected_film")
    if not selected:
        bot.send_message(chat_id, "⚠️ Немає збереженого фільму для повтору.")
        return
    handle_similar_search(chat_id, selected)

def handle_similar_search(chat_id, selected):
    user_data[chat_id]["last_selected_film"] = selected

    prompt = (
        f"Користувач бачив фільм '{selected}' і хоче щось схоже у цьому ж жанрі або настрої. "
        f"Порекомендуй 5 схожих фільмів. Формат: 1) Назва (рік); 2) Назва (рік); ... Без коментарів."
    )

    searching_msg = bot.send_message(chat_id, "🔍 Шукаю схожі фільми...", reply_markup=types.ReplyKeyboardRemove())
    
    start_time = time.time()
    gpt_response = ask_ai_with_timeout(prompt)
    elapsed_time = time.time() - start_time

    try:
        bot.delete_message(chat_id, searching_msg.message_id)
    except:
        pass

    if not gpt_response:
        bot.send_message(chat_id, "⚠ Час очікування вичерпано. Спробуйте ще раз:", reply_markup=get_retry_markup(is_history=True))
        return

    films = [line.strip().replace("*", "") for line in gpt_response.split('\n') if line.strip()][:5]
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for film in films:
        markup.add(film)
    markup.add("⬅️ Повернутись в головне меню")

    bot.send_message(chat_id, "\n".join(films), reply_markup=markup)
    user_data[chat_id]['recommendations'] = films
    user_data[chat_id]['step'] = 'done'

@bot.message_handler(func=lambda msg: user_data.get(msg.chat.id, {}).get('step') == 'similar' and msg.text != "🗑 Очистити історію")
def handle_similar_film(message):
    chat_id = message.chat.id
    selected = message.text.strip()

    if selected == "⬅️ Повернутись в головне меню":
        send_welcome(message)
        return

    handle_similar_search(chat_id, selected)

@bot.message_handler(func=lambda msg: msg.text == "🗑 Очистити історію")
def clear_history(message):
    chat_id = message.chat.id
    clear_user_recommendations(chat_id)
    bot.send_message(chat_id, "✅ Історію очищено. Натисни ⬅️ щоб повернутись.")
    user_data[chat_id]['step'] = 'history_cleared'

def generate_personal_recommendation(chat_id):
    data = user_data[chat_id]
    genre = data['genre']
    favorites = ", ".join(data['favorites'])
    preferences = data['preferences']

    prompt = (
        f"Користувач любить фільми: {favorites or 'не вказано'}. "
        f"Його бажаний жанр — {genre or 'не вказано'}. Він хоче, щоб у фільмах було: {preferences or 'не вказано'}. "
        f"Назви 5 фільмів у жанрі {genre or 'будь-якому'} з елементами {preferences or 'будь-якими'}, які схожі на перелічені вище. "
        f"Формат: 1) Назва (рік); 2) Назва (рік); ... Без коментарів."
    )

    searching_msg = bot.send_message(chat_id, "⏳ Шукаю найкращі варіанти для вас...", reply_markup=types.ReplyKeyboardRemove())
    
    start_time = time.time()
    gpt_response = ask_ai_with_timeout(prompt)
    elapsed_time = time.time() - start_time

    try:
        bot.delete_message(chat_id, searching_msg.message_id)
    except:
        pass

    if not gpt_response:
        bot.send_message(chat_id, "⚠ Час очікування вичерпано. Спробуйте ще раз:", reply_markup=get_retry_markup(is_history=False))
        return

    films = [line.strip().replace("*", "") for line in gpt_response.split('\n') if re.match(r"^\d+\)", line.strip())][:5]
    user_data[chat_id]['recommendations'] = films

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for film in films:
        clean_film = re.sub(r"^\d+\)\s*", "", film)
        markup.add(clean_film)
        save_recommendation(chat_id, clean_film, genre, preferences)

    markup.add("⬅️ Повернутись в головне меню")

    bot.send_message(
        chat_id,
        "📽 Ось мої рекомендації для тебе:\n" + "\n".join(films) + "\n\nМожеш обрати фільм, щоб отримати більше інформації:",
        reply_markup=markup
    )

def get_continue_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("⏭️ Пропустити", "⬅️ Повернутись в головне меню")
    return markup

def is_valid_input(text):
    return bool(re.match(r"^[a-zA-Zа-яА-ЯіІїЇєЄґҐ\s,]+$", text.strip()))

@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    user_data[chat_id] = {
        'step': None,
        'genre': '',
        'favorites': [],
        'preferences': ''
    }
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🔍 Новий підбір фільмів", "📜 Історія рекомендацій")

    intro_text = (
        "👋 Привіт! Я - бот створений з використанням штучного інтелекту для рекомендації фільмів.\n\n"
        "🎯 Я допоможу тобі знайти цікаві фільми на основі:\n"
        "• жанру 🎭\n"
        "• улюблених фільмів 🎞\n"
        "• особистих побажань ✨\n\n"
        "📌 Ти можеш:\n"
        "1️⃣ Почати новий підбір фільмів — натисни «🔍 Новий підбір фільмів»\n"
        "2️⃣ Переглянути історію своїх рекомендацій — «📜 Історія рекомендацій», щоб знайти *схожі фільми* на ті, що тобі вже сподобались\n\n"
        "Обери дію нижче:"
    )

    bot.send_message(chat_id, intro_text, reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda msg: msg.text == "📜 Історія рекомендацій")
def show_previous_films(message):
    chat_id = message.chat.id
    films = get_user_recommendations(chat_id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("⬅️ Повернутись в головне меню")

    if not films:
        bot.send_message(chat_id, "😔 У вас ще немає збережених рекомендацій.", reply_markup=markup)
        return

    for film in films:
        markup.add(film)

    markup.add("🗑 Очистити історію")
    bot.send_message(
        chat_id,
        "🔁 Обери фільм зі списку нижче, щоб я знайшов *схожі фільми* на нього. Також можна очистити історію, якщо потрібно.",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    user_data[chat_id] = {'step': 'similar', 'last_action': 'show_history'}

@bot.message_handler(func=lambda msg: msg.text == "⬅️ Повернутись в головне меню")
def back_to_menu(message):
    send_welcome(message)

@bot.message_handler(func=lambda msg: msg.text == "🔍 Новий підбір фільмів")
def start_new_recommendation(message):
    chat_id = message.chat.id
    user_data[chat_id] = {
        'step': 'genre',
        'genre': '',
        'favorites': [],
        'preferences': ''
    }
    bot.send_message(chat_id, "🎭 Напиши бажаний жанр або натисни ⏭️ Пропустити:", reply_markup=get_continue_markup())

@bot.message_handler(func=lambda msg: user_data.get(msg.chat.id, {}).get('step') == 'genre')
def handle_genre(message):
    chat_id = message.chat.id
    if message.text == "⬅️ Повернутись в головне меню":
        send_welcome(message)
        return

    if message.text != "⏭️ Пропустити":
        if not is_valid_input(message.text):
            bot.send_message(chat_id, "⚠️ Жанр повинен містити лише літери та пробіли. Спробуй ще раз.")
            return
        genre = message.text.strip()
    else:
        genre = ""

    user_data[chat_id]['genre'] = genre
    user_data[chat_id]['step'] = 'favorites'
    bot.send_message(chat_id, "🎞 Напиши улюблені фільми (через кому) або натисни ⏭️ Пропустити:", reply_markup=get_continue_markup())

@bot.message_handler(func=lambda msg: user_data.get(msg.chat.id, {}).get('step') == 'favorites')
def handle_favorites(message):
    chat_id = message.chat.id
    if message.text == "⬅️ Повернутись в головне меню":
        send_welcome(message)
        return

    if message.text != "⏭️ Пропустити":
        favorites_raw = [f.strip() for f in message.text.split(',') if f.strip()]
        invalid = [f for f in favorites_raw if not is_valid_input(f)]
        if invalid:
            bot.send_message(chat_id, f"⚠️ Ці назви містять недопустимі символи: {', '.join(invalid)}. Спробуй ще раз.")
            return
        favorites = favorites_raw
    else:
        favorites = []

    user_data[chat_id]['favorites'] = favorites
    user_data[chat_id]['step'] = 'preferences'
    bot.send_message(chat_id, "✨ Що хочеш бачити у фільмі? (або натисни ⏭️ Пропустити):", reply_markup=get_continue_markup())

@bot.message_handler(func=lambda msg: user_data.get(msg.chat.id, {}).get('step') == 'preferences')
def handle_preferences(message):
    chat_id = message.chat.id
    if message.text == "⬅️ Повернутись в головне меню":
        send_welcome(message)
        return

    if message.text != "⏭️ Пропустити":
        if not is_valid_input(message.text):
            bot.send_message(chat_id, "⚠️ Побажання можуть містити лише літери, пробіли та кому. Спробуй ще раз.")
            return
        prefs = message.text.strip()
    else:
        prefs = ""

    genre = user_data[chat_id].get('genre', '').strip()
    favorites = user_data[chat_id].get('favorites', [])

    if not genre and not favorites and not prefs:
        bot.send_message(chat_id, "⚠️ Потрібно вказати хоча б один параметр: жанр, улюблені фільми або побажання. Спробуйте знову.")
        user_data[chat_id]['step'] = 'genre'
        bot.send_message(chat_id, "🎭 Напиши бажаний жанр (або натисни ⏭️ Пропустити):", reply_markup=get_continue_markup())
        return

    user_data[chat_id]['preferences'] = prefs
    user_data[chat_id]['step'] = 'done'
    generate_personal_recommendation(chat_id)

@bot.message_handler(func=lambda message: user_data.get(message.chat.id, {}).get('recommendations'))
def show_film_details(message):
    try:
        chat_id = message.chat.id
        selected_film = message.text.strip()

        film_name = selected_film
        film_name = re.sub(r"^\d+\)\s*", "", film_name)
        film_name = re.sub(r"\s*\(\d{4}\)", "", film_name)
        film_name = film_name.strip()

        search_response = requests.get(
            f"https://api.themoviedb.org/3/search/movie",
            params={"query": film_name, "api_key": TMDB_API_KEY, "language": "uk"},
            timeout=10
        ).json()

        if not search_response.get("results"):
            bot.send_message(chat_id, f"😔 Не вдалося знайти інформацію про фільм: {film_name}")
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
            bot.send_photo(chat_id, poster_url, caption, parse_mode="HTML")
        else:
            bot.send_message(chat_id, caption, parse_mode="HTML")

    except Exception as e:
        print(f"[ERROR] show_film_details: {e}")
        bot.send_message(chat_id, f"⚠ Виникла помилка: {str(e)}")

if __name__ == "__main__":
    init_db()
    print("Bot started")
    bot.polling()