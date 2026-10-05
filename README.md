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

- `/setup` — guruhni ulash va yangi davra boshlash (admin, guruhda)
- `/bugun` — bugungi navbatchi
- `/jadval` — kelgusi navbatchilar
- `/royxat` — barcha a'zolar va davra holati
- `/tarix` — oxirgi 30 kunlik tarix
- `/bajarildi` — bugungi vazifani bajarilgan deb belgilash
- `/ism_qosh Ism Familiya @username` — a'zo qo'shish
- `/ism_ochir 3` — `/royxat` dagi raqam bo'yicha o'chirish
- `/zaxira` — DB snapshot (names + state + history) ni shaxsiy chatga yuborish

Admin huquqi faqat `.env` dagi `ADMIN_IDS` orqali belgilanadi.

---

## 7. E'lonlar jadvali

Bot har kuni **Toshkent vaqti bilan 08:00** da guruhga bugungi navbatchi haqida xabar yuboradi.
Shu maqsadda `python-telegram-bot` ning `job_queue.run_daily` funksiyasi ishlatiladi — vaqt zona bilan (tzinfo=Asia/Tashkent) berilgan.

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
