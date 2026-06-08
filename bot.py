from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)

import os, json
from datetime import datetime, timezone

TOKEN    = os.getenv("TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_ID", "").split(",") if x.strip()]

BOT_DIR          = os.path.dirname(__file__)
FLAVORS_FILE     = os.path.join(BOT_DIR, "flavors.json")
CARTRIDGES_FILE  = os.path.join(BOT_DIR, "cartridges.json")
POD_SYSTEMS_FILE = os.path.join(BOT_DIR, "pod_systems.json")
ORDERS_FILE      = os.path.join(BOT_DIR, "orders.json")
USERS_FILE       = os.path.join(BOT_DIR, "users.json")

DEFAULT_FLAVORS = [
    "Blueberry Raspberry", "Cherry Lemon", "Blackberry Lemonade",
    "Turbo Mint", "Energetic", "Spearmint", "Sweet Mint",
    "Energy Grape", "Energy Raspberry", "Triple Berry",
    "Pink Lemonade", "Grape Blackberry", "Blueberry Lemon",
    "Tropic Punch", "Forest Mix",
]

STATUS_EMOJI = {"pending": "⏳", "accepted": "✅", "rejected": "❌", "delivered": "🎁"}

WAITING_FLAVOR_NAME, WAITING_BRAND_NAME, WAITING_PRODUCT_BRAND, WAITING_STOCK_AMOUNT = range(4)


# ── Persistence ───────────────────────────────────────────────────────────────

def _load(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default() if callable(default) else default

def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

STOCK_FILE       = os.path.join(BOT_DIR, "stock.json")

load_flavors     = lambda: _load(FLAVORS_FILE,     list(DEFAULT_FLAVORS))
save_flavors     = lambda d: _save(FLAVORS_FILE, d)
load_cartridges  = lambda: _load(CARTRIDGES_FILE,  {})
save_cartridges  = lambda d: _save(CARTRIDGES_FILE, d)
load_pod_systems = lambda: _load(POD_SYSTEMS_FILE, {"Xros": ["Xros 5 mini — 30€"]})
save_pod_systems = lambda d: _save(POD_SYSTEMS_FILE, d)
load_orders      = lambda: _load(ORDERS_FILE, [])
save_orders      = lambda d: _save(ORDERS_FILE, d)
load_stock       = lambda: _load(STOCK_FILE, {})
save_stock       = lambda d: _save(STOCK_FILE, d)
load_users       = lambda: _load(USERS_FILE, {})
save_users       = lambda d: _save(USERS_FILE, d)

def get_user(user_id: int, username: str = "") -> dict:
    users = load_users()
    uid   = str(user_id)
    if uid not in users:
        users[uid] = {"username": username, "delivered": 0, "referrer": None, "referrals_done": []}
        save_users(users)
    return users[uid]

def get_discount(user_id: int) -> int:
    delivered = get_user(user_id).get("delivered", 0)
    if delivered >= 20:
        return 20
    if delivered >= 10:
        return 10
    return 0

def register_referral(new_user_id: int, referrer_id: int):
    users = load_users()
    uid   = str(new_user_id)
    if uid not in users:
        users[uid] = {"username": "", "delivered": 0, "referrer": referrer_id, "referrals_done": []}
        save_users(users)
    elif users[uid].get("referrer") is None:
        users[uid]["referrer"] = referrer_id
        save_users(users)

def on_order_delivered(user_id: int) -> dict:
    """Increments delivered count. Returns info about unlocked bonuses."""
    users    = load_users()
    uid      = str(user_id)
    if uid not in users:
        users[uid] = {"username": "", "delivered": 0, "referrer": None, "referrals_done": []}
    users[uid]["delivered"] = users[uid].get("delivered", 0) + 1
    delivered = users[uid]["delivered"]
    save_users(users)

    bonuses = {"delivered": delivered, "discount_unlocked": None, "referral_reward": False}
    if delivered == 10:
        bonuses["discount_unlocked"] = 10
    elif delivered == 20:
        bonuses["discount_unlocked"] = 20

    referrer_id = users[uid].get("referrer")
    if referrer_id:
        ruid = str(referrer_id)
        if ruid not in users:
            users[ruid] = {"username": "", "delivered": 0, "referrer": None, "referrals_done": []}
        done = users[ruid].get("referrals_done", [])
        if user_id not in done:
            done.append(user_id)
            users[ruid]["referrals_done"] = done
            save_users(users)
            if len(done) == 5:
                bonuses["referral_reward"] = True
                bonuses["referrer_id"]     = referrer_id
    return bonuses

def get_stock(item: str) -> int:
    return load_stock().get(item, 0)

def deduct_stock(items: list) -> list:
    """Deducts 1 of each item. Returns list of items whose stock fell below 3."""
    stock = load_stock()
    low = []
    for item in items:
        if item in stock and stock[item] > 0:
            stock[item] -= 1
            if stock[item] < 3:
                low.append((item, stock[item]))
    save_stock(stock)
    return low

def set_stock(item: str, qty: int):
    stock = load_stock()
    stock[item] = max(0, qty)
    save_stock(stock)

def all_items_list() -> list:
    items = []
    for brand, products in load_cartridges().items():
        items.extend(products)
    for brand, products in load_pod_systems().items():
        items.extend(products)
    items.extend(load_flavors())
    return items

def add_order(user_id, username, items):
    orders   = load_orders()
    order_id = len(orders) + 1
    orders.append({
        "id": order_id, "user_id": user_id, "username": username or "—",
        "items": items, "status": "pending",
        "created_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M"),
    })
    save_orders(orders)
    return order_id

def update_order_status(user_id, order_id, status):
    orders = load_orders()
    for o in orders:
        if o["id"] == order_id and o["user_id"] == user_id:
            o["status"] = status
            break
    save_orders(orders)


# ── Shared cart button ────────────────────────────────────────────────────────

def cart_btn(cart):
    if cart:
        return [InlineKeyboardButton(f"🛒 Корзина ({len(cart)})  →", callback_data="cart:view")]
    return []


# ── Shop keyboards ────────────────────────────────────────────────────────────

def start_keyboard(cart):
    rows = [
        [InlineKeyboardButton("🔌 Картриджи",   callback_data="nav:cartridges")],
        [InlineKeyboardButton("💧 Жидкости",    callback_data="nav:liquids")],
        [InlineKeyboardButton("🖥 Под системы", callback_data="nav:podsystems")],
    ]
    if cart:
        rows.append(cart_btn(cart))
    return InlineKeyboardMarkup(rows)


def _catalog_keyboard(data: dict, nav_prefix: str, back_cb: str, cart: list):
    rows = [[InlineKeyboardButton(brand, callback_data=f"{nav_prefix}:{brand}")] for brand in data]
    rows.append([InlineKeyboardButton("← Назад", callback_data=back_cb)])
    if cart:
        rows.append(cart_btn(cart))
    return InlineKeyboardMarkup(rows)


def _stock_label(name: str, in_cart: bool) -> str:
    qty = get_stock(name)
    if in_cart:
        return f"✅ {name} [{qty} шт]"
    if qty == 0:
        return f"❌ {name} [нет]"
    return f"{name} [{qty} шт]"

def _brand_products_keyboard(products: list, brand: str, back_cb: str, cart: list):
    cart_set = set(cart)
    rows = []
    for p in products:
        label = _stock_label(p, p in cart_set)
        cb    = f"toggle:{p}" if (p in cart_set or get_stock(p) > 0) else f"nostock:{p}"
        rows.append([InlineKeyboardButton(label, callback_data=cb)])
    rows.append([InlineKeyboardButton("← Назад", callback_data=back_cb)])
    if cart:
        rows.append(cart_btn(cart))
    return InlineKeyboardMarkup(rows)


def liquids_keyboard(flavors, cart):
    cart_set = set(cart)
    rows = []
    for f in flavors:
        label = _stock_label(f, f in cart_set)
        cb    = f"toggle:{f}" if (f in cart_set or get_stock(f) > 0) else f"nostock:{f}"
        rows.append([InlineKeyboardButton(label, callback_data=cb)])
    rows.append([InlineKeyboardButton("← Назад", callback_data="nav:start")])
    if cart:
        rows.append(cart_btn(cart))
    return InlineKeyboardMarkup(rows)


def cart_screen_keyboard(cart, back_nav):
    rows = [[InlineKeyboardButton(f"❌  {i}", callback_data=f"cart:remove:{i}")] for i in cart]
    rows.append([InlineKeyboardButton("↩️ Продолжить покупки", callback_data=f"nav:{back_nav}")])
    rows.append([InlineKeyboardButton("✅ Подтвердить заказ",  callback_data="cart:confirm")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, отправить", callback_data="cart:submit"),
        InlineKeyboardButton("❌ Отмена",        callback_data="cart:view"),
    ]])


# ── Navigation ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data.setdefault("cart", [])
    context.user_data["last_nav"] = "start"
    get_user(user.id, user.username or "")

    # Handle referral link: /start ref_123456789
    args = context.args
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0][4:])
            if referrer_id != user.id:
                register_referral(user.id, referrer_id)
        except ValueError:
            pass

    discount = get_discount(user.id)
    user_data = get_user(user.id)
    delivered = user_data.get("delivered", 0)
    ref_done  = len(user_data.get("referrals_done", []))

    info = ""
    if discount:
        info += f"\n🎫 У тебя скидка {discount}% — скажи при получении!"
    if ref_done > 0:
        info += f"\n👥 Рефералов: {ref_done}/5"
    if delivered > 0:
        info += f"\n📦 Заказов получено: {delivered}"

    await update.message.reply_text(
        f"👋 Привет! Выбери категорию:{info}",
        reply_markup=start_keyboard(context.user_data["cart"]),
    )


async def navigate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    await q.answer()
    parts = q.data.split(":", 2)
    dest  = parts[1]
    cart  = context.user_data.setdefault("cart", [])

    if dest == "start":
        context.user_data["last_nav"] = "start"
        await q.edit_message_text("👋 Выбери категорию:", reply_markup=start_keyboard(cart))

    elif dest == "cartridges":
        context.user_data["last_nav"] = "cartridges"
        data = load_cartridges()
        if not data:
            await q.edit_message_text(
                "🔌 Картриджи\n\nПока товаров нет.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data="nav:start")]] + ([cart_btn(cart)] if cart else [])),
            )
        else:
            await q.edit_message_text(
                "🔌 Картриджи\nВыбери бренд:",
                reply_markup=_catalog_keyboard(data, "nav:car_brand", "nav:start", cart),
            )

    elif dest == "car_brand":
        brand = parts[2]
        context.user_data["last_nav"] = f"car_brand:{brand}"
        data  = load_cartridges()
        await q.edit_message_text(
            f"🔌 Картриджи — {brand}",
            reply_markup=_brand_products_keyboard(data.get(brand, []), brand, "nav:cartridges", cart),
        )

    elif dest == "podsystems":
        context.user_data["last_nav"] = "podsystems"
        data = load_pod_systems()
        if not data:
            await q.edit_message_text(
                "🖥 Под системы\n\nПока товаров нет.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Назад", callback_data="nav:start")]] + ([cart_btn(cart)] if cart else [])),
            )
        else:
            await q.edit_message_text(
                "🖥 Под системы\nВыбери бренд:",
                reply_markup=_catalog_keyboard(data, "nav:pod_brand", "nav:start", cart),
            )

    elif dest == "pod_brand":
        brand = parts[2]
        context.user_data["last_nav"] = f"pod_brand:{brand}"
        data  = load_pod_systems()
        await q.edit_message_text(
            f"🖥 Под системы — {brand}",
            reply_markup=_brand_products_keyboard(data.get(brand, []), brand, "nav:podsystems", cart),
        )

    elif dest == "liquids":
        context.user_data["last_nav"] = "liquids"
        await q.edit_message_text(
            "💧 Жидкости\nВыбери вкусы:",
            reply_markup=liquids_keyboard(load_flavors(), cart),
        )


# ── Toggle item ───────────────────────────────────────────────────────────────

async def toggle_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    item = q.data.split(":", 1)[1]
    cart = context.user_data.setdefault("cart", [])
    last = context.user_data.get("last_nav", "start")

    if item in cart:
        cart.remove(item)
    else:
        cart.append(item)

    screen, _, arg = last.partition(":")

    if screen == "liquids":
        await q.edit_message_reply_markup(reply_markup=liquids_keyboard(load_flavors(), cart))
    elif screen == "car_brand":
        data = load_cartridges()
        await q.edit_message_reply_markup(
            reply_markup=_brand_products_keyboard(data.get(arg, []), arg, "nav:cartridges", cart)
        )
    elif screen == "pod_brand":
        data = load_pod_systems()
        await q.edit_message_reply_markup(
            reply_markup=_brand_products_keyboard(data.get(arg, []), arg, "nav:podsystems", cart)
        )
    else:
        await q.edit_message_reply_markup(reply_markup=start_keyboard(cart))


# ── Cart ──────────────────────────────────────────────────────────────────────

async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    cart = context.user_data.get("cart", [])
    if not cart:
        await q.answer("Корзина пуста!", show_alert=True)
        return
    last  = context.user_data.get("last_nav", "start")
    lines = "\n".join(f"  • {i}" for i in cart)
    await q.edit_message_text(
        f"🛒 Ваша корзина:\n\n{lines}\n\nУбери ненужное или подтверди заказ:",
        reply_markup=cart_screen_keyboard(cart, last),
    )

async def remove_from_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    item = q.data.split("cart:remove:", 1)[1]
    cart = context.user_data.setdefault("cart", [])
    if item in cart:
        cart.remove(item)
    last = context.user_data.get("last_nav", "start")
    if not cart:
        await q.edit_message_text("🛒 Корзина пуста.\n\nВыбери категорию:", reply_markup=start_keyboard([]))
        return
    lines = "\n".join(f"  • {i}" for i in cart)
    await q.edit_message_text(
        f"🛒 Ваша корзина:\n\n{lines}\n\nУбери ненужное или подтверди заказ:",
        reply_markup=cart_screen_keyboard(cart, last),
    )

async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    cart = context.user_data.get("cart", [])
    if not cart:
        await q.answer("Корзина пуста!", show_alert=True)
        return
    lines = "\n".join(f"  • {i}" for i in cart)
    await q.edit_message_text(f"📋 Ваш заказ:\n\n{lines}\n\nВсё верно?", reply_markup=confirm_keyboard())

async def submit_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    cart = context.user_data.get("cart", [])
    if not cart:
        await q.answer("Корзина пуста!", show_alert=True)
        return
    user     = q.from_user
    order_id = add_order(user.id, user.username, list(cart))
    lines    = "\n".join(f"  • {i}" for i in cart)
    discount = get_discount(user.id)
    discount_note = f"\n🎫 Скидка клиента: {discount}%" if discount else ""
    admin_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Принять",   callback_data=f"admin:accept:{user.id}:{order_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"admin:reject:{user.id}:{order_id}"),
    ]])
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=f"🆕 Заказ #{order_id}\n\n{lines}\n\n@{user.username or '—'}  |  ID: {user.id}{discount_note}",
            reply_markup=admin_kb,
        )
    context.user_data["cart"] = []
    client_note = f"\n\n🎫 Ваша скидка {discount}% — скажите при получении!" if discount else ""
    await q.edit_message_text(
        f"✅ Заказ #{order_id} отправлен!\n\nВы заказали:\n{lines}\n\nОжидайте подтверждения. ⏳{client_note}"
    )


# ── Admin order response ──────────────────────────────────────────────────────

async def admin_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Нет доступа.", show_alert=True)
        return
    parts    = q.data.split(":", 3)
    action   = parts[1]
    user_id  = int(parts[2])
    order_id = int(parts[3])
    orders   = load_orders()
    order    = next((o for o in orders if o["id"] == order_id), None)
    lines    = "\n".join(f"  • {i}" for i in (order.get("items", order.get("flavors", [])) if order else []))
    if action == "accept":
        update_order_status(user_id, order_id, "accepted")
        low_stock = []
        if order:
            low_stock = deduct_stock(order.get("items", order.get("flavors", [])))
        await context.bot.send_message(chat_id=user_id, text=f"✅ Ваш заказ #{order_id} принят! Ожидайте выдачи.\n\n{lines}")
        deliver_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📦 Выдан", callback_data=f"admin:deliver:{user_id}:{order_id}")
        ]])
        await q.edit_message_text(q.message.text + "\n\n✅ Заказ принят — нажми «Выдан» когда отдашь", reply_markup=deliver_kb)
        if low_stock:
            warning_lines = "\n".join(
                f"  ⚠️ {item}: осталось {qty} шт" if qty > 0 else f"  🚨 {item}: ЗАКОНЧИЛСЯ"
                for item, qty in low_stock
            )
            for admin_id in ADMIN_IDS:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"📦 Заканчиваются товары:\n\n{warning_lines}\n\nПополни запасы: /admin → 📦 Запасы"
                )
    elif action == "deliver":
        update_order_status(user_id, order_id, "delivered")
        bonuses = on_order_delivered(user_id)
        delivered_count = bonuses["delivered"]
        next_discount = 10 if delivered_count < 10 else (20 if delivered_count < 20 else None)
        client_msg = f"🎁 Ваш заказ #{order_id} выдан! Спасибо!\n\n{lines}"
        if bonuses.get("discount_unlocked"):
            client_msg += f"\n\n🎉 Поздравляем! Вы получили скидку {bonuses['discount_unlocked']}% на все следующие заказы!"
        elif next_discount:
            remaining = (10 if next_discount == 10 else 20) - delivered_count
            client_msg += f"\n\n📊 Заказов получено: {delivered_count}. До скидки {next_discount}%: ещё {remaining} заказ(а)"
        await context.bot.send_message(chat_id=user_id, text=client_msg)
        await q.edit_message_text(q.message.text.replace("нажми «Выдан» когда отдашь", "") + "\n\n📦 Выдан", reply_markup=None)
        if bonuses.get("referral_reward"):
            referrer_id = bonuses["referrer_id"]
            await context.bot.send_message(
                chat_id=referrer_id,
                text="🎉 Поздравляем! 5 твоих рефералов получили заказы!\n\n🥤 Тебе полагается БЕСПЛАТНАЯ ЖИДКОСТЬ!\n\nСвяжись с нами для получения."
            )
    elif action == "reject":
        update_order_status(user_id, order_id, "rejected")
        await context.bot.send_message(chat_id=user_id, text=f"❌ Ваш заказ #{order_id} отклонён.\n\n{lines}")
        await q.edit_message_text(q.message.text + "\n\n❌ Заказ отклонён", reply_markup=None)


# ── /stock ────────────────────────────────────────────────────────────────────

async def stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Нет доступа.")
        return
    items = all_items_list()
    if not items:
        await update.message.reply_text("Товаров пока нет.")
        return
    stock = load_stock()
    lines = []
    for item in items:
        qty = stock.get(item, 0)
        if qty == 0:
            emoji = "🚨"
        elif qty < 3:
            emoji = "⚠️"
        else:
            emoji = "✅"
        lines.append(f"{emoji} {item}: {qty} шт")
    await update.message.reply_text("📦 Текущие запасы:\n\n" + "\n".join(lines))


# ── /orders ───────────────────────────────────────────────────────────────────

async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Нет доступа.")
        return
    orders = load_orders()
    if not orders:
        await update.message.reply_text("Заказов пока нет.")
        return
    recent = list(reversed(orders[-20:]))
    lines  = []
    for o in recent:
        emoji = STATUS_EMOJI.get(o["status"], "❓")
        items = ", ".join(o.get("items", o.get("flavors", [])))
        lines.append(f"{emoji} #{o['id']} | {o['created_at']}\n   {items}\n   @{o['username']} (ID: {o['user_id']})")
    await update.message.reply_text(f"📦 Последние заказы ({len(orders)} всего):\n\n" + "\n\n".join(lines))


# ── Admin menu keyboards ──────────────────────────────────────────────────────

def main_admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔌 Картриджи",   callback_data="amenu:cartridges")],
        [InlineKeyboardButton("💧 Жидкости",    callback_data="amenu:flavors")],
        [InlineKeyboardButton("🖥 Под системы", callback_data="amenu:podsystems")],
        [InlineKeyboardButton("📦 Запасы",      callback_data="amenu:stock")],
    ])

def _catalog_admin_kb(section_cb, back_cb="amenu:back"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить бренд",    callback_data=f"{section_cb}:addbrand")],
        [InlineKeyboardButton("➕ Добавить товар",    callback_data=f"{section_cb}:addproduct")],
        [InlineKeyboardButton("🗑 Удалить товар",     callback_data=f"{section_cb}:delproduct")],
        [InlineKeyboardButton("🗑 Удалить бренд",     callback_data=f"{section_cb}:delbrand")],
        [InlineKeyboardButton("📋 Список",            callback_data=f"{section_cb}:list")],
        [InlineKeyboardButton("← Назад",              callback_data=back_cb)],
    ])

def flavors_admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить вкус", callback_data="amenu:flavor_add")],
        [InlineKeyboardButton("🗑 Удалить вкус",  callback_data="amenu:flavor_del")],
        [InlineKeyboardButton("📋 Список вкусов", callback_data="amenu:flavor_list")],
        [InlineKeyboardButton("← Назад",          callback_data="amenu:back")],
    ])


# ── Admin ConversationHandler ─────────────────────────────────────────────────

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Нет доступа.")
        return ConversationHandler.END
    await update.message.reply_text("⚙️ Меню администратора:", reply_markup=main_admin_kb())
    return ConversationHandler.END


async def amenu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    action = q.data.split(":", 1)[1]

    # ── navigation ──
    if action == "back":
        await q.edit_message_text("⚙️ Меню администратора:", reply_markup=main_admin_kb())
        return ConversationHandler.END

    if action == "flavors":
        await q.edit_message_text("💧 Управление жидкостями:", reply_markup=flavors_admin_kb())
        return ConversationHandler.END

    if action == "cartridges":
        await q.edit_message_text("🔌 Управление картриджами:", reply_markup=_catalog_admin_kb("acar"))
        return ConversationHandler.END

    if action == "podsystems":
        await q.edit_message_text("🖥 Управление под системами:", reply_markup=_catalog_admin_kb("apod"))
        return ConversationHandler.END

    # ── flavors ──
    if action == "flavor_list":
        flavors = load_flavors()
        await q.message.reply_text("📋 Вкусы:\n\n" + "\n".join(f"• {f}" for f in flavors), reply_markup=flavors_admin_kb())
        return ConversationHandler.END

    if action == "stock":
        items = all_items_list()
        if not items:
            await q.edit_message_text("Товаров нет. Сначала добавь товары.", reply_markup=main_admin_kb())
            return ConversationHandler.END
        stock = load_stock()
        rows = []
        for item in items:
            qty = stock.get(item, 0)
            rows.append([InlineKeyboardButton(
                f"📦 {item}: {qty} шт  →  изменить",
                callback_data=f"setstock:{item}"
            )])
        rows.append([InlineKeyboardButton("← Назад", callback_data="amenu:back")])
        await q.edit_message_text("📦 Управление запасами:\n\nНажми на товар чтобы изменить количество:", reply_markup=InlineKeyboardMarkup(rows))
        return ConversationHandler.END

    if action == "flavor_add":
        context.user_data["amenu_state"] = "flavor_add"
        await q.message.reply_text("Напиши название нового вкуса:")
        return WAITING_FLAVOR_NAME

    if action == "flavor_del":
        flavors = load_flavors()
        if not flavors:
            await q.message.reply_text("Список пуст.", reply_markup=flavors_admin_kb())
            return ConversationHandler.END
        rows = [[InlineKeyboardButton(f"❌ {f}", callback_data=f"del_flavor:{f}")] for f in flavors]
        rows.append([InlineKeyboardButton("← Назад", callback_data="amenu:flavors")])
        await q.message.reply_text("Выбери вкус для удаления:", reply_markup=InlineKeyboardMarkup(rows))
        return ConversationHandler.END

    return ConversationHandler.END


def _catalog_handler(load_fn, save_fn, admin_kb_fn, section_label):
    """Returns (amenu_action_handler, pick_brand_handler) for a catalog section."""

    async def handle_catalog_action(update, context, action, q):
        if action == "list":
            data  = load_fn()
            lines = []
            for brand, products in data.items():
                lines.append(f"• {brand}:")
                lines += [f"   - {p}" for p in products]
            text = "\n".join(lines) if lines else "Пусто."
            await q.message.reply_text(text, reply_markup=admin_kb_fn())

        elif action == "addbrand":
            context.user_data["amenu_state"] = f"{section_label}_addbrand"
            await q.message.reply_text("Напиши название нового бренда:")
            return WAITING_BRAND_NAME

        elif action == "addproduct":
            data = load_fn()
            if not data:
                await q.message.reply_text("Сначала добавь бренд.", reply_markup=admin_kb_fn())
                return ConversationHandler.END
            rows = [[InlineKeyboardButton(b, callback_data=f"pickbrand_{section_label}:{b}")] for b in data]
            rows.append([InlineKeyboardButton("← Назад", callback_data=f"amenu:{section_label}")])
            await q.message.reply_text("Выбери бренд:", reply_markup=InlineKeyboardMarkup(rows))

        elif action == "delproduct":
            data = load_fn()
            rows = []
            for brand, products in data.items():
                for p in products:
                    rows.append([InlineKeyboardButton(f"❌ [{brand}] {p}", callback_data=f"delprod_{section_label}:{brand}:{p}")])
            if not rows:
                await q.message.reply_text("Товаров нет.", reply_markup=admin_kb_fn())
                return ConversationHandler.END
            rows.append([InlineKeyboardButton("← Назад", callback_data=f"amenu:{section_label}")])
            await q.message.reply_text("Выбери товар для удаления:", reply_markup=InlineKeyboardMarkup(rows))

        elif action == "delbrand":
            data = load_fn()
            if not data:
                await q.message.reply_text("Брендов нет.", reply_markup=admin_kb_fn())
                return ConversationHandler.END
            rows = [[InlineKeyboardButton(f"🗑 {b}", callback_data=f"delbrand_{section_label}:{b}")] for b in data]
            rows.append([InlineKeyboardButton("← Назад", callback_data=f"amenu:{section_label}")])
            await q.message.reply_text("Выбери бренд для удаления:", reply_markup=InlineKeyboardMarkup(rows))

        return ConversationHandler.END

    return handle_catalog_action


_car_catalog = _catalog_handler(
    load_cartridges, save_cartridges,
    lambda: _catalog_admin_kb("acar"), "cartridges"
)
_pod_catalog = _catalog_handler(
    load_pod_systems, save_pod_systems,
    lambda: _catalog_admin_kb("apod"), "podsystems"
)


async def acar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return ConversationHandler.END
    action = q.data.split(":", 1)[1]
    return await _car_catalog(update, context, action, q)


async def apod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return ConversationHandler.END
    action = q.data.split(":", 1)[1]
    return await _pod_catalog(update, context, action, q)


async def pickbrand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles pickbrand_cartridges:BrandName and pickbrand_podsystems:BrandName"""
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return ConversationHandler.END
    raw    = q.data  # e.g. "pickbrand_cartridges:Xros"
    prefix, _, brand = raw.partition(":")
    section = prefix.split("_", 1)[1]  # "cartridges" or "podsystems"
    context.user_data["target_brand"]   = brand
    context.user_data["target_section"] = section
    await q.message.reply_text(f"Напиши название товара для «{brand}»:")
    return WAITING_PRODUCT_BRAND


# ── Text input handlers ───────────────────────────────────────────────────────

async def receive_flavor_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    name    = update.message.text.strip()
    flavors = load_flavors()
    if name in flavors:
        await update.message.reply_text(f"⚠️ «{name}» уже есть.", reply_markup=flavors_admin_kb())
    else:
        flavors.append(name); save_flavors(flavors)
        await update.message.reply_text(f"✅ Вкус «{name}» добавлен!", reply_markup=flavors_admin_kb())
    return ConversationHandler.END


async def receive_brand_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    name    = update.message.text.strip()
    section = context.user_data.get("amenu_state", "")
    if "cartridges" in section:
        load_fn, save_fn = load_cartridges, save_cartridges
        kb = lambda: _catalog_admin_kb("acar")
    else:
        load_fn, save_fn = load_pod_systems, save_pod_systems
        kb = lambda: _catalog_admin_kb("apod")
    data = load_fn()
    if name in data:
        await update.message.reply_text(f"⚠️ «{name}» уже есть.", reply_markup=kb())
    else:
        data[name] = []; save_fn(data)
        await update.message.reply_text(f"✅ Бренд «{name}» добавлен!", reply_markup=kb())
    return ConversationHandler.END


async def receive_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    name    = update.message.text.strip()
    brand   = context.user_data.get("target_brand", "")
    section = context.user_data.get("target_section", "cartridges")
    if section == "podsystems":
        load_fn, save_fn = load_pod_systems, save_pod_systems
        kb = lambda: _catalog_admin_kb("apod")
    else:
        load_fn, save_fn = load_cartridges, save_cartridges
        kb = lambda: _catalog_admin_kb("acar")
    data = load_fn()
    if brand not in data:
        await update.message.reply_text("Бренд не найден.", reply_markup=kb())
        return ConversationHandler.END
    if name in data[brand]:
        await update.message.reply_text(f"⚠️ «{name}» уже есть.", reply_markup=kb())
    else:
        data[brand].append(name); save_fn(data)
        await update.message.reply_text(f"✅ «{name}» добавлен в «{brand}»!", reply_markup=kb())
    return ConversationHandler.END


# ── Stock management callbacks ────────────────────────────────────────────────

async def setstock_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return ConversationHandler.END
    item = q.data.split("setstock:", 1)[1]
    context.user_data["stock_item"] = item
    qty = get_stock(item)
    await q.message.reply_text(
        f"📦 Товар: {item}\nСейчас: {qty} шт\n\nВведи новое количество (число):"
    )
    return WAITING_STOCK_AMOUNT


async def receive_stock_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    text = update.message.text.strip()
    item = context.user_data.get("stock_item", "")
    if not text.isdigit():
        await update.message.reply_text("⚠️ Введи целое число, например: 10")
        return WAITING_STOCK_AMOUNT
    qty = int(text)
    set_stock(item, qty)
    await update.message.reply_text(
        f"✅ Запасы обновлены!\n\n📦 {item}: {qty} шт",
        reply_markup=main_admin_kb()
    )
    return ConversationHandler.END


async def nostock_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("❌ Этого товара нет в наличии!", show_alert=True)


# ── Delete callbacks ──────────────────────────────────────────────────────────

async def del_flavor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    name    = q.data.split(":", 1)[1]
    flavors = load_flavors()
    if name in flavors:
        flavors.remove(name); save_flavors(flavors)
        await q.edit_message_text(f"🗑 «{name}» удалён.", reply_markup=flavors_admin_kb())
    else:
        await q.edit_message_text("Не найдено.", reply_markup=flavors_admin_kb())


async def del_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    raw     = q.data  # delprod_cartridges:Brand:Product or delprod_podsystems:Brand:Product
    prefix, _, rest = raw.partition(":")
    section = prefix.split("_", 1)[1]
    brand, _, product = rest.partition(":")
    if section == "podsystems":
        load_fn, save_fn = load_pod_systems, save_pod_systems
        kb = lambda: _catalog_admin_kb("apod")
    else:
        load_fn, save_fn = load_cartridges, save_cartridges
        kb = lambda: _catalog_admin_kb("acar")
    data = load_fn()
    if brand in data and product in data[brand]:
        data[brand].remove(product); save_fn(data)
        await q.edit_message_text(f"🗑 «{product}» удалён из «{brand}».", reply_markup=kb())
    else:
        await q.edit_message_text("Не найдено.", reply_markup=kb())


async def del_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    raw     = q.data  # delbrand_cartridges:Brand or delbrand_podsystems:Brand
    prefix, _, brand = raw.partition(":")
    section = prefix.split("_", 1)[1]
    if section == "podsystems":
        load_fn, save_fn = load_pod_systems, save_pod_systems
        kb = lambda: _catalog_admin_kb("apod")
    else:
        load_fn, save_fn = load_cartridges, save_cartridges
        kb = lambda: _catalog_admin_kb("acar")
    data = load_fn()
    if brand in data:
        del data[brand]; save_fn(data)
        await q.edit_message_text(f"🗑 Бренд «{brand}» удалён.", reply_markup=kb())
    else:
        await q.edit_message_text("Не найдено.", reply_markup=kb())


# ── /myorders ─────────────────────────────────────────────────────────────────

async def myorders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    orders = load_orders()
    mine   = [o for o in orders if o["user_id"] == user.id]
    if not mine:
        await update.message.reply_text("У тебя ещё нет заказов.")
        return
    status_names = {
        "pending":   "⏳ Ожидает",
        "accepted":  "✅ Принят",
        "rejected":  "❌ Отклонён",
        "delivered": "🎁 Выдан",
        "cancelled": "🚫 Отменён",
    }
    lines = []
    for o in reversed(mine[-10:]):
        status = status_names.get(o["status"], o["status"])
        items  = ", ".join(o.get("items", o.get("flavors", [])))
        lines.append(f"{status} | #{o['id']} | {o['created_at']}\n   {items}")
    await update.message.reply_text(
        f"📋 Твои последние заказы ({len(mine)} всего):\n\n" + "\n\n".join(lines)
    )


# ── /cancel ───────────────────────────────────────────────────────────────────

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    orders = load_orders()
    last   = next((o for o in reversed(orders) if o["user_id"] == user.id), None)
    if not last:
        await update.message.reply_text("У тебя ещё нет заказов.")
        return
    if last["status"] != "pending":
        status_names = {"accepted": "уже принят", "rejected": "уже отклонён", "delivered": "уже выдан"}
        status_text  = status_names.get(last["status"], last["status"])
        items = ", ".join(last.get("items", last.get("flavors", [])))
        await update.message.reply_text(
            f"❌ Заказ #{last['id']} нельзя отменить — он {status_text}.\n\n"
            f"Состав: {items}"
        )
        return
    last["status"] = "cancelled"
    save_orders(orders)
    items = ", ".join(last.get("items", last.get("flavors", [])))
    await update.message.reply_text(f"✅ Заказ #{last['id']} отменён.\n\nСостав был: {items}")
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=f"🚫 Заказ #{last['id']} отменён клиентом @{user.username or '—'}\n\nСостав: {items}"
        )


# ── /mystats ──────────────────────────────────────────────────────────────────

async def mystats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user      = update.effective_user
    get_user(user.id, user.username or "")
    user_data = get_user(user.id)
    delivered = user_data.get("delivered", 0)
    ref_done  = len(user_data.get("referrals_done", []))
    discount  = get_discount(user.id)

    if discount == 20:
        discount_line = "🎫 Скидка: 20% (максимальная!)"
    elif discount == 10:
        discount_line = f"🎫 Скидка: 10%\n📊 До скидки 20%: ещё {20 - delivered} заказ(а)"
    else:
        discount_line = f"📊 До скидки 10%: ещё {10 - delivered} заказ(а)"

    ref_line = f"👥 Рефералов (получили заказ): {ref_done}/5"
    if ref_done >= 5:
        ref_line += "\n🥤 Бесплатная жидкость уже выдана!"
    else:
        ref_line += f"\n   До бесплатной жидкости: ещё {5 - ref_done} чел."

    bot_name = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_name}?start=ref_{user.id}"

    await update.message.reply_text(
        f"📈 Твоя статистика:\n\n"
        f"📦 Заказов получено: {delivered}\n"
        f"{discount_line}\n\n"
        f"{ref_line}\n\n"
        f"🔗 Твоя реф. ссылка:\n{ref_link}"
    )


# ── /ref ──────────────────────────────────────────────────────────────────────

async def ref_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user      = update.effective_user
    get_user(user.id, user.username or "")
    bot_name  = (await context.bot.get_me()).username
    ref_link  = f"https://t.me/{bot_name}?start=ref_{user.id}"
    user_data = get_user(user.id)
    ref_done  = len(user_data.get("referrals_done", []))
    remaining = max(0, 5 - ref_done)
    await update.message.reply_text(
        f"👥 Твоя реферальная ссылка:\n{ref_link}\n\n"
        f"Приглашено (и получили заказ): {ref_done}/5\n"
        f"До бесплатной жидкости: ещё {remaining} чел.\n\n"
        f"Поделись ссылкой — когда 5 приглашённых получат свои заказы, тебе придёт уведомление о бесплатной жидкости 🥤"
    )


# ── App setup ─────────────────────────────────────────────────────────────────

app = Application.builder().token(TOKEN).build()

conv = ConversationHandler(
    entry_points=[
        CommandHandler("admin", admin_cmd),
        CallbackQueryHandler(amenu,      pattern=r"^amenu:"),
        CallbackQueryHandler(acar,       pattern=r"^acar:"),
        CallbackQueryHandler(apod,       pattern=r"^apod:"),
        CallbackQueryHandler(pickbrand,  pattern=r"^pickbrand_"),
        CallbackQueryHandler(setstock_cb,pattern=r"^setstock:"),
    ],
    states={
        WAITING_FLAVOR_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_flavor_name)],
        WAITING_BRAND_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_brand_name)],
        WAITING_PRODUCT_BRAND: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_product_name)],
        WAITING_STOCK_AMOUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_stock_amount)],
    },
    fallbacks=[CommandHandler("admin", admin_cmd)],
    per_message=False,
)

app.add_handler(conv)
app.add_handler(CommandHandler("orders", orders_cmd))
app.add_handler(CommandHandler("stock",  stock_cmd))
app.add_handler(CommandHandler("ref",      ref_cmd))
app.add_handler(CommandHandler("mystats", mystats_cmd))
app.add_handler(CommandHandler("cancel",   cancel_cmd))
app.add_handler(CommandHandler("myorders", myorders_cmd))
app.add_handler(CommandHandler("start",   start))
app.add_handler(CallbackQueryHandler(admin_response,   pattern=r"^admin:"))
app.add_handler(CallbackQueryHandler(del_flavor,       pattern=r"^del_flavor:"))
app.add_handler(CallbackQueryHandler(del_product,      pattern=r"^delprod_"))
app.add_handler(CallbackQueryHandler(del_brand,        pattern=r"^delbrand_"))
app.add_handler(CallbackQueryHandler(navigate,         pattern=r"^nav:"))
app.add_handler(CallbackQueryHandler(show_cart,        pattern=r"^cart:view$"))
app.add_handler(CallbackQueryHandler(remove_from_cart, pattern=r"^cart:remove:"))
app.add_handler(CallbackQueryHandler(confirm_order,    pattern=r"^cart:confirm$"))
app.add_handler(CallbackQueryHandler(submit_order,     pattern=r"^cart:submit$"))
app.add_handler(CallbackQueryHandler(toggle_item,      pattern=r"^toggle:"))
app.add_handler(CallbackQueryHandler(nostock_cb,       pattern=r"^nostock:"))

app.run_polling()
