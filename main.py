import requests
import time
import sys
import asyncio
from aiohttp import web

# Ваш токен бота
BOT_TOKEN = "7754845550:AAH7-ciDXMkBWW5qgYMwq6C1wvMOrzWDa7w"

def clear_bot_state():
    """Полная очистка состояния бота"""
    try:
        # Удаляем webhook
        response = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
        print(f"Webhook cleared: {response.json()}")
        
        # Получаем все pending updates и очищаем их
        response = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", 
                               json={"offset": -1, "limit": 1})
        if response.status_code == 200:
            data = response.json()
            if data['result']:
                last_update_id = data['result'][0]['update_id']
                # Пропускаем все старые обновления
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", 
                            json={"offset": last_update_id + 1, "limit": 1})
                print(f"Cleared pending updates up to {last_update_id}")
        
        print("Bot state cleared successfully")
        time.sleep(5)  # Ждем 5 секунд перед запуском
        
    except Exception as e:
        print(f"Error clearing bot state: {e}")
        sys.exit(1)

# Очищаем состояние при запуске
clear_bot_state()

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

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')

# Создаем credentials из переменной окружения
credentials_json = os.getenv('GOOGLE_CREDENTIALS')
if not credentials_json:
    logger.error("GOOGLE_CREDENTIALS не найдена в переменных окружения")
    sys.exit(1)

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
            
            # Парсим JSON из переменной окружения
            creds_dict = json.loads(credentials_json)
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            
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
            
            # Отладочный вывод первых записей
            for i, record in enumerate(records[:3]):
                logger.info(f"Участник {i+1}: {record}")
                logger.info(f"Ключи записи: {list(record.keys())}")
            
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
    
    def find_participant(self, query: str) -> Optional[Dict]:
        """Поиск участника по email или ФИО"""
        query_lower = query.lower().strip()
        logger.info(f"Поиск участника по запросу: '{query_lower}'")
        
        for i, participant in enumerate(self.participants_data):
            logger.info(f"Проверяем участника {i+1}: {participant}")
            
            # Проверяем все возможные поля с именем
            name_fields = ['имя', 'name', 'ФИО', 'Имя', 'Name', 'Full Name', 'Полное имя']
            email_fields = ['email', 'Email', 'почта', 'Почта', 'E-mail']
            
            # Поиск по email
            for email_field in email_fields:
                if email_field in participant:
                    participant_email = str(participant[email_field]).lower().strip()
                    logger.info(f"Проверяем email поле '{email_field}': '{participant_email}'")
                    if participant_email == query_lower:
                        logger.info(f"НАЙДЕН участник по email: {participant}")
                        return participant
            
            # Поиск по имени (частичное совпадение)
            for name_field in name_fields:
                if name_field in participant:
                    participant_name = str(participant[name_field]).lower().strip()
                    logger.info(f"Проверяем имя поле '{name_field}': '{participant_name}'")
                    if query_lower in participant_name or participant_name in query_lower:
                        logger.info(f"НАЙДЕН участник по имени: {participant}")
                        return participant
                
        logger.info("Участник не найден")
        return None
    
    def determine_diploma_type(self, participant: Dict) -> Tuple[str, str]:
        """Определение типа диплома на основе данных участника"""
        logger.info(f"Определяем тип диплома для участника: {participant}")
        
        # Возможные поля для роли/должности
        role_fields = ['роль', 'должность', 'Role', 'Position', 'Должность', 'Роль']
        points_fields = ['баллы', 'points', 'очки', 'Баллы', 'Points', 'Очки']
        
        role = ""
        points = 0
        
        # Ищем роль
        for role_field in role_fields:
            if role_field in participant and participant[role_field]:
                role = str(participant[role_field]).lower().strip()
                logger.info(f"Найдена роль в поле '{role_field}': '{role}'")
                break
        
        # Ищем баллы
        for points_field in points_fields:
            if points_field in participant and participant[points_field]:
                try:
                    points = int(participant[points_field])
                    logger.info(f"Найдены баллы в поле '{points_field}': {points}")
                    break
                except (ValueError, TypeError):
                    logger.warning(f"Не удалось преобразовать баллы в число: {participant[points_field]}")
        
        logger.info(f"Роль: '{role}', Баллы: {points}")
        
        # Определяем тип диплома
        if 'организатор' in role:
            return 'Диплом организатора', 'за организацию конференции'
        elif 'спикер' in role or 'докладчик' in role:
            return 'Диплом спикера', 'за выступление на конференции'
        elif points >= 50:  # Порог для активного участия
            return 'Диплом за активное участие', 'за активное участие в конференции'
        elif 'призер' in role or 'призёр' in role:
            return 'Диплом призера', 'за призовое место'
        elif points >= 20:  # Минимальный порог
            return 'Диплом участника', 'за участие в конференции'
        else:
            logger.info(f"Не удалось определить тип диплома для роли '{role}' и баллов {points}")
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
        
        normal_style = styles['Normal']
        normal_style.alignment = TA_CENTER
        normal_style.fontSize = 14
        
        # Получаем имя из разных возможных полей
        name_fields = ['имя', 'name', 'ФИО', 'Имя', 'Name', 'Full Name', 'Полное имя']
        participant_name = "Участник"
        
        for name_field in name_fields:
            if name_field in participant and participant[name_field]:
                participant_name = str(participant[name_field]).strip()
                break
        
        # Содержание диплома
        story.append(Spacer(1, 3*cm))
        story.append(Paragraph(diploma_type, title_style))
        story.append(Spacer(1, 2*cm))
        
        story.append(Paragraph("Настоящий диплом выдается", normal_style))
        story.append(Spacer(1, 1*cm))
        
        name_style = styles['Heading1']
        name_style.alignment = TA_CENTER
        name_style.fontSize = 20
        story.append(Paragraph(participant_name, name_style))
        
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

# Веб-сервер для Render
async def health_check(request):
    """Health check endpoint"""
    return web.Response(text="Diploma Bot is running!")

async def init_web_server():
    """Инициализация веб-сервера"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    # Render предоставляет порт через переменную окружения
    port = int(os.getenv('PORT', 8080))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"Веб-сервер запущен на порту {port}")
    return runner

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
• Диплом участника (от 20 баллов)
• Диплом спикера
• Диплом организатора
• Диплом за активное участие (от 50 баллов)
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
    
    # Обновляем данные перед поиском
    bot.load_participants_data()
    
    participant = bot.find_participant(query)
    
    if not participant:
        await update.message.reply_text(
            "❌ Участник не найден в базе данных.\n"
            "Проверьте правильность написания email или ФИО.\n\n"
            "Попробуйте:\n"
            "• Полный email\n"
            "• Полное ФИО\n"
            "• Только фамилию"
        )
        return
    
    # Определение типа диплома
    diploma_type, description = bot.determine_diploma_type(participant)
    
    if not diploma_type:
        await update.message.reply_text(
            "❌ К сожалению, диплом для вас не предусмотрен.\n"
            "Возможно, не выполнены условия для получения диплома.\n\n"
            "Минимальные требования:\n"
            "• 20 баллов для диплома участника\n"
            "• 50 баллов для диплома за активное участие\n"
            "• Или специальная роль (спикер, организатор, призер)"
        )
        return
    
    # Генерация диплома
    try:
        # Получаем имя участника
        name_fields = ['имя', 'name', 'ФИО', 'Имя', 'Name', 'Full Name', 'Полное имя']
        participant_name = "Участник"
        
        for name_field in name_fields:
            if name_field in participant and participant[name_field]:
                participant_name = str(participant[name_field]).strip()
                break
        
        await update.message.reply_text(f"✅ Найден участник: {participant_name}")
        await update.message.reply_text(f"📜 Генерирую {diploma_type.lower()}...")
        
        pdf_buffer = bot.generate_diploma_pdf(participant, diploma_type, description)
        
        # Отправка PDF
        filename = f"diploma_{participant_name.replace(' ', '_')}.pdf"
        await update.message.reply_document(
            document=pdf_buffer,
            filename=filename,
            caption=f"🎓 Ваш {diploma_type.lower()} готов!"
        )
        
    except Exception as e:
        logger.error(f"Ошибка генерации диплома: {e}")
        await update.message.reply_text(
            "❌ Ошибка при генерации диплома. Обратитесь к организаторам."
        )

async def run_bot():
    """Запуск бота"""
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
    await application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

async def main():
    """Главная функция"""
    try:
        # Запускаем веб-сервер
        web_runner = await init_web_server()
        
        # Запускаем бота
        await run_bot()
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        # Останавливаем веб-сервер при завершении
        if 'web_runner' in locals():
            await web_runner.cleanup()

if __name__ == '__main__':
    asyncio.run(main())
