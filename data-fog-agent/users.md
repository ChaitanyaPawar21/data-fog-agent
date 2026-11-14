# Demo users

Log in at http://localhost:3000. All of these come from `demo/generate.py` (company: **Chaiwala Co.**).
Demo-only credentials: change or delete these users before real people get access.

There are two roles: **admin** (sees and manages everything) and **member** (sees only what their
group is allowed to see). The groups are sales, finance, hr and support, so five users cover every type.

| # | Who | Email | Password | Role | Group |
|---|---|---|---|---|---|
| 1 | Admin | `admin@chaiwala.co` | the one you set (see below) | admin | (all) |
| 2 | Priya Sharma | `priya.sharma@chaiwala.co` | `chaiwala-demo` | member | sales |
| 3 | Anita Desai | `anita.desai@chaiwala.co` | `chaiwala-demo` | member | finance |
| 4 | Meera Iyer | `meera.iyer@chaiwala.co` | `chaiwala-demo` | member | hr |
| 5 | Arjun Nair | `arjun.nair@chaiwala.co` | `chaiwala-demo` | member | support |

Tested on 2026-11-14: logins 2–5 work. Only the first two "things to try" below have been checked end to end. The admin doesn't accept `chaiwala-demo`, because the admin
account existed before the demo ran and the demo never overwrites existing users. To set a new
admin password (it asks for it, nothing is echoed):

```bash
docker compose exec backend uv run python -c "import getpass; from sqlalchemy import select; from app.auth import hash_password; from app.db import system_session; from app.models import User; db=system_session(); u=db.scalar(select(User).where(User.email=='admin@chaiwala.co')); u.password_hash=hash_password(getpass.getpass('New password: ')); db.commit(); print('done')"
```

Three more members exist if you need a second person in a group: Rahul Verma (sales), Vikram Rao
(finance) and Kavya Reddy (support), all with the password `chaiwala-demo`.

## What each user can see

| User | Databases | Drive folder | Slack channels |
|---|---|---|---|
| Admin | sales_db, finance_db, support_db | sales, finance, hr | #data, #sales, #finance |
| Priya (sales) | sales_db: customers, orders, orders_v2, ord_dtl, ... (8 tables) | sales | #data, #sales |
| Anita (finance) | finance_db: revenue_monthly, refunds, tbl_cst_mst | finance | #data, #finance |
| Meera (hr) | none | hr (employee_list.xlsx) | #data |
| Arjun (support) | support_db: tickets, app_logs | none | #data |

Personal data (names, emails, phones, PAN, Aadhaar, addresses) is masked before anything is sent
to the LLM.

## Things to try

- **Priya:** "What were our latest orders?" The agent should pick `orders_v2` and convert paise to rupees.
- **Anita:** "What was net revenue in March 2026?" The answer is ₹241,626.
- **Meera:** "Who works in Retail?" Names and Aadhaar numbers should come back masked.
- **Arjun:** "Show me the revenue numbers." This should be refused, because support can't see finance data.
- **Admin:** open **Sources** and click **Sync now**, then look at the **Catalogue**.

The data itself is in [demo-data.md](demo-data.md).
