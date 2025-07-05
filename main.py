import logging
import os
import json
import asyncio
from io import BytesIO
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

import gspread
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from google.oauth2.service_account import Credentials
pdfmetrics.registerFont(TTFont('DejaVuSans', 'DejaVuSans.ttf'))

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import smtplib
from email.message import EmailMessage

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_fake_server():
    server_address = ('0.0.0.0', 10000)
    httpd = HTTPServer(server_address, HealthHandler)
    httpd.serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip().isdigit()]

# Создаем временный файл credentials из переменной окружения
credentials_json = os.getenv('GOOGLE_CREDENTIALS')
if credentials_json:
    with open('temp_credentials.json', 'w') as f:
        f.write(credentials_json)
    GOOGLE_CREDENTIALS_FILE = 'temp_credentials.json'
else:
    GOOGLE_CREDENTIALS_FILE = "credentials.json"

class DiplomaBot:
    def __init__(self):
        self.gc = None
        self.sheet = None
        self.participants_data = []
        self.last_update = None
        self.update_interval = 30 * 60  # 30 минут в секундах
        self.init_google_sheets()

    def init_google_sheets(self):
        """Инициализация подключения к Google Sheets"""
        try:
            # Настройка аутентификации
            scope = ['https://spreadsheets.google.com/feeds',
                    'https://www.googleapis.com/auth/drive']

            creds = Credentials.from_service_account_file(
                GOOGLE_CREDENTIALS_FILE, scopes=scope)

            self.gc = gspread.authorize(creds)
            self.sheet = self.gc.open_by_key(GOOGLE_SHEET_ID).sheet1

            # Загрузка данных участников
            self.load_participants_data()
            self.last_update = datetime.now()

        except Exception as e:
            logger.error(f"Ошибка инициализации Google Sheets: {e}")

    def load_participants_data(self):
        """Загрузка данных участников из Google Sheets"""
        try:
            # Получаем все данные из таблицы
            records = self.sheet.get_all_records()
            self.participants_data = records
            self.last_update = datetime.now()
            logger.info(f"Загружено {len(records)} участников в {self.last_update}")
            logger.info(f"Пример первой записи: {records[0] if records else 'Нет данных'}")    
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")

    def add_participant(self, name: str, email: str, role: str, points: int) -> bool:
        """Добавление нового участника в таблицу"""
        try:
            # Добавляем новую строку в таблицу
            self.sheet.append_row([name, email, role, points])
            # Обновляем локальные данные
            self.load_participants_data()
            logger.info(f"Добавлен участник: {name}, {email}, {role}, {points}")
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления участника: {e}")
            return False

    def remove_participant(self, email: str) -> bool:
        """Удаление участника по email"""
        try:
            # Находим строку с указанным email
            all_values = self.sheet.get_all_values()
            for i, row in enumerate(all_values):
                if len(row) > 1 and row[1].lower() == email.lower():
                    # Удаляем строку (нумерация начинается с 1)
                    self.sheet.delete_rows(i + 1)
                    # Обновляем локальные данные
                    self.load_participants_data()
                    logger.info(f"Удален участник с email: {email}")
                    return True
            return False
        except Exception as e:
            logger.error(f"Ошибка удаления участника: {e}")
            return False

    def find_participant(self, query: str) -> Optional[Dict]:
        """Поиск участника по email или ФИО"""
        query_lower = query.lower().strip()

        for participant in self.participants_data:
            # Поиск по email
            if 'email' in participant and participant['email'].lower() == query_lower:
                return participant

            # Поиск по ФИО
            name_field = participant.get('имя') or participant.get('name')
            if name_field:
                name_lower = name_field.lower().strip()
                name_words = set(name_lower.split())
                query_words = set(query_lower.split())

                # Все слова запроса должны присутствовать в имени
                if query_words.issubset(name_words):
                    return participant

        return None

    def determine_diploma_type(self, participant: Dict) -> Tuple[str, str]:
        """Определение типа диплома на основе данных участника"""
        role = participant.get('роль', '').lower()
        raw_points = participant.get('баллы', 0)
        try:
            points = int(raw_points) if str(raw_points).strip().isdigit() else 0
        except Exception:
            points = 0

        # Определяем тип диплома
        if 'организатор' in role:
            return 'Диплом организатора', 'за организацию конференции'
        elif 'спикер' in role or 'докладчик' in role:
            return 'Диплом спикера', 'за выступление на конференции'
        elif points >= 50:  # Порог для активного участия
            return 'Диплом за активное участие', 'за активное участие в конференции'
        elif 'призер' in role:
            return 'Диплом призера', 'за призовое место'
        elif points >= 20:  # Минимальный порог
            return 'Диплом участника', 'за участие в конференции'
        else:
            return None, None

    def generate_diploma_pdf(self, participant: Dict, diploma_type: str, description: str) -> BytesIO:
        """Генерация PDF диплома"""
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []

        # Стили
        styles = getSampleStyleSheet()
        title_style = styles['Title']
        title_style.alignment = TA_CENTER
        title_style.fontSize = 24
        title_style.fontName = 'DejaVuSans'

        normal_style = styles['Normal']
        normal_style.alignment = TA_CENTER
        normal_style.fontSize = 14
        normal_style.fontName = 'DejaVuSans'

        # Содержание диплома
        story.append(Spacer(1, 3*cm))
        story.append(Paragraph(diploma_type, title_style))
        story.append(Spacer(1, 2*cm))

        story.append(Paragraph("Настоящий диплом выдается", normal_style))
        story.append(Spacer(1, 1*cm))

        name_style = styles['Heading1']
        name_style.alignment = TA_CENTER
        name_style.fontSize = 20
        name_style.fontName = 'DejaVuSans'
        story.append(Paragraph(participant.get('имя', participant.get('name', 'Участник')), name_style))

        story.append(Spacer(1, 1*cm))
        story.append(Paragraph(description, normal_style))
        story.append(Spacer(1, 2*cm))

        story.append(Paragraph("Организаторы конференции", normal_style))
        story.append(Spacer(1, 1*cm))

        story.append(Paragraph(f"Дата: {datetime.now().strftime('%d.%m.%Y')}", normal_style))

        # Создание PDF
        doc.build(story)
        buffer.seek(0)
        return buffer

    def get_data_status(self) -> str:
        """Получение статуса данных"""
        if not self.last_update:
            return "Данные не загружены"
        
        time_since_update = datetime.now() - self.last_update
        minutes_ago = int(time_since_update.total_seconds() / 60)
        
        return f"Данные обновлены {minutes_ago} минут назад\nВсего участников: {len(self.participants_data)}"

def send_email_with_attachment(to_email: str, pdf_buffer: BytesIO, filename: str):
    """Отправка PDF диплома по email"""
    try:
        msg = EmailMessage()
        msg['Subject'] = 'Ваш диплом конференции'
        msg['From'] = os.getenv('EMAIL_USER')
        msg['To'] = to_email
        msg.set_content('Здравствуйте!\n\nВо вложении — ваш диплом.\n\nС уважением, команда конференции.')

        # Прикрепляем PDF
        pdf_data = pdf_buffer.read()
        msg.add_attachment(pdf_data, maintype='application', subtype='pdf', filename=filename)

        # Отправка
        with smtplib.SMTP(os.getenv('EMAIL_HOST'), int(os.getenv('EMAIL_PORT'))) as smtp:
            smtp.starttls()
            smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASS'))
            smtp.send_message(msg)

        logger.info(f"📧 Email успешно отправлен на {to_email}")

    except Exception as e:
        logger.error(f"Ошибка отправки email: {e}")

# Функция автоматического обновления данных (исправленная)
async def auto_update_data(context: ContextTypes.DEFAULT_TYPE):
    """Автоматическое обновление данных каждые 30 минут"""
    try:
        bot = context.bot_data.get('diploma_bot')
        if bot:
            bot.load_participants_data()
            logger.info("🔄 Автоматическое обновление данных выполнено")
        else:
            logger.warning("⚠️ DiplomaBot не найден в bot_data")
    except Exception as e:
        logger.error(f"❌ Ошибка автоматического обновления: {e}")

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь администратором"""
    return user_id in ADMIN_IDS

def get_main_keyboard(is_admin_user: bool = False):
    """Получение основной клавиатуры"""
    keyboard = [
        [KeyboardButton("📜 Запросить диплом")],
        [KeyboardButton("🔄 Обновить данные"), KeyboardButton("ℹ️ Статус данных")]
    ]

    if is_admin_user:
        keyboard.append([KeyboardButton("👑 Админ панель")])

    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard():
    """Получение клавиатуры для админов"""
    keyboard = [
        [KeyboardButton("➕ Добавить участника")],
        [KeyboardButton("➖ Удалить участника")],
        [KeyboardButton("🔙 Главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user_id = update.effective_user.id
    is_admin_user = is_admin(user_id)

    welcome_text = """
🎓 Добро пожаловать в систему выдачи дипломов!

Для получения диплома отправьте мне:
• Ваш email
• Или ваше ФИО

Я найду вас в базе участников и сгенерирую персональный диплом.

Команды:
/diploma - запросить диплом
/help - помощь
"""

    if is_admin_user:
        welcome_text += "\n👑 Вы - администратор. Доступна админ панель."

    reply_markup = get_main_keyboard(is_admin_user)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def diploma_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /diploma"""
    await update.message.reply_text(
        "📧 Отправьте мне ваш email или ФИО для поиска в базе участников:"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = """
🆘 Помощь по использованию бота:

1. Отправьте ваш email или ФИО
2. Бот найдет вас в базе участников
3. Если вы имеете право на диплом - он будет сгенерирован и отправлен

Типы дипломов:
• Диплом участника
• Диплом спикера
• Диплом организатора
• Диплом за активное участие
• Диплом призера

Кнопки:
🔄 Обновить данные - принудительно обновить базу участников
ℹ️ Статус данных - информация о последнем обновлении

❓ Если возникли проблемы - обратитесь к организаторам конференции.
"""
    await update.message.reply_text(help_text)

async def handle_refresh_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки обновления данных"""
    await update.message.reply_text("🔄 Обновляю данные…")

    bot = context.bot_data.get('diploma_bot')
    if not bot:
        await update.message.reply_text("❌ Ошибка подключения к базе данных")
        return

    try:
        bot.load_participants_data()
        status = bot.get_data_status()
        await update.message.reply_text(f"✅ Данные успешно обновлены!\n\n{status}")
    except Exception as e:
        await update.message.reply_text("❌ Ошибка при обновлении данных")
        logger.error(f"Ошибка обновления данных: {e}")

async def handle_data_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки статуса данных"""
    bot = context.bot_data.get('diploma_bot')
    if not bot:
        await update.message.reply_text("❌ Ошибка подключения к базе данных")
        return

    status = bot.get_data_status()
    await update.message.reply_text(f"📊 Статус данных:\n\n{status}")

async def handle_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки админ панели"""
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав доступа к админ панели")
        return

    await update.message.reply_text(
        "👑 Админ панель\n\nВыберите действие:",
        reply_markup=get_admin_keyboard()
    )

async def handle_add_participant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка добавления участника"""
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав доступа к этой функции")
        return

    await update.message.reply_text(
        "➕ Добавление участника\n\n"
        "Отправьте данные в формате:\n"
        "ФИО,email,роль,баллы\n\n"
        "Например: Иванов Иван Иванович,ivan@example.com,участник,25"
    )

    context.user_data['action'] = 'add_participant'

async def handle_remove_participant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка удаления участника"""
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав доступа к этой функции")
        return

    await update.message.reply_text(
        "➖ Удаление участника\n\n"
        "Отправьте email участника для удаления:"
    )

    context.user_data['action'] = 'remove_participant'

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в главное меню"""
    user_id = update.effective_user.id
    is_admin_user = is_admin(user_id)

    context.user_data.clear()  # Очищаем данные действий

    await update.message.reply_text(
        "🏠 Главное меню",
        reply_markup=get_main_keyboard(is_admin_user)
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    query = update.message.text

    # Обработка кнопок
    if query == "📜 Запросить диплом":
        await diploma_command(update, context)
        return
    elif query == "🔄 Обновить данные":
        await handle_refresh_data(update, context)
        return
    elif query == "ℹ️ Статус данных":
        await handle_data_status(update, context)
        return
    elif query == "👑 Админ панель":
        await handle_admin_panel(update, context)
        return
    elif query == "➕ Добавить участника":
        await handle_add_participant(update, context)
        return
    elif query == "➖ Удалить участника":
        await handle_remove_participant(update, context)
        return
    elif query == "🔙 Главное меню":
        await handle_main_menu(update, context)
        return

    # Обработка админских действий
    user_action = context.user_data.get('action')

    if user_action == 'add_participant':
        await process_add_participant(update, context, query)
        return
    elif user_action == 'remove_participant':
        await process_remove_participant(update, context, query)
        return

    # Обычный поиск участника для диплома
    await process_diploma_request(update, context, query)

async def process_add_participant(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Обработка добавления участника"""
    try:
        parts = [part.strip() for part in data.split(',')]
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Неверный формат данных!\n"
                "Используйте: ФИО,email,роль,баллы"
            )
            return

        name, email, role, points_str = parts
        
        try:
            points = int(points_str)
        except ValueError:
            await update.message.reply_text("❌ Баллы должны быть числом!")
            return
        
        bot = context.bot_data.get('diploma_bot')
        if not bot:
            await update.message.reply_text("❌ Ошибка подключения к базе данных")
            return
        
        if bot.add_participant(name, email, role, points):
            await update.message.reply_text(
                f"✅ Участник успешно добавлен:\n"
                f"ФИО: {name}\n"
                f"Email: {email}\n"
                f"Роль: {role}\n"
                f"Баллы: {points}"
            )
        else:
            await update.message.reply_text("❌ Ошибка при добавлении участника")
        
        context.user_data.clear()
        
    except Exception as e:
        await update.message.reply_text("❌ Ошибка при обработке данных")
        logger.error(f"Ошибка добавления участника: {e}")

async def process_remove_participant(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str):
    """Обработка удаления участника"""
    try:
        bot = context.bot_data.get('diploma_bot')
        if not bot:
            await update.message.reply_text("❌ Ошибка подключения к базе данных")
            return

        if bot.remove_participant(email):
            await update.message.reply_text(f"✅ Участник с email {email} успешно удален")
        else:
            await update.message.reply_text(f"❌ Участник с email {email} не найден")
        
        context.user_data.clear()
        
    except Exception as e:
        await update.message.reply_text("❌ Ошибка при удалении участника")
        logger.error(f"Ошибка удаления участника: {e}")

async def process_diploma_request(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    """Обработка запроса на диплом"""
    # Поиск участника
    await update.message.reply_text("🔍 Ищу вас в базе участников…")

    bot = context.bot_data.get('diploma_bot')
    if not bot:
        await update.message.reply_text("❌ Ошибка подключения к базе данных")
        return

    participant = bot.find_participant(query)

    if not participant:
        await update.message.reply_text(
            "❌ Участник не найден в базе данных.\n"
            "Проверьте правильность написания email или ФИО."
        )
        return

    # Определение типа диплома
    diploma_type, description = bot.determine_diploma_type(participant)

    if not diploma_type:
        await update.message.reply_text(
            "❌ К сожалению, диплом для вас не предусмотрен.\n"
            "Возможно, не выполнены условия для получения диплома."
        )
        return

    # Генерация диплома
    try:
        await update.message.reply_text(f"✅ Найден участник: {participant.get('имя', participant.get('name'))}")
        await update.message.reply_text(f"📜 Генерирую {diploma_type.lower()}...")

        pdf_buffer = bot.generate_diploma_pdf(participant, diploma_type, description)

        # Отправка PDF
        filename = f"diploma_{participant.get('имя', 'participant').replace(' ', '_')}.pdf"
        await update.message.reply_document(
            document=pdf_buffer,
            filename=filename,
            caption=f"🎓 Ваш {diploma_type.lower()} готов!"
        )
        
        # Отправка PDF на email
        recipient_email = participant.get('email')
        if recipient_email:
            try:
                # Вернуть указатель на начало файла
                pdf_buffer.seek(0)
                send_email_with_attachment(recipient_email, pdf_buffer, filename)
                await update.message.reply_text(f"📬 Диплом также отправлен на email: {recipient_email}")
            except Exception as e:
                await update.message.reply_text("⚠️ Не удалось отправить диплом по email.")
                logger.error(f"Ошибка при отправке email: {e}")
                
    except Exception as e:
        logger.error(f"Ошибка генерации диплома: {e}")
        await update.message.reply_text(
            "❌ Ошибка при генерации диплома. Обратитесь к организаторам."
        )

def main():
    """Основная функция запуска бота"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан!")
        return

    if not GOOGLE_SHEET_ID:
        logger.error("GOOGLE_SHEET_ID не задан!")
        return

    # Создание приложения
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Инициализация бота
    diploma_bot = DiplomaBot()
    application.bot_data['diploma_bot'] = diploma_bot

    # Добавление задачи автоматического обновления данных (ИСПРАВЛЕНО)
    job_queue = application.job_queue
    job_queue.run_repeating(
        auto_update_data,
        interval=1800,  # 30 минут = 1800 секунд
        first=1800,     # Первое выполнение через 30 минут после запуска
        name='auto_update_data'
    )
    logger.info("🔄 Автоматическое обновление данных запланировано каждые 30 минут")

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("diploma", diploma_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Запуск бота
    logger.info("🚀 Бот запущен...")
    logger.info(f"👑 Администраторы: {ADMIN_IDS}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
