# Demo data

This is everything Data Fog Agent works on: the fictional tea company **Chaiwala Co.**, built by
`demo/generate.py`. Each table, file and channel below shows its first 5 rows. All of the people,
phone numbers, PAN and Aadhaar numbers are fake.

The data is messy on purpose, because the agent has to cope with real-company mess:

| Trap | Where | What the right answer is |
|---|---|---|
| Stale table | `orders` stopped in Dec 2025 | Use `orders_v2` |
| Units | `orders_v2.amount` is in **paise**, `orders.amount` in rupees | Divide `orders_v2` by 100 |
| Duplicate tables | `customers`, `customers_new`, `customers_backup_2023` | `customers_new` is current (Priya owns it) |
| Cryptic names | `finance_db.tbl_cst_mst` with `col_1..col_7` | Only the values show it's a customer master with PAN |
| PII in free text | `tickets.notes`, `app_logs.message` | Aadhaar, phones and emails get masked |
| Duplicate files | `sales_report_final`, `_v2`, `_FINAL_use_this` and `orders_export_jan (1).csv` | `FINAL_use_this` is correct (per #sales) |
| Gross vs net | Sales shows gross, finance reports net of returns | `revenue_monthly.net_revenue` |
| Definition drift | The policy says active means 60 days, the `active_customers` view uses 90 | Flag it |

Regenerate it after `generate.py` (reads with the same SELECT-only role the connectors use):

```bash
docker compose exec -T backend uv run python - < demo/dump_data.py > demo-data.md
```
(That replaces this intro too.)

## sales_db (Postgres)

### active_customers — 181 rows

| customer_id |
|---|
| 116 |
| 71 |
| 68 |
| 267 |
| 51 |

### customers — 218 rows

| customer_id | name | email | phone | city | signup_date |
|---|---|---|---|---|---|
| 1 | Isaac Bakshi | isaac.bakshi1@rediffmail.com | 9204451095 | Sambalpur | 2022-01-02 |
| 2 | Oni Kannan | oni.kannan2@gmail.com | 6065082363 | Giridih | 2022-01-07 |
| 3 | Caleb Sharaf | caleb.sharaf3@rediffmail.com | 7452991960 | Burhanpur | 2022-01-14 |
| 4 | Kamala Pant | kamala.pant4@gmail.com | 6700235246 | Shimoga | 2022-01-15 |
| 5 | Divya Kala | divya.kala5@rediffmail.com | 9516854671 | Tenali | 2022-01-24 |

### customers_backup_2023 — 130 rows

| customer_id | name | email | phone | city | signup_date |
|---|---|---|---|---|---|
| 1 | Isaac Bakshi | isaac.bakshi1@rediffmail.com | 9204451095 | Sambalpur | 02/01/2022 |
| 2 | Oni Kannan | oni.kannan2@gmail.com | 6065082363 | Giridih | 07/01/2022 |
| 3 | Caleb Sharaf | caleb.sharaf3@rediffmail.com | 7452991960 | Burhanpur | 14/01/2022 |
| 4 | Kamala Pant | kamala.pant4@gmail.com | 6700235246 | Shimoga | 15/01/2022 |
| 5 | Divya Kala | divya.kala5@rediffmail.com | 9516854671 | Tenali | 24/01/2022 |

### customers_new — 300 rows

| customer_id | full_name | email | phone | city | address | signup_date |
|---|---|---|---|---|---|---|
| 1 | Isaac Bakshi | isaac.bakshi1@rediffmail.com | 9204451095 | Sambalpur | 18/196, Balan Nagar, Bellary-890838 | 2022-01-02 |
| 2 | Oni Kannan | oni.kannan2@gmail.com | 6065082363 | Giridih | 654, Kale Street, Kota 615594 | 2022-01-07 |
| 3 | Caleb Sharaf | caleb.sharaf3@rediffmail.com | 7452991960 | Burhanpur | 95/931, Kari Nagar, Vijayawada 164752 | 2022-01-14 |
| 4 | Kamala Pant | kamala.pant4@gmail.com | 6700235246 | Shimoga | 32/76, Kanda Road, Pimpri-Chinchwad 056413 | 2022-01-15 |
| 5 | Divya Kala | divya.kala5@rediffmail.com | 9516854671 | Tenali | H.No. 23, Yohannan Circle, Bhimavaram-965328 | 2022-01-24 |

### ord_dtl — 5,981 rows

| order_id | line_no | sku | qty | unit_price |
|---|---|---|---|---|
| 1 | 1 | GINGER-CHAI-250G | 2 | 32900 |
| 1 | 2 | ELAICHI-CHAI-250G | 3 | 36900 |
| 1 | 3 | ASSAM-GOLD-500G | 2 | 54900 |
| 2 | 1 | DARJEELING-100G | 2 | 44900 |
| 2 | 2 | ASSAM-GOLD-500G | 2 | 54900 |

### orders — 1,624 rows

| order_id | customer_id | order_date | channel | store_code | amount | status |
|---|---|---|---|---|---|---|
| 1 | 100 | 2025-01-01 | online | ONLINE | 2863.00 | completed |
| 2 | 98 | 2025-01-01 | retail | STORE-PUN-01 | 2894.00 | cancelled |
| 3 | 155 | 2025-01-01 | online | ONLINE | 1636.00 | completed |
| 4 | 176 | 2025-01-01 | online | ONLINE | 658.00 | completed |
| 5 | 38 | 2025-01-01 | retail | STORE-MUM-01 | 2563.00 | completed |

### orders_v2 — 3,000 rows

| order_id | customer_id | order_date | channel | store_code | amount | status |
|---|---|---|---|---|---|---|
| 1 | 100 | 2025-01-01 | online | ONLINE | 286300 | completed |
| 2 | 98 | 2025-01-01 | retail | STORE-PUN-01 | 289400 | cancelled |
| 3 | 155 | 2025-01-01 | online | ONLINE | 163600 | completed |
| 4 | 176 | 2025-01-01 | online | ONLINE | 65800 | completed |
| 5 | 38 | 2025-01-01 | retail | STORE-MUM-01 | 256300 | completed |

### sales_2023 — 24 rows

| month | channel | gross_amount |
|---|---|---|
| 2023-01-01 | online | 365974.46 |
| 2023-01-01 | retail | 253854.28 |
| 2023-02-01 | online | 394528.74 |
| 2023-02-01 | retail | 445021.68 |
| 2023-03-01 | online | 448998.04 |


## finance_db (Postgres)

### refunds — 249 rows

| refund_id | order_id | refund_date | amount | reason |
|---|---|---|---|---|
| 1 | 47 | 2025-01-17 | 3401.00 | wrong item |
| 2 | 59 | 2025-01-21 | 7032.00 | wrong item |
| 3 | 77 | 2025-01-30 | 1196.00 | not needed |
| 4 | 142 | 2025-02-10 | 3303.00 | late |
| 5 | 146 | 2025-02-07 | 1196.00 | not needed |

### revenue_monthly — 22 rows

| month | gross_revenue | returns | net_revenue |
|---|---|---|---|
| 2025-01-01 | 328663.00 | 11629.00 | 317034.00 |
| 2025-02-01 | 315836.00 | 36003.00 | 279833.00 |
| 2025-03-01 | 330446.00 | 30711.00 | 299735.00 |
| 2025-04-01 | 334264.00 | 32730.00 | 301534.00 |
| 2025-05-01 | 309870.00 | 22969.00 | 286901.00 |

### tbl_cst_mst — 300 rows

| col_1 | col_2 | col_3 | col_4 | col_5 | col_6 | col_7 |
|---|---|---|---|---|---|---|
| 1 | Isaac Bakshi | 9204451095 | isaac.bakshi1@rediffmail.com | Sambalpur | CRJPN7651X | 25000.00 |
| 2 | Oni Kannan | 6065082363 | oni.kannan2@gmail.com | Giridih | KIWPH3139G | 25000.00 |
| 3 | Caleb Sharaf | 7452991960 | caleb.sharaf3@rediffmail.com | Burhanpur | EMWPC7260Z | 5000.00 |
| 4 | Kamala Pant | 6700235246 | kamala.pant4@gmail.com | Shimoga | ZVZPY3872F | 25000.00 |
| 5 | Divya Kala | 9516854671 | divya.kala5@rediffmail.com | Tenali | FKBPM0035M | 25000.00 |


## support_db (Postgres)

### app_logs — 1,000 rows

| log_id | logged_at | level | message |
|---|---|---|---|
| 1 | 2026-10-19 20:37:18+00:00 | INFO | GET /api/products 200 453ms |
| 2 | 2026-10-13 05:33:22+00:00 | INFO | GET /api/products 201 777ms |
| 3 | 2026-10-17 22:03:01+00:00 | INFO | GET /api/cart 200 107ms |
| 4 | 2026-10-25 11:55:00+00:00 | INFO | GET /api/products 200 468ms |
| 5 | 2026-10-06 04:38:17+00:00 | WARN | login failed for maanas.misra207@yahoo.co.in |

### tickets — 400 rows

| ticket_id | customer_id | created_at | subject | status | notes |
|---|---|---|---|---|---|
| 1 | 57 | 2026-08-22 02:55:51+00:00 | Coupon | closed | Customer wants to change the delivery address before dispatc… |
| 2 | 186 | 2025-09-23 13:13:47+00:00 | Product question | closed | KYC for refund done. Customer shared Aadhaar 8331 1404 7866 … |
| 3 | 257 | 2026-05-28 15:07:10+00:00 | Damaged item | closed | Customer wants to change the delivery address before dispatc… |
| 4 | 206 | 2025-04-20 04:53:45+00:00 | Product question | closed | Refund processed to original payment method. |
| 5 | 279 | 2025-08-13 00:55:10+00:00 | Refund | closed | Refund processed to original payment method. |


## Google Drive folder (files)

### finance/metrics_policy.pdf — PDF, 1,360 bytes

### finance/targets_2026.xlsx — 24 rows

| month | channel | target_rupees |
|---|---|---|
| 2026-01-01 00:00:00 | online | 730000 |
| 2026-01-01 00:00:00 | retail | 610000 |
| 2026-02-01 00:00:00 | online | 430000 |
| 2026-02-01 00:00:00 | retail | 890000 |
| 2026-03-01 00:00:00 | online | 500000 |

### hr/employee_list.xlsx — 25 rows

| employee_id | name | email | phone | pan | aadhaar | city | department | monthly_salary |
|---|---|---|---|---|---|---|---|---|
| EMP001 | Madhavi Lata | madhavi.lata@chaiwala.co | 7185565699 | ARMPF1986Q | 6329 6571 2883 | Ozhukarai | Retail | 157000 |
| EMP002 | Mahika Aggarwal | mahika.aggarwal@chaiwala.co | 9376646770 | CJUPA3239F | 5095 0406 9408 | Morena | Ops | 56000 |
| EMP003 | Rajata Raval | rajata.raval@chaiwala.co | 7025789914 | BNOPP5727Q | 9363 9550 3283 | Panvel | Support | 70000 |
| EMP004 | Upkaar Aggarwal | upkaar.aggarwal@chaiwala.co | 7419646802 | KHKPO2916Q | 8919 2607 9625 | Ongole | Ops | 36000 |
| EMP005 | Jairaj Wason | jairaj.wason@chaiwala.co | 8973524069 | KCWPI3168J | 5891 7978 4948 | Thoothukudi | Retail | 197000 |

### sales/orders_export_jan (1).csv — 156 rows

| order_id | customer_id | order_date | channel | amount | status |
|---|---|---|---|---|---|
| 1625 | 145 | 2026-01-01 | retail | 379000 | cancelled |
| 1626 | 51 | 2026-01-01 | online | 147600 | completed |
| 1627 | 239 | 2026-01-01 | online | 131600 | completed |
| 1628 | 140 | 2026-01-02 | online | 329600 | completed |
| 1629 | 21 | 2026-01-02 | retail | 139600 | completed |

### sales/orders_export_jan.csv — 156 rows

| order_id | customer_id | order_date | channel | amount | status |
|---|---|---|---|---|---|
| 1625 | 145 | 2026-01-01 | retail | 379000 | cancelled |
| 1626 | 51 | 2026-01-01 | online | 147600 | completed |
| 1627 | 239 | 2026-01-01 | online | 131600 | completed |
| 1628 | 140 | 2026-01-02 | online | 329600 | completed |
| 1629 | 21 | 2026-01-02 | retail | 139600 | completed |

### sales/sales_report_FINAL_use_this.xlsx — 100 rows

| order_id | order_date | channel | store_code | amount_rupees |
|---|---|---|---|---|
| 2766 | 2026-09-11 00:00:00 | online | ONLINE | 4595 |
| 2767 | 2026-09-12 00:00:00 | retail | STORE-BLR-01 | 6623 |
| 2768 | 2026-09-12 00:00:00 | retail | STORE-BLR-01 | 1446 |
| 2769 | 2026-09-13 00:00:00 | online | ONLINE | 2196 |
| 2770 | 2026-09-13 00:00:00 | retail | STORE-MUM-01 | 1047 |

### sales/sales_report_final.xlsx — 100 rows

| order_id | order_date | channel | store_code | amount_rupees |
|---|---|---|---|---|
| 2721 | 2026-09-01 00:00:00 | online | ONLINE | 2703 |
| 2723 | 2026-09-01 00:00:00 | online | ONLINE | 2234 |
| 2724 | 2026-09-01 00:00:00 | online | ONLINE | 2694 |
| 2725 | 2026-09-01 00:00:00 | retail | STORE-DEL-01 | 549 |
| 2726 | 2026-09-01 00:00:00 | online | ONLINE | 1098 |

### sales/sales_report_final_v2.xlsx — 100 rows

| order_id | order_date | channel | store_code | amount_rupees |
|---|---|---|---|---|
| 2745 | 2026-09-06 00:00:00 | online | ONLINE | 1796 |
| 2746 | 2026-09-07 00:00:00 | online | ONLINE | 1905 |
| 2747 | 2026-09-07 00:00:00 | online | ONLINE | 2253 |
| 2748 | 2026-09-07 00:00:00 | online | ONLINE | 1845 |
| 2749 | 2026-09-07 00:00:00 | online | ONLINE | 6792 |


## Slack export

### #data — 9 messages

- **Vikram Rao**: Heads up: use orders_v2 not orders. The old orders table stopped updating in Dec 2025.
- **Rahul Verma**: Got it. Is the amount column the same?
- **Vikram Rao**: No! amounts in orders_v2 are in paise. Divide by 100 for rupees. orders had rupees.
- **Anita Desai**: Who owns the customer tables?
- **Vikram Rao**: Priya owns customer data. customers_new is the current one, ask her before changing it.

### #finance — 4 messages

- **Anita Desai**: Reminder: finance revenue is net of returns. Sales dashboards show gross.
- **Vikram Rao**: revenue_monthly has gross, returns and net columns if you need both.
- **Anita Desai**: Per the metrics policy an active customer ordered in the last 60 days.
- **Vikram Rao**: The active_customers view still uses 90 days. Need to fix that.

### #sales — 4 messages

- **Rahul Verma**: Which sales report is right? final, final_v2 or FINAL_use_this?
- **Priya Sharma**: Use sales_report_FINAL_use_this, it has the corrected amounts.
- **Priya Sharma**: Targets for 2026 are in finance/targets_2026.xlsx, not in the old deck.
- **Rahul Verma**: Great Diwali week in Bengaluru store!

