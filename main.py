import logging
import os
import json
from io import BytesIO
from typing import Dict, List, Optional, Tuple, Set

import gspread
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
import asyncio
pdfmetrics.registerFont(TTFont('DejaVuSans', 'DejaVuSans.ttf'))  # путь к файлу .ttf

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

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

# Создаем временный файл credentials из переменной окружения
credentials_json = os.getenv('GOOGLE_CREDENTIALS')
if credentials_json:
    with open('temp_credentials.json', 'w') as f:
        f.write(credentials_json)
    GOOGLE_CREDENTIALS_FILE = 'temp_credentials.json'
else:
    GOOGLE_CREDENTIALS_FILE = "credentials.json"

from some_module import (
    GoogleSheetsService, DiplomaGenerator, EmailService, 
    start, diploma_command, help_command, 
    run_health_server, validate_environment, TELEGRAM_BOT_TOKEN,
    EMAIL_USER, logger
)

class GoogleSheetsService:
    def __init__(self):
        self.gc = None
        self.sheet = None
        self.participants_data = []
        self.last_update = None
        self.update_interval = 30 * 60  # 30 минут в секундах
        self._setup_credentials()
        self.init_google_sheets()

    def force_reload_data(self):
        """Принудительная перезагрузка данных"""
        try:
            logger.info("🔄 Принудительная перезагрузка данных из Google Sheets...")
            records = self.sheet.get_all_records()
            self.participants_data = records
            self.last_update = datetime.now()
            logger.info(f"✅ Данные обновлены. Загружено {len(records)} участников")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка принудительной перезагрузки: {e}")
            return False

    def is_data_outdated(self) -> bool:
        """Проверить, устарели ли данные"""
        if not self.last_update:
            return True
        return datetime.now() - self.last_update > timedelta(seconds=self.update_interval)

    def get_last_update_info(self) -> str:
        """Получить информацию о последнем обновлении"""
        if not self.last_update:
            return "Данные не загружены"

        time_diff = datetime.now() - self.last_update
        minutes_ago = int(time_diff.total_seconds() / 60)

        if minutes_ago < 1:
            return "Обновлено только что"
        elif minutes_ago < 60:
            return f"Обновлено {minutes_ago} мин назад"
        else:
            hours_ago = minutes_ago // 60
            return f"Обновлено {hours_ago} ч назад"

class DiplomaBot:
    def __init__(self):
        self.gc = None
        self.sheet = None
        self.participants_data = []
        self.init_google_sheets()
        
        self.sheets_service = GoogleSheetsService()
        self.diploma_generator = DiplomaGenerator()
        self.email_service = EmailService()
        self.admin_users: Set[int] = set()
        admin_ids = os.getenv('ADMIN_USER_IDS', '')
        if admin_ids:
            self.admin_users = {int(uid.strip()) for uid in admin_ids.split(',') if uid.strip()}
    

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_users

    def find_participant_with_refresh(self, query: str):
        participant = self.sheets_service.find_participant(query)
        if not participant:
            logger.info("🔄 Участник не найден. Обновляем данные...")
            if self.sheets_service.force_reload_data():
                participant = self.sheets_service.find_participant(query)
        return participant

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
            
        except Exception as e:
            logger.error(f"Ошибка инициализации Google Sheets: {e}")
    
    def load_participants_data(self):
        """Загрузка данных участников из Google Sheets"""
        try:
            # Получаем все данные из таблицы
            records = self.sheet.get_all_records()
            self.participants_data = records
            logger.info(f"Загружено {len(records)} участников")
            logger.info(f"Пример первой записи: {records[0] if records else 'Нет данных'}")    
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
    
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
        
        from datetime import datetime
        story.append(Paragraph(f"Дата: {datetime.now().strftime('%d.%m.%Y')}", normal_style))
        
        # Создание PDF
        doc.build(story)
        buffer.seek(0)
        return buffer

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
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
    
    keyboard = [[KeyboardButton("📜 Запросить диплом")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
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

❓ Если возникли проблемы - обратитесь к организаторам конференции.
    """
    await update.message.reply_text(help_text)

async def handle_text_updated(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    if query == "📜 Запросить диплом":
        await diploma_command(update, context)
        return

    await update.message.reply_text("🔍 Ищу вас в базе участников...")
    bot = context.bot_data.get('diploma_bot')
    if not bot:
        await update.message.reply_text("❌ Ошибка подключения к базе данных")
        return

    participant = bot.find_participant_with_refresh(query)

    if not participant:
        await update.message.reply_text(
            "❌ Участник не найден в базе данных.\n"
            "Проверьте правильность написания email или ФИО.\n"
            "Данные были обновлены из Google Sheets."
        )
        return

    diploma_type, description = bot.determine_diploma_type(participant)

    if not diploma_type:
        await update.message.reply_text(
            "❌ К сожалению, диплом для вас не предусмотрен.\n"
            "Возможно, не выполнены условия для получения диплома."
        )
        return

    try:
        participant_name = participant.get('имя') or participant.get('name') or 'Участник'
        await update.message.reply_text(f"✅ Найден участник: {participant_name}")
        await update.message.reply_text(f"📜 Генерирую {diploma_type.lower()}...")

        pdf_buffer = bot.generate_diploma(participant, diploma_type, description)
        filename = f"diploma_{participant_name.replace(' ', '_')}.pdf"
        await update.message.reply_document(
            document=pdf_buffer,
            filename=filename,
            caption=f"🎓 Ваш {diploma_type.lower()} готов!"
        )

        recipient_email = participant.get('email')
        if recipient_email and EMAIL_USER:
            try:
                pdf_buffer.seek(0)
                bot.send_email(recipient_email, pdf_buffer, filename)
                await update.message.reply_text(f"📬 Диплом также отправлен на email: {recipient_email}")
            except Exception as e:
                await update.message.reply_text("⚠️ Не удалось отправить диплом по email.")
                logger.error(f"Email sending error: {e}")
    except Exception as e:
        logger.error(f"Diploma generation error: {e}")
        await update.message.reply_text("❌ Ошибка при генерации диплома. Обратитесь к организаторам.")

async def auto_update_task(sheets_service: GoogleSheetsService):
    """Автоматическое обновление данных каждые 30 минут"""
    while True:
        try:
            await asyncio.sleep(1800)
            logger.info("🕐 Время автоматического обновления данных…")
            if sheets_service.force_reload_data():
                logger.info("✅ Автоматическое обновление выполнено")
            else:
                logger.error("❌ Ошибка автоматического обновления")
        except Exception as e:
            logger.error(f"❌ Ошибка в задаче автоматического обновления: {e}")
            await asyncio.sleep(300)
async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = context.bot_data.get('diploma_bot')
    if not bot or not bot.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для выполнения этой команды")
        return

    await update.message.reply_text("🔄 Обновляю данные из Google Sheets...")

    if bot.sheets_service.force_reload_data():
        count = len(bot.sheets_service.participants_data)
        await update.message.reply_text(f"✅ Данные успешно обновлены!\nЗагружено участников: {count}")
    else:
        await update.message.reply_text("❌ Ошибка при обновлении данных")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = context.bot_data.get('diploma_bot')
    if not bot or not bot.is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав для выполнения этой команды")
        return

    sheets_service = bot.sheets_service
    status_text = f"""
📊 **Статус системы:**

👥 Участников в базе: {len(sheets_service.participants_data)}
🕐 {sheets_service.get_last_update_info()}
📡 Данные {'устарели' if sheets_service.is_data_outdated() else 'актуальны'}

🔧 Автообновление: каждые 30 минут
"""
    await update.message.reply_text(status_text)

def main():
    """Основная функция запуска бота"""
    if not validate_environment():
        return

    threading.Thread(target=run_health_server, daemon=True).start()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    diploma_bot = DiplomaBot()
    application.bot_data['diploma_bot'] = diploma_bot

    asyncio.create_task(auto_update_task(diploma_bot.sheets_service))

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("diploma", diploma_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reload", reload_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_updated))

    logger.info("Bot started...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

import smtplib
from email.message import EmailMessage

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

if __name__ == '__main__':
    main()
