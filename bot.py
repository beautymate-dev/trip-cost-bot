import logging
import os
import requests
import anthropic
from bs4 import BeautifulSoup
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
(FUEL_PRICE, FUEL_PRICE_METHOD, REGION, FUEL_TYPE, CONFIRM_FUEL_PRICE,
 FUEL_EFFICIENCY, FUEL_EFFICIENCY_METHOD, CAR_YEAR, CAR_MAKE,
 CAR_MODEL, CONFIRM_EFFICIENCY, RUC, DISTANCE, TRIP_TYPE,
 ANOTHER_TRIP, SAME_CAR) = range(16)

RUC_RATE = 76 / 1000  # per km

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Configure Anthropic
anthropic_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

REGIONS = [
    "Northland", "Auckland", "Coromandel", "Bay of Plenty", "Waikato",
    "East Coast", "Central Plateau", "Hawkes Bay", "Manawatu - Wanganui",
    "Wairarapa", "Wellington", "Nelson", "Marlborough", "Canterbury",
    "West Coast", "Otago", "Southland"
]

FUEL_TYPES = ["91", "95/96", "98", "Diesel"]

def scrape_fuel_price(region: str, fuel_type: str):
    try:
        response = requests.get("https://www.pricewatch.co.nz/", timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        # Find all region header cells
        all_text = soup.get_text(separator="\n")
        lines = [line.strip() for line in all_text.splitlines() if line.strip()]

        # Map fuel type to column header text used on the site
        fuel_map = {
            "91": "91",
            "95/96": "95/96",
            "98": "98",
            "Diesel": "Diesel"
        }
        fuel_label = fuel_map[fuel_type]

        # Find tables for the region
        tables = soup.find_all("table")
        for table in tables:
            header_text = table.get_text()
            if region.lower() in header_text.lower() and "Fuel Prices" in header_text:
                # Find the fuel type link in header row
                headers = table.find_all("a")
                fuel_col_index = None
                for i, h in enumerate(headers):
                    if fuel_label in h.get_text():
                        fuel_col_index = i
                        break

                if fuel_col_index is None:
                    return None

                # Get all rows and find minimum price
                rows = table.find_all("tr")
                prices = []
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) > fuel_col_index + 1:
                        cell = cells[fuel_col_index + 1]
                        text = cell.get_text().strip()
                        if text.startswith("$"):
                            try:
                                price = float(text.replace("$", ""))
                                prices.append(price)
                            except ValueError:
                                continue

                if prices:
                    return min(prices)

        return None

    except Exception as e:
        logging.error(f"PriceWatch scrape failed: {e}")
        return None


async def lookup_fuel_efficiency(year, make, model):
    try:
        prompt = (
            f"What is the estimated average fuel consumption in L/100km for a "
            f"{year} {make} {model}? Reply with just a single number, no units or explanation."
        )
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=16,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        logging.info(f"Claude raw response for {year} {make} {model}: '{raw}'")
        value = float(raw)
        return value
    except Exception as e:
        logging.error(f"Claude lookup failed for {year} {make} {model}: {e}")
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("Look Up Current Price", callback_data="fp_lookup"),
         InlineKeyboardButton("Enter Manually", callback_data="fp_manual")]
    ]
    await update.message.reply_text(
        "👋 Welcome! Let's calculate your trip cost.\n\nHow would you like to enter the fuel price?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return FUEL_PRICE_METHOD


async def fuel_price_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "fp_manual":
        await query.edit_message_text("What is the current fuel price? ($/L)")
        return FUEL_PRICE

    # Build region buttons
    keyboard = []
    row = []
    for i, region in enumerate(REGIONS):
        row.append(InlineKeyboardButton(region, callback_data=f"region_{region}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await query.edit_message_text(
        "Which region are you in?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return REGION


async def region_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    region = query.data.replace("region_", "")
    context.user_data["region"] = region

    keyboard = [
        [InlineKeyboardButton(ft, callback_data=f"ft_{ft}") for ft in FUEL_TYPES]
    ]
    await query.edit_message_text(
        f"Region: {region}\n\nWhat fuel type does your vehicle use?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return FUEL_TYPE


async def fuel_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    fuel_type = query.data.replace("ft_", "")
    context.user_data["fuel_type"] = fuel_type
    region = context.user_data["region"]

    await query.edit_message_text(f"🔍 Looking up {fuel_type} prices in {region}...")

    price = scrape_fuel_price(region, fuel_type)

    if price is None:
        await query.message.reply_text(
            "⚠️ Sorry, I couldn't find a price for that region and fuel type. Please enter the fuel price manually. ($/L)"
        )
        return FUEL_PRICE

    context.user_data["looked_up_price"] = price
    keyboard = [
        [InlineKeyboardButton("✅ Use This Price", callback_data="fp_confirm"),
         InlineKeyboardButton("✏️ Enter Manually", callback_data="fp_override")]
    ]
    await query.message.reply_text(
        f"The minimum {fuel_type} price in {region} is *${price:.3f}/L*.\n\nWould you like to use this price?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CONFIRM_FUEL_PRICE


async def confirm_fuel_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "fp_confirm":
        context.user_data["fuel_price"] = context.user_data["looked_up_price"]
        await query.edit_message_text(
            f"✅ Using ${context.user_data['fuel_price']:.3f}/L"
        )
    else:
        await query.edit_message_text("What is the current fuel price? ($/L)")
        return FUEL_PRICE

    keyboard = [
        [InlineKeyboardButton("Enter Manually", callback_data="efficiency_manual"),
         InlineKeyboardButton("Look Up My Car", callback_data="efficiency_lookup")]
    ]
    await query.message.reply_text(
        "How would you like to enter your vehicle's fuel efficiency?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return FUEL_EFFICIENCY_METHOD


async def fuel_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text)
        context.user_data["fuel_price"] = price
        keyboard = [
            [InlineKeyboardButton("Enter Manually", callback_data="efficiency_manual"),
             InlineKeyboardButton("Look Up My Car", callback_data="efficiency_lookup")]
        ]
        await update.message.reply_text(
            "How would you like to enter your vehicle's fuel efficiency?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return FUEL_EFFICIENCY_METHOD
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number for fuel price.")
        return FUEL_PRICE


async def fuel_efficiency_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "efficiency_manual":
        await query.edit_message_text("What is your vehicle's average fuel efficiency? (L/100km)")
        return FUEL_EFFICIENCY
    else:
        await query.edit_message_text("What year is your vehicle? (e.g. 2015)")
        return CAR_YEAR


async def fuel_efficiency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        efficiency = float(update.message.text)
        context.user_data["fuel_efficiency"] = efficiency
        keyboard = [
            [InlineKeyboardButton("Yes", callback_data="ruc_yes"),
             InlineKeyboardButton("No", callback_data="ruc_no")]
        ]
        await update.message.reply_text(
            "Do Road User Charges (RUC) apply to your vehicle?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return RUC
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number for fuel efficiency.")
        return FUEL_EFFICIENCY


async def car_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        year = int(update.message.text)
        if year < 1980 or year > 2026:
            raise ValueError
        context.user_data["car_year"] = year
        await update.message.reply_text("What is the make of your vehicle? (e.g. Toyota)")
        return CAR_MAKE
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid year (e.g. 2015).")
        return CAR_YEAR


async def car_make(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["car_make"] = update.message.text.strip()
    await update.message.reply_text("What is the model of your vehicle? (e.g. Corolla)")
    return CAR_MODEL


async def car_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["car_model"] = update.message.text.strip()
    year = context.user_data["car_year"]
    make = context.user_data["car_make"]
    model = context.user_data["car_model"]

    await update.message.reply_text(f"🔍 Looking up fuel efficiency for a {year} {make} {model}...")

    efficiency = await lookup_fuel_efficiency(year, make, model)

    if efficiency is None:
        await update.message.reply_text(
            "⚠️ Sorry, I couldn't find that vehicle. Please enter the fuel efficiency manually. (L/100km)"
        )
        return FUEL_EFFICIENCY

    context.user_data["looked_up_efficiency"] = efficiency
    keyboard = [
        [InlineKeyboardButton("✅ Confirm", callback_data="efficiency_confirm"),
         InlineKeyboardButton("✏️ Enter Manually", callback_data="efficiency_override")]
    ]
    await update.message.reply_text(
        f"I found an estimated fuel efficiency of *{efficiency:.1f} L/100km* for a {year} {make} {model}.\n\nWould you like to use this value?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CONFIRM_EFFICIENCY


async def confirm_efficiency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "efficiency_confirm":
        context.user_data["fuel_efficiency"] = context.user_data["looked_up_efficiency"]
        keyboard = [
            [InlineKeyboardButton("Yes", callback_data="ruc_yes"),
             InlineKeyboardButton("No", callback_data="ruc_no")]
        ]
        await query.edit_message_text(
            "Do Road User Charges (RUC) apply to your vehicle?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return RUC
    else:
        await query.edit_message_text("Please enter the fuel efficiency manually. (L/100km)")
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
        context.user_data["one_way_distance"] = dist
        keyboard = [
            [InlineKeyboardButton("One Way", callback_data="trip_oneway"),
             InlineKeyboardButton("Return Trip", callback_data="trip_return")]
        ]
        await update.message.reply_text(
            "Is this a one way or return trip?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return TRIP_TYPE
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number for distance.")
        return DISTANCE


async def trip_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = context.user_data
    dist = data["one_way_distance"]

    if query.data == "trip_return":
        dist = dist * 2

    fuel_cost = (data["fuel_efficiency"] / 100) * data["fuel_price"] * dist
    ruc_cost = RUC_RATE * dist if data["ruc"] else 0
    total = fuel_cost + ruc_cost

    await query.edit_message_text(
        f"🚗 *Trip Cost Summary*\n"
        f"Distance: {dist:.1f} km\n"
        f"Total Cost: *${total:.2f}*",
        parse_mode="Markdown"
    )

    keyboard = [
        [InlineKeyboardButton("Yes", callback_data="another_yes"),
         InlineKeyboardButton("No", callback_data="another_no")]
    ]
    await query.message.reply_text(
        "Would you like to calculate another trip?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ANOTHER_TRIP


async def another_trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "another_no":
        await query.edit_message_text("Thanks for using the Trip Cost Bot! Safe travels! 🚗")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("Yes", callback_data="same_yes"),
         InlineKeyboardButton("No", callback_data="same_no")]
    ]
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
        keyboard = [
            [InlineKeyboardButton("Look Up Current Price", callback_data="fp_lookup"),
             InlineKeyboardButton("Enter Manually", callback_data="fp_manual")]
        ]
        await query.edit_message_text(
            "How would you like to enter the fuel price?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return FUEL_PRICE_METHOD


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled. Type /start to begin again.")
    return ConversationHandler.END


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            FUEL_PRICE_METHOD: [CallbackQueryHandler(fuel_price_method)],
            REGION: [CallbackQueryHandler(region_selected)],
            FUEL_TYPE: [CallbackQueryHandler(fuel_type_selected)],
            CONFIRM_FUEL_PRICE: [CallbackQueryHandler(confirm_fuel_price)],
            FUEL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_price)],
            FUEL_EFFICIENCY_METHOD: [CallbackQueryHandler(fuel_efficiency_method)],
            FUEL_EFFICIENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_efficiency)],
            CAR_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_year)],
            CAR_MAKE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_make)],
            CAR_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_model)],
            CONFIRM_EFFICIENCY: [CallbackQueryHandler(confirm_efficiency)],
            RUC: [CallbackQueryHandler(ruc)],
            DISTANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, distance)],
            TRIP_TYPE: [CallbackQueryHandler(trip_type)],
            ANOTHER_TRIP: [CallbackQueryHandler(another_trip)],
            SAME_CAR: [CallbackQueryHandler(same_car)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.run_polling()


if __name__ == "__main__":
    main()
