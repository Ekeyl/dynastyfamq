import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, InputText, Select
import aiosqlite
from datetime import datetime, timedelta

# --- НАСТРОЙКИ РОЛЕЙ И КАНАЛОВ ---
ADMIN_ROLES = [1449567765810778211, 1449567765810778210, 1449567765810778205, 1449567765810778202]
ACCEPTED_ROLE_ID = [1449575866630934640]

BASE_ACCEPTED_ROLE = 1449567765798191154

REMOVE_ROLE_ID = 1362061684579106841
ACCEPTANCE_CHANNEL_ID = 1449567767857729704
MENTION_ROLES = [1449567765810778211, 1449567765810778210, 1449567765810778205, 1449567765810778202]

# Единый цвет для всех эмбедов
EMBED_COLOR = discord.Color(0x393a41)

# --- БАЗЫ ДАННЫХ ---
APPLICATIONS_DB = 'applications.db'
STATS_DB = 'stats.db'


async def setup_stats_db():
    async with aiosqlite.connect(STATS_DB) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS recruiter_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_number INTEGER,
                recruiter_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                accepted_at TEXT NOT NULL
            )
        ''')
        await db.commit()


# ========================
#   MODALS
# ========================

class ApplicationModal(Modal):
    def __init__(self):
        super().__init__(title="Заявка на вступление в семью")
        self.add_item(InputText(label="Имя IC | Статик", placeholder="John | STATIC"))
        self.add_item(InputText(label="OOC Возраст", placeholder="Пример: 18"))
        self.add_item(InputText(label="Средний онлайн в день", placeholder="Пример: 4 часа"))
        self.add_item(InputText(label="Ссылки на откаты", placeholder="Пример: https://youtu.be/example", style=discord.InputTextStyle.long))
        self.add_item(InputText(label="Почему выбрали нас и где были раньше", placeholder="Пример: Хочу быть частью вашей команды...", style=discord.InputTextStyle.long))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        new_nickname = self.children[0].value

        async with aiosqlite.connect(APPLICATIONS_DB) as db:
            await db.execute('''
                INSERT INTO applications (user_id, new_nickname, application_type)
                VALUES (?, ?, ?)
            ''', (user.id, new_nickname, 'family'))
            await db.commit()
            async with db.execute('SELECT last_insert_rowid()') as cursor:
                ticket_number = (await cursor.fetchone())[0]

        thread_name = f"ticket-{ticket_number:03d}"
        thread = await interaction.channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread
        )

        role_mentions = " ".join(f"<@&{role_id}>" for role_id in MENTION_ROLES)
        user_mention = f"<@{user.id}>"

        embed = discord.Embed(
            title="Новая заявка на вступление в семью",
            description=f"**Подал:** {user.mention} ({user.name})",
            color=EMBED_COLOR
        )
        
        avatar_url = user.avatar.url if user.avatar else user.default_avatar.url
        embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="Имя IC | Статик", value=self.children[0].value, inline=False)
        embed.add_field(name="OOC Возраст", value=self.children[1].value, inline=False)
        embed.add_field(name="Средний онлайн в день", value=self.children[2].value, inline=False)
        embed.add_field(name="Ссылки на откаты", value=self.children[3].value, inline=False)
        embed.add_field(name="Почему выбрали нас и где были", value=self.children[4].value, inline=False)
        embed.set_footer(text=f"ID заявки: {thread_name}")

        view = ApplicationView()
        message = await thread.send(content=f"{role_mentions} {user_mention}", embed=embed, view=view)

        async with aiosqlite.connect(APPLICATIONS_DB) as db:
            await db.execute('''
                UPDATE applications
                SET thread_id = ?, message_id = ?
                WHERE ticket_number = ?
            ''', (thread.id, message.id, ticket_number))
            await db.commit()

        await interaction.followup.send("Заявка успешно подана!", ephemeral=True)


class CloseConfirmationModal(Modal):
    def __init__(self, message_id):
        super().__init__(title="Подтверждение закрытия заявки")
        self.message_id = message_id
        self.add_item(InputText(
            label="Вы уверены, что хотите закрыть заявку?",
            placeholder="Введите 'да' для подтверждения или 'нет' для отмены",
            required=True
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        answer = self.children[0].value.strip().lower()
        if answer == 'да':
            cog = interaction.client.get_cog("FamilyApplicationCog")
            await cog.handle_close(interaction, self.message_id)
        else:
            await interaction.followup.send("❌ Закрытие заявки отменено", ephemeral=True)


class AcceptConfirmationModal(Modal):
    def __init__(self, message_id):
        super().__init__(title="Подтверждение принятия")
        self.message_id = message_id
        self.add_item(InputText(
            label="Вы уверены, что хотите принять игрока?",
            placeholder="Введите 'да' для подтверждения",
            required=True
        ))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        answer = self.children[0].value.strip().lower()
        if answer == 'да':
            cog = interaction.client.get_cog("FamilyApplicationCog")
            await cog.handle_accept(interaction, self.message_id)
        else:
            await interaction.followup.send("❌ Принятие заявки отменено", ephemeral=True)


class RejectModal(Modal):
    def __init__(self, message_id):
        super().__init__(title="Причина отказа")
        self.message_id = message_id
        self.add_item(InputText(label="Причина отказа", placeholder="Введите причину"))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        reason = self.children[0].value

        try:
            async with aiosqlite.connect(APPLICATIONS_DB) as db:
                async with db.execute('''
                    SELECT thread_id, user_id, ticket_number 
                    FROM applications 
                    WHERE message_id = ? AND status = "pending"
                ''', (self.message_id,)) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        await interaction.followup.send("Заявка не найдена или уже обработана.", ephemeral=True)
                        return

            thread_id, user_id, ticket_number = row

            try:
                user = interaction.guild.get_member(user_id) or await interaction.guild.fetch_member(user_id)
            except discord.HTTPException:
                await interaction.followup.send("Не удалось найти пользователя на сервере.", ephemeral=True)
                return

            thread = None
            try:
                thread = interaction.guild.get_channel(thread_id) or await interaction.guild.fetch_channel(thread_id)
            except discord.HTTPException as e:
                if interaction.channel:
                    await interaction.channel.send(f"Ошибка при получении ветки: {e}.")

            try:
                await user.send(f"Ваша заявка отклонена. Причина: {reason}")
            except (discord.Forbidden, discord.HTTPException):
                pass

            if thread:
                try:
                    await thread.send(f"{user.mention} получил отказ. Причина: {reason}. Решение принял: {interaction.user.mention}")
                    await thread.remove_user(user)
                except discord.HTTPException:
                    pass

            now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
            async with aiosqlite.connect(APPLICATIONS_DB) as db2:
                await db2.execute('''
                    UPDATE applications 
                    SET status = "rejected", recruiter_id = ?, accepted_at = ? 
                    WHERE ticket_number = ?
                ''', (interaction.user.id, now_str, ticket_number))
                await db2.commit()

        except Exception as e:
            print(f"Error in RejectModal: {e}")
            await interaction.followup.send("❌ Произошла ошибка при обработке отказа!", ephemeral=True)
            return

        await interaction.followup.send("Отказ отправлен!", ephemeral=True)


# ========================
#   ADMIN ADD MENU
# ========================

class AdminAcceptModal(Modal):
    def __init__(self):
        super().__init__(title="Принятие игрока")
        self.add_item(InputText(label="ID Рекрутера", placeholder="Например: 420285458011127810", required=True))
        self.add_item(InputText(label="ID Игрока (кого приняли)", placeholder="Например: 123456789012345678", required=True))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            recruiter_id = int(self.children[0].value.strip())
            user_id = int(self.children[1].value.strip())
            now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

            async with aiosqlite.connect(STATS_DB) as db:
                await db.execute('''
                    INSERT INTO recruiter_stats (ticket_number, recruiter_id, user_id, accepted_at)
                    VALUES (?, ?, ?, ?)
                ''', (0, recruiter_id, user_id, now_str))
                await db.commit()

            await interaction.followup.send(f"✅ Успешно добавлено **принятие** рекрутеру <@{recruiter_id}>!", ephemeral=True)
        except ValueError:
            await interaction.followup.send("❌ Ошибка: ID должны быть числами.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)

class AdminRejectModal(Modal):
    def __init__(self):
        super().__init__(title="Отказ")
        self.add_item(InputText(label="ID Рекрутера", placeholder="Например: 420285458011127810", required=True))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            recruiter_id = int(self.children[0].value.strip())
            now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

            async with aiosqlite.connect(APPLICATIONS_DB) as db:
                await db.execute('''
                    INSERT INTO applications (user_id, status, recruiter_id, accepted_at)
                    VALUES (?, ?, ?, ?)
                ''', (0, 'rejected', recruiter_id, now_str))
                await db.commit()

            await interaction.followup.send(f"✅ Успешно добавлен **отказ** рекрутеру <@{recruiter_id}>!", ephemeral=True)
        except ValueError:
            await interaction.followup.send("❌ Ошибка: ID должны быть числами.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)

class AdminMenuView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Принял", style=discord.ButtonStyle.green, custom_id="admin:acc", row=0)
    async def acc(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(AdminAcceptModal())

    @discord.ui.button(label="❌ Отказ", style=discord.ButtonStyle.red, custom_id="admin:rej", row=0)
    async def rej(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(AdminRejectModal())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != 420285458011127810:
            await interaction.response.send_message("❌ Доступ запрещен.", ephemeral=True)
            return False
        return True


# ========================
#   VIEWS
# ========================

class ApplicationView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="Принять", style=discord.ButtonStyle.green, custom_id="application:accept"))
        self.add_item(Button(label="Отказать", style=discord.ButtonStyle.red, custom_id="application:reject"))
        self.add_item(Button(label="Обзвон", style=discord.ButtonStyle.blurple, custom_id="application:call"))
        self.add_item(Button(label="Закрыть заявку", style=discord.ButtonStyle.grey, custom_id="application:close"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return self.has_permission(interaction.user)

    def has_permission(self, user: discord.Member) -> bool:
        if user.guild_permissions.administrator:
            return True
        user_role_ids = [role.id for role in user.roles]
        return any(role_id in ADMIN_ROLES for role_id in user_role_ids)


class ApplyView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="Заявка в семью", style=discord.ButtonStyle.blurple, custom_id="submit_application"))


# ========================
#   RECRUITER STATS MENU
# ========================

class RecruiterStatsMenu(View):
    def __init__(self, ctx, period, main_embed, detailed_data, rejected_data):
        super().__init__(timeout=600)
        self.ctx = ctx
        self.period = period
        self.main_embed = main_embed
        self.detailed_data = detailed_data
        self.rejected_data = rejected_data

        sorted_recruiters = sorted(detailed_data.items(), key=lambda item: len(item[1]), reverse=True)[:25]

        options = []
        for rec_id, data in sorted_recruiters:
            member = ctx.guild.get_member(rec_id)
            name = member.display_name if member else f"ID: {rec_id}"

            active = 0
            for row in data:
                t_member = ctx.guild.get_member(row[0])
                if t_member and REMOVE_ROLE_ID not in [r.id for r in t_member.roles]:
                    active += 1

            options.append(discord.SelectOption(
                label=name[:100],
                description=f"Принято: {len(data)} | В активе: {active}",
                value=str(rec_id),
                emoji="📋"
            ))

        self.select = Select(placeholder="🔍 Выберите рекрутера для просмотра деталей...", options=options, row=0)
        self.select.callback = self.on_select
        self.add_item(self.select)

        self.back_button = Button(label="← Общая статистика", style=discord.ButtonStyle.secondary, row=1)
        self.back_button.callback = self.on_back
        self.back_button.disabled = True
        self.add_item(self.back_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ Вы не можете использовать это меню!", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        rec_id = int(self.select.values[0])
        self.back_button.disabled = False
        embed = self.build_detailed_embed(rec_id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_back(self, interaction: discord.Interaction):
        self.back_button.disabled = True
        await interaction.response.edit_message(embed=self.main_embed, view=self)

    def build_detailed_embed(self, rec_id):
        member = self.ctx.guild.get_member(rec_id)
        name = member.display_name if member else f"ID {rec_id}"
        
        data = self.detailed_data.get(rec_id, [])
        rej_info = self.rejected_data.get(rec_id, {'count': 0})
        
        total_accepted = len(data)
        active_count = 0
        left_count = 0
        fired_count = 0
        
        rejected_count = rej_info['count']

        lines = []
        for idx, row in enumerate(data, 1):
            user_id, accepted_at = row
            member_target = self.ctx.guild.get_member(user_id)

            if member_target is None:
                status_emoji = "❌"
                status_text = "Ливнул"
                left_count += 1
            elif REMOVE_ROLE_ID in [r.id for r in member_target.roles]:
                status_emoji = "🛑"
                status_text = "Уволен"
                fired_count += 1
            else:
                status_emoji = "✅"
                status_text = "Актив"
                active_count += 1

            date_str = "—"
            if accepted_at:
                try:
                    date_str = datetime.fromisoformat(accepted_at).strftime("%d.%m.%Y")
                except ValueError:
                    pass

            lines.append(
                f"`{idx:>2}.` <@{user_id}>\n"
                f"     {status_emoji} **{status_text}** · 📅 `{date_str}`"
            )

        player_list = "\n".join(lines)
        if len(player_list) > 3800:
            player_list = player_list[:3800] + "\n\n*... список обрезан*"
            
        inactive_count = left_count + fired_count
        salary_active = active_count * 20000
        salary_inactive = inactive_count * 5000
        salary_rejected = rejected_count * 1000
        total_salary = salary_active + salary_inactive + salary_rejected

        embed = discord.Embed(
            color=EMBED_COLOR
        )

        icon_url = None
        if member:
            icon_url = member.avatar.url if member.avatar else member.default_avatar.url

        embed.set_author(
            name=f"Детализация · {name}",
            icon_url=icon_url
        )
        
        if icon_url:
            embed.set_thumbnail(url=icon_url)

        embed.description = (
            f"```\n"
            f"  Период      {self.period}\n"
            f"  Принято     {total_accepted}\n"
            f"  В активе    {active_count}\n"
            f"  Уволено     {fired_count}\n"
            f"  Ливнуло     {left_count}\n"
            f"  Отказов     {rejected_count}\n"
            f"```\n"
            f"💸 **К выплате:** `{total_salary:,}$`\n"
            f"*(Актив: {salary_active:,}$ | Неактив: {salary_inactive:,}$ | Отказы: {salary_rejected:,}$)*\n\n"
            f"**Список принятых игроков:**\n\n"
            f"{player_list}"
        ).replace(',', ' ')

        embed.set_footer(text=f"Статистика рекрутера", icon_url=self.ctx.guild.icon.url if self.ctx.guild.icon else None)
        return embed


# ========================
#   COG
# ========================

class FamilyApplicationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self.setup_db())
        self.bot.loop.create_task(self.register_views())

    async def register_views(self):
        await self.bot.wait_until_ready()
        self.bot.add_view(ApplyView())
        self.bot.add_view(ApplicationView())
        self.bot.add_view(AdminMenuView())

    async def setup_db(self):
        async with aiosqlite.connect(APPLICATIONS_DB) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS applications (
                    ticket_number INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER,
                    message_id INTEGER,
                    user_id INTEGER NOT NULL,
                    new_nickname TEXT,
                    application_type TEXT,
                    status TEXT DEFAULT 'pending'
                )
            ''')
            for col, definition in [
                ('recruiter_id', 'INTEGER'),
                ('accepted_at', 'TIMESTAMP'),
            ]:
                try:
                    await db.execute(f'ALTER TABLE applications ADD COLUMN {col} {definition}')
                except Exception:
                    pass
            await db.commit()

        await setup_stats_db()

    # ---- SLASH COMMANDS ----

    @discord.slash_command(name="admin_menu", description="Секретное меню администратора (только для владельца)")
    async def admin_menu_command(self, ctx: discord.ApplicationContext):
        if ctx.author.id != 420285458011127810:
            await ctx.respond("❌ Доступ запрещен. Эта команда только для разработчика.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🛠️ Панель Администратора",
            description="Выберите, какую именно статистику вы хотите добавить рекруту, нажав на соответствующую кнопку ниже:",
            color=EMBED_COLOR
        )
        view = AdminMenuView()
        await ctx.respond(embed=embed, view=view, ephemeral=True)

    @discord.slash_command(name="apply", description="Подать заявку на вступление в семью")
    async def apply_command(self, ctx: discord.ApplicationContext):
        embed = discord.Embed(
            title="Заявка на вступление в семью",
            description=(
                "Если у тебя появилось желание пополнить наши ряды, то ты можешь оставить заявку, нажав на кнопку ниже.\n\n"
                "**Требования для вступления:**\n"
                "- Возраст 16+\n"
                "- Уровень стрельбы и понимания игры выше среднего.\n"
                "- Два отката с игры GunGame, продолжительностью 2 минуты. «Сайга» + «Тяжки» или «Карабинка»."
            ),
            color=EMBED_COLOR
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.set_image(url="https://media.discordapp.net/attachments/1436676730612879370/1462457377910689893/standard_2_1.gif?ex=696e4312&is=696cf192&hm=f5d435dba5cd31e35a5159503a1b12cfa95ca5c1082d8392e0451099f449e146&=")
        view = ApplyView()
        await ctx.respond(embed=embed, view=view)

    @discord.slash_command(name="recruiter_stats", description="Статистика рекрутеров")
    async def recruiter_stats(
        self,
        ctx: discord.ApplicationContext,
        period: discord.Option(str, "Выберите период", choices=["Неделя", "Месяц", "За все время"])  # type: ignore
    ):
        if not ctx.user.guild_permissions.administrator:
            user_roles = [role.id for role in ctx.user.roles]
            if not any(role_id in ADMIN_ROLES for role_id in user_roles):
                await ctx.respond("❌ У вас недостаточно прав!", ephemeral=True)
                return

        await ctx.defer()

        time_filter = ""
        params = []

        now = datetime.utcnow()
        if period == "Неделя":
            time_limit = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
            time_filter = "AND accepted_at >= ?"
            params.append(time_limit)
        elif period == "Месяц":
            time_limit = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
            time_filter = "AND accepted_at >= ?"
            params.append(time_limit)

        async with aiosqlite.connect(STATS_DB) as db:
            query = f'''
                SELECT recruiter_id, user_id, accepted_at
                FROM recruiter_stats
                WHERE recruiter_id IS NOT NULL {time_filter}
                ORDER BY accepted_at DESC
            '''
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                
        async with aiosqlite.connect(APPLICATIONS_DB) as db:
            query_rej = f'''
                SELECT recruiter_id, status
                FROM applications 
                WHERE status = "rejected" AND recruiter_id IS NOT NULL {time_filter}
            '''
            async with db.execute(query_rej, params) as cursor:
                rejected_rows = await cursor.fetchall()
                
        rejected_data = {}
        for rec_id, status in rejected_rows:
            if rec_id not in rejected_data:
                rejected_data[rec_id] = {'count': 0}
            
            rejected_data[rec_id]['count'] += 1

        if not rows and not rejected_rows:
            embed = discord.Embed(
                description=f"📊 Нет данных за период **{period}**",
                color=EMBED_COLOR
            )
            await ctx.followup.send(embed=embed)
            return

        detailed_data = {}
        for row in rows:
            rec_id = row[0]
            if rec_id not in detailed_data:
                detailed_data[rec_id] = []
            detailed_data[rec_id].append((row[1], row[2]))

        sorted_recruiters = sorted(detailed_data.items(), key=lambda item: len(item[1]), reverse=True)[:25]
        
        for rec_id in rejected_data:
            if rec_id not in detailed_data:
                detailed_data[rec_id] = []
                sorted_recruiters.append((rec_id, []))
                
        sorted_recruiters = sorted(sorted_recruiters, key=lambda item: len(item[1]), reverse=True)[:25]

        embed = discord.Embed(
            color=EMBED_COLOR
        )
        embed.set_author(
            name=f"Статистика рекрутеров · {period}",
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None
        )

        total_all = sum(len(d) for d in detailed_data.values())
        total_active = 0
        for _, data in detailed_data.items():
            for r in data:
                t = ctx.guild.get_member(r[0])
                if t and REMOVE_ROLE_ID not in [role.id for role in t.roles]:
                    total_active += 1

        embed.description = (
            f"```\n"
            f"  Всего принято    {total_all}\n"
            f"  В активе сейчас  {total_active}\n"
            f"  Ушли / Уволены   {total_all - total_active}\n"
            f"  Рекрутеров       {len(detailed_data)}\n"
            f"```\n"
            f"Выберите рекрутера в меню ниже для детального просмотра."
        )

        medal_map = {1: "🥇", 2: "🥈", 3: "🥉"}
        for place, (rec_id, data) in enumerate(sorted_recruiters, 1):
            member = ctx.guild.get_member(rec_id)
            name = member.display_name if member else f"ID {rec_id}"

            total_accepted = len(data)
            active = 0
            for r in data:
                t = ctx.guild.get_member(r[0])
                if t and REMOVE_ROLE_ID not in [role.id for role in t.roles]:
                    active += 1

            rej_info = rejected_data.get(rec_id, {'count': 0})
            rejected_count = rej_info['count']

            inactive = total_accepted - active
            salary_active = active * 20000
            salary_inactive = inactive * 5000
            salary_rejected = rejected_count * 1000
            total_salary = salary_active + salary_inactive + salary_rejected

            medal = medal_map.get(place, f"`#{place}`")

            field_value = (
                f"> 👥 **{total_accepted}** принято\n"
                f"> ✅ **{active}** в активе  ·  ⛔ **{inactive}** ушли\n"
                f"> 💸 **ЗП:** `{total_salary:,}$`"
            ).replace(',', ' ')

            embed.add_field(
                name=f"{medal} {name}",
                value=field_value,
                inline=True
            )

        remainder = len(sorted_recruiters) % 3
        if remainder == 1:
            embed.add_field(name="\u200b", value="\u200b", inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
        elif remainder == 2:
            embed.add_field(name="\u200b", value="\u200b", inline=True)

        embed.set_footer(
            text=f"Общая статистика",
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None
        )

        view = RecruiterStatsMenu(ctx, period, embed, detailed_data, rejected_data)
        await ctx.followup.send(embed=embed, view=view)

    @discord.slash_command(name="salary", description="Калькулятор зарплаты рекрутеров")
    async def calculate_salary(
        self,
        ctx: discord.ApplicationContext,
        period: discord.Option(str, "Выберите период", choices=["Неделя", "Месяц"])  # type: ignore
    ):
        if not ctx.user.guild_permissions.administrator:
            user_roles = [role.id for role in ctx.user.roles]
            if not any(role_id in ADMIN_ROLES for role_id in user_roles):
                await ctx.respond("❌ У вас недостаточно прав!", ephemeral=True)
                return

        await ctx.defer()

        now = datetime.utcnow()
        if period == "Неделя":
            time_limit = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        elif period == "Месяц":
            time_limit = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")

        salary_data = {}

        def get_or_create(rec_id):
            if rec_id not in salary_data:
                salary_data[rec_id] = {'active': 0, 'inactive': 0, 'rejected': 0, 'total': 0}
            return salary_data[rec_id]

        async with aiosqlite.connect(STATS_DB) as db:
            async with db.execute('''
                SELECT recruiter_id, user_id
                FROM recruiter_stats 
                WHERE recruiter_id IS NOT NULL AND accepted_at >= ?
            ''', (time_limit,)) as cursor:
                accepted_rows = await cursor.fetchall()

        for rec_id, user_id in accepted_rows:
            data = get_or_create(rec_id)
            
            member = ctx.guild.get_member(user_id)
            if member is not None and REMOVE_ROLE_ID not in [r.id for r in member.roles]:
                data['active'] += 1
                data['total'] += 20000
            else:
                data['inactive'] += 1
                data['total'] += 5000

        async with aiosqlite.connect(APPLICATIONS_DB) as db:
            async with db.execute('''
                SELECT recruiter_id, status
                FROM applications 
                WHERE status = "rejected" AND accepted_at >= ?
            ''', (time_limit,)) as cursor:
                rejected_rows = await cursor.fetchall()

        for rec_id, status in rejected_rows:
            if not rec_id: 
                continue
            data = get_or_create(rec_id)
            
            data['rejected'] += 1
            data['total'] += 1000

        if not salary_data:
            embed = discord.Embed(
                description=f"📊 Нет данных для расчета за период **{period}**",
                color=EMBED_COLOR
            )
            await ctx.followup.send(embed=embed)
            return

        sorted_salaries = sorted(salary_data.items(), key=lambda item: item[1]['total'], reverse=True)[:25]

        embed = discord.Embed(
            title=f"💸 Калькулятор ЗП · {period}",
            color=EMBED_COLOR
        )
        embed.set_footer(text="Зарплаты рекрутеров", icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

        for place, (rec_id, stats) in enumerate(sorted_salaries, 1):
            member = ctx.guild.get_member(rec_id)
            name = member.display_name if member else f"ID {rec_id}"
            
            val = (
                f"> 💰 **Итого к выплате:** `{stats['total']:,}$`\n"
                f"> ✅ Активных: **{stats['active']}** (`{stats['active']*20000:,}$`)\n"
                f"> ⛔ Неактивных/Ливнули: **{stats['inactive']}** (`{stats['inactive']*5000:,}$`)\n"
                f"> ❌ Отказов: **{stats['rejected']}** (`{stats['rejected']*1000:,}$`)"
            ).replace(',', ' ')
            
            embed.add_field(name=f"`#{place}` {name}", value=val, inline=False)

        await ctx.followup.send(embed=embed)

    # ---- INTERACTION LISTENER ----

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id")
        if not custom_id:
            return

        if custom_id == "submit_application":
            modal = ApplicationModal()
            await interaction.response.send_modal(modal)

        elif custom_id.startswith("application:"):
            if not interaction.user.guild_permissions.administrator:
                user_roles = [role.id for role in interaction.user.roles]
                if not any(role_id in ADMIN_ROLES for role_id in user_roles):
                    await interaction.response.send_message("❌ У вас недостаточно прав!", ephemeral=True)
                    return

            action = custom_id.split(":")[1]
            message_id = interaction.message.id

            try:
                if action == "accept":
                    modal = AcceptConfirmationModal(message_id)
                    await interaction.response.send_modal(modal)
                elif action == "reject":
                    await self.handle_reject(interaction, message_id)
                elif action == "call":
                    await self.handle_call(interaction, message_id)
                elif action == "close":
                    modal = CloseConfirmationModal(message_id)
                    await interaction.response.send_modal(modal)
            except Exception as e:
                print(f"Error handling application button: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Произошла ошибка!", ephemeral=True)

    # ---- HANDLERS ----

    async def handle_accept(self, interaction: discord.Interaction, message_id: int):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        try:
            async with aiosqlite.connect(APPLICATIONS_DB) as db:
                async with db.execute('''
                    SELECT ticket_number, thread_id, user_id, new_nickname, application_type
                    FROM applications 
                    WHERE message_id = ? AND status = "pending"
                ''', (message_id,)) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        await interaction.followup.send("Заявка не найдена или уже обработана.", ephemeral=True)
                        return

            ticket_number, thread_id, user_id, new_nickname, application_type = row

            try:
                user = interaction.guild.get_member(user_id) or await interaction.guild.fetch_member(user_id)
            except discord.HTTPException:
                await interaction.followup.send("Не удалось найти пользователя на сервере.", ephemeral=True)
                return

            guild = interaction.guild
            thread = None
            try:
                thread = guild.get_channel(thread_id) or await guild.fetch_channel(thread_id)
            except discord.HTTPException:
                pass

            roles_to_add_ids = list(ACCEPTED_ROLE_ID)

            roles_to_add = [guild.get_role(rid) for rid in roles_to_add_ids if guild.get_role(rid)]
            if roles_to_add:
                try:
                    await user.add_roles(*roles_to_add)
                except discord.Forbidden:
                    await interaction.followup.send("⚠️ Не удалось выдать роли (нет прав).", ephemeral=True)

            remove_role = guild.get_role(REMOVE_ROLE_ID)
            if remove_role and remove_role in user.roles:
                try:
                    await user.remove_roles(remove_role)
                except discord.Forbidden:
                    pass

            if new_nickname and application_type == "family":
                try:
                    await user.edit(nick=new_nickname)
                    try:
                        await user.send(f"Ваш никнейм изменен на: {new_nickname}")
                    except discord.Forbidden:
                        pass
                except discord.Forbidden:
                    pass

            try:
                await user.send("Поздравляем! Ваша заявка принята!")
            except discord.Forbidden:
                pass

            if thread:
                try:
                    await thread.send(f"{user.mention} принят в семью. Решение принял: {interaction.user.mention}")
                    await thread.remove_user(user)
                except discord.HTTPException:
                    pass

            acceptance_channel = guild.get_channel(ACCEPTANCE_CHANNEL_ID)
            if acceptance_channel:
                try:
                    report_message = f"{interaction.user.mention} принял {user.mention} в семью."
                    if thread:
                        report_message += f" [Ветка заявки]({thread.jump_url})"
                    await acceptance_channel.send(report_message)
                except discord.HTTPException:
                    pass

            now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

            async with aiosqlite.connect(APPLICATIONS_DB) as db:
                await db.execute('''
                    UPDATE applications 
                    SET status = "accepted", recruiter_id = ?, accepted_at = ?
                    WHERE ticket_number = ?
                ''', (interaction.user.id, now_str, ticket_number))
                await db.commit()

            async with aiosqlite.connect(STATS_DB) as db:
                await db.execute('''
                    INSERT INTO recruiter_stats (ticket_number, recruiter_id, user_id, accepted_at)
                    VALUES (?, ?, ?, ?)
                ''', (ticket_number, interaction.user.id, user_id, now_str))
                await db.commit()

            await interaction.followup.send("✅ Заявка обработана!", ephemeral=True)

        except Exception as e:
            print(f"Error in handle_accept: {e}")
            await interaction.followup.send("❌ Произошла ошибка!", ephemeral=True)

    async def handle_reject(self, interaction: discord.Interaction, message_id: int):
        modal = RejectModal(message_id)
        await interaction.response.send_modal(modal)

    async def handle_call(self, interaction: discord.Interaction, message_id: int):
        await interaction.response.defer(ephemeral=True)

        try:
            async with aiosqlite.connect(APPLICATIONS_DB) as db:
                async with db.execute('''
                    SELECT thread_id, user_id 
                    FROM applications 
                    WHERE message_id = ? AND status = "pending"
                ''', (message_id,)) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        await interaction.followup.send("Заявка не найдена или уже обработана.", ephemeral=True)
                        return

            thread_id, user_id = row

            try:
                user = interaction.guild.get_member(user_id) or await interaction.guild.fetch_member(user_id)
            except discord.HTTPException:
                await interaction.followup.send("Не удалось найти пользователя.", ephemeral=True)
                return

            thread = None
            try:
                thread = interaction.guild.get_channel(thread_id) or await interaction.guild.fetch_channel(thread_id)
            except discord.HTTPException:
                pass

            try:
                await user.send("Вас вызывают на обзвон по вашей заявке.")
            except discord.Forbidden:
                pass

            if thread:
                try:
                    await thread.send(f"{user.mention}, вас вызывают на обзвон! Решение принял: {interaction.user.mention}")
                except discord.HTTPException:
                    pass

            await interaction.followup.send("✅ Уведомление отправлено!", ephemeral=True)

        except Exception as e:
            print(f"Error in handle_call: {e}")
            await interaction.followup.send("❌ Ошибка!", ephemeral=True)

    async def handle_close(self, interaction: discord.Interaction, message_id: int):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        try:
            async with aiosqlite.connect(APPLICATIONS_DB) as db:
                async with db.execute('''
                    SELECT thread_id, ticket_number 
                    FROM applications 
                    WHERE message_id = ?
                ''', (message_id,)) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        await interaction.followup.send("Заявка не найдена.", ephemeral=True)
                        return

                thread_id, ticket_number = row

                thread = None
                try:
                    thread = interaction.guild.get_channel(thread_id) or await interaction.guild.fetch_channel(thread_id)
                except Exception:
                    pass

                async with aiosqlite.connect(APPLICATIONS_DB) as db2:
                    await db2.execute('UPDATE applications SET status = "closed" WHERE ticket_number = ?', (ticket_number,))
                    await db2.commit()

                if thread:
                    try:
                        await thread.edit(archived=True)
                    except Exception:
                        pass

        except Exception as e:
            print(f"Error in handle_close: {e}")
            await interaction.followup.send("❌ Ошибка при закрытии!", ephemeral=True)


def setup(bot: discord.Bot):
    bot.add_cog(FamilyApplicationCog(bot))