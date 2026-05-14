"""
Shop cog — product listing, cart, and payment flow.
"""
import discord
from discord import app_commands
from discord.ext import commands

import database as db
from config import (
    COLOR_PRIMARY, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING,
    EMOJI_CART, EMOJI_CONFIRM, EMOJI_CROSS, EMOJI_PRODUCT,
    EMOJI_PENCIL, EMOJI_TAG, EMOJI_TRASH, EMOJI_PAYMENT,
    EMOJI_QR, EMOJI_KEY, EMOJI_WARNING, EMOJI_GIFT,
)


# ── helpers ───────────────────────────────────────────────────────────────────

async def _settings():
    return await db.get_settings() or {}


def _effective_pix(order: dict, settings: dict) -> str | None:
    return order.get("pix_key") or settings.get("global_pix_key")


def _calc_total(unit_price: float, quantity: int, coupon: dict | None) -> tuple[float, float]:
    """Return (total, discount_amount)."""
    subtotal = unit_price * quantity
    if not coupon:
        return subtotal, 0.0
    if coupon["discount_type"] == "percent":
        discount = subtotal * coupon["discount_value"] / 100
    else:
        discount = min(coupon["discount_value"], subtotal)
    return max(subtotal - discount, 0.0), discount


def _cart_embed(order: dict, product: dict, coupon: dict | None, settings: dict) -> discord.Embed:
    total, discount = _calc_total(order["unit_price"], order["quantity"], coupon)
    footer = settings.get("footer_text") or "NeverMiss Apps © 2026"
    color  = settings.get("embed_color") or COLOR_PRIMARY

    embed = discord.Embed(
        title=f"{EMOJI_CART} Seu Carrinho",
        color=color,
    )
    embed.add_field(name="Produto",    value=product["name"],          inline=True)
    embed.add_field(name="Quantidade", value=str(order["quantity"]),   inline=True)
    embed.add_field(name="Preço unit.", value=product["price_label"],  inline=True)

    if coupon:
        embed.add_field(name=f"{EMOJI_TAG} Cupom",    value=f"`{coupon['code']}`", inline=True)
        embed.add_field(name="Desconto",               value=f"-R$ {discount:.2f}", inline=True)

    embed.add_field(name="💰 Total", value=f"**R$ {total:.2f}**", inline=False)
    if product.get("image_url"):
        embed.set_thumbnail(url=product["image_url"])
    embed.set_footer(text=footer)
    return embed


def _payment_embed(order: dict, product: dict, coupon: dict | None, settings: dict) -> discord.Embed:
    total, _ = _calc_total(order["unit_price"], order["quantity"], coupon)
    pix = _effective_pix({"pix_key": product.get("pix_key")}, settings)
    footer = settings.get("footer_text") or "NeverMiss Apps © 2026"

    embed = discord.Embed(
        title=f"{EMOJI_PAYMENT} Pagamento via Pix",
        description=(
            f"Produto: **{product['name']}**\n"
            f"Quantidade: **{order['quantity']}**\n"
            f"**Total: R$ {total:.2f}**"
        ),
        color=COLOR_WARNING,
    )
    if pix:
        embed.add_field(
            name=f"{EMOJI_QR} Chave Pix",
            value=f"```{pix}```",
            inline=False,
        )
        embed.add_field(
            name="Como pagar",
            value=(
                "1. Abra seu banco e vá em **Pix**\n"
                "2. Cole a chave acima\n"
                "3. Confirme o valor e pague\n"
                "4. Aguarde a confirmação do admin"
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name=f"{EMOJI_WARNING} Atenção",
            value="Nenhuma chave Pix configurada. Contate um administrador.",
            inline=False,
        )
    embed.set_footer(text=footer)
    return embed


# ── Cart view ─────────────────────────────────────────────────────────────────

class ChangeQuantityModal(discord.ui.Modal, title="Alterar Quantidade"):
    quantity = discord.ui.TextInput(
        label="Nova quantidade",
        placeholder="Ex: 2",
        min_length=1,
        max_length=3,
    )

    def __init__(self, order_id: int):
        super().__init__()
        self.order_id = order_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qty = int(self.quantity.value)
            if qty < 1:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Quantidade inválida.", ephemeral=True
            )
            return

        order = await db.get_order(self.order_id)
        if not order:
            await interaction.response.send_message(f"{EMOJI_CROSS} Pedido não encontrado.", ephemeral=True)
            return

        product = await db.get_product(order["product_id"])
        settings = await _settings()

        # Check stock
        if product["available_keys"] < qty:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Estoque insuficiente. Disponível: {product['available_keys']}.",
                ephemeral=True,
            )
            return

        total, discount = _calc_total(order["unit_price"], qty, None)
        await db.update_order(self.order_id, quantity=qty, total_price=total)
        order = await db.get_order(self.order_id)

        embed = _cart_embed(order, product, None, settings)
        view = CartView(self.order_id, order["product_id"])
        await interaction.response.edit_message(embed=embed, view=view)


class AddCouponModal(discord.ui.Modal, title="Aplicar Cupom"):
    code = discord.ui.TextInput(
        label="Código do cupom",
        placeholder="Ex: DESCONTO10",
        min_length=1,
        max_length=50,
    )

    def __init__(self, order_id: int):
        super().__init__()
        self.order_id = order_id

    async def on_submit(self, interaction: discord.Interaction):
        coupon = await db.get_coupon(self.code.value.strip().upper())
        if not coupon:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Cupom inválido ou expirado.", ephemeral=True
            )
            return

        if coupon["uses_left"] == 0:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Este cupom atingiu o limite de usos.", ephemeral=True
            )
            return

        order = await db.get_order(self.order_id)
        if not order:
            await interaction.response.send_message(f"{EMOJI_CROSS} Pedido não encontrado.", ephemeral=True)
            return

        product = await db.get_product(order["product_id"])
        settings = await _settings()

        total, discount = _calc_total(order["unit_price"], order["quantity"], coupon)
        await db.update_order(
            self.order_id,
            coupon_code=coupon["code"],
            discount=discount,
            total_price=total,
        )
        order = await db.get_order(self.order_id)

        embed = _cart_embed(order, product, coupon, settings)
        view = CartView(self.order_id, order["product_id"])
        await interaction.response.edit_message(embed=embed, view=view)


class CartView(discord.ui.View):
    def __init__(self, order_id: int, product_id: int):
        super().__init__(timeout=600)
        self.order_id   = order_id
        self.product_id = product_id

    @discord.ui.button(label="Alterar Quantidade", emoji="✏️", style=discord.ButtonStyle.secondary, row=0)
    async def change_qty(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChangeQuantityModal(self.order_id))

    @discord.ui.button(label="Adicionar Cupom", emoji="🏷️", style=discord.ButtonStyle.secondary, row=0)
    async def add_coupon(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddCouponModal(self.order_id))

    @discord.ui.button(label="Remover Item", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def remove_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.cancel_order(self.order_id)
        embed = discord.Embed(
            description=f"{EMOJI_TRASH} Seu pedido foi removido.",
            color=COLOR_ERROR,
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Ir para Pagamento", emoji="🛒", style=discord.ButtonStyle.success, row=1)
    async def go_to_payment(self, interaction: discord.Interaction, button: discord.ui.Button):
        order   = await db.get_order(self.order_id)
        product = await db.get_product(self.product_id)
        settings = await _settings()

        if not order or not product:
            await interaction.response.send_message(f"{EMOJI_CROSS} Pedido não encontrado.", ephemeral=True)
            return

        coupon = None
        if order.get("coupon_code"):
            coupon = await db.get_coupon(order["coupon_code"])

        await db.update_order(self.order_id, status="awaiting_payment")

        embed = _payment_embed(order, product, coupon, settings)
        view  = PaymentView(self.order_id, self.product_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Cancelar Pedido", emoji="❌", style=discord.ButtonStyle.danger, row=1)
    async def cancel_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.cancel_order(self.order_id)
        embed = discord.Embed(
            description=f"{EMOJI_CROSS} Pedido cancelado.",
            color=COLOR_ERROR,
        )
        await interaction.response.edit_message(embed=embed, view=None)


# ── Payment view ──────────────────────────────────────────────────────────────

class PaymentView(discord.ui.View):
    def __init__(self, order_id: int, product_id: int):
        super().__init__(timeout=3600)
        self.order_id   = order_id
        self.product_id = product_id

    @discord.ui.button(label="Voltar ao Carrinho", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back_to_cart(self, interaction: discord.Interaction, button: discord.ui.Button):
        order   = await db.get_order(self.order_id)
        product = await db.get_product(self.product_id)
        settings = await _settings()

        if not order or not product:
            await interaction.response.send_message(f"{EMOJI_CROSS} Pedido não encontrado.", ephemeral=True)
            return

        await db.update_order(self.order_id, status="pending")

        coupon = None
        if order.get("coupon_code"):
            coupon = await db.get_coupon(order["coupon_code"])

        embed = _cart_embed(order, product, coupon, settings)
        view  = CartView(self.order_id, self.product_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Cancelar Pedido", emoji="❌", style=discord.ButtonStyle.danger)
    async def cancel_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.cancel_order(self.order_id)
        embed = discord.Embed(
            description=f"{EMOJI_CROSS} Pedido cancelado.",
            color=COLOR_ERROR,
        )
        await interaction.response.edit_message(embed=embed, view=None)


# ── Product select ────────────────────────────────────────────────────────────

class ProductSelect(discord.ui.Select):
    def __init__(self, products: list[dict]):
        options = []
        for p in products[:25]:  # Discord limit: 25 options
            stock = p["available_keys"]
            desc  = f"{p['price_label']}"
            if stock > 0:
                desc += f" — {stock} em estoque"
            else:
                desc += " — ⚠️ Sem estoque"
            options.append(
                discord.SelectOption(
                    label=p["name"][:100],
                    description=desc[:100],
                    value=str(p["id"]),
                )
            )
        super().__init__(
            placeholder="Selecione um produto ou categoria",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        product_id = int(self.values[0])
        product    = await db.get_product(product_id)
        settings   = await _settings()

        if not product:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Produto não encontrado.", ephemeral=True
            )
            return

        if not product["active"]:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Este produto não está disponível no momento.", ephemeral=True
            )
            return

        if product["available_keys"] < 1:
            await interaction.response.send_message(
                f"{EMOJI_WARNING} **{product['name']}** está sem estoque no momento. "
                "Tente novamente mais tarde.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Create a private thread for this purchase
        channel = interaction.channel
        thread_name = f"🛒 {interaction.user.display_name} — {product['name']}"[:100]

        try:
            thread = await channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
            )
            await thread.add_user(interaction.user)
        except discord.Forbidden:
            await interaction.followup.send(
                f"{EMOJI_CROSS} Sem permissão para criar threads privadas neste canal.", ephemeral=True
            )
            return
        except Exception as e:
            await interaction.followup.send(
                f"{EMOJI_CROSS} Erro ao criar thread: {e}", ephemeral=True
            )
            return

        # Create the order
        order = await db.create_order(
            user_id=str(interaction.user.id),
            user_name=str(interaction.user),
            product_id=product_id,
            quantity=1,
            unit_price=product["price"],
            price_label=product["price_label"],
            total_price=product["price"],
            thread_id=str(thread.id),
        )

        # Send cart in thread
        embed = _cart_embed(order, product, None, settings)
        view  = CartView(order["id"], product_id)
        msg = await thread.send(
            content=f"Olá {interaction.user.mention}! Aqui está seu carrinho:",
            embed=embed,
            view=view,
        )
        await db.update_order(order["id"], message_id=str(msg.id))

        await interaction.followup.send(
            f"{EMOJI_CART} Sua compra foi iniciada! Acesse {thread.mention} para continuar.",
            ephemeral=True,
        )


class ShopView(discord.ui.View):
    def __init__(self, products: list[dict]):
        super().__init__(timeout=None)
        self.add_item(ProductSelect(products))


# ── cog ───────────────────────────────────────────────────────────────────────

class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))
