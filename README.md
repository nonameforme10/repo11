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

Bot ishga tushganda Telegramdagi buyruqlar menyusi avtomatik sozlanadi.
Guruhda yoki shaxsiy chatda `/` yozilsa, `/start`, `/bugun`, `/jadval` va
`/id` tavsiya qilinadi. Guruh adminlari va `ADMIN_IDS` dagi bot adminlari
boshqaruv buyruqlarini ham ko'radi; shaxsiy chatdagi **Menu** tugmasi ham
buyruqlar ro'yxatini ochadi. Eski `/royxat` va `/cancel` buyruqlari ishlashda
davom etadi, ammo tavsiya ro'yxatida ko'rsatilmaydi.

- `/setup` — guruhni ulash (bot admini, guruhda); qayta yuborish joriy davrani saqlaydi
- `/bugun` — bugungi navbatchi
- `/jadval` — ikki kunlik navbatchilik davrlari va kelgusi navbatchilar
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

Har bir a'zo **ikki kun** navbatchi bo'ladi. Masalan, Azimning navbati
1–2-oktabr, Bahromniki 3–4-oktabr, keyingi a'zoniki 5–6-oktabr bo'ladi.
Admin `/bajarildi` yoki **✅ Bajarildi** tugmasi bilan tasdiqlasa ham, birinchi
kun tugashi bilan navbat o'tmaydi: Azim 1-oktabrda bajarildi deb belgilansa,
2-oktabrda ham Azim qoladi, Bahrom 3-oktabrda boshlaydi. Tasdiqlash ikkinchi
kuni ham saqlanadi; uni qayta bosish shart emas.

Ikki kun tugaganida navbatchilik hali bajarildi deb belgilanmagan bo'lsa,
shu a'zo navbatchi bo'lib qoladi va kelgusi navbatlar suriladi. Masalan,
Azim 1–2-oktabrdan keyin ham tasdiqlanmasa, 3-oktabrda ham Azim qoladi.
Azim 3-oktabrda bajarildi deb belgilansa, Bahrom 4–5-oktabrda navbatchi
bo'ladi. Bot bir necha kun ishlamasa ham bajarilmagan navbatchilik va
ikki kunlik davrning boshlangan sanasi saqlanadi. Yangi davra oxirgi a'zo
kamida ikki kun navbatchi bo'lib, bajarildi deb belgilangandan keyin boshlanadi.

Oddiy guruhda `/bugun` va `/jadval` faqat matnli navbatchilik xabarini yuboradi;
bu buyruqlar va avtomatik e'londa menyu yoki tugmalar chiqmaydi. Bot guruhga qo'shilganda yoki birinchi marta
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

Bot ikki kunlik navbatchilikning birinchi kuni **Toshkent vaqti bilan 08:00**
da guruhga e'lon yuboradi. Navbatlar o'z vaqtida bajarilsa, e'lonlar
1-oktabr, 3-oktabr, 5-oktabr kabi ikki kun oralig'ida chiqadi. Birinchi kuni
e'lon yetkazilgan bo'lsa, ikkinchi kuni takrorlanmaydi. Bajarilmagan
navbatchilik cho'zilsa, shu a'zo haqida uning boshlanish sanasidan hisoblab
har ikki kunlik davrda eslatma yuboriladi. Kech tasdiqlash navbatdagi a'zoning
boshlanish sanasini surishi mumkin; yangi navbatchilikning e'loni shu sanaga
bog'liq bo'ladi.

`job_queue.run_daily` har kuni 08:00 da tekshiradi, ammo faqat e'lon kuni
xabar yuboradi; vaqt zonasi `Asia/Tashkent`. Bot har 60 soniyada yuborilmagan
e'lonni ham tekshiradi: e'lon kunidagi 08:00 urinish muvaffaqiyatsiz bo'lsa
yoki bot/guruh keyinroq ulansa, qayta uriniladi. Bot birinchi kuni ishlamagan
bo'lsa, shu ikki kunlik davrning ikkinchi kuni 08:00 dan keyin o'tkazib
yuborilgan e'lonni yetkazadi. Yetkazilgan e'lon o'sha ikki kunlik davr ichida
yana avtomatik yuborilmaydi.
Yetkazilgan sana faqat guruhga xabar muvaffaqiyatli yuborilgandan keyin saqlanadi;
shaxsiy admin panelida yangi davra boshlash e'lonni yuborilgan deb belgilamaydi.
Parallel tekshiruvlar bir xil e'lonni takrorlamaydi. Admin `/elon` bilan
navbatchini istalgan kuni, jumladan ikkinchi kuni ham hozir e'lon qilishi mumkin.

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

Ikki kunlik navbatchilikning boshlanish sanasi `bot_state` ichidagi
`duty_started_date` kalitida saqlanadi; jadvallar sxemasini o'zgartirish
talab qilinmaydi. Eski bazada bu kalit bo'lmasa, bot mavjud navbatchilik
sanasini boshlanish sanasi sifatida oladi va oldingi tarixni o'zgartirmaydi.

---

## 9. Qo'lda tekshirish

1. `.env`ga haqiqiy token va admin ID kiriting, `DATABASE_URL`ni to'ldiring.
2. `python3 migrate_to_pg.py` bilan mavjud ma'lumotlarni import qiling.
3. `pm2 start ecosystem.config.js` bilan botni ishga tushiring.
4. `pm2 logs navbatchilik-bot` orqali qayta ishga tushish xabari va xatolarni tekshiring.
5. Guruhda `/setup` yozing, so'ng `/bugun`, `/jadval`, `/royxat`, `/tarix` ni sinab ko'ring.
