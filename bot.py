import os
import re
import json
import datetime
import logging
import asyncio
from telegram import (
    ReplyKeyboardMarkup, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    ReplyKeyboardRemove,
    Update
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
import gspread
from google.oauth2.service_account import Credentials

# Настройка логгирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния разговора
(
    ENTER_DATE, 
    FOUND_DATE,
    CHANGE_TRAIN_FIELD,
    TRAIN_ANSWER,
    CHANGE_CONTENT_FIELD,
    CONTENT_ANSWER,
    CHANGE_TARGET_FIELD,
    TARGET_ANSWER
) = range(8)

# Глобальные переменные
worksheet = None

def init_google_sheet():
    """Инициализация подключения к Google Таблице"""
    creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    sheet_id = os.environ.get('GOOGLE_SHEET_ID')
    
    if not creds_json or not sheet_id:
        raise ValueError("Не заданы переменные окружения")
    
    creds_data = json.loads(creds_json)
    scope = ['https://spreadsheets.google.com/feeds', 
             'https://www.googleapis.com/auth/drive',
             'https://www.googleapis.com/auth/spreadsheets']
    
    creds = Credentials.from_service_account_info(creds_data, scopes=scope)
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id).sheet1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    reply_markup = ReplyKeyboardMarkup([['Сегодня']], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "Введите дату тренировки в формате ДД.ММ.ГГГГ",
        reply_markup=reply_markup
    )
    return ENTER_DATE

async def enter_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода даты пользователем"""
    user_input = update.message.text
    
    # Обработка кнопки "Сегодня"
    if user_input == 'Сегодня':
        date_str = datetime.datetime.now().strftime('%d.%m.%Y')
    else:
        # Проверка формата даты
        if not re.match(r'^\d{2}\.\d{2}\.\d{4}$', user_input):
            await update.message.reply_text("Неверный формат. Введите дату в формате ДД.ММ.ГГГГ")
            return ENTER_DATE
        
        # Проверка валидности даты
        try:
            day, month, year = user_input.split('.')
            datetime.datetime(int(year), int(month), int(day))
            date_str = user_input
        except ValueError:
            await update.message.reply_text("Некорректная дата. Введите правильную дату")
            return ENTER_DATE
    
    # Поиск даты в таблице
    result = await asyncio.to_thread(find_date_in_sheet, date_str)
    if not result:
        await update.message.reply_text(
            f"Дата {date_str} НЕ найдена в плане. Введите новую дату",
            reply_markup=ReplyKeyboardMarkup([['Сегодня']], resize_keyboard=True)
        return ENTER_DATE
    
    # Сохранение данных в контексте
    row_idx, row_data = result
    context.user_data.update({
        'date': date_str,
        'row_idx': row_idx,
        'train': row_data.get(5, ''),
        'content': row_data.get(6, ''),
        'target': row_data.get(7, '')
    })
    
    # Формирование сообщения с тренировкой
    load_type = row_data[4] if len(row_data) > 4 and row_data[4] else "Не заполнено"
    train = row_data[5] if len(row_data) > 5 and row_data[5] else "Не заполнено"
    content = row_data[6] if len(row_data) > 6 and row_data[6] else "Не заполнено"
    target = row_data[7] if len(row_data) > 7 and row_data[7] else "Не заполнено"
    
    message = (
        f"{date_str} {load_type} нагрузка.\n"
        f"Тренировка: {train}\n"
        f"Объем/Содержание: {content}\n"
        f"Цель: {target}"
    )
    
    # Кнопки действий
    keyboard = [
        [InlineKeyboardButton("Изменить тренировку", callback_data='change')],
        [InlineKeyboardButton("Поиск новой тренировки", callback_data='new_search')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)
    
    return FOUND_DATE

def find_date_in_sheet(date_str):
    """Поиск даты в таблице и возврат данных строки"""
    try:
        col_values = worksheet.col_values(4)  # Колонка D (даты)
        if date_str in col_values:
            row_idx = col_values.index(date_str) + 1
            return row_idx, worksheet.row_values(row_idx)
    except Exception as e:
        logger.error(f"Ошибка поиска даты: {e}")
    return None

async def handle_found_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка действий после нахождения даты"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'new_search':
        await query.edit_message_reply_markup()
        reply_markup = ReplyKeyboardMarkup([['Сегодня']], resize_keyboard=True)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Введите новую дату в формате ДД.ММ.ГГГГ",
            reply_markup=reply_markup
        )
        return ENTER_DATE
    
    # Переход к изменению тренировки
    return await ask_change_train(update, context)

async def ask_change_train(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос на изменение тренировки"""
    query = update.callback_query
    train = context.user_data['train'] or "Не заполнено"
    
    keyboard = [[InlineKeyboardButton("Не менять", callback_data='no_change')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"Текущая тренировка: {train}\nБудешь менять данные?",
        reply_markup=reply_markup
    )
    return TRAIN_ANSWER

async def handle_train_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответа на изменение тренировки"""
    if update.callback_query:
        # Обработка кнопки "Не менять"
        query = update.callback_query
        await query.answer()
        return await ask_change_content(update, context)
    
    # Обработка текстового ввода
    new_train = update.message.text
    context.user_data['train'] = new_train
    row_idx = context.user_data['row_idx']
    
    # Асинхронное обновление ячейки
    await asyncio.to_thread(worksheet.update_cell, row_idx, 6, new_train)  # Колонка F
    
    return await ask_change_content(update, context)

async def ask_change_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос на изменение объема/содержания"""
    if update.callback_query:
        query = update.callback_query
        chat_id = query.message.chat_id
        await query.answer()
        await query.edit_message_reply_markup()
    else:
        chat_id = update.message.chat_id
    
    content = context.user_data['content'] or "Не заполнено"
    
    keyboard = [[InlineKeyboardButton("Не менять", callback_data='no_change')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Текущий объем/содержание: {content}\nБудешь менять данные?",
        reply_markup=reply_markup
    )
    return CONTENT_ANSWER

async def handle_content_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответа на изменение объема/содержания"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        return await ask_change_target(update, context)
    
    new_content = update.message.text
    context.user_data['content'] = new_content
    row_idx = context.user_data['row_idx']
    
    # Асинхронное обновление ячейки
    await asyncio.to_thread(worksheet.update_cell, row_idx, 7, new_content)  # Колонка G
    
    return await ask_change_target(update, context)

async def ask_change_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос на изменение цели"""
    if update.callback_query:
        query = update.callback_query
        chat_id = query.message.chat_id
        await query.answer()
        await query.edit_message_reply_markup()
    else:
        chat_id = update.message.chat_id
    
    target = context.user_data['target'] or "Не заполнено"
    
    keyboard = [[InlineKeyboardButton("Не менять", callback_data='no_change')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Текущая цель: {target}\nБудешь менять данные?",
        reply_markup=reply_markup
    )
    return TARGET_ANSWER

async def handle_target_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответа на изменение цели и завершение"""
    if update.callback_query:
        query = update.callback_query
        chat_id = query.message.chat_id
        await query.answer()
        await query.edit_message_reply_markup()
    else:
        new_target = update.message.text
        context.user_data['target'] = new_target
        row_idx = context.user_data['row_idx']
        
        # Асинхронное обновление ячейки
        await asyncio.to_thread(worksheet.update_cell, row_idx, 8, new_target)  # Колонка H
        chat_id = update.message.chat_id
    
    # Завершение процесса
    reply_markup = ReplyKeyboardMarkup([['Сегодня']], resize_keyboard=True)
    await context.bot.send_message(
        chat_id=chat_id,
        text="Тренировка обновлена. Для поиска следующей тренировки введите дату в формате ДД.ММ.ГГГГ",
        reply_markup=reply_markup
    )
    return ENTER_DATE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена операции"""
    await update.message.reply_text(
        'Операция отменена',
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")
    if update and hasattr(update, 'message') and update.message:
        await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте снова.")
    return ENTER_DATE

def main() -> None:
    """Основная функция запуска бота"""
    global worksheet
    
    # Инициализация Google Таблицы
    try:
        worksheet = init_google_sheet()
    except Exception as e:
        logger.error(f"Ошибка инициализации Google Sheets: {e}")
        return
    
    # Получение токена из переменных окружения
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        logger.error("TELEGRAM_TOKEN не задан")
        return
    
    # Создание обработчика диалога
    application = Application.builder().token(token).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            ENTER_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_date)
            ],
            FOUND_DATE: [
                CallbackQueryHandler(handle_found_date)
            ],
            TRAIN_ANSWER: [
                CallbackQueryHandler(handle_train_answer, pattern='^no_change$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_train_answer)
            ],
            CONTENT_ANSWER: [
                CallbackQueryHandler(handle_content_answer, pattern='^no_change$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_content_answer)
            ],
            TARGET_ANSWER: [
                CallbackQueryHandler(handle_target_answer, pattern='^no_change$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_target_answer)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)
    
    # Запуск бота
    application.run_polling()
    logger.info("Бот запущен")

if __name__ == '__main__':
    main()
