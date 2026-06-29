# Collectly API

**Collectly API** is the cloud backend for the [Collectly](https://github.com/CyberArcenal/collectly) debt management system. It provides a REST API for multi‑user access, data synchronization, and background processing (penalties, interest accrual, notifications). Built with **Django** and **Django REST Framework**, it enables seamless offline‑first sync for desktop clients.

---

## 🚀 Features

- **RESTful API** – full CRUD for Borrowers, Debts, Payments, Penalties, Loan Agreements, and more.
- **JWT Authentication** – secure, stateless authentication with refresh tokens.
- **Offline‑First Sync** – `pull` (get changes since last sync) and `push` (batch create/update/delete) endpoints.
- **Background Jobs** – Celery + Redis for automatic penalty application, interest accrual, and overdue reminders.
- **Audit Trail** – centralized logging of all CREATE/UPDATE/DELETE actions.
- **File Upload** – store loan agreement PDFs (local or S3).
- **Email/SMS Notifications** – using SMTP and Twilio, integrated with Celery.
- **Admin Dashboard** – Django’s built‑in admin interface for superusers.

---

## 🛠️ Tech Stack

| Layer            | Technology |
|------------------|------------|
| **Framework**    | Django 5.x, Django REST Framework |
| **Database**     | PostgreSQL (production) / SQLite (development) |
| **Background**   | Celery, Redis |
| **Auth**         | JWT (Simple JWT) |
| **File Storage** | django-storages (S3) or local |
| **Notifications**| SMTP, Twilio |
| **Deployment**   | Render / Fly.io / Railway |
| **Testing**      | Django TestCase, pytest |

---

## 📦 Installation (Local Development)

### Prerequisites
- Python 3.10+
- PostgreSQL (or SQLite for development)
- Redis (for Celery, optional)

### Step‑by‑Step

1. **Clone the repository**
   ```bash
   git clone https://github.com/CyberArcenal/collectly-api.git
   cd collectly-api
   ```

2. **Create and activate virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**  
   Create a `.env` file in the project root (see [Environment Variables](#environment-variables)).

5. **Run migrations**
   ```bash
   python manage.py migrate
   ```

6. **Create a superuser** (for admin access)
   ```bash
   python manage.py createsuperuser
   ```

7. **Start the development server**
   ```bash
   python manage.py runserver
   ```

   The API will be available at `http://localhost:8000/api/v1/`.

8. **(Optional) Start Celery worker**  
   In a separate terminal:
   ```bash
   celery -A collectly_backend worker --loglevel=info
   ```

---

## 🔐 Authentication

The API uses **JWT** (JSON Web Tokens). Obtain tokens at:

- `POST /api/v1/auth/token/` – username/password → access & refresh tokens.
- `POST /api/v1/auth/token/refresh/` – get a new access token using the refresh token.

All subsequent requests must include the token in the `Authorization` header:
```
Authorization: Bearer <your_access_token>
```

---

## 📌 API Endpoints (Overview)

All endpoints are versioned under `/api/v1/`.

| Resource               | Methods                                   | Description |
|------------------------|-------------------------------------------|-------------|
| `/auth/token/`         | POST                                      | Obtain JWT pair |
| `/auth/token/refresh/` | POST                                      | Refresh access token |
| `/borrowers/`          | GET, POST                                 | List / create borrowers |
| `/borrowers/{id}/`     | GET, PUT, PATCH, DELETE                   | Retrieve / update / soft‑delete |
| `/debts/`              | GET, POST                                 | List / create debts |
| `/debts/{id}/`         | GET, PUT, PATCH, DELETE                   | Retrieve / update / soft‑delete |
| `/debts/{id}/payments/`| GET, POST                                 | List / add payments to a debt |
| `/payments/`           | GET, POST                                 | List all / create payment |
| `/penalties/`          | GET, POST                                 | List / create penalties |
| `/loan-agreements/`    | GET, POST                                 | List / create agreements |
| `/groups/`             | GET, POST                                 | Manage debtor groups |
| `/groups/{id}/members/`| GET, POST, DELETE                         | Manage group members |
| `/audit-logs/`         | GET                                       | Retrieve audit logs (admin only) |
| `/notifications/`      | GET, POST                                 | Manage notification logs |
| `/settings/`           | GET, PUT (admin)                          | System settings (future) |

> 💡 **Full endpoint documentation** is available via Swagger UI at `/api/v1/docs/` (when DEBUG=True).

---

## 🔄 Sync Mechanism

The desktop app (Electron) can operate in **online** or **offline** mode. When online, it synchronizes data with the API.

### Pull – Get Changes
```
GET /api/v1/sync/pull?last_sync=2025-01-01T00:00:00Z&entity=debts
```
Returns all records of the specified entity that have been updated **after** `last_sync` (including soft‑deleted ones). This allows the client to fetch only what changed.

### Push – Send Changes
```
POST /api/v1/sync/push
{
  "changes": [
    { "action": "create", "model": "Debt", "data": {...} },
    { "action": "update", "model": "Debt", "id": 123, "data": {...} },
    { "action": "delete", "model": "Debt", "id": 456 }
  ]
}
```
The API processes each change transactionally and returns a list of results (success/error).

> **Conflict resolution**: The API uses a **last‑write‑wins** strategy based on the `updated_at` timestamp. If a conflict occurs, the server will reject the push with a 409 response, and the client should re‑pull the latest version.

---

## ⚙️ Background Jobs (Celery)

The following tasks run automatically via Celery beat (scheduler):

- `apply_penalties` – daily, applies overdue penalties to debts.
- `accrue_interest` – daily, adds interest to active debts based on their rate and calculation period.
- `send_overdue_reminders` – daily, sends email/SMS reminders for debts that are due soon or overdue.
- `cleanup_audit_logs` – weekly, deletes audit logs older than the retention period.

To run the scheduler:
```bash
celery -A collectly_backend beat --loglevel=info
```

---

## ☁️ Deployment

We recommend deploying on **Render** (free tier available) using the included `render.yaml`.

1. Push your code to a GitHub repository.
2. Create a new Web Service on Render, point it to your repo, and select "Django".
3. Render will automatically set up:
   - Web server (gunicorn)
   - PostgreSQL database
   - Redis (if needed)
   - Celery worker

Alternatively, you can deploy manually on **Fly.io**, **Railway**, or **AWS**.

---

## 🔧 Environment Variables

Create a `.env` file with the following keys:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key (keep secret!) |
| `DEBUG` | Set to `False` in production |
| `DATABASE_URL` | PostgreSQL connection string |
| `ALLOWED_HOSTS` | Comma‑separated list of allowed hosts |
| `REDIS_URL` | Redis URL for Celery (e.g., `redis://localhost:6379/0`) |
| `EMAIL_HOST` | SMTP host |
| `EMAIL_PORT` | SMTP port |
| `EMAIL_HOST_USER` | SMTP username |
| `EMAIL_HOST_PASSWORD` | SMTP password |
| `TWILIO_ACCOUNT_SID` | Twilio account SID (for SMS) |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Twilio phone number |
| `AWS_ACCESS_KEY_ID` | (Optional) S3 access key |
| `AWS_SECRET_ACCESS_KEY` | (Optional) S3 secret |
| `AWS_STORAGE_BUCKET_NAME` | (Optional) S3 bucket name |

For local development, you can use SQLite by setting `DATABASE_URL=sqlite:///db.sqlite3` (not recommended for production).

---

## 🧪 Testing

Run the test suite with:
```bash
python manage.py test
```

For coverage reports:
```bash
coverage run manage.py test
coverage report
```

---

## 📄 License

This project is proprietary software. For commercial licensing, contact:
- **CyberArcenal** – [cyberarcenal1@gmail.com](mailto:cyberarcenal1@gmail.com)

---

## 🤝 Contributing

Contributions are welcome! Please follow the standard GitHub flow:
1. Fork the repository.
2. Create a feature branch.
3. Commit your changes.
4. Open a Pull Request.

Make sure to include tests for new features.

---

## 📧 Contact

- Author: CyberArcenal
- Email: cyberarcenal1@gmail.com
- GitHub: [CyberArcenal](https://github.com/CyberArcenal)

---

*Collectly – Helping you stay on top of collections, one payment at a time.*