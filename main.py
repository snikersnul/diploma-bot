import logging
import os
import json
from io import BytesIO
from typing import Dict, List, Optional, Tuple

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
pdfmetrics.registerFont(TTFont('DejaVuSans', 'DejaVuSans.ttf'))  # путь к файлу .ttf


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

class DiplomaBot:
    def __init__(self):
        self.gc = None
        self.sheet = None
        self.participants_data = []
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
            if 'имя' in participant and query_lower in participant['имя'].lower():
                return participant
                
            # Альтернативные названия полей
            if 'name' in participant and query_lower in participant['name'].lower():
                return participant
                
        return None
    
    def determine_diploma_type(self, participant: Dict) -> Tuple[str, str]:
        """Определение типа диплома на основе данных участника"""
        role = participant.get('роль', '').lower()
        points = int(participant.get('баллы', 0))
        
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

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    query = update.message.text
    
    # Игнорируем команды клавиатуры
    if query == "📜 Запросить диплом":
        await diploma_command(update, context)
        return
    
    # Поиск участника
    await update.message.reply_text("🔍 Ищу вас в базе участников...")
    
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
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("diploma", diploma_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Запуск бота
    logger.info("Бот запущен...")
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
