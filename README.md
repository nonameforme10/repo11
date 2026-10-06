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
Guruhda `/` yozilsa, barcha a'zolar, jumladan guruh adminlari va `ADMIN_IDS`
dagi bot adminlari uchun faqat `/start`, `/bugun` va `/jadval` tavsiya qilinadi.
Boshqaruv buyruqlari `ADMIN_IDS` dagi adminlarga botning shaxsiy chatida
ko'rsatiladi; shaxsiy chatdagi **Menu** tugmasi shu buyruqlar ro'yxatini ochadi.
Oddiy foydalanuvchining shaxsiy chatida ham faqat uchta asosiy buyruq bor.
`/setup` faqat guruhda qo'lda yoziladi va tavsiya ro'yxatiga kiritilmaydi.
Eski `/royxat` va `/cancel` buyruqlari ishlashda davom etadi, ammo tavsiya
ro'yxatida ko'rsatilmaydi.

- `/setup` — asosiy boshqaruv guruhini tanlash (bot admini, guruhda); barcha guruh obunalari va joriy davra saqlanadi
- `/bugun` — bugungi navbatchi
- `/jadval` — navbatchilik boshlanish sanalari va kelgusi navbatchilar
- `/royxat` — barcha a'zolar va davra holati
- `/tarix` — oxirgi 30 kunlik tarix
- `/bajarildi` — bugungi vazifani bajarilgan deb belgilash
- `/ism_qosh Ism Familiya @username` — a'zo qo'shish
- `/ism_ochir 3` — `/royxat` dagi raqam bo'yicha o'chirish
- `/zaxira` — DB snapshot (names + state + history) ni shaxsiy chatga yuborish
- `/admin` — admin paneli va tasdiqlash bilan yangi davra boshlash
- `/elon` — bugungi navbatchini teg bilan hozir e'lon qilish: guruhdan yuborilsa shu guruhga, shaxsiy chatdan yuborilsa barcha obuna guruhlarga
- `/odamlar` — odamlarni inline tugmalar orqali qo'shish, ismini tahrirlash va o'chirish
- `/bekor` yoki `/cancel` — ism kiritish so'rovini bekor qilish

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
6-oktabrda, Bahromniki 8-oktabrda, keyingi a'zoniki 10-oktabrda boshlanadi.
Jadval, bugungi navbatchi xabari va odam kartochkasida **faqat boshlanish
sanasi** ko'rsatiladi: `06.10.2026`, `08.10.2026`, `10.10.2026`. Sana oralig'i
yoki tugash sanasi yozilmaydi. Ikkinchi kuni ham shu navbatning boshlanish
sanasi saqlanadi: 7-oktabrda Azim uchun `06.10.2026` ko'rinadi.
Admin `/bajarildi` yoki **✅ Bajarildi** tugmasi bilan tasdiqlasa ham, birinchi
kun tugashi bilan navbat o'tmaydi: Azim 6-oktabrda bajarildi deb belgilansa,
7-oktabrda ham Azim qoladi, Bahrom 8-oktabrda boshlaydi. Tasdiqlash ikkinchi
kuni ham saqlanadi; uni qayta bosish shart emas.

Ikki kun tugaganida navbatchilik hali bajarildi deb belgilanmagan bo'lsa,
shu a'zo navbatchi bo'lib qoladi va kelgusi navbatlar suriladi. Masalan,
Azim ikki kundan keyin ham tasdiqlanmasa, 8-oktabrda ham Azim qoladi va
uning boshlanish sanasi `06.10.2026` bo'lib qoladi. Azim 8-oktabrda bajarildi
deb belgilansa, Bahrom 9-oktabrda navbatchilikni boshlaydi. Bot bir necha kun
ishlamasa ham bajarilmagan navbatchilik va
ikki kunlik davrning boshlangan sanasi saqlanadi. Yangi davra oxirgi a'zo
kamida ikki kun navbatchi bo'lib, bajarildi deb belgilangandan keyin boshlanadi.

Oddiy guruhda `/bugun` va `/jadval` faqat matnli navbatchilik xabarini yuboradi;
bu buyruqlar va avtomatik e'londa menyu yoki tugmalar chiqmaydi. Bot qo'shilgan
har bir guruh avtomatik obuna bo'ladi. Guruhdan xabar kelganda ham obuna
tekshiriladi; avval ulangan guruhlar saqlanadi. `/setup` talab qilinmaydi.
Bot guruhdan chiqarilsa faqat shu guruhning obunasi o'chadi. Guruh superguruhga
aylansa, obuna va yetkazilgan e'lon holati yangi Telegram ID'ga ko'chadi.
Barcha guruhlar bir xil odamlar ro'yxati va navbatchilik jadvalidan foydalanadi.
Guruh qo'shish yoki olib tashlash ro'yxat va joriy davrani o'zgartirmaydi.

`.env` dagi `ADMIN_IDS` bot adminlarini belgilaydi. Birinchi ulangan guruh asosiy
boshqaruv guruhi bo'ladi; `ADMIN_IDS` dagi bot admini `/setup` orqali boshqa
asosiy guruhni tanlashi mumkin. Bu tanlov barcha guruhlarning e'lon obunalarini
saqlaydi. Bot asosiy guruhdan chiqarilsa, qolgan guruhlarga e'lon davom etadi;
boshqaruv uchun tanlangan guruh o'z-o'zidan almashtirilmaydi. Asosiy guruhning egasi va Telegram adminlari shu guruh ichida umumiy
ro'yxatni boshqara oladi. Ularning huquqi har bir so'rovda Telegram orqali
tekshiriladi; bot guruhda admin bo'lishi kerak. Qo'shimcha obuna guruhlarda
umumiy ro'yxatni boshqarish uchun `ADMIN_IDS` talab qilinadi. Shaxsiy chatdagi
boshqaruv ham `ADMIN_IDS` dagi adminlar uchun mavjud.

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
6-oktabr, 8-oktabr, 10-oktabr kabi ikki kun oralig'ida chiqadi. Birinchi kuni
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
Har bir guruhning yetkazilgan e'lon sanasi alohida saqlanadi. Bir guruhga
yuborish xatosi qolgan guruhlarga xabar yuborishni to'xtatmaydi; keyingi
tekshiruv faqat e'loni yetkazilmagan guruhlarga qayta urinadi. Parallel
tekshiruvlar bir xil e'lonni takrorlamaydi. Admin `/elon` bilan navbatchini
istalgan kuni, jumladan ikkinchi kuni ham hozir e'lon qilishi mumkin.

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
Guruh obunalari va ularning alohida e'lon holatlari `bot_state` ichidagi
`group_subscriptions` JSON qiymatida saqlanadi. Eski bazaning `chat_id` va
yetkazilgan e'lon holati birinchi ishga tushishda shu ro'yxatga o'tkaziladi.

---

## 9. Qo'lda tekshirish

1. `.env`ga haqiqiy token va admin ID kiriting, `DATABASE_URL`ni to'ldiring.
2. `python3 migrate_to_pg.py` bilan mavjud ma'lumotlarni import qiling.
3. `pm2 start ecosystem.config.js` bilan botni ishga tushiring.
4. `pm2 logs navbatchilik-bot` orqali qayta ishga tushish xabari va xatolarni tekshiring.
5. Botni ikki guruhga qo'shing va har ikkisida `/bugun` hamda `/jadval` ni sinab ko'ring; `/` ro'yxatida faqat `/start`, `/bugun` va `/jadval` borligini tekshiring. Asosiy boshqaruv guruhini tanlash uchun shu guruhda `/setup` yuboring.
6. `ADMIN_IDS` dagi admin sifatida botning shaxsiy chatida `/start` yuboring va **Menu** orqali `/admin`, `/odamlar` va boshqa boshqaruv buyruqlarini tekshiring; `/setup` bu ro'yxatda bo'lmaydi.
