import discord
from discord.ext import commands
import sqlite3

# --- Константы ---
TARGET_CHANNEL_ID = 1449567768755175454  # Для /personal
ACADEMY_CHANNEL_ID = 1431388120724672711  # Для /academy

MENTION_ROLES = [
    1449567765810778211,
    1449567765810778210,
    1449567765810778205,
]

DB_PATH = "threads.db"
EMBED_COLOR = discord.Color(0x393a41) # Тот самый цвет


# --- Работа с БД ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS personal_threads (
            user_id INTEGER,
            thread_id INTEGER PRIMARY KEY,
            channel_id INTEGER
        )
    """)
    conn.commit()
    conn.close()


def has_thread(user_id: int, channel_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM personal_threads WHERE user_id=? AND channel_id=?",
        (user_id, channel_id),
    )
    result = c.fetchone()
    conn.close()
    return result is not None


def add_thread(user_id: int, thread_id: int, channel_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO personal_threads VALUES (?, ?, ?)",
        (user_id, thread_id, channel_id),
    )
    conn.commit()
    conn.close()


def remove_thread(thread_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM personal_threads WHERE thread_id=?", (thread_id,))
    conn.commit()
    conn.close()


# --- UI ---
class CreateThreadView(discord.ui.View):
    def __init__(self, view_type="personal", label="🔒 Создать личную ветку"):
        super().__init__(timeout=None)
        custom_id = f"{view_type}:create"
        self.add_item(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.blurple,
                custom_id=custom_id,
            )
        )


# --- Cog ---
class PersonalThreadCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        init_db()
        self.bot.loop.create_task(self.register_views())
        self.bot.loop.create_task(self.cleanup_db())

    async def register_views(self):
        await self.bot.wait_until_ready()
        self.bot.add_view(CreateThreadView("personal", "🔒 Создать личную ветку"))
        self.bot.add_view(CreateThreadView("academy", "🔒 Создать ветку Академии"))

    async def cleanup_db(self):
        """Очищает базу от удалённых веток при запуске бота"""
        await self.bot.wait_until_ready()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT thread_id FROM personal_threads")
        threads = c.fetchall()
        conn.close()

        for (thread_id,) in threads:
            thread = self.bot.get_channel(thread_id)
            if thread is None:
                remove_thread(thread_id)

    @discord.slash_command(
        name="personal",
        description="Отправить кнопку для создания личной ветки"
    )
    async def send_button(self, ctx: discord.ApplicationContext):
        if ctx.channel.id != TARGET_CHANNEL_ID:
            await ctx.respond(
                "Эта команда доступна только в указанном канале!",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🔒 Личная ветка",
            description="Нажмите на кнопку ниже, чтобы создать свою личную ветку.\n\nВ этой ветке сможете писать только вы и администрация сервера.",
            color=EMBED_COLOR,
        )
        embed.add_field(name="📁 Категория", value="#— личная ветка", inline=True)
        embed.add_field(name="👤 Доступ", value="Только вы и администрация", inline=True)
        # Пустое имя поля для текста снизу
        embed.add_field(name="\u200b", value="Личные ветки создаются автоматически", inline=False)

        await ctx.respond(embed=embed, view=CreateThreadView("personal", "🔒 Создать личную ветку"))

    @discord.slash_command(
        name="academy",
        description="Отправить кнопку для создания ветки Академии"
    )
    async def send_academy_button(self, ctx: discord.ApplicationContext):
        if ctx.channel.id != ACADEMY_CHANNEL_ID:
            await ctx.respond(
                "Эта команда доступна только в канале Академии!",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🔒 Ветка Академии",
            description="Нажмите на кнопку ниже, чтобы создать ветку для отчётов.\n\nВ этой ветке сможете писать только вы и администрация сервера.",
            color=EMBED_COLOR,
        )
        embed.add_field(name="📁 Категория", value="#— ветка академии", inline=True)
        embed.add_field(name="👤 Доступ", value="Только вы и администрация", inline=True)
        embed.add_field(name="\u200b", value="Ветки создаются автоматически", inline=False)

        await ctx.respond(embed=embed, view=CreateThreadView("academy", "🔒 Создать ветку Академии"))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id")
        if custom_id not in ("personal:create", "academy:create"):
            return

        is_academy = custom_id.startswith("academy:")
        expected_channel = ACADEMY_CHANNEL_ID if is_academy else TARGET_CHANNEL_ID

        if interaction.channel.id != expected_channel:
            await interaction.response.send_message(
                "❌ Ветки можно создавать только в указанном канале!",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        user = interaction.user

        if has_thread(user.id, interaction.channel.id):
            await interaction.followup.send(
                "❌ У вас уже есть ветка в этом канале!",
                ephemeral=True,
            )
            return

        thread_name = f"Ветка {user.display_name}"
        try:
            thread = await interaction.channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=10080,
            )
            await thread.add_user(user)

            add_thread(user.id, thread.id, interaction.channel.id)

            role_mentions = " ".join(f"<@&{role_id}>" for role_id in MENTION_ROLES)
            await thread.send(f"{role_mentions} {user.mention}")

            info_embed = discord.Embed(
                description=(
                    "📌 Здесь нужно скидывать **откаты со всех каптов и МЦЛ**, которые вы отыграли.\n"
                    "А также прикладывать скрины с Арены.\n\n"
                    "⚠️ Ветка закрепляется за одним человеком. "
                    "При выходе с сервера — ветка удаляется."
                ),
                color=EMBED_COLOR,
            )
            await thread.send(embed=info_embed)

            await interaction.followup.send(
                "✅ Ветка успешно создана!", ephemeral=True
            )
        except Exception as e:
            print(f"Error creating thread: {e}")
            await interaction.followup.send(
                "❌ Произошла ошибка при создании ветки!",
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        remove_thread(thread.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Удаляет ветку, если её владелец вышел/кикнут"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT thread_id FROM personal_threads WHERE user_id=?", (member.id,))
        row = c.fetchone()
        conn.close()

        if row:
            thread_id = row[0]

            thread = member.guild.get_thread(thread_id)
            if thread is None:
                try:
                    thread = await member.guild.fetch_channel(thread_id)
                except discord.NotFound:
                    thread = None

            if thread:
                try:
                    await thread.delete()
                except Exception as e:
                    print(f"Ошибка при удалении ветки после выхода: {e}")

            remove_thread(thread_id)


# Функция для регистрации Cog'а
def setup(bot: discord.Bot):
    bot.add_cog(PersonalThreadCog(bot))