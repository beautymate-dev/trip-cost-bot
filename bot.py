import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# States
FUEL_PRICE, FUEL_EFFICIENCY, RUC, DISTANCE, ANOTHER_TRIP, SAME_CAR = range(6)

RUC_RATE = 76 / 1000  # per km

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("👋 Welcome! Let's calculate your trip cost.\n\nWhat is the current fuel price? ($/L)")
    return FUEL_PRICE

async def fuel_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text)
        context.user_data["fuel_price"] = price
        await update.message.reply_text("What is your vehicle's average fuel efficiency? (L/100km)")
        return FUEL_EFFICIENCY
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number for fuel price.")
        return FUEL_PRICE

async def fuel_efficiency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        efficiency = float(update.message.text)
        context.user_data["fuel_efficiency"] = efficiency
        keyboard = [[InlineKeyboardButton("Yes", callback_data="ruc_yes"),
                     InlineKeyboardButton("No", callback_data="ruc_no")]]
        await update.message.reply_text(
            "Do Road User Charges (RUC) apply to your vehicle?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return RUC
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number for fuel efficiency.")
        return FUEL_EFFICIENCY

async def ruc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["ruc"] = query.data == "ruc_yes"
    await query.edit_message_text("What is the total distance of your trip? (km)")
    return DISTANCE

async def distance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        dist = float(update.message.text)
        data = context.user_data

        fuel_cost = (data["fuel_efficiency"] / 100) * data["fuel_price"] * dist
        ruc_cost = RUC_RATE * dist if data["ruc"] else 0
        total = fuel_cost + ruc_cost

        await update.message.reply_text(
            f"🚗 *Trip Cost Summary*\n"
            f"Distance: {dist:.1f} km\n"
            f"Total Cost: *${total:.2f}*",
            parse_mode="Markdown"
        )

        context.user_data["last_distance"] = dist

        keyboard = [[InlineKeyboardButton("Yes", callback_data="another_yes"),
                     InlineKeyboardButton("No", callback_data="another_no")]]
        await update.message.reply_text(
            "Would you like to calculate another trip?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ANOTHER_TRIP
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number for distance.")
        return DISTANCE

async def another_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "another_no":
        await query.edit_message_text("Thanks for using the Trip Cost Bot! Safe travels! 🚗")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton("Yes", callback_data="same_yes"),
                 InlineKeyboardButton("No", callback_data="same_no")]]
    await query.edit_message_text(
        "Are you using the same car?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SAME_CAR

async def same_car(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "same_yes":
        await query.edit_message_text("What is the total distance of your trip? (km)")
        return DISTANCE
    else:
        context.user_data.clear()
        await query.edit_message_text("What is the current fuel price? ($/L)")
        return FUEL_PRICE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled. Type /start to begin again.")
    return ConversationHandler.END

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            FUEL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_price)],
            FUEL_EFFICIENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_efficiency)],
            RUC: [CallbackQueryHandler(ruc)],
            DISTANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, distance)],
            ANOTHER_TRIP: [CallbackQueryHandler(another_trip)],
            SAME_CAR: [CallbackQueryHandler(same_car)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
