import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, InputText, Select, button
import json
import os
from datetime import datetime, timedelta, timezone

# --- КОНСТАНТЫ ---
TARGET_CHANNEL_ID = 1470893024392249476
NEW_PRIORITY_CHANNEL_ID = 1449567767605940278
SECOND_PRIORITY_CHANNEL_ID = 11449567767605940282

PRIORITY_CHANNELS = [
    (NEW_PRIORITY_CHANNEL_ID, "Основной канал мероприятий (высший приоритет)"),
    (SECOND_PRIORITY_CHANNEL_ID, "Второй приоритетный канал"),
    (TARGET_CHANNEL_ID, "Основной канал мероприятий")
]

EVENTS_FILE = 'events.json'
PRIORITY_ROLE_ID = [1460386174840475658, 1420506515386798141]

# Роли, которые могут управлять ивентами
ADMIN_ROLES = [1449567765810778211, 1449567765810778210, 1449567765810778205]

# Эмодзи для реакций
EMOJI_MAIN = "✅"
EMOJI_SPARE = "🔥"

# --- ФУНКЦИИ УПРАВЛЕНИЯ ДАННЫМИ ---
def load_events():
    try:
        with open(EVENTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_events_to_file(events):
    with open(EVENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(events, f, ensure_ascii=False, indent=4)

# --- МОДАЛЬНОЕ ОКНО СОЗДАНИЯ ---
class EventModal(Modal):
    def __init__(self):
        super().__init__(title="Создать мероприятие")
        self.add_item(InputText(label="Название", placeholder="Название мероприятия"))
        self.add_item(InputText(label="Время (МСК)", placeholder="ЧЧ:ММ (например: 18:00)"))
        self.add_item(InputText(label="Лимит основы", placeholder="Число мест в основе"))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            max_participants = int(self.children[2].value)
            if max_participants < 1: raise ValueError
        except ValueError:
            await interaction.followup.send("❌ Неверное количество участников!", ephemeral=True)
            return
        
        raw_time = self.children[1].value
        try:
            msk_tz = timezone(timedelta(hours=3))
            now = datetime.now(msk_tz)
            parsed_time = datetime.strptime(raw_time, "%H:%M").time()
            event_dt = datetime.combine(now.date(), parsed_time)
            event_dt = event_dt.replace(tzinfo=msk_tz)
            
            if event_dt < now:
                event_dt += timedelta(days=1)
            
            timestamp = int(event_dt.timestamp())
            
        except ValueError:
            await interaction.followup.send("❌ Неверный формат времени! Используйте: `ЧЧ:ММ` (например: 18:30)", ephemeral=True)
            return

        view = ChannelSelectView(
            self.children[0].value,
            timestamp, 
            max_participants,
            interaction
        )
        await interaction.followup.send("Выберите канал:", view=view, ephemeral=True)

# --- ВЫБОР КАНАЛА ---
class ChannelSelectView(View):
    def __init__(self, event_name, timestamp, max_participants, interaction):
        super().__init__(timeout=120)
        self.event_name = event_name
        self.timestamp = timestamp
        self.event_time = f"<t:{timestamp}:t> (<t:{timestamp}:R>)"
        self.max_participants = max_participants
        self.interaction = interaction
        
        selector = self.ChannelSelector(interaction.guild)
        if selector.options:
            self.add_item(selector)
        else:
            self.add_item(Button(label="Нет доступных каналов!", style=discord.ButtonStyle.secondary, disabled=True))

    class ChannelSelector(Select):
        def __init__(self, guild):
            channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages]
            options = []
            
            for channel_id, description in PRIORITY_CHANNELS:
                ch = guild.get_channel(channel_id)
                if ch and ch in channels:
                    options.append(discord.SelectOption(label=f"⭐ {ch.name}"[:100], value=str(ch.id), description=description[:100] if description else None))
                    channels.remove(ch)
            
            # Лимит 25 для опций меню Discord
            for ch in channels[:25 - len(options)]:
                options.append(discord.SelectOption(label=f"#{ch.name}"[:100], value=str(ch.id)))
            
            super().__init__(placeholder="Выберите канал...", min_values=1, max_values=1, options=options)

        async def callback(self, interaction: discord.Interaction):
            cog = interaction.client.get_cog('EventCog')
            channel = interaction.guild.get_channel(int(self.values[0]))
            
            embed = discord.Embed(
                title=self.view.event_name,
                description=f"**Начало:** {self.view.event_time}\n\n**Инструкция:**\nПиши `+` в ветку, чтобы встать в очередь.",
                color=0x2b2d31
            )
            
            view = EventView()
            message = await channel.send(content="@everyone", embed=embed, view=view)
            
            try:
                thread = await message.create_thread(name=f"Сбор:", auto_archive_duration=1440)
            except Exception as e:
                print(f"Не удалось создать ветку: {e}")
                thread = None

            cog.events[str(message.id)] = {
                "name": self.view.event_name,
                "time": self.view.event_time,
                "timestamp": self.view.timestamp,
                "max": self.view.max_participants,
                "main": [],        
                "spares": [],        
                "candidates": [],  
                "removed": [],       
                "open": True,
                "channel": channel.id,
                "thread_id": thread.id if thread else None
            }
            cog.save_events()
            
            await view.update_embed(message, cog)
            await interaction.response.send_message("✅ Мероприятие создано!", ephemeral=True)

# --- МОДАЛЬНОЕ ОКНО ПЕРЕНОСА ВРЕМЕНИ ---
class PostponeModal(Modal):
    def __init__(self, cog, message_id):
        super().__init__(title="Перенос мероприятия")
        self.cog = cog
        self.message_id = message_id
        self.add_item(InputText(label="Минуты", placeholder="На сколько минут перенести? (например: 15)"))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            minutes = int(self.children[0].value)
        except ValueError:
            return await interaction.followup.send("❌ Введите целое число минут!", ephemeral=True)

        event = self.cog.events.get(self.message_id)
        if not event:
            return await interaction.followup.send("❌ Ивент не найден в базе.", ephemeral=True)

        old_ts = event.get("timestamp")
        if not old_ts:
            return await interaction.followup.send("❌ Старый формат ивента. Перенос не поддерживается.", ephemeral=True)

        new_ts = old_ts + (minutes * 60)
        event["timestamp"] = new_ts
        event["time"] = f"<t:{new_ts}:t> (<t:{new_ts}:R>)"
        
        self.cog.save_events()

        channel = interaction.guild.get_channel(event["channel"])
        if channel:
            try:
                msg = await channel.fetch_message(int(self.message_id))
                await EventView().update_embed(msg, self.cog)

                if event.get("thread_id"):
                    thread = interaction.guild.get_thread(event["thread_id"]) or channel.get_thread(event["thread_id"])
                    if thread:
                        await thread.send(f"⏳ **Время сбора перенесено на {minutes} минут!**\nНовое время: <t:{new_ts}:t>")
            except Exception as e:
                pass

        await interaction.followup.send(f"✅ Время успешно перенесено на {minutes} минут!", ephemeral=True)

# --- ГЛАВНОЕ МЕНЮ ИВЕНТА ---
class EventView(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @button(label="Завершить сбор", style=discord.ButtonStyle.danger, custom_id="event:end_gathering", row=0)
    async def end_gathering_button(self, button: Button, interaction: discord.Interaction):
        if not any(r.id in ADMIN_ROLES for r in interaction.user.roles):
            return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        cog = interaction.client.get_cog('EventCog')
        await interaction.response.send_modal(ConfirmActionModal(cog, str(interaction.message.id), "end"))

    @button(label="Возобновить сбор", style=discord.ButtonStyle.success, custom_id="event:resume_gathering", row=0)
    async def resume_gathering_button(self, button: Button, interaction: discord.Interaction):
        if not any(r.id in ADMIN_ROLES for r in interaction.user.roles):
            return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        cog = interaction.client.get_cog('EventCog')
        await interaction.response.send_modal(ConfirmActionModal(cog, str(interaction.message.id), "resume"))

    @button(label="Перенос", style=discord.ButtonStyle.danger, custom_id="event:postpone", row=0)
    async def postpone_button(self, button: Button, interaction: discord.Interaction):
        if not any(r.id in ADMIN_ROLES for r in interaction.user.roles):
            return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        cog = interaction.client.get_cog('EventCog')
        await interaction.response.send_modal(PostponeModal(cog, str(interaction.message.id)))

    @button(label="Позвать всех (@everyone)", style=discord.ButtonStyle.blurple, custom_id="event:ping_channel", row=1)
    async def ping_channel_button(self, button: Button, interaction: discord.Interaction):
        if not any(r.id in ADMIN_ROLES for r in interaction.user.roles):
            return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        
        await interaction.message.reply(content="@everyone 📢 ОБЩИЙ СБОР!")
        await interaction.response.send_message("✅ Пинг отправлен в канал.", ephemeral=True)

    @button(label="Кто не в войсе? (ЛС)", style=discord.ButtonStyle.secondary, custom_id="event:check_voice", row=1)
    async def check_voice_button(self, button: Button, interaction: discord.Interaction):
        if not any(r.id in ADMIN_ROLES for r in interaction.user.roles):
            return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        cog = interaction.client.get_cog('EventCog')
        await interaction.response.send_message("Выберите войс для проверки:", view=VoiceChannelSelectView(cog, str(interaction.message.id), interaction.guild), ephemeral=True)

    @button(label="Напомнить всем (ЛС)", style=discord.ButtonStyle.secondary, custom_id="event:dm_all", row=1)
    async def dm_all_button(self, button: Button, interaction: discord.Interaction):
        if not any(r.id in ADMIN_ROLES for r in interaction.user.roles):
            return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        cog = interaction.client.get_cog('EventCog')
        await interaction.response.send_modal(ConfirmActionModal(cog, str(interaction.message.id), "dm_all"))

    async def update_embed(self, message: discord.Message, cog):
        event_id = str(message.id)
        if event_id not in cog.events: return
        event = cog.events[event_id]
        guild = message.guild

        # Функция теперь разбивает по 35 человек
        def get_mentions_chunks(user_ids, chunk_size=35):
            chunks = []
            for i in range(0, len(user_ids), chunk_size):
                chunk_ids = user_ids[i:i+chunk_size]
                mentions = []
                for j, uid in enumerate(chunk_ids):
                    m = guild.get_member(uid)
                    if m:
                        role_mark = ' ★' if any(r.id in PRIORITY_ROLE_ID for r in m.roles) else ''
                        mentions.append(f"{i+j+1}. {m.mention}{role_mark}")
                    else:
                        mentions.append(f"{i+j+1}. <@{uid}>")
                chunks.append("\n".join(mentions))
            return chunks if chunks else ["Пусто"]

        embed = message.embeds[0]
        embed.description = f"**Начало:** {event.get('time', 'Неизвестно')}\n\n**Инструкция:**\nПиши `+` в ветку, чтобы встать в очередь."
        embed.clear_fields()
        
        main_ids = event.get("main", [])
        spares_ids = event.get("spares", [])
        candidates_ids = event.get("candidates", [])
        removed_ids = event.get("removed", [])

        main_chunks = get_mentions_chunks(main_ids, 35)
        spares_chunks = get_mentions_chunks(spares_ids, 35)
        candidates_chunks = get_mentions_chunks(candidates_ids, 35)

        # Переменная для умного отслеживания сетки (Максимум 3 колонки в Discord)
        current_col = 0
        
        def add_inline_field(name, val):
            nonlocal current_col
            embed.add_field(name=name, value=val, inline=True)
            current_col = (current_col + 1) % 3
            
        def pad_to_new_row():
            # Заполняет остаток строки пустыми полями, чтобы следующая группа началась с новой строки
            nonlocal current_col
            while current_col != 0:
                embed.add_field(name="\u200b", value="\u200b", inline=True)
                current_col = (current_col + 1) % 3

        # 1. ОСНОВА
        for i, chunk in enumerate(main_chunks):
            name = f"ОСНОВА [{len(main_ids)}/{event['max']}]" if i == 0 else "ОСНОВА (продолжение)"
            add_inline_field(name, chunk)

        # 2. ЗАПАСНЫЕ
        for i, chunk in enumerate(spares_chunks):
            name = f"ЗАПАСНЫЕ [{len(spares_ids)}]" if i == 0 else "ЗАПАСНЫЕ (продолжение)"
            add_inline_field(name, chunk)

        # 3. РЕЗЕРВ
        # УМНАЯ СЕТКА: Если резерв разделен на 2 части (35+ человек), мы принудительно 
        # переносим его на новую строку. Это гарантирует, что "Продолжение" будет ровно СПРАВА!
        if len(candidates_chunks) > 1:
            pad_to_new_row()
            
        for i, chunk in enumerate(candidates_chunks):
            name = f"⚪ РЕЗЕРВ [{len(candidates_ids)}]" if i == 0 else "⚪ РЕЗЕРВ (продолжение)"
            add_inline_field(name, chunk)
            
        # 4. УБРАЛИ ПЛЮС (Отдельными блоками снизу)
        if removed_ids:
            removed_chunks = get_mentions_chunks(removed_ids, 35)
            for i, chunk in enumerate(removed_chunks):
                name = f"❌ УБРАЛИ ПЛЮС [{len(removed_ids)}]" if i == 0 else "❌ УБРАЛИ ПЛЮС (продолжение)"
                embed.add_field(name=name, value=chunk, inline=False)
        else:
            embed.add_field(name="❌ УБРАЛИ ПЛЮС [0]", value="Пусто", inline=False)
        
        # СТАТУС
        status = "🟢 Сбор открыт (Пиши + в ветку)" if event["open"] else "🔴 Сбор закрыт"
        embed.add_field(name="Статус", value=status, inline=False)
        
        await message.edit(embed=embed)

# --- УНИВЕРСАЛЬНОЕ ОКНО ПОДТВЕРЖДЕНИЯ ---
class ConfirmActionModal(Modal):
    def __init__(self, cog, message_id, action):
        title = "Подтверждение"
        self.cog = cog
        self.message_id = message_id
        self.action = action
        
        if action == "end": title = "Завершить сбор?"
        elif action == "resume": title = "Возобновить сбор?"
        elif action == "dm_all": title = "Написать всем в ЛС?"
        
        super().__init__(title=title)
        self.add_item(InputText(label="Напишите 'да'", placeholder="да"))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True) 

        if self.children[0].value.strip().lower() == "да":
            event = self.cog.events.get(self.message_id)
            if not event: return

            if self.action == "end":
                event["open"] = False
                self.cog.save_events()
                try:
                    channel = interaction.guild.get_channel(event["channel"])
                    if channel:
                        msg = await channel.fetch_message(int(self.message_id))
                        await EventView().update_embed(msg, self.cog)
                        if event.get("thread_id"):
                            thread = interaction.guild.get_thread(event["thread_id"]) or channel.get_thread(event["thread_id"])
                            if thread: await thread.edit(locked=True, archived=True)
                except: pass
                await interaction.followup.send("🔴 Сбор закрыт!", ephemeral=True)

            elif self.action == "resume":
                event["open"] = True
                self.cog.save_events()
                try:
                    channel = interaction.guild.get_channel(event["channel"])
                    if channel:
                        msg = await channel.fetch_message(int(self.message_id))
                        await EventView().update_embed(msg, self.cog)
                        if event.get("thread_id"):
                            thread = interaction.guild.get_thread(event["thread_id"]) or channel.get_thread(event["thread_id"])
                            if thread: 
                                await thread.edit(locked=False, archived=False)
                                await thread.send("🟢 Сбор возобновлен!")
                except: pass
                await interaction.followup.send("🟢 Сбор возобновлен!", ephemeral=True)

            elif self.action == "dm_all":
                all_participants = list(set(event.get("main", []) + event.get("spares", []) + event.get("candidates", [])))
                count = 0
                for uid in all_participants:
                    member = interaction.guild.get_member(uid)
                    if member:
                        try:
                            msg_link = f"https://discord.com/channels/{interaction.guild.id}/{event['channel']}/{self.message_id}"
                            await member.send(f"🔔 **Напоминание о мероприятии!**\nСбор идет здесь: {msg_link}")
                            count += 1
                        except: pass
                await interaction.followup.send(f"✅ Уведомления отправлены {count} участникам!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Отмена", ephemeral=True)

# --- ИСПРАВЛЕННЫЙ ВЫБОР ВОЙСА ---
class VoiceChannelSelectView(View):
    def __init__(self, cog, message_id, guild):
        super().__init__(timeout=120)
        self.cog = cog
        self.message_id = message_id
        
        # Лимит 25 для опций
        vcs = [ch for ch in guild.voice_channels if ch.permissions_for(guild.me).connect][:25]
        
        if not vcs:
            self.add_item(Button(label="Нет доступных войс-каналов", disabled=True, style=discord.ButtonStyle.secondary))
        else:
            self.add_item(self.VoiceChannelSelector(vcs))

    class VoiceChannelSelector(Select):
        def __init__(self, channels):
            options = []
            for ch in channels:
                options.append(discord.SelectOption(label=ch.name[:100], value=str(ch.id)))
            
            super().__init__(placeholder="Выберите войс канал...", min_values=1, max_values=1, options=options)

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            ch = interaction.guild.get_channel(int(self.values[0]))
            event = self.view.cog.events.get(self.view.message_id)
            if not ch or not event: 
                return await interaction.followup.send("❌ Ошибка получения данных", ephemeral=True)
            
            all_participants_ids = list(set(event.get("main", []) + event.get("spares", []) + event.get("candidates", [])))
            members_in_voice_ids = [m.id for m in ch.members]
            missing_ids = [uid for uid in all_participants_ids if uid not in members_in_voice_ids]
            
            count = 0
            for uid in missing_ids:
                member = interaction.guild.get_member(uid)
                if member:
                    try: 
                        await member.send(f"📢 **Вы записаны на ивент, но вас нет в войсе!**\nЗаходите скорее: {ch.mention}")
                        count += 1
                    except: pass
            
            await interaction.followup.send(f"✅ Найдено отсутствующих: {len(missing_ids)}. Оповещено в ЛС: {count}.", ephemeral=True)
            self.view.stop()

# --- КНОПКА СОЗДАНИЯ ---
class CreateEventView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @button(label="Создать", style=discord.ButtonStyle.blurple, custom_id="create_event")
    async def create_button(self, button: Button, interaction: discord.Interaction):
        await interaction.response.send_modal(EventModal())

# --- MAIN COG ---
class EventCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.events = load_events()

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(CreateEventView())
        self.bot.add_view(EventView())
        print(f"[Events] Loaded {len(self.events)} events")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        
        if isinstance(message.channel, discord.Thread):
            event_id = str(message.channel.id)
            
            if event_id in self.events:
                event = self.events[event_id]
                if not event["open"]: return
                content = message.content.strip().lower()
                user_id = message.author.id
                
                if "main" not in event: event["main"] = []
                if "spares" not in event: event["spares"] = []
                if "candidates" not in event: event["candidates"] = []
                if "removed" not in event: event["removed"] = []
                changed = False
                
                if content in ["+", "plus", "go", "++", "ghb", "gkj", "гг"]:
                    if user_id in event["removed"]:
                        event["removed"].remove(user_id)
                        changed = True
                    
                    is_active = (user_id in event["main"] or user_id in event["spares"] or user_id in event["candidates"])
                    
                    if not is_active:
                        event["candidates"].append(user_id)
                        changed = True
                
                elif content in ["-", "minus", "pass", "--", "vby", "пас"]:
                    if user_id in event["main"]: event["main"].remove(user_id); changed = True
                    if user_id in event["spares"]: event["spares"].remove(user_id); changed = True
                    if user_id in event["candidates"]: event["candidates"].remove(user_id); changed = True
                    
                    if user_id not in event["removed"]:
                        event["removed"].append(user_id)
                        await message.add_reaction("👌")
                        changed = True
                
                if changed:
                    self.save_events()
                    parent_channel = message.channel.parent
                    if parent_channel:
                        try:
                            msg = await parent_channel.fetch_message(int(event_id))
                            view = EventView()
                            await view.update_embed(msg, self)
                        except: pass

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot: return
        if not isinstance(message.channel, discord.Thread): return
        event_id = str(message.channel.id)
        if event_id not in self.events: return
        event = self.events[event_id]
        plus_words = ["+", "plus", "go", "++", "ghb", "gkj", "гг"]
        content = message.content.strip().lower()
        
        if content in plus_words:
            user_id = message.author.id
            changed = False
            if user_id in event["main"]: event["main"].remove(user_id); changed = True
            if user_id in event["spares"]: event["spares"].remove(user_id); changed = True
            if user_id in event["candidates"]: event["candidates"].remove(user_id); changed = True
            
            if changed:
                if user_id not in event["removed"]:
                    event["removed"].append(user_id)
                self.save_events()
                parent_channel = message.channel.parent
                if parent_channel:
                    try:
                        msg = await parent_channel.fetch_message(int(event_id))
                        view = EventView()
                        await view.update_embed(msg, self)
                    except: pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.member.bot: return
        if not any(r.id in ADMIN_ROLES for r in payload.member.roles): return
        
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.Thread): return
        
        event_id = str(channel.id)
        if event_id not in self.events: return
        event = self.events[event_id]
        if payload.user_id == self.bot.user.id: return
        
        try:
            target_message = await channel.fetch_message(payload.message_id)
            target_user_id = target_message.author.id
            if target_message.author.bot: return
        except: return
        
        changed = False
        emoji = str(payload.emoji)
        
        if emoji == EMOJI_MAIN:
            if target_user_id in event["candidates"]: event["candidates"].remove(target_user_id)
            if target_user_id in event["spares"]: event["spares"].remove(target_user_id)
            if target_user_id not in event["main"]:
                event["main"].append(target_user_id)
                changed = True
        elif emoji == EMOJI_SPARE:
            if target_user_id in event["candidates"]: event["candidates"].remove(target_user_id)
            if target_user_id in event["main"]: event["main"].remove(target_user_id)
            if target_user_id not in event["spares"]:
                event["spares"].append(target_user_id)
                changed = True
                
        if changed:
            self.save_events()
            parent_channel = channel.parent
            if parent_channel:
                try:
                    msg = await parent_channel.fetch_message(int(event_id))
                    view = EventView()
                    await view.update_embed(msg, self)
                except: pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        if not member or member.bot: return
        if not any(r.id in ADMIN_ROLES for r in member.roles): return
        
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.Thread): return
        
        event_id = str(channel.id)
        if event_id not in self.events: return
        event = self.events[event_id]
        
        try:
            target_message = await channel.fetch_message(payload.message_id)
            target_user_id = target_message.author.id
        except: return
        
        changed = False
        emoji = str(payload.emoji)
        
        if emoji == EMOJI_MAIN:
            if target_user_id in event["main"]:
                event["main"].remove(target_user_id)
                if target_user_id not in event["candidates"] and target_user_id not in event["spares"] and target_user_id not in event["removed"]:
                    event["candidates"].append(target_user_id)
                changed = True
        elif emoji == EMOJI_SPARE:
            if target_user_id in event["spares"]:
                event["spares"].remove(target_user_id)
                if target_user_id not in event["candidates"] and target_user_id not in event["main"] and target_user_id not in event["removed"]:
                    event["candidates"].append(target_user_id)
                changed = True
                
        if changed:
            self.save_events()
            parent_channel = channel.parent
            if parent_channel:
                try:
                    msg = await parent_channel.fetch_message(int(event_id))
                    view = EventView()
                    await view.update_embed(msg, self)
                except: pass

    @commands.slash_command(name="events", description="Управление мероприятиями")
    async def events_command(self, ctx: discord.ApplicationContext):
        if not any(r.id in ADMIN_ROLES for r in ctx.author.roles):
            return await ctx.respond("❌ Нет прав!", ephemeral=True)
        embed = discord.Embed(title="📅 Менеджер", description="Создать мероприятие:", color=0x5865F2)
        await ctx.respond(embed=embed, view=CreateEventView())

    def save_events(self):
        save_events_to_file(self.events)

def setup(bot):
    bot.add_cog(EventCog(bot))
