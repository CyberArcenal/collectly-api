import os
from dotenv import load_dotenv

load_dotenv()
# PayMongo API keys and URLs
PAYMONGO_SECRET_KEY = os.getenv("PAYMONGO_SECRET_KEY")  # Secret key for server-side API calls
PAYMONGO_PUBLIC_KEY = os.getenv("PAYMONGO_PUBLIC_KEY")  # Public key for client-side (if needed)
PAYMONGO_WEBHOOK_SECRET = os.getenv("PAYMONGO_WEBHOOK_SECRET")  # For verifying webhook signatures
PAYMONGO_BASE_URL = os.getenv("PAYMONGO_BASE_URL")

# Wallet top-up redirect URLs
WALLET_TOPUP_SUCCESS_URL = os.getenv("WALLET_TOPUP_SUCCESS_URL")
WALLET_TOPUP_FAILED_URL = os.getenv("WALLET_TOPUP_FAILED_URL")