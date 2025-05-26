import random
import sqlite3
import os
from telegram.ext import ApplicationBuilder

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters
)

# SQLite DB connection
conn = sqlite3.connect('game.db', check_same_thread=False)
cursor = conn.cursor()

# Create tables if not exists
cursor.execute('''
CREATE TABLE IF NOT EXISTS players (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    xp INTEGER DEFAULT 0
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS pokemons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT,
    level INTEGER DEFAULT 5,
    hp INTEGER,
    max_hp INTEGER,
    xp INTEGER DEFAULT 0,
    caught INTEGER DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES players(user_id)
)
''')
conn.commit()

# Constants
STARTERS = [
    {"name": "Treecko", "hp": 40},
    {"name": "Torchic", "hp": 45},
    {"name": "Mudkip", "hp": 50},
]

WILD_POKEMON_POOL = [
    {"name": "Poochyena", "hp": 30},
    {"name": "Zigzagoon", "hp": 28},
    {"name": "Ralts", "hp": 25},
    {"name": "Pikachu", "hp": 35},
]

# States for conversation handlers
CHOOSING, BATTLE_ACTION, CATCH_DECISION = range(3)

# Utility functions

def get_player(user_id, username):
    cursor.execute('SELECT * FROM players WHERE user_id=?', (user_id,))
    player = cursor.fetchone()
    if not player:
        cursor.execute('INSERT INTO players (user_id, username) VALUES (?, ?)', (user_id, username))
        conn.commit()
        return (user_id, username, 0)
    return player

def add_pokemon(user_id, name, hp):
    cursor.execute('''
        INSERT INTO pokemons (user_id, name, level, hp, max_hp, xp, caught)
        VALUES (?, ?, 5, ?, ?, 0, 1)
    ''', (user_id, name, hp, hp))
    conn.commit()

def get_team(user_id):
    cursor.execute('SELECT id, name, level, hp, max_hp, xp FROM pokemons WHERE user_id=?', (user_id,))
    return cursor.fetchall()

def update_pokemon_hp(pokemon_id, new_hp):
    cursor.execute('UPDATE pokemons SET hp=? WHERE id=?', (new_hp, pokemon_id))
    conn.commit()

def add_xp(user_id, xp_gained):
    cursor.execute('UPDATE players SET xp = xp + ? WHERE user_id=?', (xp_gained, user_id))
    conn.commit()

def level_up_pokemon(pokemon_id):
    cursor.execute('SELECT level, xp FROM pokemons WHERE id=?', (pokemon_id,))
    row = cursor.fetchone()
    if not row:
        return False
    level, xp = row
    # Simple leveling: every 100 XP -> level up
    while xp >= 100 * level:
        level += 1
        xp -= 100 * (level - 1)
        # Increase max_hp +10 each level
        cursor.execute('SELECT max_hp FROM pokemons WHERE id=?', (pokemon_id,))
        max_hp = cursor.fetchone()[0]
        new_max_hp = max_hp + 10
        # Update level, xp, max_hp, restore HP to max_hp
        cursor.execute('''
            UPDATE pokemons SET level=?, xp=?, max_hp=?, hp=?
            WHERE id=?
        ''', (level, xp, new_max_hp, new_max_hp, pokemon_id))
        conn.commit()
    return True

def add_pokemon_xp(pokemon_id, xp_gained):
    cursor.execute('SELECT xp FROM pokemons WHERE id=?', (pokemon_id,))
    current_xp = cursor.fetchone()[0]
    new_xp = current_xp + xp_gained
    cursor.execute('UPDATE pokemons SET xp=? WHERE id=?', (new_xp, pokemon_id))
    conn.commit()
    level_up_pokemon(pokemon_id)

# Bot handlers

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    player = get_player(user_id, username)

    team = get_team(user_id)
    if team:
        await update.message.reply_text(
            "Welcome back, Trainer! Use /explore to find wild Pokémon, /team to see your Pokémon, /leaderboard to see top trainers."
        )
        return

    # If no team, ask to pick starter
    keyboard = [
        [InlineKeyboardButton(p["name"], callback_data=f"starter_{i}")] for i, p in enumerate(STARTERS)
    ]
    await update.message.reply_text(
        "Welcome Trainer! Choose your starter Pokémon:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSING

async def starter_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    idx = int(query.data.split('_')[1])
    starter = STARTERS[idx]

    add_pokemon(user_id, starter["name"], starter["hp"])
    await query.edit_message_text(f"You chose {starter['name']} as your starter! Your adventure begins.\nUse /explore to find wild Pokémon.")
    return ConversationHandler.END

async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    team = get_team(user_id)
    if not team:
        await update.message.reply_text("You need to pick a starter first! Use /start.")
        return

    # 70% chance to find wild Pokémon
    if random.random() < 0.7:
        wild = random.choice(WILD_POKEMON_POOL)
        context.user_data["wild_pokemon"] = {
            "name": wild["name"],
            "hp": wild["hp"],
            "max_hp": wild["hp"],
        }
        await update.message.reply_text(
            f"A wild {wild['name']} appeared! Use /battle to fight or /run to escape."
        )
    else:
        await update.message.reply_text("You explored but found nothing this time.")

async def show_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    team = get_team(user_id)
    if not team:
        await update.message.reply_text("You have no Pokémon yet. Use /start to begin your adventure.")
        return

    text = "Your Pokémon Team:\n"
    for idx, (pid, name, level, hp, max_hp, xp) in enumerate(team, 1):
        text += f"{idx}. {name} Lv.{level} HP:{hp}/{max_hp} XP:{xp}\n"
    await update.message.reply_text(text)

async def battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "wild_pokemon" not in context.user_data:
        await update.message.reply_text("No wild Pokémon to battle. Use /explore to find some!")
        return

    user_id = update.effective_user.id
    team = get_team(user_id)
    if not team:
        await update.message.reply_text("You have no Pokémon to battle with. Use /start first.")
        return

    # Pick first pokemon in team for battle (simplified)
    active = team[0]
    wild = context.user_data["wild_pokemon"]

    # Battle loop simplified: each turn player attacks, wild attacks back
    # We'll simulate one turn here with fixed damage

    # Player attacks wild
    player_damage = random.randint(8, 15)
    wild["hp"] -= player_damage

    # Wild attacks player pokemon
    wild_damage = random.randint(5, 12)
    new_hp = max(active[3] - wild_damage, 0)  # active[3] = current hp
    update_pokemon_hp(active[0], new_hp)

    battle_text = (
        f"Your {active[1]} dealt {player_damage} damage to wild {wild['name']}.\n"
        f"Wild {wild['name']} HP: {max(wild['hp'], 0)}/{wild['max_hp']}\n"
        f"Wild {wild['name']} dealt {wild_damage} damage back.\n"
        f"Your {active[1]} HP: {new_hp}/{active[4]}"
    )

    if wild["hp"] <= 0:
        # Wild fainted, player wins
        xp_gain = 50
        add_xp(user_id, xp_gain)
        add_pokemon_xp(active[0], 30)
        del context.user_data["wild_pokemon"]
        battle_text += f"\n\nWild {wild['name']} fainted! You gained {xp_gain} XP. Use /explore to find more Pokémon."
    elif new_hp <= 0:
        # Player's pokemon fainted
        battle_text += f"\n\nYour {active[1]} fainted! You lost the battle. Use /explore to try again."
        del context.user_data["wild_pokemon"]
    else:
        battle_text += "\n\nUse /battle to attack again or /run to flee."

    await update.message.reply_text(battle_text)

async def run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "wild_pokemon" in context.user_data:
        del context.user_data["wild_pokemon"]
        await update.message.reply_text("You ran away safely. Use /explore to look for other Pokémon.")
    else:
        await update.message.reply_text("No wild Pokémon to run from.")

async def catch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "wild_pokemon" not in context.user_data:
        await update.message.reply_text("No wild Pokémon to catch. Use /explore to find some.")
        return

    user_id = update.effective_user.id
    wild = context.user_data["wild_pokemon"]
    catch_rate = 0.4  # 40% chance to catch

    if random.random() < catch_rate:
        add_pokemon(user_id, wild["name"], wild["max_hp"])
        del context.user_data["wild_pokemon"]
        await update.message.reply_text(f"Congratulations! You caught a wild {wild['name']}! Use /team to see your Pokémon.")
    else:
        await update.message.reply_text(f"Oh no! The wild {wild['name']} escaped your Pokéball! Use /battle to continue fighting or /run to flee.")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute('SELECT username, xp FROM players ORDER BY xp DESC LIMIT 10')
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text("Leaderboard is empty!")
        return
    text = "🏆 Leaderboard - Total XP Gained 🏆\n\n"
    for i, (username, xp) in enumerate(rows, start=1):
        text += f"{i}. {username}: {xp} XP\n"
    await update.message.reply_text(text)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END

def main():
    TOKEN = os.getenv("BOT_TOKEN")  # reads from environment variable
    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING: [CallbackQueryHandler(starter_choice, pattern="^starter_")],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("explore", explore))
    app.add_handler(CommandHandler("team", show_team))
    app.add_handler(CommandHandler("battle", battle))
    app.add_handler(CommandHandler("run", run))
    app.add_handler(CommandHandler("catch", catch))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("cancel", cancel))

    print("Bot started. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == '__main__':
    main()
