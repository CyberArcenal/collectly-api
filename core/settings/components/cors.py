
CSRF_TRUSTED_ORIGINS = [
    "https://oriented-frank-airedale.ngrok-free.app",
    "https://model-sharply-dane.ngrok-free.app",
    "https://vercel.app",
    "https://now.sh",
    "http://127.0.0.1",
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:8000",
    "http://localhost:8000",

]

CORS_ALLOWED_ORIGINS = [
    "https://oriented-frank-airedale.ngrok-free.app",
    "https://model-sharply-dane.ngrok-free.app",
    "https://vercel.app",
    "https://now.sh",
    "http://127.0.0.1",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://localhost:3001",
]


CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "idempotency-key",
    # "ngrok-skip-browser-warning",
]
