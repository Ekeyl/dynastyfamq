import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.voice_states = True
intents.messages = True
intents.presences = True  # ← ДОБАВИТЬ

# --- СПИСОК СЕРВЕРОВ ---
# Добавляйте новые ID сюда через запятую
MY_GUILDS = [1449567765475364938,]

TOKEN = os.getenv('TOKEN')
# -----------------------

bot = discord.Bot(debug_guilds=MY_GUILDS, intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print(f'ID: {bot.user.id}')
    print('------')

# Загрузка когов (расширений)
#bot.load_extension("cogs.mute_context")
#bot.load_extension('cogs.nickname_protector')
#bot.load_extension('cogs.warn') 
#bot.load_extension('cogs.fdsf') 
#bot.load_extension('cogs.tiket') 
#bot.load_extension('cogs.capt_event')
#bot.load_extension('cogs.afk')
bot.load_extension('cogs.Events')
bot.load_extension('cogs.family_application')
#bot.load_extension('cogs.Mass')
#bot.load_extension("cogs.leave_family")
bot.load_extension('cogs.personal_thread')
#bot.load_extension('cogs.afk_cog')
#bot.load_extension('cogs.logbot')
#bot.load_extension('cogs.telegram_bot')
#bot.load_extension('cogs.otpusk')
#bot.load_extension('cogs.roles')
#bot.load_extension('cogs.clear')
#bot.load_extension('cogs.role_manager')
#bot.load_extension('cogs.sms_cog')
#bot.load_extension("cogs.delivery_cog")
#bot.load_extension("cogs.promotion_system")
#bot.load_extension("cogs.ai_cog")

bot.run(TOKEN)
