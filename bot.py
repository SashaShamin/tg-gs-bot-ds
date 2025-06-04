import logging
import os
import base64
import json
import gspread
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, ConversationHandler, CallbackContext
)
from datetime import datetime
from google.oauth2.service_account import Credentials

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получение переменных окружения
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
GOOGLE_CREDS_BASE64 = os.getenv('GOOGLE_CREDS_BASE64')

# Проверка наличия переменных
if not all([TELEGRAM_TOKEN, SPREADSHEET_ID, GOOGLE_CREDS_BASE64]):
    logger.error("Не все обязательные переменные окружения установлены!")
    exit(1)

# Состояния для ConversationHandler
SELECTING_ACTION, VIEW_DATE, EDIT_DATE, EDIT_WORKOUT, EDIT_VOLUME, EDIT_GOAL, ADD_TEXT = range(7)

# Инициализация Google Sheets
def init_google_sheets():
    try:
        # Декодируем credentials
        creds_json = base64.b64decode(GOOGLE_CREDS_BASE64).decode('utf-8')
        creds_dict = json.loads(creds_json)
        
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open_by_key(SPREADSHEET_ID).sheet1
    except Exception as e:
        logger.error(f"Ошибка инициализации Google Sheets: {e}")
        raise

# Получение данных о тренировке по дате
def get_training_data(date: str, worksheet):
    try:
        date_cell = worksheet.find(date)
        row = worksheet.row_values(date_cell.row)
        return {
            'date': row[0],
            'type': row[1] if len(row) > 1 else None,
            'workout': row[2] if len(row) > 2 else None,
            'volume': row[3] if len(row) > 3 else None,
            'goal': row[4] if len(row) > 4 else None
        }
    except gspread.exceptions.CellNotFound:
        return None
    except Exception as e:
        logger.error(f"Ошибка при получении данных: {e}")
        return None

# Обновление данных в таблице
def update_training_data(date: str, field: str, value: str, worksheet, append=False):
    try:
        date_cell = worksheet.find(date)
        col_idx = {'workout': 3, 'volume': 4, 'goal': 5}.get(field)
        
        if not col_idx:
            return False
        
        if append:
            current_value = worksheet.cell(date_cell.row, col_idx).value or ""
            value = f"{current_value} {value}".strip()
        
        worksheet.update_cell(date_cell.row, col_idx, value)
        return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении данных: {e}")
        return False

# ===================== Обработчики команд =====================

def start(update: Update, context: CallbackContext) -> int:
    keyboard = [['Посмотреть тренировку', 'Изменить тренировку']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        "Выберите дальнейшее действие:",
        reply_markup=reply_markup
    )
    return SELECTING_ACTION

def view_training(update: Update, context: CallbackContext) -> int:
    update.message.reply_text(
        "Введите дату тренировки (в формате ДД.ММ.ГГГГ):",
        reply_markup=ReplyKeyboardRemove()
    )
    return VIEW_DATE

def handle_view_date(update: Update, context: CallbackContext) -> int:
    date = update.message.text
    worksheet = context.bot_data.get('worksheet')
    
    if not is_valid_date(date):
        update.message.reply_text("Неверный формат даты. Используйте ДД.ММ.ГГГГ")
        return VIEW_DATE
    
    training_data = get_training_data(date, worksheet)
    
    if not training_data:
        update.message.reply_text("В данном периоде тренировок не предусмотрено.")
        return ConversationHandler.END
    
    response = (
        f"{training_data['date']} "
        f"{training_data['type'] or 'Не заполнено'} тренировка. "
        f"Подобранная нагрузка {training_data['workout'] or 'Не заполнено'}, "
        f"ее объем и содержание {training_data['volume'] or 'Не заполнено'}, "
        f"ее цель {training_data['goal'] or 'Не заполнено'}."
    )
    
    update.message.reply_text(response)
    return ConversationHandler.END

def edit_training(update: Update, context: CallbackContext) -> int:
    today_btn = KeyboardButton("Сегодня")
    keyboard = [[today_btn]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        "Введите дату тренировки или нажмите 'Сегодня':",
        reply_markup=reply_markup
    )
    return EDIT_DATE

def handle_edit_date(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text
    worksheet = context.bot_data.get('worksheet')
    
    if user_input == "Сегодня":
        date = datetime.now().strftime("%d.%m.%Y")
    else:
        date = user_input
    
    if not is_valid_date(date):
        update.message.reply_text("Неверный формат даты. Используйте ДД.ММ.ГГГГ")
        return EDIT_DATE
    
    training_data = get_training_data(date, worksheet)
    
    if not training_data:
        update.message.reply_text("В данном периоде тренировок не предусмотрено.")
        return ConversationHandler.END
    
    context.user_data['edit_date'] = date
    context.user_data['current_field'] = 'workout'
    
    keyboard = [['Не менять', 'Добавить']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        f"Заполните тренировку, текущие данные: {training_data['workout'] or 'Пусто'}\n\n"
        "Вы можете ввести новое значение или выбрать опцию:",
        reply_markup=reply_markup
    )
    
    return EDIT_WORKOUT

def handle_edit_workout(update: Update, context: CallbackContext) -> int:
    return handle_edit_field(update, context, 'workout', 'volume', EDIT_VOLUME)

def handle_edit_volume(update: Update, context: CallbackContext) -> int:
    return handle_edit_field(update, context, 'volume', 'goal', EDIT_GOAL)

def handle_edit_goal(update: Update, context: CallbackContext) -> int:
    return handle_edit_field(update, context, 'goal', None, None)

def handle_edit_field(update, context, current_field, next_field, next_state):
    user_input = update.message.text
    date = context.user_data['edit_date']
    worksheet = context.bot_data.get('worksheet')
    
    if user_input == 'Не менять':
        # Пропускаем поле без изменений
        pass
    elif user_input == 'Добавить':
        context.user_data['add_to_field'] = current_field
        update.message.reply_text(
            "Введите дополнительную информацию:",
            reply_markup=ReplyKeyboardRemove()
        )
        return ADD_TEXT
    else:
        # Обновляем поле новым значением
        success = update_training_data(
            date, 
            current_field, 
            user_input, 
            worksheet
        )
        if not success:
            update.message.reply_text("Ошибка при обновлении данных. Попробуйте еще раз.")
            return get_current_edit_state(current_field)
    
    if next_field:
        # Переходим к следующему полю
        context.user_data['current_field'] = next_field
        training_data = get_training_data(date, worksheet)
        
        keyboard = [['Не менять', 'Добавить']]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        field_value = training_data.get(next_field) or 'Пусто'
        field_name = {
            'volume': 'Объем / Содержание',
            'goal': 'Цель'
        }.get(next_field, next_field)
        
        update.message.reply_text(
            f"Заполните {field_name}, текущие данные: {field_value}\n\n"
            "Вы можете ввести новое значение или выбрать опцию:",
            reply_markup=reply_markup
        )
        
        return next_state
    else:
        # Все поля обработаны
        update.message.reply_text(
            "Данные по тренировке обновлены!",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

def handle_add_text(update: Update, context: CallbackContext) -> int:
    text = update.message.text
    date = context.user_data['edit_date']
    field = context.user_data['add_to_field']
    worksheet = context.bot_data.get('worksheet')
    
    success = update_training_data(
        date, 
        field, 
        text, 
        worksheet, 
        append=True
    )
    
    if success:
        # Возвращаемся к редактированию
        current_field = context.user_data['current_field']
        return get_current_edit_state(current_field)
    else:
        update.message.reply_text("Ошибка при добавлении данных. Попробуйте еще раз.")
        return ConversationHandler.END

def get_current_edit_state(field):
    return {
        'workout': EDIT_WORKOUT,
        'volume': EDIT_VOLUME,
        'goal': EDIT_GOAL
    }.get(field, ConversationHandler.END)

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text(
        'Действие отменено',
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def is_valid_date(date_str):
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
        return True
    except ValueError:
        return False

def error_handler(update: Update, context: CallbackContext):
    logger.error(msg="Ошибка в обработчике Telegram:", exc_info=context.error)
    update.message.reply_text('Произошла ошибка. Пожалуйста, попробуйте позже.')

# ===================== Основная функция =====================

def main():
    # Инициализация Google Sheets
    try:
        worksheet = init_google_sheets()
        logger.info("Успешное подключение к Google Таблице")
    except Exception as e:
        logger.error(f"Критическая ошибка подключения к Google Таблицам: {e}")
        return

    # Создание Updater
    updater = Updater(TELEGRAM_TOKEN)
    dispatcher = updater.dispatcher
    dispatcher.bot_data['worksheet'] = worksheet

     # Сохраняем объект worksheet в bot_data
    dispatcher.bot_data['worksheet'] = worksheet

    # Обработчики диалогов
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_ACTION: [
                MessageHandler(
                    Filters.regex('^Посмотреть тренировку$'), 
                    view_training
                ),
                MessageHandler(
                    Filters.regex('^Изменить тренировку$'), 
                    edit_training
                )
            ],
            VIEW_DATE: [
                MessageHandler(Filters.text & ~Filters.command, handle_view_date)
            ],
            EDIT_DATE: [
                MessageHandler(Filters.text & ~Filters.command, handle_edit_date)
            ],
            EDIT_WORKOUT: [
                MessageHandler(Filters.text & ~Filters.command, handle_edit_workout)
            ],
            EDIT_VOLUME: [
                MessageHandler(Filters.text & ~Filters.command, handle_edit_volume)
            ],
            EDIT_GOAL: [
                MessageHandler(Filters.text & ~Filters.command, handle_edit_goal)
            ],
            ADD_TEXT: [
                MessageHandler(Filters.text & ~Filters.command, handle_add_text)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    dispatcher.add_handler(conv_handler)
    dispatcher.add_error_handler(error_handler)

    # Запуск бота
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()