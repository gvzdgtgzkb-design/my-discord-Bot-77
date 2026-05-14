"""
Admin cog — all staff-only slash commands.
"""
import discord
from discord import app_commands
from discord.ext import commands

import database as db
from config import (
    OWNER_ROLE_IDS,
    COLOR_PRIMARY, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING,
    EMOJI_ADMIN, EMOJI_PRODUCT, EMOJI_KEY, EMOJI_TRASH,
    EMOJI_PENCIL, EMOJI_TAG, EMOJI_CLIPBOARD, EMOJI_GIFT,
    EMOJI_CROSS, EMOJI_CONFIRM, EMOJI_WARNING,
)


# ── permission check ──────────────────────────────────────────────────────────

async def _has_admin(member: discord.Member) -> bool:
    user_role_ids = {str(r.id) for r in member.roles}
    for rid in OWNER_ROLE_IDS:
        if rid in user_role_ids:
            return True
    settings = await db.get_settings()
    if settings and settings.get("admin_role_ids"):
        admin_ids = {r.strip() for r in settings["admin_role_ids"].split(",")}
        if user_role_ids & admin_ids:
            return True
    return False


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("Este comando só pode ser usado em servidores.")
        if not await _has_admin(interaction.user):
            raise app_commands.CheckFailure(
                f"{EMOJI_CROSS} Você não tem permissão para usar este comando."
            )
        return True
    return app_commands.check(predicate)


# ── helper ────────────────────────────────────────────────────────────────────

def _footer(settings: dict | None) -> str:
    if settings:
        return settings.get("footer_text") or "NeverMiss Apps © 2026"
    return "NeverMiss Apps © 2026"


def _color(settings: dict | None) -> int:
    if settings:
        return settings.get("embed_color") or COLOR_PRIMARY
    return COLOR_PRIMARY


# ── cog ───────────────────────────────────────────────────────────────────────

class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── error handler ────────────────────────────────────────────────────────

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        msg = str(error)
        if isinstance(error, app_commands.CheckFailure):
            msg = str(error)
        else:
            msg = f"{EMOJI_CROSS} Erro: {error}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    # ── /post ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="post", description="Posta o catálogo de produtos no canal da loja")
    @app_commands.describe(channel="Canal onde a loja será postada (opcional)")
    @admin_only()
    async def post(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        await interaction.response.defer(ephemeral=True)

        target = channel or interaction.channel
        products = await db.get_products(active_only=True)
        settings = await db.get_settings()

        if not products:
            await interaction.followup.send(
                f"{EMOJI_WARNING} Nenhum produto ativo encontrado. Adicione produtos primeiro com `/addproduct`.",
                ephemeral=True
            )
            return

        bot_name = (settings or {}).get("bot_name", "Shop Bot")
        footer   = _footer(settings)
        color    = _color(settings)

        embed = discord.Embed(
            title=f"🛒 {bot_name}",
            description="Selecione um produto abaixo para iniciar sua compra.",
            color=color,
        )
        for p in products:
            stock = p["available_keys"]
            stock_label = f"{stock} disponível(is)" if stock > 0 else "⚠️ Sem estoque"
            embed.add_field(
                name=f"{p['name']}",
                value=f"**{p['price_label']}** — {stock_label}\n{p['description'] or ''}",
                inline=False,
            )
        embed.set_footer(text=footer)

        from cogs.shop import ShopView
        view = ShopView(products)
        await target.send(embed=embed, view=view)

        await interaction.followup.send(
            f"{EMOJI_CONFIRM} Loja postada em {target.mention}.", ephemeral=True
        )

    # ── /addproduct ───────────────────────────────────────────────────────────

    @app_commands.command(name="addproduct", description="Adiciona um novo produto à loja")
    @app_commands.describe(
        name="Nome do produto",
        price="Preço numérico (ex: 29.90)",
        price_label="Rótulo de preço exibido (ex: R$ 29,90)",
        description="Descrição do produto",
        image_url="URL da imagem (opcional)",
        pix_key="Chave Pix específica (opcional — usa a global se omitida)",
        stock_type="Tipo de estoque: keys ou manual",
    )
    @admin_only()
    async def addproduct(
        self,
        interaction: discord.Interaction,
        name: str,
        price: float,
        price_label: str,
        description: str = "",
        image_url: str | None = None,
        pix_key: str | None = None,
        stock_type: str = "keys",
    ):
        await interaction.response.defer(ephemeral=True)
        p = await db.create_product(name, description, price, price_label, image_url, pix_key, stock_type)
        embed = discord.Embed(
            title=f"{EMOJI_CONFIRM} Produto adicionado",
            color=COLOR_SUCCESS,
        )
        embed.add_field(name="ID",    value=str(p["id"]),    inline=True)
        embed.add_field(name="Nome",  value=p["name"],       inline=True)
        embed.add_field(name="Preço", value=p["price_label"], inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /editproduct ──────────────────────────────────────────────────────────

    @app_commands.command(name="editproduct", description="Edita um produto existente")
    @app_commands.describe(
        product_id="ID do produto",
        name="Novo nome (opcional)",
        price="Novo preço numérico (opcional)",
        price_label="Novo rótulo de preço (opcional)",
        description="Nova descrição (opcional)",
        image_url="Nova URL de imagem (opcional)",
        pix_key="Nova chave Pix (opcional)",
    )
    @admin_only()
    async def editproduct(
        self,
        interaction: discord.Interaction,
        product_id: int,
        name: str | None = None,
        price: float | None = None,
        price_label: str | None = None,
        description: str | None = None,
        image_url: str | None = None,
        pix_key: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        updates = {k: v for k, v in {
            "name": name, "price": price, "price_label": price_label,
            "description": description, "image_url": image_url, "pix_key": pix_key,
        }.items() if v is not None}

        if not updates:
            await interaction.followup.send(f"{EMOJI_WARNING} Nenhum campo fornecido para edição.", ephemeral=True)
            return

        p = await db.update_product(product_id, **updates)
        if not p:
            await interaction.followup.send(f"{EMOJI_CROSS} Produto #{product_id} não encontrado.", ephemeral=True)
            return

        embed = discord.Embed(title=f"{EMOJI_PENCIL} Produto editado", color=COLOR_SUCCESS)
        embed.add_field(name="ID",    value=str(p["id"]),    inline=True)
        embed.add_field(name="Nome",  value=p["name"],       inline=True)
        embed.add_field(name="Preço", value=p["price_label"], inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /removeproduct ────────────────────────────────────────────────────────

    @app_commands.command(name="removeproduct", description="Remove um produto da loja")
    @app_commands.describe(product_id="ID do produto a remover")
    @admin_only()
    async def removeproduct(self, interaction: discord.Interaction, product_id: int):
        await interaction.response.defer(ephemeral=True)
        p = await db.delete_product(product_id)
        if not p:
            await interaction.followup.send(f"{EMOJI_CROSS} Produto #{product_id} não encontrado.", ephemeral=True)
            return
        await interaction.followup.send(
            f"{EMOJI_TRASH} Produto **{p['name']}** (ID #{product_id}) removido.", ephemeral=True
        )

    # ── /toggleproduct ────────────────────────────────────────────────────────

    @app_commands.command(name="toggleproduct", description="Ativa ou desativa um produto")
    @app_commands.describe(product_id="ID do produto")
    @admin_only()
    async def toggleproduct(self, interaction: discord.Interaction, product_id: int):
        await interaction.response.defer(ephemeral=True)
        p = await db.get_product(product_id)
        if not p:
            await interaction.followup.send(f"{EMOJI_CROSS} Produto #{product_id} não encontrado.", ephemeral=True)
            return
        new_state = 0 if p["active"] else 1
        await db.update_product(product_id, active=new_state)
        label = "ativado ✅" if new_state else "desativado ⛔"
        await interaction.followup.send(
            f"Produto **{p['name']}** foi **{label}**.", ephemeral=True
        )

    # ── /listproducts ─────────────────────────────────────────────────────────

    @app_commands.command(name="listproducts", description="Lista todos os produtos")
    @admin_only()
    async def listproducts(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        products = await db.get_products(active_only=False)
        if not products:
            await interaction.followup.send("Nenhum produto cadastrado.", ephemeral=True)
            return

        embed = discord.Embed(title=f"{EMOJI_PRODUCT} Produtos", color=COLOR_PRIMARY)
        for p in products:
            status = "✅" if p["active"] else "⛔"
            embed.add_field(
                name=f"{status} [{p['id']}] {p['name']}",
                value=f"{p['price_label']} | {p['available_keys']}/{p['total_keys']} keys | tipo: `{p['stock_type']}`",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /addkey ───────────────────────────────────────────────────────────────

    @app_commands.command(name="addkey", description="Adiciona uma chave a um produto")
    @app_commands.describe(product_id="ID do produto", key="Valor da chave/licença")
    @admin_only()
    async def addkey(self, interaction: discord.Interaction, product_id: int, key: str):
        await interaction.response.defer(ephemeral=True)
        result = await db.add_key(product_id, key)
        if result:
            await interaction.followup.send(
                f"{EMOJI_KEY} Chave adicionada ao produto #{product_id}.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"{EMOJI_CROSS} Chave duplicada ou produto não encontrado.", ephemeral=True
            )

    # ── /bulkaddkeys ──────────────────────────────────────────────────────────

    @app_commands.command(name="bulkaddkeys", description="Adiciona várias chaves de uma vez (separadas por vírgula)")
    @app_commands.describe(product_id="ID do produto", keys="Chaves separadas por vírgula")
    @admin_only()
    async def bulkaddkeys(self, interaction: discord.Interaction, product_id: int, keys: str):
        await interaction.response.defer(ephemeral=True)
        key_list = [k.strip() for k in keys.split(",") if k.strip()]
        if not key_list:
            await interaction.followup.send(f"{EMOJI_CROSS} Nenhuma chave válida fornecida.", ephemeral=True)
            return
        added, skipped = await db.bulk_add_keys(product_id, key_list)
        await interaction.followup.send(
            f"{EMOJI_KEY} **{added}** chaves adicionadas, **{skipped}** duplicadas/ignoradas.", ephemeral=True
        )

    # ── /removekey ────────────────────────────────────────────────────────────

    @app_commands.command(name="removekey", description="Remove uma chave pelo ID")
    @app_commands.describe(key_id="ID da chave")
    @admin_only()
    async def removekey(self, interaction: discord.Interaction, key_id: int):
        await interaction.response.defer(ephemeral=True)
        k = await db.delete_key(key_id)
        if k:
            await interaction.followup.send(
                f"{EMOJI_TRASH} Chave #{key_id} removida.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"{EMOJI_CROSS} Chave #{key_id} não encontrada.", ephemeral=True
            )

    # ── /listkeys ─────────────────────────────────────────────────────────────

    @app_commands.command(name="listkeys", description="Lista as chaves de um produto")
    @app_commands.describe(product_id="ID do produto", status="Filtrar por status: available, used ou todos")
    @admin_only()
    async def listkeys(
        self, interaction: discord.Interaction, product_id: int, status: str | None = None
    ):
        await interaction.response.defer(ephemeral=True)
        keys = await db.get_keys(product_id=product_id, status=status or None)
        if not keys:
            await interaction.followup.send("Nenhuma chave encontrada.", ephemeral=True)
            return

        lines = []
        for k in keys[:30]:
            icon = "✅" if k["status"] == "available" else "🔴"
            lines.append(f"{icon} `[{k['id']}]` `{k['key_value']}` — {k['status']}")

        embed = discord.Embed(
            title=f"{EMOJI_KEY} Chaves — Produto #{product_id}",
            description="\n".join(lines),
            color=COLOR_PRIMARY,
        )
        if len(keys) > 30:
            embed.set_footer(text=f"Mostrando 30 de {len(keys)} chaves.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /addcoupon ────────────────────────────────────────────────────────────

    @app_commands.command(name="addcoupon", description="Cria um cupom de desconto")
    @app_commands.describe(
        code="Código do cupom",
        discount_type="percent ou fixed",
        discount_value="Valor do desconto",
        uses="Número de usos (-1 = ilimitado)",
    )
    @admin_only()
    async def addcoupon(
        self,
        interaction: discord.Interaction,
        code: str,
        discount_type: str,
        discount_value: float,
        uses: int = -1,
    ):
        await interaction.response.defer(ephemeral=True)
        if discount_type not in ("percent", "fixed"):
            await interaction.followup.send(
                f"{EMOJI_CROSS} Tipo inválido. Use `percent` ou `fixed`.", ephemeral=True
            )
            return
        c = await db.create_coupon(code.upper(), discount_type, discount_value, uses)
        if not c:
            await interaction.followup.send(f"{EMOJI_CROSS} Erro ao criar cupom (código já existe?).", ephemeral=True)
            return
        embed = discord.Embed(title=f"{EMOJI_TAG} Cupom criado", color=COLOR_SUCCESS)
        embed.add_field(name="Código",    value=c["code"],                        inline=True)
        embed.add_field(name="Tipo",      value=c["discount_type"],               inline=True)
        embed.add_field(name="Desconto",  value=str(c["discount_value"]),         inline=True)
        embed.add_field(name="Usos",      value="Ilimitado" if uses == -1 else str(uses), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /removecoupon ─────────────────────────────────────────────────────────

    @app_commands.command(name="removecoupon", description="Remove um cupom")
    @app_commands.describe(code="Código do cupom a remover")
    @admin_only()
    async def removecoupon(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        await db.delete_coupon(code.upper())
        await interaction.followup.send(
            f"{EMOJI_TRASH} Cupom `{code.upper()}` removido (se existia).", ephemeral=True
        )

    # ── /confirmorder ─────────────────────────────────────────────────────────

    @app_commands.command(name="confirmorder", description="Confirma pagamento e entrega a chave ao comprador")
    @app_commands.describe(order_id="ID do pedido")
    @admin_only()
    async def confirmorder(self, interaction: discord.Interaction, order_id: int):
        await interaction.response.defer(ephemeral=True)
        order = await db.get_order(order_id)
        if not order:
            await interaction.followup.send(f"{EMOJI_CROSS} Pedido #{order_id} não encontrado.", ephemeral=True)
            return
        if order["status"] == "completed":
            await interaction.followup.send(f"{EMOJI_WARNING} Pedido #{order_id} já foi confirmado.", ephemeral=True)
            return
        if order["status"] == "cancelled":
            await interaction.followup.send(f"{EMOJI_CROSS} Pedido #{order_id} foi cancelado.", ephemeral=True)
            return

        key = await db.pop_available_key(order["product_id"])
        if not key:
            await interaction.followup.send(
                f"{EMOJI_CROSS} Sem chaves disponíveis para **{order['product_name']}**.", ephemeral=True
            )
            return

        await db.update_order(order_id, status="completed")
        await db.log_activity("order_complete", f"Order #{order_id} completed by admin")

        # DM the key to the buyer
        try:
            user = await interaction.client.fetch_user(int(order["user_id"]))
            dm_embed = discord.Embed(
                title=f"{EMOJI_GIFT} Seu produto chegou!",
                description=f"Obrigado pela compra de **{order['product_name']}**!",
                color=COLOR_SUCCESS,
            )
            dm_embed.add_field(name=f"{EMOJI_KEY} Sua chave", value=f"```{key['key_value']}```", inline=False)
            dm_embed.set_footer(text="NeverMiss Apps © 2026")
            await user.send(embed=dm_embed)
            dm_status = f"✅ Chave enviada por DM para <@{order['user_id']}>."
        except Exception as e:
            dm_status = f"⚠️ Não foi possível enviar DM: {e}"

        # Notify in the order thread if it exists
        if order.get("thread_id"):
            try:
                thread = interaction.guild.get_thread(int(order["thread_id"]))
                if thread:
                    notify_embed = discord.Embed(
                        title=f"{EMOJI_CONFIRM} Pagamento confirmado!",
                        description="Sua chave foi enviada por mensagem privada (DM). Verifique sua DM.",
                        color=COLOR_SUCCESS,
                    )
                    await thread.send(embed=notify_embed)
            except Exception:
                pass

        embed = discord.Embed(title=f"{EMOJI_CONFIRM} Pedido #{order_id} confirmado", color=COLOR_SUCCESS)
        embed.add_field(name="Produto",  value=order["product_name"], inline=True)
        embed.add_field(name="Comprador", value=f"<@{order['user_id']}>", inline=True)
        embed.add_field(name="Status DM", value=dm_status, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /cancelorder ─────────────────────────────────────────────────────────

    @app_commands.command(name="cancelorder", description="Cancela um pedido")
    @app_commands.describe(order_id="ID do pedido")
    @admin_only()
    async def cancelorder(self, interaction: discord.Interaction, order_id: int):
        await interaction.response.defer(ephemeral=True)
        order = await db.get_order(order_id)
        if not order:
            await interaction.followup.send(f"{EMOJI_CROSS} Pedido #{order_id} não encontrado.", ephemeral=True)
            return
        await db.cancel_order(order_id)
        # Notify thread
        if order.get("thread_id"):
            try:
                thread = interaction.guild.get_thread(int(order["thread_id"]))
                if thread:
                    await thread.send(
                        embed=discord.Embed(
                            description=f"{EMOJI_CROSS} Este pedido foi cancelado por um administrador.",
                            color=COLOR_ERROR,
                        )
                    )
            except Exception:
                pass
        await interaction.followup.send(
            f"{EMOJI_TRASH} Pedido #{order_id} cancelado.", ephemeral=True
        )

    # ── /stats ────────────────────────────────────────────────────────────────

    @app_commands.command(name="stats", description="Exibe estatísticas da loja")
    @admin_only()
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        s = await db.get_stats()
        activity = await db.get_recent_activity(5)

        embed = discord.Embed(title=f"{EMOJI_CLIPBOARD} Estatísticas da Loja", color=COLOR_PRIMARY)
        embed.add_field(name="🛍️ Produtos",    value=f"{s['active_products']} ativos / {s['total_products']} total", inline=True)
        embed.add_field(name="🔑 Chaves",       value=f"{s['available_keys']} disponíveis / {s['total_keys']} total", inline=True)
        embed.add_field(name="📦 Pedidos",      value=f"{s['completed_orders']} concluídos / {s['total_orders']} total", inline=True)
        embed.add_field(name="⏳ Pendentes",    value=str(s["pending_orders"]), inline=True)
        embed.add_field(name="🔴 Usadas",       value=str(s["used_keys"]), inline=True)

        if activity:
            lines = [f"`{a['type']}` — {a['message']}" for a in activity]
            embed.add_field(name="📋 Atividade recente", value="\n".join(lines), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /settings ─────────────────────────────────────────────────────────────

    @app_commands.command(name="settings", description="Ver ou atualizar configurações do bot")
    @app_commands.describe(
        shop_channel_id="ID do canal da loja",
        admin_role_ids="IDs de papéis admin separados por vírgula",
        log_channel_id="ID do canal de logs",
        global_pix_key="Chave Pix global",
        footer_text="Texto do rodapé dos embeds",
        bot_name="Nome exibido do bot",
    )
    @admin_only()
    async def settings(
        self,
        interaction: discord.Interaction,
        shop_channel_id: str | None = None,
        admin_role_ids: str | None = None,
        log_channel_id: str | None = None,
        global_pix_key: str | None = None,
        footer_text: str | None = None,
        bot_name: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        updates = {k: v for k, v in {
            "shop_channel_id": shop_channel_id,
            "admin_role_ids": admin_role_ids,
            "log_channel_id": log_channel_id,
            "global_pix_key": global_pix_key,
            "footer_text": footer_text,
            "bot_name": bot_name,
        }.items() if v is not None}

        if updates:
            await db.update_settings(**updates)

        s = await db.get_settings()
        embed = discord.Embed(title=f"{EMOJI_ADMIN} Configurações", color=COLOR_PRIMARY)
        embed.add_field(name="Bot name",        value=s.get("bot_name") or "—", inline=True)
        embed.add_field(name="Shop channel",    value=s.get("shop_channel_id") or "—", inline=True)
        embed.add_field(name="Log channel",     value=s.get("log_channel_id") or "—", inline=True)
        embed.add_field(name="Admin role IDs",  value=s.get("admin_role_ids") or "—", inline=False)
        embed.add_field(name="Global Pix key",  value=s.get("global_pix_key") or "—", inline=False)
        embed.add_field(name="Footer text",     value=s.get("footer_text") or "—", inline=False)
        if updates:
            embed.set_footer(text="✅ Configurações atualizadas")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
