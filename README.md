# Navbatchilik Telegram boti

Ma'lumotlar **PostgreSQL**da saqlanadi. Jarayon **PM2** bilan boshqariladi (Docker yo'q).

---

## 1. Talablar (VPS da)

| Dastur | Versiya |
|--------|---------|
| Python | 3.12+   |
| pip    | —       |
| PM2    | 5+      |
| PostgreSQL | 14+ |

---

## 2. PostgreSQL sozlash

```bash
sudo -u postgres psql
```

```sql
CREATE USER navbat_user WITH PASSWORD 'STRONG_PASSWORD';
CREATE DATABASE navbatchilik OWNER navbat_user;
\q
```

---

## 3. Loyihani sozlash

```bash
git clone <repo-url> navbatchilik
cd navbatchilik

# Virtual muhit (ixtiyoriy)
python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

`.env` faylni yarating:

```bash
cp .env.example .env
nano .env
```

`.env` ichida to'ldiring:

```
BOT_TOKEN=<BotFather tokeni>
ADMIN_IDS=123456789,987654321
DATABASE_URL=postgresql://navbat_user:STRONG_PASSWORD@localhost:5432/navbatchilik
```

---

## 4. Mavjud JSON ma'lumotlarni PostgreSQLga ko'chirish (faqat bir marta)

Agar eski `data/`, `names.json`, `state.json` yoki `history.json` fayllaringiz bo'lsa:

```bash
python3 migrate_to_pg.py
```

Skript barcha jadvallarni yaratib, ma'lumotlarni import qiladi.
Yangi o'rnatmalarda bu qadam shart emas — bot birinchi ishga tushganda `DEFAULT_NAMES`dan avtomatik seed qiladi.

---

## 5. PM2 bilan ishga tushirish

```bash
pm2 start ecosystem.config.js
pm2 save          # tizim qayta ishga tushganda avtomatik start
pm2 startup       # startup skriptini o'rnatish (chiqgan buyruqni bajaring)
```

Loglarni ko'rish:

```bash
pm2 logs navbatchilik-bot
```

To'xtatish / qayta ishga tushirish:

```bash
pm2 stop navbatchilik-bot
pm2 restart navbatchilik-bot
```

---

## 6. Admin buyruqlari

- `/setup` — guruhni ulash (bot admini, guruhda); qayta yuborish joriy davrani saqlaydi
- `/bugun` — bugungi navbatchi
- `/jadval` — kelgusi navbatchilar
- `/royxat` — barcha a'zolar va davra holati
- `/tarix` — oxirgi 30 kunlik tarix
- `/bajarildi` — bugungi vazifani bajarilgan deb belgilash
- `/ism_qosh Ism Familiya @username` — a'zo qo'shish
- `/ism_ochir 3` — `/royxat` dagi raqam bo'yicha o'chirish
- `/zaxira` — DB snapshot (names + state + history) ni shaxsiy chatga yuborish
- `/admin` — admin paneli va tasdiqlash bilan yangi davra boshlash
- `/elon` — bugungi navbatchini ulangan guruhga teg bilan hozir yuborish (qayta yuborish ham mumkin)
- `/odamlar` — odamlarni inline tugmalar orqali qo'shish, ismini tahrirlash va o'chirish
- `/bekor` yoki `/cancel` — ism kiritish so'rovini bekor qilish
- `/id` — o'z Telegram ID'ingizni ko'rish

Admin panelidagi **👥 Odamlarni boshqarish** tugmasi odamlar ro'yxatini ochadi.
Ro'yxat ostida **➕ Odam qo'shish**, **✏️ Tahrirlash** va **🗑 O'chirish**
tugmalari bor. Ismni tahrirlash uchun odamni tanlang va bot so'raganda yangi
ismni yuboring. Qo'shishda username ixtiyoriy: `Ali Valiyev @ali_valiyev` yoki
faqat `Ali Valiyev`. Ismlar bitta qatorda, 100 ta belgigacha bo'lishi kerak.
O'chirish tasdiqlashdan keyin bajariladi. Ism o'zgarsa odamning ID'si, username'i
va navbatdagi o'rni saqlanadi; o'tgan kunlar tarixidagi ismlar o'zgarmaydi.
Guruhda botning ism so'ragan xabariga javob bering; oddiy guruh xabarlari ism
sifatida saqlanmaydi. Ro'yxat 10 kishidan sahifalanadi.

Admin bugungi navbatchilikni `/bajarildi` yoki **✅ Bajarildi** tugmasi bilan
tasdiqlamaguncha, shu a'zo ertasi kuni ham navbatchi bo'lib qoladi. Navbatdagi
a'zolarning sanalari har bir bajarilmagan kun uchun bir kunga suriladi.
Masalan, Azim 12-oktabrda bajarildi deb belgilanmasa, 13-oktabrda ham Azim
navbatchi bo'ladi; Bahromning navbati 14-oktabrga suriladi. Azim 13-oktabrda
bajarildi deb belgilangach, 14-oktabrda Bahrom navbatchi bo'ladi. Bot bir necha
kun ishlamasa ham bajarilmagan navbatchilik saqlanadi; davra oxirgi a'zo
bajarildi deb belgilangandan keyingina avtomatik yangilanadi.

Oddiy guruhda `/bugun` va `/jadval` faqat matnli navbatchilik xabarini yuboradi;
bu buyruqlar va kundalik e'londa menyu yoki tugmalar chiqmaydi. Bot guruhga qo'shilganda yoki birinchi marta
shu buyruqlardan biri yuborilganda guruh avtomatik ulanadi. Agar oldindan boshqa
guruh ulangan bo'lsa, bot admini `/setup` bilan yangi guruhni tanlaydi. Guruhni
ulash ro'yxat va joriy davrani o'zgartirmaydi.

`.env` dagi `ADMIN_IDS` bot adminlarini belgilaydi. Ulangan guruhning egasi va
Telegram adminlari ham shu guruh ichida botni boshqara oladi. Ularning huquqi
har bir so'rovda Telegram orqali tekshiriladi; bot guruhda admin bo'lishi kerak.
Guruhni boshqa guruhga ko'chirish uchun `ADMIN_IDS` dagi bot admini `/setup`
yuborishi kerak. Shaxsiy chatda guruh admini bo'lishning o'zi
yetarli emas — `ADMIN_IDS` talab qilinadi.

Username'lar haqiqiy Telegram mention entity'lari bilan yuboriladi. Ismda emoji
bo'lsa ham tegning joylashuvi to'g'ri hisoblanadi. Username yo'q a'zolar ism bilan
ko'rsatiladi; teg uchun a'zoning ro'yxatida haqiqiy username bo'lishi kerak.

Shaxsiy chatdagi asosiy menyuda bugungi navbatchi va jadval, adminlarda esa
admin paneli ham bor. Ro'yxat tugmasi asosiy menyudan olib tashlangan; odamlar
admin panelida boshqariladi, `/royxat` buyrug'i esa mavjud. Eski klaviaturani yangilash
uchun `/start`, `/jadval` yoki `/bugun` yuboring. Tarix `/tarix` orqali mavjud. Yangi davra raqami jadval va bugungi navbatchi
xabarida ko'rinadi; eski davra uchun tasdiqlash tugmasi qayta ishlatilmaydi.

---

## 7. E'lonlar jadvali

Bot har kuni **Toshkent vaqti bilan 08:00** da guruhga bugungi navbatchi haqida xabar yuboradi.
Shu maqsadda `python-telegram-bot` ning `job_queue.run_daily` funksiyasi ishlatiladi — vaqt zona bilan (tzinfo=Asia/Tashkent) berilgan.
Bot har 60 soniyada yuborilmagan e'lonni ham tekshiradi: 08:00 dagi urinish
muvaffaqiyatsiz bo'lsa yoki bot/guruh keyinroq ulansa, e'lon qayta yuboriladi.
Yetkazilgan sana faqat guruhga xabar muvaffaqiyatli yuborilgandan keyin saqlanadi;
shaxsiy admin panelida yangi davra boshlash e'lonni yuborilgan deb belgilamaydi.
Parallel tekshiruvlar bir xil e'lonni takrorlamaydi. Oldingi xato sabab bugungi
e'lon yuborilmagan bo'lsa, admin `/elon` bilan uni hozir yuborishi mumkin.

E'londa username oddiy havola emas, haqiqiy `@username` mention entity sifatida
yuboriladi va bot xabarni ovozsiz yuborishni so'ramaydi. Username to'g'ri bo'lishi
kerak; bildirishnoma chiqishi a'zoning Telegram va telefon sozlamalariga ham bog'liq.

---

## 8. Ma'lumotlar strukturasi (PostgreSQL)

| Jadval | Maqsad |
|--------|--------|
| `members` | A'zolar ro'yxati (id, name, username) |
| `bot_state` | Davra holati (key-value) |
| `duty_history` | Kunlik navbatchilar tarixi |

---

## 9. Qo'lda tekshirish

1. `.env`ga haqiqiy token va admin ID kiriting, `DATABASE_URL`ni to'ldiring.
2. `python3 migrate_to_pg.py` bilan mavjud ma'lumotlarni import qiling.
3. `pm2 start ecosystem.config.js` bilan botni ishga tushiring.
4. `pm2 logs navbatchilik-bot` orqali qayta ishga tushish xabari va xatolarni tekshiring.
5. Guruhda `/setup` yozing, so'ng `/bugun`, `/jadval`, `/royxat`, `/tarix` ni sinab ko'ring.
